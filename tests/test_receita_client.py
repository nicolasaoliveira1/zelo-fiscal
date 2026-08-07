"""Cliente de consulta de CNPJ (spec 08, DATA-01.5/01.6/01.7).

`requests` e SEMPRE mockado: nenhum teste desta suite bate em rede real — API
externa dentro do CI vira flake sem culpa do codigo.

Os payloads abaixo sao recortes das respostas REAIS das duas APIs, capturadas
consultando o CNPJ publico da Petrobras durante o design. Nao sao inventados —
e o que garante que o normalizador case com o formato de verdade.
"""
from datetime import date

import pytest

from app.services import receita_client as rc

PAYLOAD_BRASILAPI = {
    'cnpj': '33000167000101',
    'razao_social': 'PETROLEO BRASILEIRO S A PETROBRAS',
    'nome_fantasia': 'PETROBRAS - EDISE',
    'descricao_situacao_cadastral': 'ATIVA',
    'data_situacao_cadastral': '2005-11-03',
    'descricao_motivo_situacao_cadastral': 'SEM MOTIVO',
    'data_inicio_atividade': '1966-09-28',
    'porte': 'DEMAIS',
    'natureza_juridica': 'Sociedade de Economia Mista',
    'descricao_identificador_matriz_filial': 'MATRIZ',
    'descricao_tipo_de_logradouro': 'AVENIDA',
    'logradouro': 'REPUBLICA DO CHILE',
    'numero': '65',
    'complemento': '',
    'bairro': 'CENTRO',
    'cep': '20031170',
    'codigo_municipio_ibge': '3304557',
    'municipio': 'RIO DE JANEIRO',
    'uf': 'RJ',
    'cnae_fiscal': 600001,
    'cnae_fiscal_descricao': 'Extração de petróleo e gás natural',
    'opcao_pelo_simples': None,
    'data_opcao_pelo_simples': None,
}

PAYLOAD_RECEITAWS = {
    'status': 'OK',
    'cnpj': '33.000.167/0001-01',
    'nome': 'PETROLEO BRASILEIRO S A PETROBRAS',
    'fantasia': 'PETROBRAS - EDISE',
    'situacao': 'ATIVA',
    'data_situacao': '03/11/2005',
    'motivo_situacao': '',
    'abertura': '28/09/1966',
    'porte': 'DEMAIS',
    'natureza_juridica': '203-8 - Sociedade de Economia Mista',
    'tipo': 'MATRIZ',
    'logradouro': 'AV REPUBLICA DO CHILE',
    'numero': '65',
    'complemento': '',
    'bairro': 'CENTRO',
    'cep': '20.031-170',
    'municipio': 'RIO DE JANEIRO',
    'uf': 'RJ',
    'atividade_principal': [{'code': '06.00-0-01',
                             'text': 'Extração de petróleo e gás natural'}],
    'simples': {'optante': False, 'data_opcao': None},
}


class _Resp:
    def __init__(self, status_code=200, payload=None, explode_json=False):
        self.status_code = status_code
        self._payload = payload
        self._explode = explode_json

    def json(self):
        if self._explode:
            raise ValueError('resposta nao e JSON')
        return self._payload


def _mock_requests(monkeypatch, por_url):
    """Roteia GET por trecho de URL. Registra as chamadas para inspecao."""
    chamadas = []

    def _get(url, headers=None, timeout=None):
        chamadas.append({'url': url, 'headers': headers or {}, 'timeout': timeout})
        for trecho, resposta in por_url.items():
            if trecho in url:
                if isinstance(resposta, Exception):
                    raise resposta
                return resposta
        raise AssertionError(f'URL nao esperada: {url}')

    monkeypatch.setattr(rc.requests, 'get', _get)
    return chamadas


# --- normalizacao: as duas fontes viram o MESMO DTO (DATA-01.7) --------------

def test_brasilapi_normaliza_para_o_dto(monkeypatch):
    _mock_requests(monkeypatch, {'brasilapi': _Resp(200, PAYLOAD_BRASILAPI)})
    dto, erro = rc.consultar('33000167000101')

    assert erro is None
    assert dto.fonte == 'brasilapi'
    assert dto.razao_social == 'PETROLEO BRASILEIRO S A PETROBRAS'
    assert dto.situacao == 'ATIVA'
    assert dto.situacao_data == date(2005, 11, 3)
    assert dto.data_inicio_atividade == date(1966, 9, 28)
    # tipo do logradouro vem separado nesta fonte e e juntado na normalizacao
    assert dto.logradouro == 'AVENIDA REPUBLICA DO CHILE'
    assert dto.cep == '20031170'
    assert dto.municipio_ibge == '3304557'
    assert dto.uf == 'RJ'
    assert dto.cnae_fiscal == '600001'


