"""Lote de manifestacao nos tres modos (MANIF-14, MANIF-15, MANIF-18).

Os tres modos sao o MESMO laco, parametrizado por duas coisas so: o que entra na
fila e quando o certificado troca. Duplicar o laco por modo faria uma correcao
de desfecho valer para um e nao para os outros.

Nenhum teste toca a rede: `manifestar` e injetado.
"""
from app import db
from app.automation import batch_state
from app.models import (
    CertificadoEmpresa,
    ChaveManifestacao,
    Empresa,
    EstadoCertificado,
    StatusManifestacao,
)
from app.services import circuit_breaker
from app.services import manifestador_lote as lote
from app.services import manifestador_service as svc

CHAVES = [
    '43170107461248000107650010000045391000045390',
    '43170107461248000107650010000045401000045404',
    '43170107461248000107650010000045751000045752',
]


def _empresa(nome, cnpj, pronta=True):
    emp = Empresa(nome=nome, cnpj=cnpj, estado='RS', cidade='Imbé')
    db.session.add(emp)
    db.session.commit()
    emp.certificado = CertificadoEmpresa(
        caminho=f'Z:/{nome}.pfx',
        estado=EstadoCertificado.PRONTO if pronta else EstadoCertificado.VENCIDO)
    db.session.commit()
    return emp


def _chave(empresa, chave, status=StatusManifestacao.PENDENTE,
           competencia='2017-01'):
    linha = ChaveManifestacao(chave=chave, empresa_id=empresa.id,
                              competencia=competencia, status=status)
    db.session.add(linha)
    db.session.commit()
    return linha


# --- montagem da fila (MANIF-14) --------------------------------------------

def test_modo_individual_enfileira_uma_chave_so(app, ids):
    with app.app_context():
        emp = _empresa('A', '11.222.333/0001-81')
        alvo = _chave(emp, CHAVES[0])
        _chave(emp, CHAVES[1])

        alvos = lote.calcular_alvos(modo='individual', chave_id=alvo.id)

        assert alvos['ids'] == [alvo.id]
        assert alvos['total'] == 1


def test_modo_empresa_enfileira_as_pendentes_daquela_empresa(app, ids):
    with app.app_context():
        emp_a = _empresa('A', '11.222.333/0001-81')
        emp_b = _empresa('B', '22.333.444/0001-92')
        c1 = _chave(emp_a, CHAVES[0])
        c2 = _chave(emp_a, CHAVES[1])
        _chave(emp_b, CHAVES[2])

        alvos = lote.calcular_alvos(modo='empresa', empresa_id=emp_a.id)

        assert alvos['ids'] == [c1.id, c2.id]


def test_modo_carteira_agrupa_por_empresa(app, ids):
    """Agrupar nao e estetica: cada grupo abre UMA conexao com UM certificado, e
    intercalar empresas multiplicaria handshakes."""
    with app.app_context():
        emp_a = _empresa('A', '11.222.333/0001-81')
        emp_b = _empresa('B', '22.333.444/0001-92')
        a1 = _chave(emp_a, CHAVES[0])
        b1 = _chave(emp_b, CHAVES[1])
        a2 = _chave(emp_a, CHAVES[2])

        alvos = lote.calcular_alvos(modo='carteira')

        posicoes = [alvos['ids'].index(x) for x in (a1.id, a2.id)]
        assert abs(posicoes[0] - posicoes[1]) == 1
        assert set(alvos['ids']) == {a1.id, a2.id, b1.id}


def test_fila_respeita_o_filtro_de_competencia(app, ids):
    """O lote enfileira EXATAMENTE o que a tela mostra; divergir manifestaria
    notas que o operador nao esta olhando."""
    with app.app_context():
        emp = _empresa('A', '11.222.333/0001-81')
        julho = _chave(emp, CHAVES[0], competencia='2017-07')
        _chave(emp, CHAVES[1], competencia='2017-08')

        alvos = lote.calcular_alvos(modo='empresa', empresa_id=emp.id,
                                    competencia='2017-07')

        assert alvos['ids'] == [julho.id]


