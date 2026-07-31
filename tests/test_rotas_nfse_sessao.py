"""Rotas de sessao e disparo da emissao assistida (NFSE-11/12/14/15/19/20).

A sessao e o navegador real; aqui ela e dublada. O que importa provar e o
contrato HTTP: 409 quando ja ha emissao em andamento (sem derrubar a que
existe), 409 quando a aliquota nao foi conferida, e encerramento idempotente.
"""
import io

import pytest

from app.models import NotaNfse, StatusNotaNfse
from app.routes import nfse as rotas_nfse
from app.automation.batch_state import NFSE_BATCH_STATE, nfse_batch_opcoes
from app.services import batch_engine, nfse_lote, nfse_service

LINHA = ('"13/07/2026";"EMPRESA TESTE LTDA";"0001443038";"062623";"05/07/2026";'
         '"811,00";"16,22";"1,13";"826,09";"COBRANCA SIMPLES"')


@pytest.fixture()
def sessao_falsa(monkeypatch):
    class SessaoFalsa:
        def __init__(self):
            self.livre = True
            self.aliquota = None
            self.aliquota_confirmada = False
            self.encerrada = 0
            self.adquiriu = 0

        def adquirir(self, blocking=False):
            self.adquiriu += 1
            if not self.livre:
                return False
            self.livre = False
            return True

        def liberar(self):
            self.livre = True

        def confirmar_aliquota(self, valor=None):
            if valor is not None:
                self.aliquota = valor
            self.aliquota_confirmada = True

        def encerrar(self):
            self.encerrada += 1
            self.aliquota = None
            self.aliquota_confirmada = False

        def driver_vivo(self):
            return False

        @property
        def ocupada(self):
            return not self.livre

    falsa = SessaoFalsa()
    monkeypatch.setattr(rotas_nfse, 'SESSAO', falsa)
    # a rota checa a aliquota pelo servico, que tem a sua propria referencia
    monkeypatch.setattr(nfse_service, 'SESSAO', falsa)
    return falsa


def _importar(client):
    return client.post(
        '/nfse/importar',
        data={'arquivo': (io.BytesIO(LINHA.encode('utf-8')), 'extrato.csv')},
        content_type='multipart/form-data')


# --- autorizacao ------------------------------------------------------------

@pytest.mark.parametrize('rota', [
    '/nfse/sessao/preparar',
    '/nfse/sessao/confirmar-aliquota',
    '/nfse/sessao/encerrar',
])
def test_papel_leitura_nao_acessa(login_as, rota):
    assert login_as('leitura').post(rota).status_code == 403


def test_status_da_sessao_exige_papel(login_as):
    assert login_as('leitura').get('/nfse/sessao/status').status_code == 403


# --- preparar ---------------------------------------------------------------

def test_preparar_devolve_a_aliquota_lida(client, sessao_falsa, monkeypatch):
    monkeypatch.setattr(nfse_service, 'preparar_sessao',
                        lambda: {'aliquota': '3,87', 'aliquota_confirmada': False})
    resposta = client.post('/nfse/sessao/preparar')
    assert resposta.status_code == 200
    assert resposta.get_json()['aliquota'] == '3,87'


def test_preparar_com_aliquota_ilegivel_devolve_none(client, sessao_falsa, monkeypatch):
    monkeypatch.setattr(nfse_service, 'preparar_sessao',
                        lambda: {'aliquota': None, 'aliquota_confirmada': False})
    assert client.post('/nfse/sessao/preparar').get_json()['aliquota'] is None


def test_preparar_com_sessao_ocupada_devolve_409(client, sessao_falsa):
    sessao_falsa.livre = False
    assert client.post('/nfse/sessao/preparar').status_code == 409