def test_receitaws_normaliza_para_o_dto(monkeypatch):
    _mock_requests(monkeypatch, {
        'brasilapi': _Resp(500),
        'receitaws': _Resp(200, PAYLOAD_RECEITAWS),
    })
    dto, erro = rc.consultar('33000167000101')

    assert erro is None
    assert dto.fonte == 'receitaws'
    assert dto.razao_social == 'PETROLEO BRASILEIRO S A PETROBRAS'
    assert dto.situacao == 'ATIVA'
    # data em formato BR nesta fonte, ISO na outra: as duas viram date
    assert dto.situacao_data == date(2005, 11, 3)
    assert dto.data_inicio_atividade == date(1966, 9, 28)
    # CEP mascarado nesta fonte, cru na outra: as duas viram digitos
    assert dto.cep == '20031170'
    assert dto.cnae_fiscal == '06.00-0-01'
    assert dto.opcao_simples is False


def test_as_duas_fontes_concordam_nos_campos_de_identidade(monkeypatch):
    """DATA-01.7: nada abaixo da borda pode precisar saber de qual fonte veio."""
    _mock_requests(monkeypatch, {'brasilapi': _Resp(200, PAYLOAD_BRASILAPI)})
    dto_b, _ = rc.consultar('33000167000101')

    _mock_requests(monkeypatch, {'brasilapi': _Resp(500),
                                 'receitaws': _Resp(200, PAYLOAD_RECEITAWS)})
    dto_r, _ = rc.consultar('33000167000101')

    for campo in ('razao_social', 'nome_fantasia', 'situacao', 'situacao_data',
                  'data_inicio_atividade', 'municipio', 'uf', 'cep', 'numero',
                  'bairro', 'porte', 'matriz_filial'):
        assert getattr(dto_b, campo) == getattr(dto_r, campo), campo


def test_campo_vazio_da_receita_vira_none(monkeypatch):
    """'' em motivo_situacao/complemento nao pode virar string vazia na tela."""
    _mock_requests(monkeypatch, {'brasilapi': _Resp(500),
                                 'receitaws': _Resp(200, PAYLOAD_RECEITAWS)})
    dto, _ = rc.consultar('33000167000101')
    assert dto.situacao_motivo is None
    assert dto.complemento is None


def test_opcao_simples_preserva_none_e_false(monkeypatch):
    """Tri-estado: None ("nao informado") != False ("nao optante")."""
    _mock_requests(monkeypatch, {'brasilapi': _Resp(200, PAYLOAD_BRASILAPI)})
    dto_none, _ = rc.consultar('33000167000101')
    assert dto_none.opcao_simples is None

    _mock_requests(monkeypatch, {'brasilapi': _Resp(500),
                                 'receitaws': _Resp(200, PAYLOAD_RECEITAWS)})
    dto_false, _ = rc.consultar('33000167000101')
    assert dto_false.opcao_simples is False


# --- User-Agent e timeout (DATA-01.6) ---------------------------------------

def test_toda_chamada_manda_user_agent(monkeypatch):
    """Sem User-Agent a BrasilAPI devolve 403 — verificado ao vivo no design."""
    chamadas = _mock_requests(monkeypatch, {'brasilapi': _Resp(500),
                                            'receitaws': _Resp(200, PAYLOAD_RECEITAWS)})
    rc.consultar('33000167000101')

    assert len(chamadas) == 2
    for chamada in chamadas:
        assert chamada['headers'].get('User-Agent')


def test_toda_chamada_tem_timeout(monkeypatch):
    """Sem timeout, um worker do Flask fica pendurado na rede."""
    chamadas = _mock_requests(monkeypatch, {'brasilapi': _Resp(200, PAYLOAD_BRASILAPI)})
    rc.consultar('33000167000101')
    assert chamadas[0]['timeout'] == rc.TIMEOUT_S


