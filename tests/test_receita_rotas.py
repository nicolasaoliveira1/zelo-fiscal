"""Rotas de consulta da Receita (spec 08, DATA-01.3/01.4).

`receita_client.consultar` e sempre mockado: rota nao bate em rede real.
"""
from datetime import date

from app import db
from app.models import DadosReceita, Empresa
from app.services import receita_client, receita_service

CNPJ_VALIDO = '33.000.167/0001-01'


def _dto(**over):
    base = dict(cnpj='33000167000101', situacao='ATIVA',
                situacao_data=date(2005, 11, 3),
                razao_social='PETROLEO BRASILEIRO S A', nome_fantasia='PETROBRAS',
                municipio='Porto Alegre', uf='RS', cep='20031170',
                logradouro='AVENIDA REPUBLICA DO CHILE', numero='65',
                bairro='CENTRO', fonte='brasilapi')
    base.update(over)
    return receita_client.DadosReceitaDTO(**base)


def _mock_consulta(monkeypatch, retorno):
    chamadas = []

    def _consultar(cnpj):
        chamadas.append(cnpj)
        return retorno
    monkeypatch.setattr(receita_service.receita_client, 'consultar', _consultar)
    return chamadas


# --- happy path (DATA-01.3) ---------------------------------------------------

def test_consulta_devolve_campos_para_o_formulario(app, client, ids, monkeypatch):
    _mock_consulta(monkeypatch, (_dto(), None))

    resp = client.post('/empresa/receita/consultar', json={'cnpj': CNPJ_VALIDO})

    assert resp.status_code == 200
    corpo = resp.get_json()
    assert corpo['status'] == 'ok'
    assert corpo['dados']['cidade'] == 'Porto Alegre'
    assert corpo['dados']['estado'] == 'RS'
    assert corpo['dados']['razao_social'] == 'PETROLEO BRASILEIRO S A'
    assert corpo['dados']['ativa'] is True


def test_consulta_nao_persiste_nada(app, client, ids, monkeypatch):
    """DATA-01.3: a rota so devolve; quem grava e o POST de cadastro."""
    _mock_consulta(monkeypatch, (_dto(), None))
    with app.app_context():
        empresas_antes = Empresa.query.count()
        dados_antes = DadosReceita.query.count()

    client.post('/empresa/receita/consultar', json={'cnpj': CNPJ_VALIDO})

    with app.app_context():
        assert Empresa.query.count() == empresas_antes
        assert DadosReceita.query.count() == dados_antes


def test_consulta_aceita_form_alem_de_json(app, client, ids, monkeypatch):
    _mock_consulta(monkeypatch, (_dto(), None))
    resp = client.post('/empresa/receita/consultar', data={'cnpj': CNPJ_VALIDO})
    assert resp.status_code == 200


def test_consulta_sinaliza_empresa_baixada(app, client, ids, monkeypatch):
    _mock_consulta(monkeypatch, (_dto(situacao='BAIXADA'), None))

    corpo = client.post('/empresa/receita/consultar',
                        json={'cnpj': CNPJ_VALIDO}).get_json()

    assert corpo['dados']['ativa'] is False
    assert corpo['dados']['situacao'] == 'BAIXADA'


# --- erros ---------------------------------------------------------------------

def test_dv_invalido_nao_chama_a_rede(app, client, ids, monkeypatch):
    """Nao gasta consulta (nem cota) com CNPJ que ja sabemos estar errado."""
    chamadas = _mock_consulta(monkeypatch, (_dto(), None))

    resp = client.post('/empresa/receita/consultar',
                       json={'cnpj': '33.000.167/0001-02'})

    assert resp.status_code == 400
    assert 'dígito verificador' in resp.get_json()['message']
    assert chamadas == []


def test_cnpj_vazio_e_recusado(app, client, ids, monkeypatch):
    chamadas = _mock_consulta(monkeypatch, (_dto(), None))
    resp = client.post('/empresa/receita/consultar', json={'cnpj': ''})
    assert resp.status_code == 400
    assert chamadas == []