def test_falha_ao_preparar_encerra_a_sessao_e_libera_o_lock(client, sessao_falsa, monkeypatch):
    def explode():
        raise RuntimeError('certificado nao encontrado')
    monkeypatch.setattr(nfse_service, 'preparar_sessao', explode)

    assert client.post('/nfse/sessao/preparar').status_code == 500
    assert sessao_falsa.encerrada == 1
    assert sessao_falsa.livre, 'o lock ficou preso apos a falha'


# --- confirmar e encerrar ---------------------------------------------------

def test_confirmar_aliquota(client, sessao_falsa):
    resposta = client.post('/nfse/sessao/confirmar-aliquota', json={'aliquota': '3,87'})
    assert resposta.get_json()['aliquota_confirmada'] is True
    assert sessao_falsa.aliquota == '3,87'


def test_encerrar_sem_sessao_aberta_e_sucesso(client, sessao_falsa):
    """Idempotente: encerrar duas vezes nao pode virar 500 na cara do operador."""
    assert client.post('/nfse/sessao/encerrar').status_code == 200
    assert client.post('/nfse/sessao/encerrar').status_code == 200
    assert sessao_falsa.encerrada == 2


# --- inicio da emissao assistida (NFSE-14/15/19/20) ------------------------
#
# Nao ha rota sincrona de "preencher uma nota": os dois modos entram por
# /nfse/lote/iniciar. O modo individual espera o operador conferir e emitir, o
# que pode levar minutos — uma requisicao nao pode ficar pendurada nisso.

@pytest.fixture()
def worker_falso(monkeypatch):
    """Impede a thread real de subir: ela abriria o Chrome de verdade."""
    chamadas = []
    monkeypatch.setattr(nfse_lote, 'worker', lambda app: chamadas.append(app))
    return chamadas


@pytest.fixture(autouse=True)
def _lote_limpo():
    batch_engine.reset_batch_state(NFSE_BATCH_STATE)
    nfse_lote.preparar_nova_fila()
    yield
    batch_engine.reset_batch_state(NFSE_BATCH_STATE)
    nfse_lote.preparar_nova_fila()


def _nota_pronta(client, app):
    _importar(client)
    with app.app_context():
        from app import db
        nota = NotaNfse.query.order_by(NotaNfse.id).first()
        nota.status = StatusNotaNfse.PRONTA
        db.session.commit()
        return nota.id


def _iniciar(client, **corpo):
    return client.post('/nfse/lote/iniciar', json=corpo)


def test_individual_enfileira_so_a_nota_escolhida(
        client, app, sessao_falsa, worker_falso):
    sessao_falsa.aliquota_confirmada = True
    nota_id = _nota_pronta(client, app)

    resposta = _iniciar(client, modo='individual', nota_id=nota_id)
    assert resposta.status_code == 200
    assert resposta.get_json()['total'] == 1
    assert NFSE_BATCH_STATE['ids'] == [nota_id]


def test_lote_enfileira_todas_as_emitiveis(client, app, sessao_falsa, worker_falso):
    sessao_falsa.aliquota_confirmada = True
    _nota_pronta(client, app)

    resposta = _iniciar(client, modo='lote')
    assert resposta.status_code == 200
    assert resposta.get_json()['total'] >= 1


def test_individual_sem_nota_escolhida_e_recusado(
        client, app, sessao_falsa, worker_falso):
    sessao_falsa.aliquota_confirmada = True
    _nota_pronta(client, app)
    assert _iniciar(client, modo='individual').status_code == 400


def test_modo_desconhecido_e_recusado(client, app, sessao_falsa, worker_falso):
    sessao_falsa.aliquota_confirmada = True
    assert _iniciar(client, modo='automatico').status_code == 400


def test_fila_vazia_avisa_em_vez_de_iniciar(client, app, sessao_falsa, worker_falso):
    """Sem nota emitivel nao ha lote — e o lock precisa voltar."""
    sessao_falsa.aliquota_confirmada = True
    _importar(client)
    with app.app_context():
        from app import db
        nota = NotaNfse.query.order_by(NotaNfse.id).first()
        nota.status = StatusNotaNfse.EMPRESA_PENDENTE
        db.session.commit()

    resposta = _iniciar(client, modo='lote')
    assert resposta.status_code == 400
    assert sessao_falsa.livre, 'lock preso deixaria a feature inutilizavel'
    assert worker_falso == []


