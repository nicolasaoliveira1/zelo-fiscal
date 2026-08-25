"""Emissao assistida em fila, nos dois modos (NFSE-19/20).

Estes testes assertam o ESTADO PERSISTIDO de cada desfecho, nao so o retorno da
funcao: o que decide se uma nota fiscal sera emitida de novo no mes que vem e o
que ficou gravado na linha, e um retorno certo com gravacao errada passaria
despercebido.

A garantia mais importante do arquivo e negativa: nenhum desfecho marca uma
nota como emitida sem o portal ter mostrado a tela de confirmacao.
"""
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app import db
from app.automation.nfse import ResultadoAutorrevisao
from app.automation.batch_state import (
    NFSE_BATCH_STATE,
    definir_nfse_batch_opcoes,
    nfse_batch_opcoes,
)
from app.models import (
    Empresa,
    IncidenteContratoNfse,
    LoteNfse,
    NotaNfse,
    StatusNotaNfse,
)
from app.services import batch_engine, nfse_contrato, nfse_lote


@pytest.fixture()
def banco(app):
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture(autouse=True)
def _estado_limpo():
    """O estado do lote e de modulo (como nos lotes de certidao): sem limpar
    entre os testes, um deixa o proximo achando que ha lote rodando."""
    batch_engine.reset_batch_state(NFSE_BATCH_STATE)
    nfse_lote.preparar_nova_fila()
    definir_nfse_batch_opcoes(nfse_lote.MODO_LOTE, False)
    yield
    batch_engine.reset_batch_state(NFSE_BATCH_STATE)
    nfse_lote.preparar_nova_fila()


@pytest.fixture()
def sessao(monkeypatch):
    falsa = MagicMock()
    falsa.driver_vivo.return_value = True
    monkeypatch.setattr(nfse_lote, 'SESSAO', falsa)
    return falsa


def _nota(status=StatusNotaNfse.PRONTA, **kw):
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
        documento=empresa.cnpj, tipo_documento='cnpj', competencia='06/2026',
        valor_final=Decimal('826.09'), status=status,
    )
    dados.update(kw)
    nota = NotaNfse(**dados)
    db.session.add(nota)
    db.session.commit()
    return nota


class Relogio:
    """Tempo controlado: os testes de timeout nao podem esperar 15 minutos."""

    def __init__(self):
        self.agora = 0.0

    def __call__(self):
        return self.agora

    def dormir(self, segundos):
        self.agora += segundos


# --- espera pela confirmacao humana ----------------------------------------

def _espera(monkeypatch, emitida, sessao, timeout=5):
    """Roda a espera com o relogio controlado. `emitida` e uma lista de bools,
    um por rodada do laco (assim o teste diz QUANDO o portal confirma)."""
    respostas = list(emitida)
    monkeypatch.setattr(
        nfse_lote.automacao, 'detectar_emitida',
        lambda _d: respostas.pop(0) if respostas else False)
    relogio = Relogio()
    return nfse_lote.aguardar_confirmacao(
        MagicMock(), timeout=timeout, agora=relogio, dormir=relogio.dormir)


def test_confirmacao_no_portal_e_desfecho_emitida(monkeypatch, sessao):
    assert _espera(monkeypatch, [True], sessao) == nfse_lote.EMITIDA


def test_espera_ate_o_operador_emitir(monkeypatch, sessao):
    """Duas rodadas sem confirmacao e uma com: nao pode desistir na primeira."""
    assert _espera(monkeypatch, [False, False, True], sessao) == nfse_lote.EMITIDA


def test_navegador_fechado_e_desfecho_proprio(monkeypatch, sessao):
    sessao.driver_vivo.return_value = False
    assert _espera(monkeypatch, [False], sessao) == nfse_lote.JANELA_FECHADA


def test_pedido_de_pular_encerra_a_espera(monkeypatch, sessao):
    NFSE_BATCH_STATE['status'] = 'running'
    nfse_lote.pedir_pular()
    assert _espera(monkeypatch, [False], sessao) == nfse_lote.PULADA


def test_pular_e_consumido_e_nao_vaza_para_a_proxima_nota(monkeypatch, sessao):
    NFSE_BATCH_STATE['status'] = 'running'
    nfse_lote.pedir_pular()
    _espera(monkeypatch, [False], sessao)
    assert not NFSE_BATCH_STATE.get('pular_atual'), (
        'pular grudado pularia todas as notas seguintes sem o operador pedir')


def test_parar_encerra_a_espera(monkeypatch, sessao):
    NFSE_BATCH_STATE['stop_requested'] = True
    assert _espera(monkeypatch, [False], sessao) == nfse_lote.CANCELADA


