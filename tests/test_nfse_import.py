"""Parser do extrato do banco e calculo da competencia (NFSE-01, NFSE-02).

Funcoes puras sobre bytes/strings: nao tocam banco nem Selenium. A fixture
`tests/fixtures/extrato_banco.csv` e anonimizada, mas preserva as propriedades
estruturais do arquivo real que o parser precisa aguentar: nomes truncados em
35 caracteres, abreviacoes do banco, espaco duplo interno, nome vazio, linha com
valor nao numerico e o mesmo tomador com vencimentos em meses diferentes.
"""
import os
from datetime import date
from decimal import Decimal

import pytest

from app.services import nfse_import as imp

FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures', 'extrato_banco.csv')

LINHA = ('"13/07/2026";"L LUIS PETRY ME";"0001443038";"062623";"05/07/2026";'
         '"811,00";"16,22";"1,13";"826,09";"COBRANCA SIMPLES"')


def _fixture_bytes():
    with open(FIXTURE, 'rb') as fh:
        return fh.read()


# --- competencia: mes ANTERIOR ao vencimento -------------------------------

@pytest.mark.parametrize('vencimento,esperado', [
    (date(2026, 7, 5), '06/2026'),
    (date(2026, 6, 5), '05/2026'),
    (date(2026, 12, 10), '11/2026'),
    # virada de ano: janeiro volta para dezembro do ano anterior
    (date(2027, 1, 5), '12/2026'),
    (date(2026, 1, 31), '12/2025'),
])
def test_competencia_e_o_mes_anterior_ao_vencimento(vencimento, esperado):
    assert imp.competencia_da_descricao(vencimento) == esperado


def test_competencia_sem_vencimento_levanta():
    with pytest.raises(ValueError):
        imp.competencia_da_descricao(None)


# --- parse de uma linha ----------------------------------------------------

def test_parse_extrai_as_colunas_uteis_e_descarta_as_outras():
    linha = imp.parse_csv(LINHA)[0]
    assert linha.nome == 'L LUIS PETRY ME'
    assert linha.data_pagamento == date(2026, 7, 13)
    assert linha.vencimento == date(2026, 7, 5)
    assert linha.valor_titulo == Decimal('811.00')
    assert linha.acrescimos == Decimal('16.22')
    assert linha.deducoes == Decimal('1.13')
    assert linha.valor_final == Decimal('826.09')
    assert not linha.invalida


def test_valor_brasileiro_com_milhar_vira_decimal_exato():
    bruto = LINHA.replace('"826,09"', '"1.784,00"')
    assert imp.parse_csv(bruto)[0].valor_final == Decimal('1784.00')


def test_nome_normalizado_colapsa_espaco_e_remove_acento():
    assert imp.normalizar_nome('  Restaurante   Galetão  ME ') == 'RESTAURANTE GALETAO ME'


# --- encoding --------------------------------------------------------------

def test_le_cp1252_quando_nao_e_utf8():
    bruto = LINHA.replace('L LUIS PETRY ME', 'PADARIA SÃO JOÃO')
    linha = imp.parse_csv(bruto.encode('cp1252'))[0]
    assert linha.nome == 'PADARIA SÃO JOÃO'


def test_le_utf8_com_bom():
    bruto = LINHA.replace('L LUIS PETRY ME', 'PADARIA SÃO JOÃO')
    linha = imp.parse_csv(bruto.encode('utf-8-sig'))[0]
    assert linha.nome == 'PADARIA SÃO JOÃO'


# --- arquivo invalido recusa TUDO (NFSE-07) --------------------------------

@pytest.mark.parametrize('conteudo', ['', '   \n\n  ', b''])
def test_arquivo_vazio_e_recusado(conteudo):
    with pytest.raises(imp.ArquivoInvalidoError):
        imp.parse_csv(conteudo)


def test_arquivo_sem_nenhuma_linha_no_formato_e_recusado():
    # csv de outro sistema: virgula, com cabecalho, poucas colunas
    outro = 'nome,valor\nFulano,10.00\nBeltrano,20.00\n'
    with pytest.raises(imp.ArquivoInvalidoError):
        imp.parse_csv(outro)


def test_mensagem_de_recusa_e_acionavel():
    with pytest.raises(imp.ArquivoInvalidoError) as exc:
        imp.parse_csv('nome,valor\nFulano,10.00\n')
    texto = str(exc.value)
    assert 'colunas' in texto.lower()
    assert ';' in texto


# --- linha ruim nao aborta o arquivo (edge case) ---------------------------

def test_linha_com_valor_nao_numerico_vira_invalida_sem_abortar():
    bruto = LINHA + '\n' + LINHA.replace('"826,09"', '"A COMBINAR"')
    linhas = imp.parse_csv(bruto)
    assert len(linhas) == 2
    assert not linhas[0].invalida
    assert linhas[1].invalida
    assert 'valor final' in (linhas[1].motivo or '').lower()


def test_linha_com_menos_colunas_vira_invalida_sem_abortar():
    bruto = LINHA + '\n"01/07/2026";"SO DUAS COLUNAS"'
    linhas = imp.parse_csv(bruto)
    assert len(linhas) == 2
    assert linhas[1].invalida


def test_nome_vazio_nao_invalida_a_linha():
    # nome vazio deixa a nota pendente de empresa, mas os valores sao validos
    linha = imp.parse_csv(LINHA.replace('"L LUIS PETRY ME"', '""'))[0]
    assert not linha.invalida
    assert linha.nome == ''
    assert linha.valor_final == Decimal('826.09')


def test_linhas_em_branco_sao_ignoradas():
    assert len(imp.parse_csv(LINHA + '\n\n\n' + LINHA)) == 2


# --- o arquivo real (anonimizado) ------------------------------------------

def test_fixture_e_lida_por_inteiro():
    linhas = imp.parse_csv(_fixture_bytes())
    assert len(linhas) == 50
    assert sum(1 for linha in linhas if linha.invalida) == 1


def test_fixture_preserva_o_truncamento_do_banco_em_35_chars():
    nomes = [linha.nome for linha in imp.parse_csv(_fixture_bytes())]
    assert any(len(nome) == 35 for nome in nomes), (
        'a fixture precisa manter nomes truncados: e o caso que quebra o match exato')


def test_aritmetica_do_extrato_bate_em_todas_as_linhas_validas():
    # I = F + G - H no arquivo real; o importar() usa isso como rede de seguranca
    for linha in imp.parse_csv(_fixture_bytes()):
        if linha.invalida:
            continue
        assert linha.valor_titulo + linha.acrescimos - linha.deducoes == linha.valor_final


def test_fixture_tem_tomador_com_duas_competencias_distintas():
    # caso real que prova por que a chave de duplicidade e (empresa, competencia)
    por_nome = {}
    for linha in imp.parse_csv(_fixture_bytes()):
        if linha.invalida or not linha.nome:
            continue
        por_nome.setdefault(linha.nome_norm, set()).add(
            imp.competencia_da_descricao(linha.vencimento))
    assert any(len(comps) > 1 for comps in por_nome.values())


def test_fixture_exercita_a_virada_de_ano():
    comps = {imp.competencia_da_descricao(linha.vencimento)
             for linha in imp.parse_csv(_fixture_bytes()) if not linha.invalida}
    assert '12/2026' in comps, 'a fixture precisa de vencimento em janeiro/2027'