def test_sessao_ocupada_devolve_409_sem_derrubar_a_existente(
        client, app, sessao_falsa, worker_falso):
    """Uma segunda aba nao pode roubar o navegador de quem ja esta emitindo."""
    sessao_falsa.aliquota_confirmada = True
    nota_id = _nota_pronta(client, app)
    sessao_falsa.livre = False

    resposta = _iniciar(client, modo='individual', nota_id=nota_id)
    assert resposta.status_code == 409
    assert worker_falso == []
    assert sessao_falsa.encerrada == 0, 'a sessao existente foi encerrada'


def test_iniciar_exige_papel_operador(login_as, client, app, sessao_falsa):
    assert login_as('leitura').post('/nfse/lote/iniciar').status_code == 403


# --- aliquota: aviso confirmavel, nao bloqueio ------------------------------

def test_sem_aliquota_conferida_a_rota_devolve_motivo_reconhecivel(
        client, app, sessao_falsa, worker_falso):
    """A interface precisa distinguir "nao conferiu a aliquota" (aviso que o
    operador pode confirmar) de um erro seco — dai o campo `motivo`."""
    nota_id = _nota_pronta(client, app)

    resposta = _iniciar(client, modo='individual', nota_id=nota_id)
    assert resposta.status_code == 409
    assert resposta.get_json()['motivo'] == 'aliquota_nao_confirmada'
    assert worker_falso == []
    assert sessao_falsa.livre, 'o aviso nao pode consumir a sessao'


def test_confirmando_o_aviso_a_emissao_comeca(
        client, app, sessao_falsa, worker_falso):
    nota_id = _nota_pronta(client, app)
    resposta = _iniciar(client, modo='individual', nota_id=nota_id,
                        ignorar_aliquota=True)
    assert resposta.status_code == 200
    assert nfse_batch_opcoes()['ignorar_aliquota'] is True


def test_sem_confirmar_o_override_fica_desligado(client, app, sessao_falsa,
                                                 worker_falso):
    """Clique normal nao pode pular a conferencia por acidente."""
    _nota_pronta(client, app)
    assert _iniciar(client, modo='lote').status_code == 409


# --- comandos durante a emissao --------------------------------------------

@pytest.mark.parametrize('rota', [
    '/nfse/lote/pausar', '/nfse/lote/parar', '/nfse/lote/retomar',
    '/nfse/lote/pular',
])
def test_comandos_exigem_papel_operador(login_as, rota):
    assert login_as('leitura').post(rota).status_code == 403


def test_status_do_lote_exige_papel(login_as):
    assert login_as('leitura').get('/nfse/lote/status').status_code == 403


def test_pausar_pede_pausa_sem_descartar_a_fila(client, sessao_falsa):
    NFSE_BATCH_STATE.update({'status': 'running', 'ids': [1, 2], 'total': 2})
    assert client.post('/nfse/lote/pausar').status_code == 200
    assert NFSE_BATCH_STATE['stop_action'] == 'pause'
    assert NFSE_BATCH_STATE['ids'] == [1, 2]


def test_parar_marca_interrupcao(client, sessao_falsa):
    NFSE_BATCH_STATE.update({'status': 'running', 'ids': [1], 'total': 1})
    assert client.post('/nfse/lote/parar').status_code == 200
    assert NFSE_BATCH_STATE['stop_action'] == 'stop'


def test_retomar_so_funciona_com_lote_pausado(client, sessao_falsa, worker_falso):
    assert client.post('/nfse/lote/retomar').status_code == 400
    assert sessao_falsa.livre, 'retomar recusado nao pode reter a sessao'