def test_sem_confirmacao_no_prazo_e_timeout(monkeypatch, sessao):
    assert _espera(monkeypatch, [], sessao, timeout=3) == nfse_lote.TIMEOUT


def test_emissao_vence_o_pedido_de_pular():
    """O caso que geraria nota duplicada.

    Se o operador emitir e clicar em "pular" quase junto, tratar como pulada
    gravaria que a nota nao saiu — e o CSV do mes seguinte a traria de volta
    para ser emitida outra vez, no mesmo tomador e na mesma competencia.
    A emissao e irreversivel; o pedido de pular, nao.
    """
    NFSE_BATCH_STATE['status'] = 'running'
    NFSE_BATCH_STATE['pular_atual'] = True
    NFSE_BATCH_STATE['stop_requested'] = True

    driver = MagicMock()
    from app.automation import nfse as automacao
    original = automacao.detectar_emitida
    try:
        automacao.detectar_emitida = lambda _d: True
        relogio = Relogio()
        desfecho = nfse_lote.aguardar_confirmacao(
            driver, timeout=5, agora=relogio, dormir=relogio.dormir)
    finally:
        automacao.detectar_emitida = original

    assert desfecho == nfse_lote.EMITIDA


def test_pedido_de_pular_nao_sobrevive_a_uma_nova_fila():
    """Regressao: `pular_atual` nao e chave do estado padrao, entao
    `reset_batch_state` nao a limpa. Um pedido que chegou tarde na execucao
    anterior pularia a primeira nota da proxima sem ninguem pedir."""
    NFSE_BATCH_STATE['status'] = 'running'
    nfse_lote.pedir_pular()

    batch_engine.reset_batch_state(NFSE_BATCH_STATE)
    nfse_lote.preparar_nova_fila()

    assert not NFSE_BATCH_STATE.get('pular_atual')


def test_pular_sem_lote_rodando_nao_faz_nada():
    NFSE_BATCH_STATE['status'] = 'idle'
    assert nfse_lote.pedir_pular() is False
    assert not NFSE_BATCH_STATE.get('pular_atual')


# --- desfecho gravado na nota ----------------------------------------------

@pytest.fixture()
def fila(banco, monkeypatch, sessao):
    """Preenchimento e espera dublados; banco real. Os testes escolhem o
    desfecho e conferem o que sobrou gravado."""
    def preencher(nota_id, **kw):
        # o real deixa a nota AGUARDANDO_CONFIRMACAO antes de a espera comecar;
        # um duble que nao faz isso deixaria passar um desfecho que apaga o
        # status errado
        nota = db.session.get(NotaNfse, nota_id)
        nota.status = StatusNotaNfse.AGUARDANDO_CONFIRMACAO
        db.session.commit()
        return dict(preencheu.retorno)

    preencheu = MagicMock(side_effect=preencher)
    preencheu.retorno = {'status': 'aguardando_confirmacao'}
    monkeypatch.setattr(nfse_lote.nfse_service, 'preencher_nota', preencheu)
    monkeypatch.setattr(nfse_lote, 'log_event', MagicMock())
    return {'preencher': preencheu, 'sessao': sessao}


def _rodar(monkeypatch, nota, desfecho, modo=nfse_lote.MODO_LOTE):
    definir_nfse_batch_opcoes(modo, False)
    monkeypatch.setattr(nfse_lote, 'aguardar_confirmacao', lambda _d: desfecho)
    resultado = nfse_lote._emitir_nota(nota.id, None, 'exec-1')
    db.session.refresh(nota)
    return resultado


def test_emitida_grava_status_origem_e_horario(fila, monkeypatch):
    nota = _nota(status=StatusNotaNfse.PRONTA)
    sucesso, grave, _ = _rodar(monkeypatch, nota, nfse_lote.EMITIDA)

    assert (sucesso, grave) == (True, None)
    assert nota.status == StatusNotaNfse.EMITIDA
    assert nota.origem_emissao == 'automacao', (
        'a origem distingue o que a automacao viu do que o operador marcou na mao')
    assert nota.emitida_em is not None


def test_modo_individual_fecha_o_navegador_apos_emitir(fila, monkeypatch):
    nota = _nota()
    _rodar(monkeypatch, nota, nfse_lote.EMITIDA, modo=nfse_lote.MODO_INDIVIDUAL)
    assert fila['sessao'].encerrar.called


