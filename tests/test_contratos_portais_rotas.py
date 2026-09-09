"""Central de contratos dos portais no Diagnóstico (RAC-06, RAC-14).

Nenhum teste abre navegador: onde a rota observaria o portal, o criador de
driver é substituído. O que precisa ficar provado é a superfície — quem pode
chamar, o que a rota recusa e com qual código — e que promover contrato exige
confirmação explícita.
"""
from unittest.mock import MagicMock

import pytest

from app import db
from app.automation import trabalhista
from app.models import ContratoPortal, IncidenteContratoPortal, Usuario
from app.routes import contratos_portais
from app.services import contrato_portal, contrato_portal_recon
from app.services.contrato_portal_drift import (
    RemapeamentoSeletor,
    ResultadoComparacaoPortal,
    _diferenca,
)

BASE = '/diagnostico/contratos-portais'
FLUXO = trabalhista.FLUXO_CONTRATO
ALVO = trabalhista.ALVO_CONTRATO


def _admin_id():
    return Usuario.query.filter_by(papel='admin').first().id


def _baseline(app):
    with app.app_context():
        return contrato_portal.criar_baseline(
            fluxo=FLUXO, alvo=ALVO,
            definicao=trabalhista.definicao_baseline(),
            usuario_id=_admin_id()).id


def _incidente(app, contrato_id):
    with app.app_context():
        resultado = ResultadoComparacaoPortal(
            classificacao='revisao',
            diferencas=(_diferenca(
                'seletor_alterado', 'formulario', 'submeter',
                'botao-emitir', 'botao-emitir-2'),),
        )
        contrato_portal.registrar_incidente(contrato_id, resultado)
        return IncidenteContratoPortal.query.filter_by(
            contrato_base_id=contrato_id, estado='aberto').one().id


# --- autorização ------------------------------------------------------------

@pytest.mark.parametrize('metodo, caminho', [
    ('get', BASE),
    ('post', f'{BASE}/{FLUXO}/{ALVO}/baseline'),
    ('post', f'{BASE}/{FLUXO}/{ALVO}/recon'),
    ('post', f'{BASE}/incidentes/1/aceitar'),
    ('post', f'{BASE}/incidentes/1/rejeitar'),
    ('post', f'{BASE}/1/restaurar'),
])
def test_operador_nao_alcanca_a_central(login_as, metodo, caminho):
    resposta = getattr(login_as('operador'), metodo)(caminho, json={})

    assert resposta.status_code == 403


def test_anonimo_e_redirecionado_para_o_login(client_anon):
    resposta = client_anon.get(BASE)

    assert resposta.status_code in (302, 401)


# --- leitura ----------------------------------------------------------------

def test_sem_contrato_o_alvo_aparece_com_acao_de_baseline(client):
    dados = client.get(BASE).get_json()

    assert dados['status'] == 'ok'
    alvo = dados['alvos'][0]
    assert alvo['estado'] == contrato_portal_recon.DESCONHECIDO
    assert alvo['pode_criar_baseline'] is True
    assert alvo['versao'] is None


def test_listagem_traz_incidentes_e_historico_do_alvo(app, client):
    contrato_id = _baseline(app)
    _incidente(app, contrato_id)

    alvo = client.get(BASE).get_json()['alvos'][0]

    assert alvo['estado'] == contrato_portal_recon.BLOQUEADO
    assert alvo['versao'] == 1
    assert [i['elemento'] for i in alvo['incidentes']] == ['submeter']
    assert [v['versao'] for v in alvo['historico']] == [1]


# --- baseline ---------------------------------------------------------------

def test_baseline_criada_por_acao_explicita_do_admin(app, client):
    resposta = client.post(f'{BASE}/{FLUXO}/{ALVO}/baseline', json={})

    assert resposta.status_code == 201
    assert resposta.get_json()['versao'] == 1
    with app.app_context():
        ativa = ContratoPortal.query.filter_by(
            fluxo=FLUXO, alvo=ALVO, estado='ativa').one()
        assert ativa.origem == 'usuario'
        assert ativa.ativado_por_id is not None


def test_segunda_baseline_no_mesmo_alvo_e_409(app, client):
    _baseline(app)

    resposta = client.post(f'{BASE}/{FLUXO}/{ALVO}/baseline', json={})

    assert resposta.status_code == 409
    assert resposta.get_json()['status'] == 'error'


def test_alvo_desconhecido_e_404(client):
    resposta = client.post(f'{BASE}/nfse/inexistente/baseline', json={})

    assert resposta.status_code == 404


# --- recon sob demanda ------------------------------------------------------

def test_recon_sob_demanda_devolve_o_resultado_da_observacao(app, client, monkeypatch):
    _baseline(app)
    monkeypatch.setattr(
        contratos_portais.contrato_portal_recon, 'recon_sob_demanda',
        lambda *a, **k: contrato_portal_recon.COMPATIVEL)

    resposta = client.post(f'{BASE}/{FLUXO}/{ALVO}/recon', json={})

    assert resposta.status_code == 200
    assert resposta.get_json()['resultado'] == contrato_portal_recon.COMPATIVEL


def test_alvo_ocupado_por_emissao_responde_423(app, client, monkeypatch):
    _baseline(app)
    monkeypatch.setattr(
        contratos_portais.contrato_portal_recon, 'recon_sob_demanda',
        MagicMock(side_effect=contrato_portal_recon.AlvoOcupadoError(
            'Há uma emissão em curso neste portal. Tente novamente depois.')))

    resposta = client.post(f'{BASE}/{FLUXO}/{ALVO}/recon', json={})

    assert resposta.status_code == 423
    assert 'emissão em curso' in resposta.get_json()['message']