def test_retomar_recomeca_pela_nota_onde_parou(client, sessao_falsa, worker_falso):
    NFSE_BATCH_STATE.update({'status': 'paused', 'ids': [7, 8], 'total': 2,
                             'index': 0, 'stop_requested': True})
    assert client.post('/nfse/lote/retomar').status_code == 200
    assert NFSE_BATCH_STATE['index'] == 0, 'retomar nao pode saltar a nota atual'
    assert NFSE_BATCH_STATE['stop_requested'] is False


def test_pular_sem_emissao_em_andamento_e_recusado(client, sessao_falsa):
    assert client.post('/nfse/lote/pular').status_code == 400


def test_pular_durante_a_emissao_marca_o_pedido(client, sessao_falsa):
    NFSE_BATCH_STATE['status'] = 'running'
    assert client.post('/nfse/lote/pular').status_code == 200
    assert NFSE_BATCH_STATE['pular_atual'] is True


def test_status_traz_o_modo_e_a_nota_atual(client, sessao_falsa):
    NFSE_BATCH_STATE.update({'status': 'running', 'current_id': 42,
                             'ids': [42], 'total': 1})
    dados = client.get('/nfse/lote/status').get_json()['lote']
    assert dados['nota_id'] == 42
    assert dados['modo'] in ('individual', 'lote')


def test_lista_de_notas_reflete_o_banco_durante_a_fila(client, app, sessao_falsa):
    """A tabela envelhece enquanto a fila roda: e por esta rota que ela volta
    a bater com o que a automacao ja gravou."""
    nota_id = _nota_pronta(client, app)
    with app.app_context():
        from app import db
        nota = NotaNfse.query.get(nota_id)
        nota.status = StatusNotaNfse.EMITIDA
        db.session.commit()

    dados = client.get('/nfse/notas').get_json()
    assert [n['status'] for n in dados['notas']] == [StatusNotaNfse.EMITIDA]
    assert dados['resumo']['total'] == 1


def test_lista_de_notas_exige_papel(login_as):
    assert login_as('leitura').get('/nfse/notas').status_code == 403


def test_inicio_recusado_nao_troca_o_modo_de_um_lote_pausado(
        client, app, sessao_falsa, worker_falso):
    """Um clique em Preencher (individual) com lote pausado nao pode virar o
    modo do lote: no Retomar o navegador fecharia depois da primeira nota."""
    sessao_falsa.aliquota_confirmada = True
    nota_id = _nota_pronta(client, app)
    _iniciar(client, modo='lote')          # grava modo 'lote'
    NFSE_BATCH_STATE['status'] = 'paused'
    sessao_falsa.livre = True              # o worker falso nao devolveu o lock

    resposta = _iniciar(client, modo='individual', nota_id=nota_id)
    assert resposta.status_code == 409
    assert nfse_batch_opcoes()['modo'] == 'lote', (
        'o modo do lote pausado foi trocado por um inicio que falhou')
    assert sessao_falsa.livre, 'a recusa precisa devolver a sessao'


def test_modo_automatico_e_aceito_e_enfileira_a_lista(
        client, app, sessao_falsa, worker_falso):
    sessao_falsa.aliquota_confirmada = True
    _nota_pronta(client, app)

    resposta = _iniciar(client, modo='automatico')
    assert resposta.status_code == 200
    assert nfse_batch_opcoes()['modo'] == 'automatico'
    assert resposta.get_json()['total'] >= 1


def test_automatico_nao_escapa_da_guarda_da_aliquota(
        client, app, sessao_falsa, worker_falso):
    """O modo que emite sozinho e o que MENOS pode pular a conferencia: a
    aliquota sai na nota e nao ha revisao humana para pegar o erro."""
    _nota_pronta(client, app)
    resposta = _iniciar(client, modo='automatico')
    assert resposta.status_code == 409
    assert resposta.get_json()['motivo'] == 'aliquota_nao_confirmada'
    assert worker_falso == []