def test_modo_lote_mantem_o_navegador_aberto(fila, monkeypatch):
    """O ganho do lote e nao repetir certificado e aliquota a cada nota."""
    nota = _nota()
    _rodar(monkeypatch, nota, nfse_lote.EMITIDA, modo=nfse_lote.MODO_LOTE)
    assert not fila['sessao'].encerrar.called


def test_pulada_grava_o_status_e_nao_conta_como_falha(fila, monkeypatch):
    nota = _nota()
    antes = NFSE_BATCH_STATE.get('pendentes_resultado', 0)
    sucesso, grave, _ = _rodar(monkeypatch, nota, nfse_lote.PULADA)

    assert (sucesso, grave) == (False, None)
    assert nota.status == StatusNotaNfse.PULADA
    assert NFSE_BATCH_STATE['pendentes_resultado'] > antes, (
        'pular por escolha do operador nao e erro tecnico')


def test_timeout_pausa_o_lote_e_preserva_a_nota(fila, monkeypatch):
    """A nota preenchida continua no portal esperando: nao pode virar pulada,
    senao o operador emite no navegador uma nota que o sistema deu por perdida."""
    nota = _nota()
    sucesso, grave, mensagem = _rodar(monkeypatch, nota, nfse_lote.TIMEOUT)

    assert (sucesso, grave) == (False, None)
    assert nota.status == StatusNotaNfse.AGUARDANDO_CONFIRMACAO
    assert NFSE_BATCH_STATE['stop_requested'] is True
    assert NFSE_BATCH_STATE['stop_action'] == 'pause', (
        'parar de vez descartaria a fila; pausar deixa retomar de onde estava')
    assert 'pausad' in mensagem.lower()


def test_janela_fechada_nao_decide_pelo_operador(fila, monkeypatch):
    """Com a janela fechada nao da para saber se a nota saiu. Chutar erra dos
    dois lados: marcar emitida perde uma nota que nao existe, marcar pendente
    faz emitir de novo uma que ja existe."""
    nota = _nota()
    sucesso, grave, mensagem = _rodar(monkeypatch, nota, nfse_lote.JANELA_FECHADA)

    assert nota.status == StatusNotaNfse.AGUARDANDO_CONFIRMACAO
    assert nota.origem_emissao is None
    assert (sucesso, grave) == (False, batch_engine.GRAVE_FATAL), (
        'sem navegador o lote nao tem como seguir'
    )
    assert 'marque' in mensagem.lower(), 'a mensagem precisa dizer o que fazer'


def test_cancelada_nao_mexe_na_nota(fila, monkeypatch):
    nota = _nota()
    sucesso, grave, _ = _rodar(monkeypatch, nota, nfse_lote.CANCELADA)
    assert nota.status == StatusNotaNfse.AGUARDANDO_CONFIRMACAO
    assert (sucesso, grave) == (False, None)


def test_falha_no_preenchimento_nao_espera_confirmacao(fila, monkeypatch):
    """Sem tela de revisao nao ha o que confirmar: esperar ali prenderia o lote
    por 15 minutos a toa."""
    fila['preencher'].retorno = {'status': 'error', 'message': 'campo sumiu'}
    esperou = MagicMock()
    monkeypatch.setattr(nfse_lote, 'aguardar_confirmacao', esperou)
    nota = _nota()

    sucesso, grave, mensagem = nfse_lote._emitir_nota(nota.id, None, 'exec-1')
    assert (sucesso, grave) == (False, None)
    assert mensagem == 'campo sumiu'
    assert not esperou.called


def test_opcoes_fixam_as_duas_versoes_e_getter_devolve_copia():
    definir_nfse_batch_opcoes(
        nfse_lote.MODO_LOTE,
        contrato_id=17,
        validacao_contrato_id=23,
    )

    opcoes = nfse_batch_opcoes()
    opcoes['contrato_id'] = 99

    assert nfse_batch_opcoes()['contrato_id'] == 17
    assert nfse_batch_opcoes()['validacao_contrato_id'] == 23


def test_worker_passa_ids_fixados_a_nota_em_validacao(fila, monkeypatch):
    definir_nfse_batch_opcoes(
        nfse_lote.MODO_INDIVIDUAL,
        contrato_id=17,
        validacao_contrato_id=23,
    )
    registrar = MagicMock()
    monkeypatch.setattr(nfse_lote, '_registrar_validacao_candidata', registrar)
    monkeypatch.setattr(nfse_lote, 'aguardar_confirmacao', lambda _d: nfse_lote.TIMEOUT)
    nota = _nota()

    nfse_lote._emitir_nota(nota.id, None, 'execucao-sintetica')

    argumentos = fila['preencher'].call_args.kwargs
    assert argumentos['contrato_id'] == 17
    assert argumentos['validacao_contrato_id'] == 23
    registrar.assert_called_once_with(nota.id, 23, 'execucao-sintetica')