def test_403_da_brasilapi_cai_para_o_fallback(monkeypatch):
    """403 e bloqueio de cliente, nao "CNPJ inexistente" — tem de tentar a outra."""
    _mock_requests(monkeypatch, {'brasilapi': _Resp(403),
                                 'receitaws': _Resp(200, PAYLOAD_RECEITAWS)})
    dto, erro = rc.consultar('33000167000101')
    assert erro is None
    assert dto.fonte == 'receitaws'


# --- fallback e falhas (DATA-01.5) ------------------------------------------

@pytest.mark.parametrize('resposta', [
    _Resp(500), _Resp(429), _Resp(200, explode_json=True),
])
def test_falha_da_brasilapi_usa_receitaws(resposta, monkeypatch):
    _mock_requests(monkeypatch, {'brasilapi': resposta,
                                 'receitaws': _Resp(200, PAYLOAD_RECEITAWS)})
    dto, erro = rc.consultar('33000167000101')
    assert erro is None
    assert dto.fonte == 'receitaws'


def test_timeout_da_brasilapi_usa_receitaws(monkeypatch):
    _mock_requests(monkeypatch, {'brasilapi': rc.requests.exceptions.Timeout('demorou'),
                                 'receitaws': _Resp(200, PAYLOAD_RECEITAWS)})
    dto, erro = rc.consultar('33000167000101')
    assert erro is None
    assert dto.fonte == 'receitaws'


def test_as_duas_fontes_fora_devolve_indisponivel(monkeypatch):
    """DATA-01.5: nunca levanta — o cadastro segue com validacao local."""
    _mock_requests(monkeypatch, {'brasilapi': _Resp(500), 'receitaws': _Resp(503)})
    dto, erro = rc.consultar('33000167000101')
    assert dto is None
    assert erro == rc.ERRO_INDISPONIVEL


def test_cnpj_inexistente_nao_gasta_a_segunda_fonte(monkeypatch):
    """404 e resposta definitiva: as duas leem o mesmo cadastro, entao
    reperguntar so gastaria a cota da fonte com rate limit mais apertado."""
    chamadas = _mock_requests(monkeypatch, {'brasilapi': _Resp(404)})
    dto, erro = rc.consultar('11222333000181')

    assert dto is None
    assert erro == rc.ERRO_NAO_ENCONTRADO
    assert len(chamadas) == 1


def test_receitaws_erro_no_corpo_com_http_200(monkeypatch):
    """A ReceitaWS erra com HTTP 200 e status ERROR no CORPO: quem olhar so o
    codigo HTTP daria a resposta de erro por boa."""
    _mock_requests(monkeypatch, {
        'brasilapi': _Resp(500),
        'receitaws': _Resp(200, {'status': 'ERROR', 'message': 'CNPJ inválido'}),
    })
    dto, erro = rc.consultar('33000167000101')
    assert dto is None
    assert erro == rc.ERRO_INDISPONIVEL


def test_receitaws_nao_encontrado_no_corpo(monkeypatch):
    _mock_requests(monkeypatch, {
        'brasilapi': _Resp(500),
        'receitaws': _Resp(200, {'status': 'ERROR', 'message': 'CNPJ não encontrado'}),
    })
    dto, erro = rc.consultar('33000167000101')
    assert dto is None
    assert erro == rc.ERRO_NAO_ENCONTRADO


@pytest.mark.parametrize('valor', ['', None, '123', '3300016700010', 'abc'])
def test_cnpj_malformado_nao_chama_rede(valor, monkeypatch):
    chamadas = _mock_requests(monkeypatch, {'brasilapi': _Resp(200, PAYLOAD_BRASILAPI)})
    dto, erro = rc.consultar(valor)

    assert dto is None
    assert erro == rc.ERRO_CNPJ_INVALIDO
    assert chamadas == []


def test_cnpj_mascarado_e_aceito(monkeypatch):
    chamadas = _mock_requests(monkeypatch, {'brasilapi': _Resp(200, PAYLOAD_BRASILAPI)})
    dto, erro = rc.consultar('33.000.167/0001-01')

    assert erro is None and dto is not None
    # a URL vai com o CNPJ so em digitos
    assert '33000167000101' in chamadas[0]['url']


def test_excecao_inesperada_nao_escapa(monkeypatch):
    """Nunca levanta: o cadastro nao pode cair por causa de API externa."""
    _mock_requests(monkeypatch, {'brasilapi': RuntimeError('boom'),
                                 'receitaws': RuntimeError('boom')})
    dto, erro = rc.consultar('33000167000101')
    assert dto is None
    assert erro == rc.ERRO_INDISPONIVEL
