"""Cidade gravada na grafia canonica no cadastro e na edicao (spec 08, DATA-04).

A COV-05 (spec 07) corrigiu as grafias ja existentes por migration, mas o
cadastro e a edicao continuavam gravando o que o operador digitasse — bastava
um novo cadastro para recriar a variacao. Aqui os dois pontos de escrita passam
por `cidade_canonica.canonicalizar`.
"""
from app import db
from app.automation.emissao import _buscar_municipio_por_cidade
from app.models import Empresa, Municipio

CNPJ_VALIDO = '33.000.167/0001-01'


def _form(**over):
    base = {
        'nome': 'Empresa Cidade',
        'cnpj': CNPJ_VALIDO,
        'estado': 'RS',
        'cidade': 'Porto Alegre',
        'inscricao_mobiliaria': '',
    }
    base.update(over)
    return base


def _cidade_cadastrada(app, nome='Empresa Cidade'):
    with app.app_context():
        emp = Empresa.query.filter_by(nome=nome).first()
        return emp.cidade if emp else None


def test_cadastro_grava_cidade_canonica(app, client):
    """DATA-04.1/04.5: 'SAO PAULO' digitado vira 'São Paulo' no banco."""
    r = client.post('/empresa/adicionar', data=_form(cidade='SAO PAULO'))
    assert r.status_code == 302
    assert _cidade_cadastrada(app) == 'São Paulo'


def test_cadastro_cidade_sem_acento_vira_acentuada(app, client):
    """DATA-04.1: 'Tramandai' -> 'Tramandaí'."""
    r = client.post('/empresa/adicionar', data=_form(cidade='Tramandai'))
    assert r.status_code == 302
    assert _cidade_cadastrada(app) == 'Tramandaí'


def test_cadastro_cidade_fora_do_mapa_fica_trimada(app, client):
    """DATA-04.4: cidade desconhecida nao quebra — grava trimada e inalterada."""
    r = client.post('/empresa/adicionar', data=_form(cidade='  Curitiba  '))
    assert r.status_code == 302
    assert _cidade_cadastrada(app) == 'Curitiba'


def test_cadastro_cidade_vazia_continua_barrado(app, client):
    """DATA-04.4 / regressao: cidade vazia segue recusada, sem cadastrar."""
    r = client.post('/empresa/adicionar', data=_form(cidade='   '))
    assert r.status_code == 302
    with app.app_context():
        assert Empresa.query.filter_by(nome='Empresa Cidade').first() is None


def test_edicao_grava_cidade_canonica(app, client, ids):
    """DATA-04.2: editar para 'imbe' grava 'Imbé'."""
    empresa_id = ids['empresa']
    r = client.post(f'/empresa/{empresa_id}/editar', data={
        'nome': 'Empresa Teste', 'estado': 'RS', 'cidade': 'imbe',
        'inscricao_mobiliaria': '',
    })
    assert r.status_code == 302
    with app.app_context():
        assert Empresa.query.get(empresa_id).cidade == 'Imbé'


def test_edicao_cidade_fora_do_mapa_fica_trimada(app, client, ids):
    """DATA-04.4: na edicao tambem, cidade desconhecida passa direto."""
    empresa_id = ids['empresa']
    r = client.post(f'/empresa/{empresa_id}/editar', data={
        'nome': 'Empresa Teste', 'estado': 'PR', 'cidade': ' Curitiba ',
        'inscricao_mobiliaria': '',
    })
    assert r.status_code == 302
    with app.app_context():
        assert Empresa.query.get(empresa_id).cidade == 'Curitiba'


def test_pagina_nova_empresa_exibe_grafia_acentuada(app, login_as):
    """DATA-04.3: a lista de municipios da tela vem do mapa canonico
    compartilhado — municipio gravado sem acento aparece acentuado."""
    with app.app_context():
        db.session.add(Municipio(nome='Osorio', url_certidao='https://exemplo.test',
                                 cnpj_field_id='cnpj', by='id'))
        db.session.commit()

    resp = login_as('operador').get('/empresa/nova')
    assert resp.status_code == 200
    assert 'Osório' in resp.get_data(as_text=True)


def test_pagina_nova_empresa_deduplica_por_exibicao(app, login_as):
    """DATA-04.3 / regressao: duas linhas de Municipio que convergem para a
    mesma grafia canonica aparecem uma vez so na lista."""
    with app.app_context():
        db.session.add(Municipio(nome='Xangrila', url_certidao='https://a.test',
                                 cnpj_field_id='cnpj', by='id'))
        db.session.add(Municipio(nome='Xangri-Lá', url_certidao='https://b.test',
                                 cnpj_field_id='cnpj', by='id'))
        db.session.commit()

    corpo = login_as('operador').get('/empresa/nova').get_data(as_text=True)
    assert corpo.count('>Xangri-Lá<') == 1


def test_mapa_de_exibicao_duplicado_nao_existe_mais():
    """DATA-04.3: o dicionario local foi removido — a grafia tem uma fonte so."""
    import app.routes.empresas as mod
    assert not hasattr(mod, '_NOMES_EXIBICAO_CIDADE')


def test_cidade_canonica_ainda_casa_com_municipio(app, client):
    """DATA-04.5: a automacao municipal casa a empresa com a linha de Municipio
    pela chave normalizada. Gravar a grafia acentuada nao pode quebrar isso —
    e o risco real de canonicalizar no ponto de escrita."""
    with app.app_context():
        db.session.add(Municipio(nome='Imbé', url_certidao='https://exemplo.test',
                                 cnpj_field_id='cnpj', by='id'))
        db.session.commit()

    r = client.post('/empresa/adicionar', data=_form(cidade='IMBE'))
    assert r.status_code == 302

    with app.app_context():
        emp = Empresa.query.filter_by(nome='Empresa Cidade').first()
        assert emp.cidade == 'Imbé'
        assert _buscar_municipio_por_cidade(emp.cidade) is not None
