"""Configuração administrativa do acesso à API nacional (T2A)."""
from app import db
from app.models import ConfiguracaoNfse


def _configuracao(app):
    with app.app_context():
        return db.session.get(ConfiguracaoNfse, 1)


def test_admin_salva_empresa_ambiente_e_habilitacao(client, app, ids):
    resposta = client.post(
        '/nfse/configuracao',
        json={
            'empresa_escritorio_id': ids['empresa'],
            'api_ambiente': 'producao',
            'api_habilitada': True,
        },
    )

    assert resposta.status_code == 200
    config_json = resposta.get_json()['config']
    assert config_json['empresa_escritorio_id'] == ids['empresa']
    assert config_json['api_ambiente'] == 'producao'
    assert config_json['api_habilitada'] is True

    config = _configuracao(app)
    assert config.empresa_escritorio_id == ids['empresa']
    assert config.api_ambiente == 'producao'
    assert config.api_habilitada is True


def test_admin_pode_limpar_empresa_sem_invalidar_a_configuracao(client, app, ids):
    client.post(
        '/nfse/configuracao',
        json={
            'empresa_escritorio_id': ids['empresa'],
            'api_ambiente': 'producao',
            'api_habilitada': True,
        },
    )

    resposta = client.post(
        '/nfse/configuracao',
        json={
            'empresa_escritorio_id': None,
            'api_ambiente': 'restrita',
            'api_habilitada': False,
        },
    )

    assert resposta.status_code == 200
    config = _configuracao(app)
    assert config.empresa_escritorio_id is None
    assert config.api_ambiente == 'restrita'
    assert config.api_habilitada is False


def test_empresa_inexistente_e_recusada_antes_de_gravar(client, app):
    resposta = client.post(
        '/nfse/configuracao',
        json={'empresa_escritorio_id': 999999},
    )

    assert resposta.status_code == 400
    assert resposta.get_json()['campo'] == 'empresa_escritorio_id'
    assert _configuracao(app) is None


def test_ambiente_invalido_preserva_o_valor_anterior(client, app, ids):
    client.post(
        '/nfse/configuracao',
        json={
            'empresa_escritorio_id': ids['empresa'],
            'api_ambiente': 'restrita',
            'api_habilitada': False,
        },
    )

    resposta = client.post(
        '/nfse/configuracao',
        json={'api_ambiente': 'homologacao'},
    )

    assert resposta.status_code == 400
    assert resposta.get_json()['campo'] == 'api_ambiente'
    config = _configuracao(app)
    assert config.api_ambiente == 'restrita'
    assert config.empresa_escritorio_id == ids['empresa']


def test_habilitacao_valida_nao_oculta_outro_campo_invalido(client):
    resposta = client.post(
        '/nfse/configuracao',
        json={
            'api_ambiente': 'homologacao',
            'api_habilitada': True,
        },
    )

    assert resposta.status_code == 400
    assert resposta.get_json()['campo'] == 'api_ambiente'


def test_operador_nao_altera_configuracao_da_api(login_as, app, ids):
    administrador = login_as('admin')
    administrador.post(
        '/nfse/configuracao',
        json={
            'empresa_escritorio_id': ids['empresa'],
            'api_ambiente': 'restrita',
            'api_habilitada': False,
        },
    )

    resposta = login_as('operador').post(
        '/nfse/configuracao',
        json={
            'empresa_escritorio_id': None,
            'api_ambiente': 'producao',
            'api_habilitada': True,
        },
    )

    assert resposta.status_code == 403
    config = _configuracao(app)
    assert config.empresa_escritorio_id == ids['empresa']
    assert config.api_ambiente == 'restrita'
    assert config.api_habilitada is False


def test_configuracao_api_nao_exibe_dados_de_credencial(client):
    corpo = client.get('/nfse').get_data(as_text=True)
    inicio = corpo.index('id="configuracaoApiNacional"')
    fim = corpo.index('id="nfseContratoCentral"', inicio)
    trecho = corpo[inicio:fim].lower()

    assert 'name="empresa_escritorio_id"' in trecho
    assert 'value="producao"' in trecho
    assert 'value="restrita"' in trecho
    assert 'name="api_habilitada"' in trecho
    for termo in ('senha', 'certificado', 'pfx', 'cofre', 'chave privada'):
        assert termo not in trecho


def test_pagina_do_operador_mantem_configuracao_somente_leitura(login_as):
    corpo = login_as('operador').get('/nfse').get_data(as_text=True)
    inicio = corpo.index('id="painelConfig"')
    fim = corpo.index('id="nfseContratoCentral"', inicio)
    trecho = corpo[inicio:fim]

    assert '<fieldset' in trecho and 'disabled' in trecho
    assert 'Somente administradores podem alterar' in trecho
    assert 'type="submit">Salvar configurações</button>' not in trecho