def test_cnpj_inexistente_devolve_404(app, client, ids, monkeypatch):
    _mock_consulta(monkeypatch, (None, receita_client.ERRO_NAO_ENCONTRADO))

    resp = client.post('/empresa/receita/consultar', json={'cnpj': CNPJ_VALIDO})

    assert resp.status_code == 404
    assert 'não encontrado' in resp.get_json()['message']


def test_api_fora_devolve_503_amigavel_e_nao_500(app, client, ids, monkeypatch):
    """DATA-01.5: a mensagem diz ao operador que da para cadastrar assim mesmo."""
    _mock_consulta(monkeypatch, (None, receita_client.ERRO_INDISPONIVEL))

    resp = client.post('/empresa/receita/consultar', json={'cnpj': CNPJ_VALIDO})

    assert resp.status_code == 503
    mensagem = resp.get_json()['message']
    assert 'Cadastre a empresa normalmente' in mensagem


def test_excecao_da_consulta_nao_vira_500(app, client, ids, monkeypatch):
    """O cliente nunca levanta, mas se levantasse a rota nao pode dar 500 seco."""
    _mock_consulta(monkeypatch, (None, 'motivo_desconhecido'))
    resp = client.post('/empresa/receita/consultar', json={'cnpj': CNPJ_VALIDO})
    assert resp.status_code == 503


# --- autorizacao (AD-005) -------------------------------------------------------

def test_papel_leitura_nao_consulta(app, login_as, ids, monkeypatch):
    chamadas = _mock_consulta(monkeypatch, (_dto(), None))
    resp = login_as('leitura').post('/empresa/receita/consultar',
                                    json={'cnpj': CNPJ_VALIDO})
    assert resp.status_code == 403
    assert chamadas == []


def test_anonimo_nao_consulta(app, client_anon, ids, monkeypatch):
    chamadas = _mock_consulta(monkeypatch, (_dto(), None))
    resp = client_anon.post('/empresa/receita/consultar', json={'cnpj': CNPJ_VALIDO})
    assert resp.status_code in (302, 401, 403)
    assert chamadas == []


# --- o cadastro continua sem tocar a rede (DATA-01.4) ---------------------------

def test_cadastro_nao_chama_a_receita(app, client, ids, monkeypatch):
    """DATA-01.4: o POST de cadastro nunca fica pendurado em API externa.
    Este teste falha se alguem "melhorar" a rota consultando a Receita nela."""
    chamadas = _mock_consulta(monkeypatch, (_dto(), None))

    resp = client.post('/empresa/adicionar', data={
        'nome': 'Empresa Sem Rede', 'cnpj': CNPJ_VALIDO,
        'estado': 'RS', 'cidade': 'Porto Alegre', 'inscricao_mobiliaria': '',
    })

    assert resp.status_code == 302
    with app.app_context():
        assert Empresa.query.filter_by(nome='Empresa Sem Rede').first() is not None
    assert chamadas == [], 'o cadastro consultou a Receita — nao pode'


def test_cadastro_nao_cria_dados_receita(app, client, ids, monkeypatch):
    """O espelho da Receita nasce no recheck ou na consulta explicita, nao no
    cadastro — que e justamente o que mantem o cadastro offline."""
    _mock_consulta(monkeypatch, (_dto(), None))
    client.post('/empresa/adicionar', data={
        'nome': 'Empresa Sem Espelho', 'cnpj': CNPJ_VALIDO,
        'estado': 'RS', 'cidade': 'Porto Alegre', 'inscricao_mobiliaria': '',
    })

    with app.app_context():
        emp = Empresa.query.filter_by(nome='Empresa Sem Espelho').first()
        assert emp.dados_receita is None
        # e continua entrando no lote: nao classificada != morta
        assert receita_service.empresa_ativa(emp) is True


def test_db_nao_e_tocado_quando_a_consulta_falha(app, client, ids, monkeypatch):
    _mock_consulta(monkeypatch, (None, receita_client.ERRO_INDISPONIVEL))
    with app.app_context():
        antes = DadosReceita.query.count()

    client.post('/empresa/receita/consultar', json={'cnpj': CNPJ_VALIDO})

    with app.app_context():
        assert DadosReceita.query.count() == antes
        db.session.rollback()
