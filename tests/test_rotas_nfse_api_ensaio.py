"""Contrato HTTP das transições do ensaio restrito de NFS-e (T27)."""
import json

import pytest

from app import db
from app.models import ConfiguracaoNfse, EnsaioDpsNfse
from app.services import nfse_api_ensaio, nfse_api_sefin
from tests.test_nfse_api_ensaio import (
    _entrada,
    _injetar_fontes,
    _preparado,
    _resultado_nao_encontrado,
    _resultado_rejeitado,
)


def test_preparar_e_consultar_devolvem_resumo_sem_xml_bruto(
        app, ids, client, monkeypatch):
    nota_id, _operador_id = _entrada(app, ids)
    _injetar_fontes(monkeypatch)

    resposta = client.post(
        f'/nfse/api/ensaios/preparar/{nota_id}',
        json={},
    )

    assert resposta.status_code == 200
    dados = resposta.get_json()
    ensaio = dados['ensaio']
    assert dados['status'] == 'ok'
    assert ensaio['estado'] == nfse_api_ensaio.ESTADO_PREPARADO
    assert ensaio['sem_validade_juridica'] is True
    assert ensaio['xml']['referencia_disponivel'] is True
    assert ensaio['xml']['dps_assinada_disponivel'] is True
    assert '<NFSe' not in resposta.get_data(as_text=True)

    consulta = client.get(f"/nfse/api/ensaios/{ensaio['id']}")
    assert consulta.status_code == 200
    assert consulta.get_json()['ensaio']['identificador_dps'] == (
        ensaio['identificador_dps'])
    assert '<NFSe' not in consulta.get_data(as_text=True)


def test_preparar_id_inexistente_devolve_envelope_404(client):
    resposta = client.post('/nfse/api/ensaios/preparar/999999', json={})

    assert resposta.status_code == 404
    dados = resposta.get_json()
    assert dados['status'] == 'error'
    assert dados['request_id']
    assert '<NFSe' not in resposta.get_data(as_text=True)


def test_consultar_id_inexistente_devolve_envelope_404(client):
    resposta = client.get('/nfse/api/ensaios/999999')

    assert resposta.status_code == 404
    dados = resposta.get_json()
    assert dados['status'] == 'error'
    assert dados['message']


def test_enviar_exige_confirmacao_explicita_antes_de_carregar_a_tentativa(
        client, monkeypatch):
    monkeypatch.setattr(
        nfse_api_ensaio,
        'enviar',
        lambda *_args, **_kwargs: pytest.fail(
            'a rota não deve chamar o serviço sem confirmação'),
    )

    resposta = client.post(
        '/nfse/api/ensaios/999999/enviar',
        json={'confirmar_envio': False},
    )

    assert resposta.status_code == 400
    assert resposta.get_json()['campo'] == 'confirmar_envio'


def test_enviar_persiste_rejeicao_e_devolve_somente_resumo(
        app, ids, client, monkeypatch):
    ensaio_id, _operador_id = _preparado(app, ids, monkeypatch)
    monkeypatch.setattr(
        nfse_api_sefin,
        'consultar_dps_restrita',
        lambda identificador: _resultado_nao_encontrado(identificador),
    )
    postagens = []

    def enviar(dps):
        postagens.append(True)
        return _resultado_rejeitado(dps.find('.//{*}infDPS').get('Id'))

    monkeypatch.setattr(nfse_api_sefin, 'enviar_restrita', enviar)

    resposta = client.post(
        f'/nfse/api/ensaios/{ensaio_id}/enviar',
        json={'confirmar_envio': True},
    )

    assert resposta.status_code == 200
    dados = resposta.get_json()['ensaio']
    assert dados['estado'] == nfse_api_ensaio.ESTADO_REJEITADO
    assert dados['codigo_rejeicao'] == 'E-SINT'
    assert dados['motivo_rejeicao'] == 'Rejeição sintética'
    assert postagens == [True]
    assert '<NFSe' not in resposta.get_data(as_text=True)