def test_mudanca_do_ativo_nao_altera_ids_durante_o_lote(fila, monkeypatch):
    definir_nfse_batch_opcoes(nfse_lote.MODO_LOTE, contrato_id=17)
    monkeypatch.setattr(nfse_lote, 'aguardar_confirmacao', lambda _d: nfse_lote.EMITIDA)
    primeira = _nota()
    segunda = _nota()

    nfse_lote._emitir_nota(primeira.id, None, 'execucao-sintetica')
    monkeypatch.setattr(
        nfse_lote.nfse_contrato,
        'contrato_ativo',
        lambda: SimpleNamespace(id=99),
    )
    nfse_lote._emitir_nota(segunda.id, None, 'execucao-sintetica')

    assert [
        chamada.kwargs['contrato_id']
        for chamada in fila['preencher'].call_args_list
    ] == [17, 17]


def test_drift_critico_pausa_automatico_antes_de_qualquer_revisao(fila, monkeypatch):
    definir_nfse_batch_opcoes(nfse_lote.MODO_AUTOMATICO, contrato_id=17)
    fila['preencher'].retorno = {
        'status': 'error',
        'message': 'Contrato sintético divergente.',
        'pausar_lote': True,
    }
    revisar = MagicMock()
    monkeypatch.setattr(nfse_lote, '_emitir_sozinho', revisar)
    nota = _nota()

    sucesso, grave, mensagem = nfse_lote._emitir_nota(
        nota.id, None, 'execucao-sintetica'
    )

    assert (sucesso, grave, mensagem) == (
        False, None, 'Contrato sintético divergente.'
    )
    assert NFSE_BATCH_STATE['stop_action'] == 'pause'
    assert NFSE_BATCH_STATE['pendentes_resultado'] > 0
    revisar.assert_not_called()


def test_inicio_automatico_recusa_contrato_fechado(monkeypatch):
    erro = nfse_contrato.ContratoNfseNaoElegivelError(
        'há incidente fiscal aberto no contrato sintético'
    )
    validar = MagicMock(side_effect=erro)
    monkeypatch.setattr(nfse_lote.nfse_contrato, 'validar_contrato_automatico', validar)

    with pytest.raises(nfse_contrato.ContratoNfseNaoElegivelError):
        nfse_lote.validar_contrato_para_modo(
            nfse_lote.MODO_AUTOMATICO,
            contrato_id=17,
        )

    validar.assert_called_once_with(17)


def test_gate_automatico_recusa_incidente_fiscal_aberto(banco):
    contrato = nfse_contrato.garantir_contrato_inicial()
    incidente = IncidenteContratoNfse(
        contrato_base_id=contrato.id,
        assinatura='a' * 64,
        etapa='servico',
        tipo='controle_novo',
        severidade='fiscal',
        estado='aberto',
        primeira_observacao_em=datetime(2026, 8, 25, 12, 0),
        ultima_observacao_em=datetime(2026, 8, 25, 12, 0),
        mensagem='Incidente sintético requer revisão.',
    )
    db.session.add(incidente)
    db.session.commit()

    with pytest.raises(nfse_contrato.ContratoNfseNaoElegivelError, match='incidente fiscal'):
        nfse_lote.validar_contrato_para_modo(nfse_lote.MODO_AUTOMATICO)


def test_validacao_aceita_apenas_nota_emitivel(banco):
    nota = _nota(status=StatusNotaNfse.PRONTA)
    assert nfse_lote.validar_nota_para_validacao(nota.id) is nota

    nota.status = StatusNotaNfse.EMITIDA
    db.session.commit()
    with pytest.raises(ValueError, match='emitível'):
        nfse_lote.validar_nota_para_validacao(nota.id)


def test_registro_da_validacao_mantem_candidata_inativa_ate_ativacao(banco):
    contrato = nfse_contrato.garantir_contrato_inicial()
    contrato.estado = 'candidata'
    contrato.elegivel_automatico = False
    db.session.commit()
    nota = _nota()

    validada = nfse_contrato.registrar_validacao(
        contrato.id,
        nota.id,
        ResultadoAutorrevisao([], elegivel_automatico=True),
    )

    assert validada.estado == 'validada'
    assert validada.id == contrato.id
    assert validada.ativado_em is None
    assert validada.nota_validacao_id == nota.id
    assert validada.elegivel_automatico is True


