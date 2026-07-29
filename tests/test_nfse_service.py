"""Orquestracao do preenchimento de uma nota (NFSE-13/14/16/18).

A garantia central deste arquivo e negativa: no P1 a automacao **nao emite**.
Ela para na tela de revisao e o operador clica. Ha teste dedicado provando que
o botao de emitir nunca e acionado — se um refactor futuro o chamar, esse teste
quebra antes de qualquer nota fiscal errada sair.
"""
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app import db
from app.models import Empresa, LoteNfse, NotaNfse, StatusNotaNfse
from app.services import nfse_service


@pytest.fixture()
def banco(app):
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def ambiente(banco, monkeypatch):
    """Sessao, driver e steps do portal dublados; banco real."""
    driver = MagicMock()
    sessao = MagicMock()
    sessao.garantir.return_value = driver
    sessao.aliquota_confirmada = True

    monkeypatch.setattr(nfse_service, 'SESSAO', sessao)
    monkeypatch.setattr(nfse_service, 'log_event', MagicMock())
    monkeypatch.setattr(nfse_service, 'capturar_contexto_falha', MagicMock())

    automacao = MagicMock()
    automacao.esperar_revisao.return_value = True
    automacao.formatar_valor.side_effect = lambda v: f'{v:.2f}'.replace('.', ',')
    automacao.InteracaoPortalError = RuntimeError
    monkeypatch.setattr(nfse_service, 'automacao', automacao)

    return {'driver': driver, 'sessao': sessao, 'automacao': automacao}


def _nota(status=StatusNotaNfse.PRONTA, **kw):
    # reusa a empresa/lote quando ja existem: varios testes criam duas notas e
    # o CNPJ da Empresa e unico
    empresa = Empresa.query.first()
    if empresa is None:
        empresa = Empresa(nome='ACME', cnpj='11.111.111/0001-11',
                          cidade='Imbé', estado='RS')
        db.session.add(empresa)
        db.session.commit()
    lote = LoteNfse.query.first()
    if lote is None:
        lote = LoteNfse(nome_arquivo='extrato.csv', total=1)
        db.session.add(lote)
        db.session.commit()
    dados = dict(
        lote_id=lote.id, empresa_id=empresa.id, nome_csv='ACME TRANSPORTES LTDA',
        cnpj=empresa.cnpj, competencia='06/2026', valor_final=Decimal('826.09'),
        status=status,
    )
    dados.update(kw)
    nota = NotaNfse(**dados)
    db.session.add(nota)
    db.session.commit()
    return nota


# --- o caminho feliz para na revisao ---------------------------------------

def test_preenche_as_tres_etapas_e_para_na_revisao(ambiente):
    nota = _nota()
    resultado = nfse_service.preencher_nota(nota.id, hoje=date(2026, 7, 28))

    aut = ambiente['automacao']
    assert aut.preencher_etapa_pessoas.called
    assert aut.preencher_etapa_servico.called
    assert aut.preencher_etapa_tributacao.called
    assert resultado['status'] == 'aguardando_confirmacao'
    assert db.session.get(NotaNfse, nota.id).status == StatusNotaNfse.AGUARDANDO_CONFIRMACAO


def test_NUNCA_clica_no_botao_de_emitir(ambiente):
    """ND-005: no P1 a emissao e sempre um clique humano.

    O driver e um MagicMock, entao qualquer chamada seria aceita em silencio —
    por isso o teste inspeciona tudo que foi chamado no driver e no modulo de
    automacao procurando o botao de emitir.
    """
    nota = _nota()
    nfse_service.preencher_nota(nota.id, hoje=date(2026, 7, 28))

    chamadas = str(ambiente['driver'].mock_calls) + str(ambiente['automacao'].mock_calls)
    assert 'btnProsseguir' not in chamadas
    assert 'emitir' not in chamadas.lower()


def test_descricao_leva_a_competencia_da_nota(ambiente):
    nota = _nota(competencia='05/2026')
    resultado = nfse_service.preencher_nota(nota.id, hoje=date(2026, 7, 28))
    assert resultado['descricao'].endswith('05/2026')


def test_usa_a_data_de_hoje_como_data_de_competencia(ambiente):
    nota = _nota()
    nfse_service.preencher_nota(nota.id, hoje=date(2026, 7, 28))
    _, _, _, data = ambiente['automacao'].preencher_etapa_pessoas.call_args[0]
    assert data == date(2026, 7, 28)


# --- guardas de estado ------------------------------------------------------

@pytest.mark.parametrize('status', [
    StatusNotaNfse.EMPRESA_PENDENTE,
    StatusNotaNfse.INVALIDA,
    StatusNotaNfse.EMITIDA,
])
def test_status_que_nao_pode_emitir_recusa_com_motivo(ambiente, status):
    nota = _nota(status=status)
    with pytest.raises(nfse_service.NotaNaoEmitivelError) as exc:
        nfse_service.preencher_nota(nota.id)
    assert str(exc.value)
    assert not ambiente['automacao'].preencher_etapa_pessoas.called


