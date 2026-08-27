"""Sessao de navegador persistente da NFSe (NFSE-15).

Duas coisas que estes testes existem para garantir:

1. O login por certificado acontece UMA vez por sessao. Se cada nota relogasse,
   a policy do certificado entraria e sairia do registro 50 vezes por lote e o
   operador confirmaria a aliquota 50 vezes.
2. Nada fica preso quando algo da errado. Lock nao liberado torna a feature
   inutilizavel ate reiniciar o app; policy nao removida do registro afeta o
   Chrome do usuario FORA da aplicacao.
"""
from unittest.mock import MagicMock

import pytest

from app.services import nfse_session as sessao_mod


@pytest.fixture()
def sessao(monkeypatch):
    """Sessao nova por teste, com driver e policy dublados."""
    s = sessao_mod.NfseSession()

    criados = []

    def _criar(*_a, **_kw):
        # driver NOVO a cada chamada: e o que permite observar a substituicao
        # quando o anterior morre
        novo = MagicMock()
        novo.current_url = 'https://www.nfse.gov.br/EmissorNacional/Dashboard'
        criados.append(novo)
        return novo

    monkeypatch.setattr(sessao_mod, '_criar_driver_chrome', _criar)
    monkeypatch.setattr(sessao_mod.automacao, 'login_certificado', MagicMock(return_value=True))
    monkeypatch.setattr(sessao_mod, 'ativar', MagicMock(return_value=True))
    monkeypatch.setattr(sessao_mod, 'desativar', MagicMock())
    monkeypatch.setattr(sessao_mod, 'politica_nfse',
                        MagicMock(return_value=MagicMock(indice='2')))
    monkeypatch.setattr(sessao_mod, 'log_event', MagicMock())

    s._criados = criados
    return s


# --- exclusao mutua (base do HTTP 409) -------------------------------------

def test_segunda_aquisicao_falha_sem_bloquear(sessao):
    """A segunda tentativa vira HTTP 409 na hora, nao espera a primeira.

    Roda em thread com join(timeout): se o lock virar bloqueante, o teste
    FALHA em 2s em vez de pendurar a suite para sempre. Um deadlock nao e uma
    falha limpa — na CI vira um job travado, nao um teste vermelho.
    """
    import threading

    assert sessao.adquirir() is True
    resultado = []
    t = threading.Thread(target=lambda: resultado.append(sessao.adquirir()),
                         daemon=True)
    t.start()
    t.join(timeout=2)

    assert not t.is_alive(), 'adquirir() bloqueou em vez de recusar na hora'
    assert resultado == [False]
    sessao.liberar()


def test_liberar_sem_adquirir_nao_levanta(sessao):
    sessao.liberar()
    sessao.liberar()


def test_liberar_devolve_a_sessao(sessao):
    sessao.adquirir()
    sessao.liberar()
    assert sessao.adquirir() is True
    sessao.liberar()


def test_ocupada_reflete_o_lock(sessao):
    assert not sessao.ocupada
    sessao.adquirir()
    assert sessao.ocupada
    sessao.liberar()
    assert not sessao.ocupada


# --- login uma vez por sessao ----------------------------------------------

def test_primeira_chamada_cria_driver_e_loga(sessao):
    driver = sessao.garantir()
    assert driver is sessao._criados[0]
    assert sessao_mod.automacao.login_certificado.call_count == 1


def test_chamadas_seguintes_reusam_sem_relogar(sessao):
    sessao.garantir()
    sessao.garantir()
    sessao.garantir()
    assert sessao_mod.automacao.login_certificado.call_count == 1, (
        'relogar a cada nota reaplicaria a policy do certificado a cada nota')
    assert len(sessao._criados) == 1


def test_a_policy_e_aplicada_uma_vez(sessao):
    sessao.garantir()
    sessao.garantir()
    assert sessao_mod.ativar.call_count == 1


def test_driver_morto_faz_relogar(sessao):
    """Janela fechada na mao no meio do trabalho: a proxima nota nao pode
    tentar dirigir um navegador que nao existe mais.

    `garantir()` precisa ser chamado COM o driver morto — curar o driver antes
    da chamada faria a sessao (corretamente) reusa-lo, e o teste passaria sem
    exercitar o caminho de substituicao.
    """
    primeiro = sessao.garantir()
    type(primeiro).current_url = property(
        lambda self: (_ for _ in ()).throw(Exception('sessao morta')))
    assert sessao.driver_vivo() is False

    segundo = sessao.garantir()
    assert segundo is not primeiro
    assert len(sessao._criados) == 2
    assert sessao_mod.automacao.login_certificado.call_count == 2
    assert primeiro.quit.called, 'o driver morto precisa ser descartado'


def test_sessao_deslogada_refaz_o_login(sessao):
    """Sessao expirada no portal: o driver esta vivo, mas caiu para o login."""
    driver = sessao.garantir()
    driver.current_url = 'https://www.nfse.gov.br/EmissorNacional/Login'
    sessao.garantir()
    assert sessao_mod.automacao.login_certificado.call_count == 2