def test_validacao_registra_revisao_e_usa_espera_assistida(fila, monkeypatch):
    definir_nfse_batch_opcoes(
        nfse_lote.MODO_INDIVIDUAL,
        contrato_id=17,
        validacao_contrato_id=23,
    )
    registrar = MagicMock()
    esperar = MagicMock(return_value=nfse_lote.TIMEOUT)
    revisar = MagicMock()
    monkeypatch.setattr(nfse_lote, '_registrar_validacao_candidata', registrar)
    monkeypatch.setattr(nfse_lote, 'aguardar_confirmacao', esperar)
    monkeypatch.setattr(nfse_lote, '_emitir_sozinho', revisar)
    nota = _nota()

    nfse_lote._emitir_nota(nota.id, None, 'execucao-sintetica')

    registrar.assert_called_once_with(nota.id, 23, 'execucao-sintetica')
    esperar.assert_called_once()
    revisar.assert_not_called()


def test_linha_recusada_pelo_dominio_nao_vira_falha_tecnica(fila, monkeypatch):
    from app.services import nfse_service
    fila['preencher'].side_effect = nfse_service.NotaNaoEmitivelError(
        'Esta nota ja foi emitida.')
    nota = _nota()
    antes = NFSE_BATCH_STATE.get('pendentes_resultado', 0)

    sucesso, grave, mensagem = nfse_lote._emitir_nota(nota.id, None, 'exec-1')
    assert (sucesso, grave) == (False, None)
    assert 'ja foi emitida' in mensagem
    assert NFSE_BATCH_STATE['pendentes_resultado'] > antes


def test_a_automacao_nunca_emite_sozinha(fila, monkeypatch):
    """Guarda de ND-005 no nivel da fila: nenhum desfecho clica em emitir."""
    driver = MagicMock()
    fila['sessao'].driver = driver
    nota = _nota()
    for desfecho in (nfse_lote.EMITIDA, nfse_lote.PULADA, nfse_lote.TIMEOUT,
                     nfse_lote.CANCELADA, nfse_lote.JANELA_FECHADA):
        batch_engine.reset_batch_state(NFSE_BATCH_STATE)
        nota.status = StatusNotaNfse.PRONTA
        db.session.commit()
        _rodar(monkeypatch, nota, desfecho)

    assert not driver.find_element.called
    assert not driver.execute_script.called


# --- montagem da fila ------------------------------------------------------

def test_fila_individual_tem_so_a_nota_escolhida(banco):
    _nota()
    escolhida = _nota()
    alvos = nfse_lote.calcular_alvos(nota_id=escolhida.id)
    assert alvos['ids'] == [escolhida.id]
    assert alvos['total'] == 1


def test_fila_do_lote_pega_as_emitiveis_na_ordem_da_planilha(banco):
    pronta = _nota(status=StatusNotaNfse.PRONTA)
    emitida = _nota(status=StatusNotaNfse.EMITIDA)
    pulada = _nota(status=StatusNotaNfse.PULADA)

    ids = nfse_lote.calcular_alvos(lote_id=pronta.lote_id)['ids']
    assert ids == [pronta.id, pulada.id]
    assert emitida.id not in ids, 'reemitir nota ja emitida gera nota duplicada'


@pytest.mark.parametrize('status', [
    StatusNotaNfse.EMPRESA_PENDENTE,
    StatusNotaNfse.INVALIDA,
    StatusNotaNfse.EMITIDA,
])
def test_fila_do_lote_ignora_o_que_nao_pode_emitir(banco, status):
    nota = _nota(status=status)
    assert nfse_lote.calcular_alvos(lote_id=nota.lote_id)['ids'] == []


def test_fila_inclui_tomador_pessoa_fisica(banco):
    """Emitir para CPF nao exige cadastro de Empresa."""
    nota = _nota(status=StatusNotaNfse.PESSOA_FISICA, empresa_id=None,
                 documento='123.456.789-09', tipo_documento='cpf')
    assert nfse_lote.calcular_alvos(lote_id=nota.lote_id)['ids'] == [nota.id]


def test_duplicata_so_entra_na_fila_depois_de_liberada(banco):
    presa = _nota(status=StatusNotaNfse.DUPLICATA, duplicata_liberada=False)
    assert nfse_lote.calcular_alvos(lote_id=presa.lote_id)['ids'] == []

    presa.duplicata_liberada = True
    db.session.commit()
    assert nfse_lote.calcular_alvos(lote_id=presa.lote_id)['ids'] == [presa.id]


