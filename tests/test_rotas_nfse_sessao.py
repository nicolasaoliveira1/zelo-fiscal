"""Rotas de sessao e disparo do preenchimento (NFSE-11/12/14/15).

A sessao e o navegador real; aqui ela e dublada. O que importa provar e o
contrato HTTP: 409 quando ja ha emissao em andamento (sem derrubar a que
existe), 409 quando a aliquota nao foi conferida, e encerramento idempotente.
"""
import io

import pytest

from app.models import NotaNfse, StatusNotaNfse
from app.routes import nfse as rotas_nfse
from app.services import nfse_service

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


# --- preencher uma nota (NFSE-13/14/15) ------------------------------------

def _nota_pronta(client, app):
    _importar(client)
    with app.app_context():
        nota = NotaNfse.query.order_by(NotaNfse.id).first()
        nota.status = StatusNotaNfse.PRONTA
        from app import db
        db.session.commit()
        return nota.id


def test_preencher_devolve_a_nota_atualizada(client, app, sessao_falsa, monkeypatch):
    nota_id = _nota_pronta(client, app)
    monkeypatch.setattr(nfse_service, 'preencher_nota',
                        lambda _id, **kw: {'status': 'aguardando_confirmacao',
                                           'nota_id': _id, 'message': 'ok'})
    resposta = client.post(f'/nfse/nota/{nota_id}/preencher')
    assert resposta.status_code == 200
    assert resposta.get_json()['nota']['id'] == nota_id


def test_preencher_com_sessao_ocupada_devolve_409_sem_derrubar_a_existente(
        client, app, sessao_falsa, monkeypatch):
    """Uma segunda aba nao pode roubar o navegador de quem ja esta emitindo."""
    nota_id = _nota_pronta(client, app)
    chamou = []
    monkeypatch.setattr(nfse_service, 'preencher_nota',
                        lambda _id, **kw: chamou.append(_id))
    sessao_falsa.livre = False

    resposta = client.post(f'/nfse/nota/{nota_id}/preencher')
    assert resposta.status_code == 409
    assert chamou == [], 'a sessao em andamento foi usada por outra requisicao'
    assert sessao_falsa.encerrada == 0, 'a sessao existente foi encerrada'


def test_preencher_sem_aliquota_confirmada_devolve_409(client, app, sessao_falsa, monkeypatch):
    nota_id = _nota_pronta(client, app)

    def recusa(_id, **kw):
        raise nfse_service.NotaNaoEmitivelError('Confira a aliquota antes de emitir.')
    monkeypatch.setattr(nfse_service, 'preencher_nota', recusa)

    resposta = client.post(f'/nfse/nota/{nota_id}/preencher')
    assert resposta.status_code == 409
    assert 'aliquota' in resposta.get_json()['message'].lower()


def test_preencher_libera_o_lock_mesmo_quando_falha(client, app, sessao_falsa, monkeypatch):
    nota_id = _nota_pronta(client, app)

    def explode(_id, **kw):
        raise RuntimeError('portal fora do ar')
    monkeypatch.setattr(nfse_service, 'preencher_nota', explode)

    assert client.post(f'/nfse/nota/{nota_id}/preencher').status_code == 500
    assert sessao_falsa.livre, 'lock preso deixaria a feature inutilizavel'


def test_falha_do_preenchimento_vira_erro_http(client, app, sessao_falsa, monkeypatch):
    nota_id = _nota_pronta(client, app)
    monkeypatch.setattr(nfse_service, 'preencher_nota',
                        lambda _id, **kw: {'status': 'error', 'message': 'campo sumiu'})
    resposta = client.post(f'/nfse/nota/{nota_id}/preencher')
    assert resposta.status_code == 500
    assert 'campo sumiu' in resposta.get_json()['message']


def test_preencher_exige_papel_operador(login_as, client, app, sessao_falsa):
    nota_id = _nota_pronta(client, app)
    assert login_as('leitura').post(f'/nfse/nota/{nota_id}/preencher').status_code == 403


# --- aliquota: aviso confirmavel, nao bloqueio ------------------------------

def test_sem_aliquota_conferida_a_rota_devolve_motivo_reconhecivel(
        client, app, sessao_falsa, monkeypatch):
    """A interface precisa distinguir "nao conferiu a aliquota" (aviso que o
    operador pode confirmar) de um erro seco — daí o campo `motivo`."""
    nota_id = _nota_pronta(client, app)

    def recusa(_id, **kw):
        raise nfse_service.AliquotaNaoConfirmadaError('Aliquota nao conferida.')
    monkeypatch.setattr(nfse_service, 'preencher_nota', recusa)

    resposta = client.post(f'/nfse/nota/{nota_id}/preencher')
    assert resposta.status_code == 409
    assert resposta.get_json()['motivo'] == 'aliquota_nao_confirmada'


def test_confirmando_o_aviso_a_rota_repassa_o_override(
        client, app, sessao_falsa, monkeypatch):
    recebido = {}

    def registra(_id, **kw):
        recebido.update(kw)
        return {'status': 'aguardando_confirmacao', 'nota_id': _id, 'message': 'ok'}
    monkeypatch.setattr(nfse_service, 'preencher_nota', registra)

    nota_id = _nota_pronta(client, app)
    resposta = client.post(f'/nfse/nota/{nota_id}/preencher',
                           json={'ignorar_aliquota': True})
    assert resposta.status_code == 200
    assert recebido['ignorar_aliquota'] is True


def test_sem_corpo_o_override_fica_desligado(client, app, sessao_falsa, monkeypatch):
    """Clique normal em Preencher nao pode pular a conferencia por acidente."""
    recebido = {}

    def registra(_id, **kw):
        recebido.update(kw)
        return {'status': 'aguardando_confirmacao', 'nota_id': _id, 'message': 'ok'}
    monkeypatch.setattr(nfse_service, 'preencher_nota', registra)

    nota_id = _nota_pronta(client, app)
    client.post(f'/nfse/nota/{nota_id}/preencher')
    assert recebido['ignorar_aliquota'] is False


def test_outros_motivos_de_recusa_nao_ganham_o_motivo_da_aliquota(
        client, app, sessao_falsa, monkeypatch):
    """Uma linha sem empresa nao pode abrir o modal da aliquota."""
    nota_id = _nota_pronta(client, app)

    def recusa(_id, **kw):
        raise nfse_service.NotaNaoEmitivelError('Esta linha nao tem empresa vinculada.')
    monkeypatch.setattr(nfse_service, 'preencher_nota', recusa)

    resposta = client.post(f'/nfse/nota/{nota_id}/preencher')
    assert resposta.status_code == 409
    assert 'motivo' not in resposta.get_json()