# --- aliquota: trava por sessao (NFSE-12) ----------------------------------

def test_aliquota_comeca_nao_confirmada(sessao):
    assert sessao.aliquota_confirmada is False
    assert sessao.aliquota is None


def test_confirmar_trava_por_sessao(sessao):
    sessao.confirmar_aliquota('3,87')
    assert sessao.aliquota_confirmada is True
    assert sessao.aliquota == '3,87'


def test_ler_aliquota_usa_a_sessao_existente(sessao, monkeypatch):
    monkeypatch.setattr(sessao_mod.automacao, 'ler_aliquota_simples',
                        MagicMock(return_value='3,87'))
    assert sessao.ler_aliquota() == '3,87'
    # ler nao confirma: quem confirma e o operador
    assert sessao.aliquota_confirmada is False


# --- encerramento: nada pode ficar preso -----------------------------------

def test_encerrar_fecha_o_driver_e_libera_a_policy(sessao):
    driver = sessao.garantir()
    sessao.encerrar()
    assert driver.quit.called
    assert sessao_mod.desativar.called
    assert sessao.driver_vivo() is False


def test_encerrar_libera_a_policy_mesmo_se_o_quit_levantar(sessao):
    """A policy vive no registro do Windows e afeta o Chrome do usuario FORA da
    aplicacao: deixa-la para tras por causa de um quit() com erro seria pior do
    que o proprio erro."""
    driver = sessao.garantir()
    driver.quit.side_effect = Exception('driver ja morreu')
    sessao.encerrar()
    assert sessao_mod.desativar.called


def test_encerrar_e_idempotente(sessao):
    sessao.garantir()
    sessao.encerrar()
    sessao.encerrar()
    assert sessao.driver_vivo() is False


def test_encerrar_sem_sessao_aberta_nao_levanta(sessao):
    sessao.encerrar()


def test_encerrar_zera_a_confirmacao_da_aliquota(sessao):
    """Sessao nova exige conferir a aliquota de novo: ela muda mes a mes e o
    valor confirmado pertence a sessao anterior."""
    sessao.garantir()
    sessao.confirmar_aliquota('3,87')
    sessao.encerrar()
    assert sessao.aliquota_confirmada is False
    assert sessao.aliquota is None


def test_falha_no_login_nao_deixa_policy_presa(sessao, monkeypatch):
    monkeypatch.setattr(sessao_mod.automacao, 'login_certificado',
                        MagicMock(side_effect=RuntimeError('certificado errado')))
    with pytest.raises(RuntimeError):
        sessao.garantir()
    assert sessao_mod.desativar.called, 'policy ficou no registro apos falha de login'
    assert sessao.driver_vivo() is False


# --- politica do certificado (ND-006) --------------------------------------

def test_politica_usa_indice_proprio_e_nao_herda_o_certificado_do_rs(monkeypatch):
    """O RS aponta para um e-CPF e a NFSe para o e-CNPJ do escritorio; alem
    disso a policy e gravada por indice, e reusar o do RS apagaria a dele."""
    valores = {
        'NFSE_CERT_AUTOSELECT_ENABLED': 'true',
        'NFSE_CERT_AUTOSELECT_PATTERN': 'https://certificado.nfse.gov.br',
        'NFSE_CERT_AUTOSELECT_POLICY_INDEX': '2',
        'NFSE_CERT_AUTOSELECT_ISSUER_CN': 'AC SyngularID Multipla',
        'NFSE_CERT_AUTOSELECT_SUBJECT_CN': 'FULANO:11222333000181',
    }
    monkeypatch.setattr(sessao_mod, 'get_config_value',
                        lambda nome, default=None: valores.get(nome, default))
    politica = sessao_mod.politica_nfse()
    assert politica.indice == '2'
    assert politica.indice != '1', 'o indice 1 e do RS'
    assert politica.subject_cn.endswith('11222333000181')
    montada = politica.montar()
    assert montada['filter']['ISSUER']['CN'] == 'AC SyngularID Multipla'


def test_politica_desligada_devolve_none(monkeypatch):
    monkeypatch.setattr(sessao_mod, 'get_config_value',
                        lambda nome, default=None: 'false' if 'ENABLED' in nome else default)
    assert sessao_mod.politica_nfse() is None


# --- fiacao da configuracao (bug real, escapou dos testes acima) -----------

def test_as_envs_da_nfse_existem_no_config_da_aplicacao(app):
    """Regressao de um bug que chegou ao uso real.

    `get_config_value` le de `current_app.config` dentro de um request e devolve
    o DEFAULT quando a chave nao existe — nunca cai para `os.environ`. As envs
    NFSE_CERT_AUTOSELECT_* estavam so no `.env`, sem declaracao em `config.py`:
    dentro da aplicacao `politica_nfse()` devolvia None, nenhuma policy era
    gravada e o dialogo de certificado do Chrome aparecia para o operador.

    Os testes de politica acima nao pegaram porque monkeypatcham
    `get_config_value` — dublavam justamente a peca quebrada. Este roda com o
    config real da aplicacao.
    """
    esperadas = (
        'NFSE_CERT_AUTOSELECT_ENABLED',
        'NFSE_CERT_AUTOSELECT_PATTERN',
        'NFSE_CERT_AUTOSELECT_POLICY_INDEX',
        'NFSE_CERT_AUTOSELECT_ISSUER_CN',
        'NFSE_CERT_AUTOSELECT_SUBJECT_CN',
    )
    faltando = [nome for nome in esperadas if nome not in app.config]
    assert not faltando, (
        f'sem declaracao em config.py, get_config_value devolve o default e a '
        f'auto-selecao do certificado nao acontece: {faltando}')


