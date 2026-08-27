"""Deteccao e validacao de CPF/CNPJ do tomador.

Fonte unica compartilhada pelo import, pela rota de vinculo manual e pela
automacao: duas implementacoes divergindo emitiriam nota fiscal no documento
errado. Os CPFs usados aqui sao numeros validos por digito verificador, sem
vinculo com pessoa real.
"""
import pytest

from app import utils

CPF_OK = '529.982.247-25'
CNPJ_OK = '44.556.677/0001-86'


@pytest.mark.parametrize('valor,esperado', [
    (CPF_OK, 'cpf'),
    ('52998224725', 'cpf'),
    (CNPJ_OK, 'cnpj'),
    ('44556677000186', 'cnpj'),
    ('123', None),
    ('', None),
    (None, None),
])
def test_detecta_o_tipo_pelo_tamanho(valor, esperado):
    assert utils.detectar_tipo_documento(valor) == esperado


# --- CPF --------------------------------------------------------------------

def test_cpf_valido():
    assert utils.cpf_valido(CPF_OK)
    assert utils.cpf_valido('111.444.777-35')


def test_cpf_com_digito_trocado_e_recusado():
    """O operador digita esse campo na mao: um digito trocado emitiria a nota
    no documento de outra pessoa."""
    assert not utils.cpf_valido('529.982.247-26')


def test_cpf_com_todos_os_digitos_iguais_e_recusado():
    for d in '0123456789':
        assert not utils.cpf_valido(d * 11)


def test_cpf_com_tamanho_errado_e_recusado():
    assert not utils.cpf_valido('529.982.247-2')
    assert not utils.cpf_valido('529.982.247-255')


# --- CNPJ -------------------------------------------------------------------

def test_cnpj_valido():
    assert utils.cnpj_valido(CNPJ_OK)


def test_cnpj_com_digito_trocado_e_recusado():
    assert not utils.cnpj_valido('44.556.677/0001-87')


def test_cnpj_com_todos_os_digitos_iguais_e_recusado():
    assert not utils.cnpj_valido('11.111.111/1111-11')


# --- documento generico -----------------------------------------------------

@pytest.mark.parametrize('valor', [CPF_OK, CNPJ_OK])
def test_documento_valido_aceita_os_dois_tipos(valor):
    assert utils.documento_valido(valor)


@pytest.mark.parametrize('valor', ['', '123', '00000000000', '11.111.111/1111-11'])
def test_documento_invalido(valor):
    assert not utils.documento_valido(valor)


def test_cpf_nao_e_avaliado_como_cnpj():
    """Se a validacao caisse no algoritmo errado por engano, um CPF valido
    seria recusado (ou pior, um invalido aceito)."""
    assert utils.documento_valido(CPF_OK)
    assert not utils.cnpj_valido(CPF_OK)


# --- formatacao -------------------------------------------------------------

def test_formata_cada_tipo_com_sua_mascara():
    assert utils.formatar_documento('52998224725') == '529.982.247-25'
    assert utils.formatar_documento('44556677000186') == '44.556.677/0001-86'


def test_formatar_o_que_nao_e_documento_devolve_o_texto():
    assert utils.formatar_documento('abc') == 'abc'