def test_fila_individual_de_nota_ja_emitida_fica_vazia(banco):
    """O motor recusa comecar com fila vazia — melhor que preencher de novo."""
    nota = _nota(status=StatusNotaNfse.EMITIDA)
    assert nfse_lote.calcular_alvos(nota_id=nota.id)['ids'] == []


def test_fila_individual_de_nota_inexistente_fica_vazia(banco):
    assert nfse_lote.calcular_alvos(nota_id=99999)['ids'] == []


def test_espera_sem_driver_nao_estoura(sessao):
    """Se a sessao perder o navegador entre o preenchimento e a espera, o
    desfecho e "nao sei" — nao um AttributeError no meio do lote."""
    assert nfse_lote.aguardar_confirmacao(None) == nfse_lote.JANELA_FECHADA


# --- excecao inesperada nao pode matar a thread ----------------------------

def test_falha_de_login_marca_a_nota_e_para_o_lote(fila, monkeypatch):
    """`preencher_nota` abre a sessao ANTES do seu proprio try, entao um erro de
    login sai por fora. Sem tratamento aqui ele atravessa o motor e mata a
    thread: o lote fica `running` para sempre (toda emissao seguinte vira 409) e
    a nota trava em `preenchendo`, estado que a interface nao sabe destravar."""
    from app.automation.nfse import LoginNfseError
    fila['preencher'].side_effect = LoginNfseError(
        'O portal nao chegou ao painel apos o acesso por certificado.')
    nota = _nota()

    sucesso, grave, mensagem = nfse_lote._emitir_nota(nota.id, None, 'exec-1')
    db.session.refresh(nota)

    assert (sucesso, grave) == (False, batch_engine.GRAVE_FATAL)
    assert nota.status == StatusNotaNfse.FALHA
    assert nota.status != StatusNotaNfse.PREENCHENDO
    assert 'certificado' in mensagem or 'painel' in mensagem


def test_erro_inesperado_no_preenchimento_nao_deixa_a_nota_presa(fila, monkeypatch):
    fila['preencher'].side_effect = RuntimeError('estourou algo novo')
    nota = _nota()

    sucesso, grave, _ = nfse_lote._emitir_nota(nota.id, None, 'exec-1')
    db.session.refresh(nota)

    assert (sucesso, grave) == (False, batch_engine.GRAVE_FATAL)
    assert nota.status == StatusNotaNfse.FALHA


def test_worker_que_explode_termina_em_erro_e_nao_em_running(monkeypatch, banco):
    """Estado preso em `running` nao tem quem conserte: nao ha rota que o
    reinicie e todo inicio novo responde 409."""
    monkeypatch.setattr(nfse_lote, 'log_event', MagicMock())
    monkeypatch.setattr(nfse_lote, '_rodar_lote', MagicMock(
        side_effect=RuntimeError('motor quebrou')))
    liberou = MagicMock()
    monkeypatch.setattr(nfse_lote, 'SESSAO', liberou)
    NFSE_BATCH_STATE['status'] = 'running'

    nfse_lote.worker(banco)

    assert NFSE_BATCH_STATE['status'] == 'error'
    assert NFSE_BATCH_STATE['message']
    assert liberou.liberar.called, 'a sessao ficaria presa se o teardown nao rodou'


# --- o polling de status nao pode falar com o Selenium ---------------------

def test_status_nao_consulta_o_navegador(monkeypatch):
    """O payload e pedido de 2 em 2 segundos, da thread da requisicao, enquanto
    o worker dirige o MESMO navegador (pool de conexoes de tamanho 1). Uma ida
    ao chromedriver aqui enche o pool durante o preenchimento e, quando o
    navegador acaba de fechar, prende a resposta em retries do urllib3 — foi o
    que impediu a pagina de perceber sozinha que o lote terminou."""
    falsa = MagicMock()
    falsa.tem_driver = True
    monkeypatch.setattr(nfse_lote, 'SESSAO', falsa)

    dados = nfse_lote.status()

    assert dados['sessao_ativa'] is True
    assert not falsa.driver_vivo.called, (
        'driver_vivo() faz round-trip HTTP ao chromedriver a cada poll')


def test_status_reporta_sessao_fechada(monkeypatch):
    falsa = MagicMock()
    falsa.tem_driver = False
    monkeypatch.setattr(nfse_lote, 'SESSAO', falsa)
    assert nfse_lote.status()['sessao_ativa'] is False


# --- retomada depois da pausa ----------------------------------------------

