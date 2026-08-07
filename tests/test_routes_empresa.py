"""Caracterizacao do POST /empresa/adicionar.

Todas as respostas sao redirect (302); o efeito e verificado no banco.

Desde a spec 08 (DATA-01.1) a rota valida o digito verificador, entao o CNPJ
do caminho feliz precisa ter DV valido — o antigo '22.222.222/2222-22' passava
so porque a rota contava 14 digitos. As assercoes seguem as mesmas; mudou o
dado. O teste de duplicidade semeia a empresa pre-existente com DV valido em
vez de usar o seed do conftest, senao ele passaria pelo motivo errado (recusa
por DV, nao por duplicidade).
"""
from app import db
from app.models import Certidao, Empresa, SubtipoCertidao, TipoCertidao

N_TIPOS = len(list(TipoCertidao))

CNPJ_VALIDO = '33.000.167/0001-01'
CNPJ_VALIDO_2 = '11.222.333/0001-81'


def _form(**over):
    base = {
        'nome': 'Nova Empresa',
        'cnpj': CNPJ_VALIDO,
        'estado': 'RS',
        'cidade': 'Porto Alegre',
        'inscricao_mobiliaria': '',
    }
    base.update(over)
    return base


def test_adicionar_empresa_sucesso(app, client):
    r = client.post('/empresa/adicionar', data=_form())
    assert r.status_code == 302
    with app.app_context():
        emp = Empresa.query.filter_by(nome='Nova Empresa').first()
        assert emp is not None
        certs = Certidao.query.filter_by(empresa_id=emp.id).all()
        assert len(certs) == N_TIPOS  # 1 certidao por tipo (cidade != Imbe)


def test_adicionar_empresa_imbe_dois_subtipos(app, client):
    r = client.post('/empresa/adicionar', data=_form(cidade='Imbé'))
    assert r.status_code == 302
    with app.app_context():
        emp = Empresa.query.filter_by(nome='Nova Empresa').first()
        municipais = Certidao.query.filter_by(
            empresa_id=emp.id, tipo=TipoCertidao.MUNICIPAL).all()
        assert len(municipais) == 2
        subtipos = {c.subtipo for c in municipais}
        assert SubtipoCertidao.GERAL in subtipos
        assert SubtipoCertidao.MOBILIARIO in subtipos
        assert Certidao.query.filter_by(empresa_id=emp.id).count() == N_TIPOS + 1


def test_adicionar_empresa_cnpj_invalido(app, client):
    r = client.post('/empresa/adicionar', data=_form(cnpj='123'))
    assert r.status_code == 302
    with app.app_context():
        assert Empresa.query.filter_by(nome='Nova Empresa').first() is None


def test_adicionar_empresa_estado_invalido(app, client):
    r = client.post('/empresa/adicionar', data=_form(estado='Brasil'))
    assert r.status_code == 302
    with app.app_context():
        assert Empresa.query.filter_by(nome='Nova Empresa').first() is None


def test_adicionar_empresa_duplicada(app, client):
    """O CNPJ ja cadastrado e recusado — e a recusa tem de vir da duplicidade,
    por isso a empresa pre-existente e semeada com DV valido."""
    with app.app_context():
        db.session.add(Empresa(nome='Ja Existe', cnpj=CNPJ_VALIDO_2,
                               estado='RS', cidade='Porto Alegre'))
        db.session.commit()

    r = client.post('/empresa/adicionar',
                    data=_form(cnpj='11222333000181'))
    assert r.status_code == 302
    with app.app_context():
        assert Empresa.query.filter(
            Empresa.cnpj.in_({'11222333000181', CNPJ_VALIDO_2})).count() == 1
        assert Empresa.query.filter_by(nome='Nova Empresa').first() is None


def test_adicionar_empresa_inscricao_longa(app, client):
    r = client.post('/empresa/adicionar', data=_form(inscricao_mobiliaria='1234567'))
    assert r.status_code == 302
    with app.app_context():
        assert Empresa.query.filter_by(nome='Nova Empresa').first() is None