def test_fila_usa_a_regra_unica_de_manifestavel(app, ids):
    """Se a fila divergisse de `manifestavel`, o lote enfileiraria o que o
    servico recusa — e travaria sem explicacao."""
    with app.app_context():
        emp = _empresa('A', '11.222.333/0001-81')
        pendente = _chave(emp, CHAVES[0])
        _chave(emp, CHAVES[1], status=StatusManifestacao.MANIFESTADA)
        _chave(emp, CHAVES[2], status=StatusManifestacao.DUPLICATA)

        alvos = lote.calcular_alvos(modo='carteira')

        assert alvos['ids'] == [pendente.id]


def test_fila_vazia_devolve_zero_sem_erro(app, ids):
    with app.app_context():
        alvos = lote.calcular_alvos(modo='carteira')
        assert alvos['ids'] == []
        assert alvos['total'] == 0


# --- grupo pulado sem abortar (MANIF-15) ------------------------------------

def test_empresa_sem_certificado_pronto_tem_o_grupo_inteiro_pulado(app, ids,
                                                                   monkeypatch):
    """E o lote segue: com 24 das 93 empresas sem certificado utilizavel,
    abortar no primeiro grupo faria o modo carteira nunca terminar."""
    with app.app_context():
        ruim = _empresa('SEM CERT', '11.222.333/0001-81', pronta=False)
        boa = _empresa('OK', '22.333.444/0001-92')
        _chave(ruim, CHAVES[0])
        _chave(ruim, CHAVES[1])
        c_boa = _chave(boa, CHAVES[2])

        alvos = lote.calcular_alvos(modo='carteira')
        pulados = lote.grupos_sem_certificado(alvos['ids'])

        assert 'SEM CERT' in pulados
        assert c_boa.id in alvos['ids']


def test_grupos_sem_certificado_nomeia_a_empresa(app, ids):
    """"2 empresas puladas" manda o operador caçar quais; o nome ele resolve."""
    with app.app_context():
        ruim = _empresa('BOLL REPRESENTACOES', '11.222.333/0001-81',
                        pronta=False)
        c = _chave(ruim, CHAVES[0])

        pulados = lote.grupos_sem_certificado([c.id])

        assert list(pulados) == ['BOLL REPRESENTACOES']
        assert 'vencido' in pulados['BOLL REPRESENTACOES']


# --- emissao de um item -----------------------------------------------------

class _ManifestarFalso:
    def __init__(self, resultados=None):
        self.chamadas = []
        self.resultados = resultados or {}

    def __call__(self, chave_id, tipo_evento=None, **kwargs):
        self.chamadas.append({'chave_id': chave_id, 'tipo_evento': tipo_evento})
        return self.resultados.get(
            chave_id, svc.Resultado(True, 'Manifestada.'))


def test_item_delega_para_a_costura_com_o_tipo_do_lote(app, ids, monkeypatch):
    with app.app_context():
        emp = _empresa('A', '11.222.333/0001-81')
        linha = _chave(emp, CHAVES[0])
        falso = _ManifestarFalso()
        monkeypatch.setattr(lote, 'manifestar', falso)
        batch_state.definir_manif_opcoes(tipo_evento=svc.DESCONHECIMENTO)
        try:
            sucesso, grave, _msg = lote._manifestar_item(linha.id, None, 'exec-1')
        finally:
            batch_state.definir_manif_opcoes(tipo_evento=svc.CONFIRMACAO)

        assert sucesso is True
        assert grave is None
        assert falso.chamadas[0]['tipo_evento'] == svc.DESCONHECIMENTO


