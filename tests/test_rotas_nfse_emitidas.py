"""Contrato HTTP da conferência por intervalo, sem automação externa."""
from datetime import date, datetime
from types import SimpleNamespace

from app import db
from app.models import ConsultaEmitidaNfse
from app.services import nfse_emitidas


def _sessao_livre(monkeypatch, rotas_nfse):
    estado = SimpleNamespace(adquiridas=0, liberadas=0)

    def adquirir():
        estado.adquiridas += 1
        return True

    def liberar():
        estado.liberadas += 1

    monkeypatch.setattr(
        rotas_nfse, 'SESSAO',
        SimpleNamespace(adquirir=adquirir, liberar=liberar),
    )
    return estado


def test_post_consulta_devolve_id_e_intervalo(client, app, monkeypatch):
    import app.routes.nfse as rotas_nfse

    estado = _sessao_livre(monkeypatch, rotas_nfse)
    chamada = {}

    def consultar(inicio, fim):
        chamada['periodo'] = (inicio, fim)
        consulta = ConsultaEmitidaNfse(
            inicio=inicio, fim=fim, consultado_em=datetime(2026, 8, 31, 15, 10))
        db.session.add(consulta)
        db.session.commit()
        return {
            'blocos': 1,
            'lidas': 0,
            'novas': 0,
            'atualizadas': 0,
            'consulta_id': consulta.id,
        }

    monkeypatch.setattr(nfse_emitidas, 'consultar', consultar)
    resposta = client.post('/nfse/emitidas/consultar', json={
        'inicio': '2026-08-01',
        'fim': '2026-08-17',
    })

    assert resposta.status_code == 200
    dados = resposta.get_json()
    assert chamada['periodo'] == (date(2026, 8, 1), date(2026, 8, 17))
    assert dados['consulta_id'] == dados['painel']['consulta_id']
    assert dados['painel']['inicio'] == '2026-08-01'
    assert dados['painel']['fim'] == '2026-08-17'
    assert 'competencia' not in dados['painel']
    assert estado.adquiridas == estado.liberadas == 1


def test_get_por_consulta_id_reabre_o_intervalo_exato(client, app):
    with app.app_context():
        consulta = ConsultaEmitidaNfse(
            inicio=date(2026, 7, 3), fim=date(2026, 7, 19),
            consultado_em=datetime(2026, 7, 20, 9, 0))
        db.session.add(consulta)
        db.session.commit()
        consulta_id = consulta.id

    resposta = client.get(f'/nfse/emitidas?consulta_id={consulta_id}')

    assert resposta.status_code == 200
    painel = resposta.get_json()['painel']
    assert painel['consulta_id'] == consulta_id
    assert painel['inicio'] == '2026-07-03'
    assert painel['fim'] == '2026-07-19'


def test_get_consulta_inexistente_devolve_404(client):
    resposta = client.get('/nfse/emitidas?consulta_id=999999')

    assert resposta.status_code == 404
    assert resposta.get_json()['status'] == 'error'


def test_get_mes_sem_consulta_mostra_estado_honesto(client):
    resposta = client.get('/nfse/emitidas?mes=07/2026')

    assert resposta.status_code == 200
    painel = resposta.get_json()['painel']
    assert painel['nunca_consultado'] is True
    assert painel['consulta_id'] is None
    assert painel['sem_nota'] == []