def test_retomar_volta_a_esperar_em_vez_de_preencher_de_novo(fila, monkeypatch):
    """O bug da pausa: ao retomar, a nota esta em `aguardando_confirmacao`, que
    NAO esta em STATUS_EMITIVEIS. `preencher_nota` recusava, o motor contava
    pendente e passava para a proxima — deixando sem vigia justamente a nota que
    o operador ia emitir, que por isso nunca era marcada."""
    nota = _nota(status=StatusNotaNfse.AGUARDANDO_CONFIRMACAO)
    monkeypatch.setattr(nfse_lote, 'aguardar_confirmacao',
                        lambda _d: nfse_lote.EMITIDA)

    sucesso, grave, _ = nfse_lote._emitir_nota(nota.id, None, 'exec-1')
    db.session.refresh(nota)

    assert not fila['preencher'].called, (
        'preencher de novo abriria uma SEGUNDA DPS para o mesmo tomador')
    assert (sucesso, grave) == (True, None)
    assert nota.status == StatusNotaNfse.EMITIDA
    assert nota.origem_emissao == 'automacao'


def test_retomada_respeita_pular_e_timeout_como_qualquer_nota(fila, monkeypatch):
    nota = _nota(status=StatusNotaNfse.AGUARDANDO_CONFIRMACAO)
    monkeypatch.setattr(nfse_lote, 'aguardar_confirmacao',
                        lambda _d: nfse_lote.PULADA)

    nfse_lote._emitir_nota(nota.id, None, 'exec-1')
    db.session.refresh(nota)
    assert nota.status == StatusNotaNfse.PULADA


def test_nota_pronta_continua_sendo_preenchida(fila, monkeypatch):
    """O atalho da retomada nao pode valer para quem ainda nao foi preenchida."""
    nota = _nota(status=StatusNotaNfse.PRONTA)
    monkeypatch.setattr(nfse_lote, 'aguardar_confirmacao',
                        lambda _d: nfse_lote.EMITIDA)

    nfse_lote._emitir_nota(nota.id, None, 'exec-1')
    assert fila['preencher'].called


# --- fim do lote fecha o navegador -----------------------------------------

@pytest.mark.parametrize('status', ['stopped', 'completed', 'error'])
def test_fim_do_lote_fecha_o_navegador(monkeypatch, status):
    """Parar e um "chega por hoje" explicito: deixar o Chrome aberto obriga o
    operador a fechar na mao e mantem a policy do certificado ativa."""
    falsa = MagicMock()
    monkeypatch.setattr(nfse_lote, 'SESSAO', falsa)
    NFSE_BATCH_STATE['status'] = status

    nfse_lote._encerrar_sessao(None)

    assert falsa.encerrar.called
    assert falsa.liberar.called


def test_pausa_mantem_o_navegador_aberto(monkeypatch):
    """Retomar depende da MESMA janela, ainda na tela de revisao."""
    falsa = MagicMock()
    monkeypatch.setattr(nfse_lote, 'SESSAO', falsa)
    NFSE_BATCH_STATE['status'] = 'paused'

    nfse_lote._encerrar_sessao(None)

    assert not falsa.encerrar.called, 'fechar aqui perderia a nota da revisao'
    assert falsa.liberar.called, 'o lock precisa voltar mesmo na pausa'


# --- a fila e o que a pagina mostra ----------------------------------------

def test_fila_por_competencia_atravessa_lotes(banco):
    """Com um mes filtrado na tela, a fila tem de ser esse mes — nao o ultimo
    lote importado, ou o operador emitiria notas que nao esta olhando."""
    do_mes = _nota(competencia='06/2026')
    outro_lote = LoteNfse(nome_arquivo='segundo.csv', total=1)
    db.session.add(outro_lote)
    db.session.commit()
    tambem_do_mes = _nota(competencia='06/2026', lote_id=outro_lote.id)
    _nota(competencia='07/2026', lote_id=outro_lote.id)

    ids = nfse_lote.calcular_alvos(competencia='06/2026')['ids']
    assert ids == [do_mes.id, tambem_do_mes.id]


def test_competencia_tem_precedencia_sobre_o_lote(banco):
    nota = _nota(competencia='06/2026')
    ids = nfse_lote.calcular_alvos(lote_id=nota.lote_id,
                                   competencia='07/2026')['ids']
    assert ids == [], 'o filtro visivel manda, nao o lote'


# --- modo automatico: emite so depois de conferir (NFSE-24) ----------------