def test_politica_e_montada_a_partir_do_config_da_aplicacao(app):
    """Prova a fiacao ponta a ponta, sem dublar get_config_value."""
    with app.app_context():
        app.config.update(
            NFSE_CERT_AUTOSELECT_ENABLED=True,
            NFSE_CERT_AUTOSELECT_PATTERN='https://certificado.nfse.gov.br',
            NFSE_CERT_AUTOSELECT_POLICY_INDEX='2',
            NFSE_CERT_AUTOSELECT_ISSUER_CN='AC SyngularID Multipla',
            NFSE_CERT_AUTOSELECT_SUBJECT_CN='FULANO:11222333000181',
        )
        politica = sessao_mod.politica_nfse()

    assert politica is not None, 'policy nao montada dentro do app context'
    assert politica.montar() is not None, 'filtro vazio: o Chrome ignoraria a policy'
    assert politica.indice == '2'


def test_indice_da_nfse_nunca_colide_com_o_do_rs(app):
    """Mesmo indice sobrescreveria a policy do RS no registro do Windows."""
    assert (str(app.config.get('NFSE_CERT_AUTOSELECT_POLICY_INDEX'))
            != str(app.config.get('RS_CERT_AUTOSELECT_POLICY_INDEX')))


# --- reuso do navegador fora do painel -------------------------------------
#
# O operador sai do Dashboard na PRIMEIRA acao: ler a aliquota vai para
# /Perfil/Configuracao e preencher uma nota termina em /DPS/... Exigir o painel
# para reusar a sessao fazia o proximo `garantir()` fechar o Chrome e pedir o
# certificado de novo — o oposto do que a sessao persistente existe para fazer.

URLS_DEPOIS_DO_LOGIN = [
    'https://www.nfse.gov.br/EmissorNacional/Perfil/Configuracao',
    'https://www.nfse.gov.br/EmissorNacional/DPS/Pessoas?idr=RXN1Q0x5',
    'https://www.nfse.gov.br/EmissorNacional/DPS/EmitirNFSe?idr=RXN1Q0x5',
    'https://www.nfse.gov.br/EmissorNacional/DPS/NFSe?idr=RXN1Q0x5',
]


@pytest.mark.parametrize('url', URLS_DEPOIS_DO_LOGIN)
def test_reusa_o_navegador_fora_do_painel(sessao, url):
    driver = sessao.garantir()
    driver.current_url = url

    assert sessao.garantir() is driver
    assert sessao_mod.automacao.login_certificado.call_count == 1, (
        f'sair do painel para {url} nao pode custar um novo certificado')
    assert len(sessao._criados) == 1


def test_ler_aliquota_nao_derruba_a_sessao(sessao, monkeypatch):
    """Sequencia real do operador: abrir portal, conferir aliquota, preencher.
    O certificado tem de ser pedido uma vez so nas tres."""
    def _le(driver):
        driver.current_url = 'https://www.nfse.gov.br/EmissorNacional/Perfil/Configuracao'
        return '3,87'
    monkeypatch.setattr(sessao_mod.automacao, 'ler_aliquota_simples', _le)

    sessao.garantir()
    sessao.ler_aliquota()
    sessao.garantir()

    assert sessao_mod.automacao.login_certificado.call_count == 1
    assert sessao_mod.ativar.call_count == 1


def test_relogin_devolve_a_policy_antiga(sessao):
    """O refcount da policy e por indice e so zera com o mesmo numero de
    desativacoes. Empilhar duas ativacoes e liberar uma deixa a chave
    AutoSelectCertificateForUrls no registro depois de encerrar a sessao."""
    primeiro = sessao.garantir()
    primeiro.current_url = 'https://www.nfse.gov.br/EmissorNacional/Login'
    sessao.garantir()
    sessao.encerrar()

    assert sessao_mod.ativar.call_count == sessao_mod.desativar.call_count, (
        'ativacoes e desativacoes desbalanceadas deixam policy presa no registro')


def test_tem_driver_nao_fala_com_o_navegador(sessao):
    """Checagem barata para o polling: le atributo, nao consulta o Selenium."""
    assert sessao.tem_driver is False

    driver = sessao.garantir()
    driver.current_url = MagicMock(
        side_effect=AssertionError('tem_driver nao pode consultar a URL'))
    assert sessao.tem_driver is True

    sessao.encerrar()
    assert sessao.tem_driver is False