def test_falha_de_um_item_nao_e_grave(app, ids, monkeypatch):
    """Rejeicao de uma nota nao pode derrubar o lote: as outras 199 continuam."""
    with app.app_context():
        emp = _empresa('A', '11.222.333/0001-81')
        linha = _chave(emp, CHAVES[0])
        falso = _ManifestarFalso({
            linha.id: svc.Resultado(False, 'Recusada pela SEFAZ (596).')})
        monkeypatch.setattr(lote, 'manifestar', falso)

        sucesso, grave, msg = lote._manifestar_item(linha.id, None, 'exec-1')

        assert sucesso is False
        assert grave is None
        assert '596' in msg


# --- breaker (MANIF-18) -----------------------------------------------------

def test_alvo_do_breaker_e_a_sefaz():
    """Alvo EXPLICITO do fluxo, nao inferido de log (AD-026)."""
    assert lote.ALVO_BREAKER == circuit_breaker.ALVO_SEFAZ_AN


def test_falha_de_rede_alimenta_o_breaker(app, ids, monkeypatch):
    with app.app_context():
        emp = _empresa('A', '11.222.333/0001-81')
        linha = _chave(emp, CHAVES[0])
        resultado = svc.Resultado(False, 'Nao consegui falar com a SEFAZ')
        monkeypatch.setattr(lote, 'manifestar',
                            _ManifestarFalso({linha.id: resultado}))
        circuit_breaker.limpar()
        try:
            for _ in range(circuit_breaker.LIMIAR_PADRAO):
                lote._manifestar_item(linha.id, None, 'exec-1')

            assert circuit_breaker.aberto(lote.ALVO_BREAKER) is True
        finally:
            circuit_breaker.limpar()


def test_rejeicao_da_sefaz_NAO_alimenta_o_breaker(app, ids, monkeypatch):
    """A SEFAZ respondeu — ela esta no ar. Contar rejeicao de nota como portal
    fora pararia o lote inteiro por causa de uma nota invalida."""
    with app.app_context():
        emp = _empresa('A', '11.222.333/0001-81')
        linha = _chave(emp, CHAVES[0])
        resultado = svc.Resultado(
            False, 'Recusada pela SEFAZ (596): NF-e nao consta',
            _RespostaComCstat('596'))
        monkeypatch.setattr(lote, 'manifestar',
                            _ManifestarFalso({linha.id: resultado}))
        circuit_breaker.limpar()
        try:
            for _ in range(circuit_breaker.LIMIAR_PADRAO * 2):
                lote._manifestar_item(linha.id, None, 'exec-1')

            assert circuit_breaker.aberto(lote.ALVO_BREAKER) is False
        finally:
            circuit_breaker.limpar()


def test_sucesso_fecha_o_breaker(app, ids, monkeypatch):
    with app.app_context():
        emp = _empresa('A', '11.222.333/0001-81')
        linha = _chave(emp, CHAVES[0])
        monkeypatch.setattr(lote, 'manifestar', _ManifestarFalso())
        circuit_breaker.limpar()
        try:
            circuit_breaker.registrar_falha(lote.ALVO_BREAKER, mensagem='x')
            lote._manifestar_item(linha.id, None, 'exec-1')

            assert circuit_breaker.aberto(lote.ALVO_BREAKER) is False
        finally:
            circuit_breaker.limpar()


class _RespostaComCstat:
    def __init__(self, cstat):
        self.cstat = cstat
        self.xmotivo = None
        self.protocolo = None
        self.duplicidade = False
        self.indefinido = False


# --- status e modos ---------------------------------------------------------

def test_modos_declarados_sao_os_tres_da_spec():
    assert set(lote.MODOS) == {'individual', 'empresa', 'carteira'}


def test_status_traz_o_modo_e_a_chave_corrente(app, ids):
    with app.app_context():
        dados = lote.status()

        assert 'status' in dados
        assert 'modo' in dados
        assert 'chave_id' in dados


def test_lote_nao_cria_driver(app, ids):
    """Sem navegador: se o motor recebesse um `create_driver`, abriria um Chrome
    para nada e ainda brigaria com o lote de certidao pelo perfil."""
    import inspect

    fonte = inspect.getsource(lote._rodar_lote)
    assert 'create_driver=None' in fonte