def test_falha_ao_observar_nao_vira_500(app, client, monkeypatch):
    _baseline(app)
    monkeypatch.setattr(
        contratos_portais.contrato_portal_recon, 'recon_sob_demanda',
        MagicMock(side_effect=RuntimeError('chromedriver não abriu')))

    resposta = client.post(f'{BASE}/{FLUXO}/{ALVO}/recon', json={})

    assert resposta.status_code == 409
    assert resposta.get_json()['status'] == 'error'
    # A causa técnica fica no log, não na tela.
    assert 'chromedriver' not in resposta.get_json()['message']


# --- revisão de incidente ---------------------------------------------------

def test_aceitar_sem_confirmacao_explicita_e_400(app, client):
    contrato_id = _baseline(app)
    incidente_id = _incidente(app, contrato_id)

    resposta = client.post(f'{BASE}/incidentes/{incidente_id}/aceitar', json={})

    assert resposta.status_code == 400
    assert 'confirmação' in resposta.get_json()['message']


def test_aceitar_incidente_inexistente_e_404(client):
    resposta = client.post(
        f'{BASE}/incidentes/99999/aceitar', json={'confirmado': True})

    assert resposta.status_code == 404


def test_aceitar_promove_a_estrutura_observada(app, client, monkeypatch):
    contrato_id = _baseline(app)
    incidente_id = _incidente(app, contrato_id)
    with app.app_context():
        nova = MagicMock(versao=2)
    monkeypatch.setattr(
        contratos_portais.contrato_portal_recon, 'aceitar_incidente',
        lambda *a, **k: nova)

    resposta = client.post(
        f'{BASE}/incidentes/{incidente_id}/aceitar', json={'confirmado': True})

    assert resposta.status_code == 200
    assert resposta.get_json()['versao'] == 2


def test_aceitar_recusa_quando_a_observacao_nao_sustenta_mais(
    app, client, monkeypatch,
):
    contrato_id = _baseline(app)
    incidente_id = _incidente(app, contrato_id)
    monkeypatch.setattr(
        contratos_portais.contrato_portal_recon, 'aceitar_incidente',
        MagicMock(side_effect=contrato_portal_recon.NadaParaRevisarError(
            'A observação de agora não reproduz a mudança registrada; '
            'rode o recon antes de aprovar.')))

    resposta = client.post(
        f'{BASE}/incidentes/{incidente_id}/aceitar', json={'confirmado': True})

    assert resposta.status_code == 409
    assert 'não reproduz' in resposta.get_json()['message']


def test_rejeitar_encerra_o_incidente_sem_promover(app, client):
    contrato_id = _baseline(app)
    incidente_id = _incidente(app, contrato_id)

    resposta = client.post(f'{BASE}/incidentes/{incidente_id}/rejeitar', json={})

    assert resposta.status_code == 200
    with app.app_context():
        incidente = db.session.get(IncidenteContratoPortal, incidente_id)
        assert incidente.estado == 'rejeitado'
        ativa = ContratoPortal.query.filter_by(
            fluxo=FLUXO, alvo=ALVO, estado='ativa').one()
        assert ativa.id == contrato_id
        assert ativa.versao == 1


def test_rejeitar_duas_vezes_e_404(app, client):
    contrato_id = _baseline(app)
    incidente_id = _incidente(app, contrato_id)
    client.post(f'{BASE}/incidentes/{incidente_id}/rejeitar', json={})

    resposta = client.post(f'{BASE}/incidentes/{incidente_id}/rejeitar', json={})

    assert resposta.status_code == 404


# --- restauração ------------------------------------------------------------

def test_restaurar_sem_confirmacao_explicita_e_400(app, client):
    contrato_id = _baseline(app)

    resposta = client.post(f'{BASE}/{contrato_id}/restaurar', json={})

    assert resposta.status_code == 400


def test_restaurar_versao_inexistente_e_404(client):
    resposta = client.post(f'{BASE}/99999/restaurar', json={'confirmado': True})

    assert resposta.status_code == 404


def test_restaurar_a_propria_ativa_e_409(app, client):
    contrato_id = _baseline(app)

    resposta = client.post(
        f'{BASE}/{contrato_id}/restaurar', json={'confirmado': True})

    assert resposta.status_code == 409


def test_restaurar_devolve_a_versao_anterior_apos_autoajuste(app, client):
    contrato_id = _baseline(app)
    with app.app_context():
        base = db.session.get(ContratoPortal, contrato_id)
        resultado = ResultadoComparacaoPortal(
            classificacao='autoativavel',
            diferencas=(_diferenca(
                'seletor_alterado', 'formulario', 'documento',
                'cpfCnpj', 'cpfCnpj-2'),),
            remapeamentos=(RemapeamentoSeletor(
                chave='documento', seletor_tipo_anterior='id',
                seletor_anterior='cpfCnpj', seletor_tipo_novo='id',
                seletor_novo='cpfCnpj-2'),),
        )
        contrato_portal.autoativar(
            base.id, resultado, fingerprint_base=base.fingerprint)

    resposta = client.post(
        f'{BASE}/{contrato_id}/restaurar', json={'confirmado': True})

    assert resposta.status_code == 200
    with app.app_context():
        ativa = ContratoPortal.query.filter_by(
            fluxo=FLUXO, alvo=ALVO, estado='ativa').one()
        elementos = {e.chave: e.seletor for e in ativa.elementos}
        assert elementos['documento'] == 'cpfCnpj'