def test_duplicata_nao_liberada_e_recusada(ambiente):
    nota = _nota(status=StatusNotaNfse.DUPLICATA, duplicata_liberada=False)
    with pytest.raises(nfse_service.NotaNaoEmitivelError) as exc:
        nfse_service.preencher_nota(nota.id)
    assert 'duplicata' in str(exc.value).lower()


def test_duplicata_liberada_pode_emitir(ambiente):
    nota = _nota(status=StatusNotaNfse.DUPLICATA, duplicata_liberada=True)
    resultado = nfse_service.preencher_nota(nota.id, hoje=date(2026, 7, 28))
    assert resultado['status'] == 'aguardando_confirmacao'


def test_falha_anterior_pode_ser_retentada(ambiente):
    nota = _nota(status=StatusNotaNfse.FALHA, erro='timeout')
    resultado = nfse_service.preencher_nota(nota.id, hoje=date(2026, 7, 28))
    assert resultado['status'] == 'aguardando_confirmacao'
    assert db.session.get(NotaNfse, nota.id).erro is None


def test_nota_inexistente_recusa(ambiente):
    with pytest.raises(nfse_service.NotaNaoEmitivelError):
        nfse_service.preencher_nota(99999)


# --- trava da aliquota (NFSE-12) -------------------------------------------

def test_sem_aliquota_confirmada_nao_preenche_nada(ambiente):
    """A aliquota muda mes a mes e sai na nota: emitir sem conferir produz
    documento fiscal com tributo errado."""
    ambiente['sessao'].aliquota_confirmada = False
    nota = _nota()
    with pytest.raises(nfse_service.NotaNaoEmitivelError) as exc:
        nfse_service.preencher_nota(nota.id)
    assert 'aliquota' in str(exc.value).lower()
    assert not ambiente['automacao'].preencher_etapa_pessoas.called
    assert db.session.get(NotaNfse, nota.id).status == StatusNotaNfse.PRONTA


# --- falha isola a nota (NFSE-16) ------------------------------------------

def test_falha_no_meio_marca_so_esta_nota_e_captura(ambiente):
    nota_ruim = _nota()
    nota_ok = _nota()
    ambiente['automacao'].preencher_etapa_servico.side_effect = RuntimeError('campo sumiu')

    resultado = nfse_service.preencher_nota(nota_ruim.id, hoje=date(2026, 7, 28))

    assert resultado['status'] == 'error'
    assert db.session.get(NotaNfse, nota_ruim.id).status == StatusNotaNfse.FALHA
    assert 'campo sumiu' in db.session.get(NotaNfse, nota_ruim.id).erro
    # a outra nota do lote nao foi tocada
    assert db.session.get(NotaNfse, nota_ok.id).status == StatusNotaNfse.PRONTA
    assert nfse_service.capturar_contexto_falha.called


def test_revisao_nao_alcancada_e_falha_nao_sucesso(ambiente):
    """Sem chegar a revisao, a nota NAO pode ficar 'aguardando confirmacao':
    o operador veria uma linha dizendo que esta pronta no portal quando nao esta."""
    ambiente['automacao'].esperar_revisao.return_value = False
    nota = _nota()
    resultado = nfse_service.preencher_nota(nota.id, hoje=date(2026, 7, 28))
    assert resultado['status'] == 'error'
    assert db.session.get(NotaNfse, nota.id).status == StatusNotaNfse.FALHA


def test_captura_falha_nao_derruba_o_tratamento_de_erro(ambiente):
    nfse_service.capturar_contexto_falha.side_effect = OSError('disco cheio')
    ambiente['automacao'].preencher_etapa_pessoas.side_effect = RuntimeError('x')
    nota = _nota()
    resultado = nfse_service.preencher_nota(nota.id, hoje=date(2026, 7, 28))
    assert resultado['status'] == 'error'
    assert db.session.get(NotaNfse, nota.id).status == StatusNotaNfse.FALHA


# --- preparar sessao --------------------------------------------------------

def test_preparar_sessao_devolve_a_aliquota_sem_liberar_emissao(ambiente):
    ambiente['sessao'].ler_aliquota.return_value = '3,87'
    ambiente['sessao'].aliquota_confirmada = False
    dados = nfse_service.preparar_sessao()
    assert dados['aliquota'] == '3,87'
    assert dados['aliquota_confirmada'] is False


def test_preparar_sessao_com_aliquota_ilegivel(ambiente):
    ambiente['sessao'].ler_aliquota.return_value = None
    assert nfse_service.preparar_sessao()['aliquota'] is None
