"""Validacao do CNPJ no cadastro de empresa (spec 08, DATA-01.1/01.2).

Antes da spec 08 a rota so contava 14 digitos, entao CNPJ com digito
verificador errado entrava no banco. Aqui o criterio e o DV, reusando
`utils.cnpj_valido` (o mesmo nucleo que a NFSe ja usa).

Todas as respostas sao redirect (302); o efeito e verificado no banco.
"""
from app.models import Empresa

# CNPJs reais de orgaos publicos, usados so como amostra de DV valido.
CNPJ_VALIDO = '33.000.167/0001-01'
CNPJ_VALIDO_SEM_MASCARA = '11222333000181'


def _form(**over):
    base = {
        'nome': 'Empresa DV',
        'cnpj': CNPJ_VALIDO,
        'estado': 'RS',
        'cidade': 'Porto Alegre',
        'inscricao_mobiliaria': '',
    }
    base.update(over)
    return base


def _cadastrada(app, nome='Empresa DV'):
    with app.app_context():
        return Empresa.query.filter_by(nome=nome).first()


def test_dv_invalido_nao_cadastra(app, client):
    """DATA-01.1: 14 digitos mas DV errado -> nenhuma Empresa criada."""
    # 33.000.167/0001-02 e o CNPJ valido acima com o ultimo digito trocado.
    r = client.post('/empresa/adicionar', data=_form(cnpj='33.000.167/0001-02'))
    assert r.status_code == 302
    assert _cadastrada(app) is None


def test_dv_invalido_mensagem_aciona(app, client):
    """DATA-01.1: a mensagem diz que o digito verificador nao confere,
    em vez de um 'invalido' generico que nao ajuda o operador."""
    r = client.post('/empresa/adicionar', data=_form(cnpj='33.000.167/0001-02'),
                    follow_redirects=True)
    corpo = r.get_data(as_text=True)
    assert 'dígito verificador' in corpo
    assert _cadastrada(app) is None


def test_digitos_repetidos_nao_cadastra(app, client):
    """DATA-01.1 / edge: 11.111.111/1111-11 tem 14 digitos e passava antes."""
    r = client.post('/empresa/adicionar', data=_form(cnpj='11.111.111/1111-11'))
    assert r.status_code == 302
    assert _cadastrada(app) is None


def test_cnpj_curto_nao_cadastra(app, client):
    """DATA-01.1: menos de 14 digitos continua barrado (regressao)."""
    r = client.post('/empresa/adicionar', data=_form(cnpj='123'))
    assert r.status_code == 302
    assert _cadastrada(app) is None


def test_dv_valido_com_mascara_cadastra(app, client):
    """DATA-01.2: CNPJ valido mascarado e aceito e gravado formatado."""
    r = client.post('/empresa/adicionar', data=_form(cnpj=CNPJ_VALIDO))
    assert r.status_code == 302
    emp = _cadastrada(app)
    assert emp is not None
    assert emp.cnpj == '33.000.167/0001-01'


def test_dv_valido_sem_mascara_cadastra(app, client):
    """DATA-01.2: CNPJ valido cru e aceito e normalizado para a mascara."""
    r = client.post('/empresa/adicionar',
                    data=_form(nome='Empresa Crua', cnpj=CNPJ_VALIDO_SEM_MASCARA))
    assert r.status_code == 302
    emp = _cadastrada(app, nome='Empresa Crua')
    assert emp is not None
    assert emp.cnpj == '11.222.333/0001-81'


def test_rota_reusa_cnpj_valido_do_utils(monkeypatch, app, client):
    """DATA-01.1: a rota consome `utils.cnpj_valido` em vez de reimplementar a
    regra. Forcando o nucleo a reprovar, o cadastro tem de recusar junto."""
    import app.routes.empresas as mod
    monkeypatch.setattr(mod, 'cnpj_valido', lambda _valor: False)
    r = client.post('/empresa/adicionar', data=_form(cnpj=CNPJ_VALIDO))
    assert r.status_code == 302
    assert _cadastrada(app) is None