@pytest.fixture()
def automatico(fila, monkeypatch):
    """Modo automatico com a auto-revisao e o clique de emitir dublados."""
    definir_nfse_batch_opcoes(nfse_lote.MODO_AUTOMATICO, False)
    automacao = MagicMock()
    automacao.conferir_revisao.return_value = []
    automacao.emitir.return_value = True
    monkeypatch.setattr(nfse_lote, 'automacao', automacao)
    return {**fila, 'automacao': automacao}


def test_confere_antes_de_emitir(automatico):
    nota = _nota()
    nfse_lote._emitir_nota(nota.id, None, 'exec-1')

    assert automatico['automacao'].conferir_revisao.called, (
        'emitir sem reler a tela e emitir as cegas')
    ordem = automatico['automacao'].mock_calls
    nomes = [c[0] for c in ordem]
    assert nomes.index('conferir_revisao') < nomes.index('emitir')


def test_confere_contra_os_dados_da_nota(automatico):
    nota = _nota()
    nfse_lote._emitir_nota(nota.id, None, 'exec-1')

    _driver, documento, valor, descricao = \
        automatico['automacao'].conferir_revisao.call_args[0]
    assert documento == nota.documento
    assert valor == nota.valor_final
    assert nota.competencia in descricao, (
        'a descricao conferida precisa ser a da competencia desta nota')


def test_conferindo_emite_e_marca(automatico):
    nota = _nota()
    sucesso, grave, _ = nfse_lote._emitir_nota(nota.id, None, 'exec-1')
    db.session.refresh(nota)

    assert (sucesso, grave) == (True, None)
    assert nota.status == StatusNotaNfse.EMITIDA
    assert nota.origem_emissao == 'automacao'


def test_divergencia_nao_emite_e_pausa_o_lote(automatico):
    """O teste independente da spec: qualquer diferenca barra a emissao."""
    automatico['automacao'].conferir_revisao.return_value = [
        'O valor na tela e R$ 999,00, e a nota e de 826,09.']
    nota = _nota()

    sucesso, _grave, mensagem = nfse_lote._emitir_nota(nota.id, None, 'exec-1')
    db.session.refresh(nota)

    assert not automatico['automacao'].emitir.called, 'emitiu apesar da divergencia'
    assert sucesso is False
    assert nota.status == StatusNotaNfse.AGUARDANDO_CONFIRMACAO
    assert nota.origem_emissao is None
    assert '999,00' in nota.erro, 'o motivo precisa ficar na linha'
    assert NFSE_BATCH_STATE['stop_action'] == 'pause', (
        'seguir para a proxima abriria outra DPS e deixaria esta como rascunho '
        'orfao no portal')
    assert 'pausad' in mensagem.lower()


def test_confirmacao_que_nao_chega_nao_e_dada_como_emitida(automatico):
    """O clique saiu e a confirmacao nao apareceu: pode ter emitido ou nao.
    Marcar emitida perderia o rastro; marcar pendente reemitiria mes que vem."""
    automatico['automacao'].emitir.return_value = False
    nota = _nota()

    sucesso, _grave, _ = nfse_lote._emitir_nota(nota.id, None, 'exec-1')
    db.session.refresh(nota)

    assert sucesso is False
    assert nota.status == StatusNotaNfse.AGUARDANDO_CONFIRMACAO
    assert nota.origem_emissao is None
    assert 'portal' in nota.erro.lower()
    assert NFSE_BATCH_STATE['stop_action'] == 'pause'


def test_automatico_nao_espera_confirmacao_humana(automatico, monkeypatch):
    esperou = MagicMock()
    monkeypatch.setattr(nfse_lote, 'aguardar_confirmacao', esperou)
    nfse_lote._emitir_nota(_nota().id, None, 'exec-1')
    assert not esperou.called


def test_automatico_nao_fecha_o_navegador_entre_notas(automatico):
    nfse_lote._emitir_nota(_nota().id, None, 'exec-1')
    assert not automatico['sessao'].encerrar.called


@pytest.mark.parametrize('modo', [nfse_lote.MODO_INDIVIDUAL, nfse_lote.MODO_LOTE])
def test_modos_assistidos_nunca_emitem_sozinhos(fila, monkeypatch, modo):
    """A garantia do P1/P2 (ND-005) continua valendo com o P3 no codigo."""
    automacao = MagicMock()
    monkeypatch.setattr(nfse_lote, 'automacao', automacao)
    monkeypatch.setattr(nfse_lote, 'aguardar_confirmacao',
                        lambda _d: nfse_lote.EMITIDA)
    definir_nfse_batch_opcoes(modo, False)

    nfse_lote._emitir_nota(_nota().id, None, 'exec-1')

    assert not automacao.emitir.called
    assert not automacao.conferir_revisao.called