def test_enviar_nao_aceita_override_de_divergencia(
        app, ids, client, monkeypatch):
    ensaio_id, _operador_id = _preparado(app, ids, monkeypatch)
    with app.app_context():
        ensaio = db.session.get(EnsaioDpsNfse, ensaio_id)
        ensaio.comparacao_json = json.dumps({
            'pode_enviar': False,
            'bloqueadoras': [{'caminho': '/serv/valores'}],
        })
        db.session.commit()

    monkeypatch.setattr(
        nfse_api_sefin,
        'consultar_dps_restrita',
        lambda _identificador: pytest.fail(
            'divergência deve bloquear antes da consulta'),
    )
    monkeypatch.setattr(
        nfse_api_sefin,
        'enviar_restrita',
        lambda *_args, **_kwargs: pytest.fail(
            'divergência não pode ser ignorada'),
    )

    resposta = client.post(
        f'/nfse/api/ensaios/{ensaio_id}/enviar',
        json={'confirmar_envio': True, 'ignorar_divergencias': True},
    )

    assert resposta.status_code == 400
    assert resposta.get_json()['campo'] == 'ignorar_divergencias'

    resposta = client.post(
        f'/nfse/api/ensaios/{ensaio_id}/enviar',
        json={'confirmar_envio': True},
    )
    assert resposta.status_code == 409
    assert resposta.get_json()['status'] == 'error'
    assert '<NFSe' not in resposta.get_data(as_text=True)


def test_reconsultar_exige_estado_indefinido_e_nao_faz_post(
        app, ids, client, monkeypatch):
    ensaio_id, _operador_id = _preparado(app, ids, monkeypatch)
    monkeypatch.setattr(
        nfse_api_sefin,
        'enviar_restrita',
        lambda *_args, **_kwargs: pytest.fail(
            'reconsultar não pode fazer POST'),
    )

    resposta = client.post(
        f'/nfse/api/ensaios/{ensaio_id}/reconsultar',
        json={},
    )

    assert resposta.status_code == 409
    assert resposta.get_json()['status'] == 'error'


def test_leitura_nao_pode_operar_ensaio(login_as, app, ids, monkeypatch):
    nota_id, _operador_id = _entrada(app, ids)
    monkeypatch.setattr(
        nfse_api_ensaio,
        'preparar',
        lambda *_args, **_kwargs: pytest.fail(
            'papel leitura não deve alcançar o serviço'),
    )

    resposta = login_as('leitura').post(
        f'/nfse/api/ensaios/preparar/{nota_id}',
        json={},
    )

    assert resposta.status_code == 403
    dados = resposta.get_json()
    assert dados['status'] == 'error'
    assert dados['request_id']


def test_leitura_nao_pode_consultar_ensaio(login_as):
    resposta = login_as('leitura').get('/nfse/api/ensaios/999999')

    assert resposta.status_code == 403
    dados = resposta.get_json()
    assert dados['status'] == 'error'
    assert dados['message']


@pytest.mark.parametrize(
    ('rota', 'payload', 'campo'),
    (
        ('/nfse/api/ensaios/preparar/999999',
         {'ambiente': 'producao'}, 'ambiente'),
        ('/nfse/api/ensaios/999999/enviar',
         {'confirmar_envio': True, 'ambiente': 'producao'}, 'ambiente'),
        ('/nfse/api/ensaios/999999/enviar',
         {'confirmar_envio': True, 'url': 'https://exemplo.invalid'}, 'url'),
        ('/nfse/api/ensaios/999999/reconsultar',
         {'url': 'https://exemplo.invalid'}, 'url'),
    ),
)
def test_payload_nao_escolhe_ambiente_nem_url(client, rota, payload, campo):
    resposta = client.post(rota, json=payload)

    assert resposta.status_code == 400
    dados = resposta.get_json()
    assert dados['status'] == 'error'
    assert dados['campo'] == campo


def test_rotas_do_ensaio_exigem_csrf_em_resposta_json(app, client):
    app.config['WTF_CSRF_ENABLED'] = True
    try:
        resposta = client.post(
            '/nfse/api/ensaios/preparar/999999',
            json={},
        )
        assert resposta.status_code == 400
        dados = resposta.get_json()
        assert dados['status'] == 'error'
        assert dados['error_type'] == 'csrf'
    finally:
        app.config['WTF_CSRF_ENABLED'] = False


def test_admin_configura_serie_restrita_pelo_endpoint_existente(
        client, app):
    resposta = client.post(
        '/nfse/configuracao',
        json={'serie_dps_restrita': '37'},
    )

    assert resposta.status_code == 200
    assert resposta.get_json()['config']['serie_dps_restrita'] == '37'
    with app.app_context():
        assert db.session.get(ConfiguracaoNfse, 1).serie_dps_restrita == '37'


def test_serie_restrita_reservada_e_recusada_no_endpoint(client, app):
    resposta = client.post(
        '/nfse/configuracao',
        json={'serie_dps_restrita': '80000'},
    )

    assert resposta.status_code == 400
    assert resposta.get_json()['campo'] == 'serie_dps_restrita'
