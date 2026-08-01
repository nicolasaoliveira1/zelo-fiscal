"""Leitura do extrato do Banco Inter e interpretacao da descricao do Pix.

Funcoes puras sobre bytes e dicionarios de palavra: nao tocam banco nem
Selenium. O PDF da fixture e GERADO (`tests/fixtures/extrato_inter.py`) com as
coordenadas medidas no extrato real do banco — o extrato de verdade tem nomes de
clientes e o CNPJ do emitente e nao entra no repositorio.

Os casos de descricao sao TODOS transcritos do extrato real de julho/2026: nao
ha aqui nenhum formato inventado para o parser passar.
"""
import os
import sys
from datetime import date
from decimal import Decimal

import pytest

from app.services import nfse_extrato_inter as inter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'fixtures'))
import extrato_inter as fixture_pdf  # noqa: E402

CATEGORIA = 'HONORÁRIOS - CLIENTES'


@pytest.fixture(scope='module')
def pdf_bytes():
    return fixture_pdf.gerar(None)


@pytest.fixture(scope='module')
def lancamentos(pdf_bytes):
    return inter.ler_pdf(pdf_bytes)


# --- leitura por coordenada ------------------------------------------------

def _palavra(texto, x0, x1, topo):
    return {'text': texto, 'x0': x0, 'x1': x1, 'top': topo}


def _cabecalho(topo=10):
    """Linha de cabecalho com as coordenadas reais do extrato do banco."""
    return [
        _palavra('Data', 24.8, 39.9, topo),
        _palavra('Nome', 101.1, 120.8, topo),
        _palavra('Descrição', 279.1, 311.9, topo),
        _palavra('Ref.', 471.4, 484.6, topo),
        _palavra('Identif.', 559.6, 582.8, topo),
        _palavra('Entrada', 608.0, 633.3, topo),
        _palavra('Saída', 665.2, 683.7, topo),
        _palavra('Saldo', 710.3, 728.9, topo),
    ]


def test_le_o_pdf_inteiro_incluindo_saidas(lancamentos):
    assert len(lancamentos) == 9
    assert sum(1 for lan in lancamentos if lan.entrada is not None) == 6
    assert sum(1 for lan in lancamentos if lan.saida is not None) == 2


def test_rotulos_do_grafico_nao_viram_lancamento(lancamentos):
    # o eixo do grafico de saldo ('2/07 4/07 ...') e lido pelo pdfplumber junto
    # com a tabela; sem descricao, nao e lancamento
    assert all('2/07 4/07' not in lan.descricao for lan in lancamentos)


def test_data_e_herdada_da_linha_de_cima(lancamentos):
    """O Inter so imprime a data na primeira linha de cada dia."""
    do_dia_7 = [lan for lan in lancamentos if lan.data == date(2026, 7, 7)]
    assert len(do_dia_7) == 3
    # a terceira nao trazia data impressa e mesmo assim ficou em 07/07
    assert 'Gama Saude Produtos' in do_dia_7[2].descricao


def test_entrada_e_saida_saem_da_coluna_e_nao_da_ordem(lancamentos):
    """O que separa 1.806,00 de 3.862,63 e a coluna, nao a posicao no texto."""
    estorno = next(lan for lan in lancamentos if 'ESTORNO' in lan.descricao)
    assert estorno.saida == Decimal('1784.00')
    assert estorno.entrada is None

    honorario = next(lan for lan in lancamentos if 'Alfa Comercio' in lan.descricao)
    assert honorario.entrada == Decimal('1806.00')
    assert honorario.saida is None
    # o saldo da mesma linha nao foi confundido com a entrada
    assert honorario.saldo == Decimal('3862.63')


def test_valor_dentro_da_descricao_nao_vira_valor_da_coluna():
    """Formato de dinheiro so basta se estiver NA coluna.

    Uma descricao que cite 'parcela de 1.500,00' nao pode virar o valor da nota
    — o numero esta na faixa da Descricao, nao na da Entrada."""
    linha = [
        _palavra('06/07/26', 24.8, 60.0, 40),
        _palavra(CATEGORIA, 101.1, 200.0, 40),
        _palavra('Pix', 279.1, 292.0, 40),
        _palavra('parcela', 295.0, 330.0, 40),
        _palavra('1.500,00', 333.0, 370.0, 40),
        _palavra('325,00', 611.2, 633.3, 40),
    ]
    lidos = inter.montar_lancamentos([_cabecalho() + linha])
    assert len(lidos) == 1
    assert lidos[0].entrada == Decimal('325.00')
    assert '1.500,00' in lidos[0].descricao


def test_sem_cabecalho_recusa_o_arquivo():
    palavras = [_palavra('Comprovante', 24.8, 90.0, 10),
                _palavra('de', 95.0, 105.0, 10)]
    with pytest.raises(inter.ExtratoInterInvalidoError):
        inter.montar_lancamentos([palavras])


def test_cabecalho_da_primeira_pagina_vale_para_as_seguintes():
    """Extrato longo pode ter pagina que nao repete o cabecalho."""
    pagina1 = _cabecalho() + [
        _palavra('06/07/26', 24.8, 60.0, 40),
        _palavra(CATEGORIA, 101.1, 200.0, 40),
        _palavra('Pix', 279.1, 292.0, 40),
        _palavra('100,00', 611.2, 633.3, 40),
    ]
    pagina2 = [
        _palavra('07/07/26', 24.8, 60.0, 40),
        _palavra(CATEGORIA, 101.1, 200.0, 40),
        _palavra('Outro', 279.1, 300.0, 40),
        _palavra('200,00', 611.2, 633.3, 40),
    ]
    lidos = inter.montar_lancamentos([pagina1, pagina2])
    assert [lan.entrada for lan in lidos] == [Decimal('100.00'), Decimal('200.00')]


def test_pdf_vazio_ou_ilegivel_recusa():
    with pytest.raises(inter.ExtratoInterInvalidoError):
        inter.ler_pdf(b'')
    with pytest.raises(inter.ExtratoInterInvalidoError):
        inter.ler_pdf(b'%PDF-1.4 isto nao e um pdf de verdade')


def test_e_pdf_olha_o_conteudo_e_nao_a_extensao(pdf_bytes):
    assert inter.e_pdf(pdf_bytes) is True
    assert inter.e_pdf(b'"06/07/2026";"ALFA";"0001"') is False
    assert inter.e_pdf('texto ja decodificado') is False


# --- interpretacao da descricao do Pix -------------------------------------
# Todos os casos abaixo sao descricoes REAIS do extrato de julho/2026.

@pytest.mark.parametrize('descricao,nome,competencia', [
    ('Pix - Valeria Cabreira Brust - honor. 06/2026',
     'VALERIA CABREIRA BRUST', '06/2026'),
    ('Pix - Fl Up Produtora Multimidia Ltda - 06/2026',
     'FL UP PRODUTORA MULTIMIDIA LTDA', '06/2026'),
    # sem espaco antes da competencia
    ('Pix - Imobisis Sites E Sistemas Ltda -06/2026',
     'IMOBISIS SITES E SISTEMAS LTDA', '06/2026'),
    # nome curto e sigla
    ('Pix - Moro - 06/2026', 'MORO', '06/2026'),
    ('Pix - JDM - 06/2026', 'JDM', '06/2026'),
    # iniciais com ponto no meio do nome
    ('Pix - W. E. Fernandes - 06/2026', 'W. E. FERNANDES', '06/2026'),
])
def test_honorarios_com_competencia_escrita(descricao, nome, competencia):
    lido = inter.interpretar_descricao(descricao)
    assert lido.nome == nome
    assert lido.competencia == competencia
    assert lido.servico is None
    assert lido.pendente is False


def test_servico_abreviado_com_qualificador_de_pagamento():
    """'PARTE' qualifica o pagamento, nao o servico: nao entra na nota."""
    lido = inter.interpretar_descricao('Pix VIDA E SAUDE - ALT. CONTRATO - PARTE')
    assert lido.nome == 'VIDA E SAUDE'
    assert lido.servico == 'ALTERAÇÃO CONTRATUAL'
    assert lido.competencia is None
    assert lido.pendente is False


def test_servico_no_meio_do_segmento_deixa_o_nome():
    """'baixa Texas Cidreira e Texas Tramandaí': servico + DUAS empresas."""
    lido = inter.interpretar_descricao('Pix - baixa Texas Cidreira e Texas Tramandaí')
    assert lido.servico == 'BAIXA DE EMPRESA'
    assert lido.nome == 'TEXAS CIDREIRA E TEXAS TRAMANDAI'
    assert lido.pendente is False


def test_so_o_nome_fica_pendente():
    """Sem competencia e sem servico nao ha texto para a nota — nao se chuta."""
    lido = inter.interpretar_descricao('Pix recebido - Vida E Saude Produtos Farmaceuticos')
    assert lido.nome == 'VIDA E SAUDE PRODUTOS FARMACEUTICOS'
    assert lido.competencia is None
    assert lido.servico is None
    assert lido.pendente is True


def test_memoria_de_servico_vence_a_semente():
    class ServicoFake:
        termo_norm = 'DISTRATO'
        descricao = 'DISTRATO SOCIAL'

    lido = inter.interpretar_descricao('Pix - Alfa Ltda - distrato', [ServicoFake()])
    assert lido.servico == 'DISTRATO SOCIAL'
    assert lido.nome == 'ALFA LTDA'
    assert lido.pendente is False


def test_termo_de_servico_respeita_fronteira_de_palavra():
    """'BAIXA' nao pode casar dentro de 'BAIXADA'."""
    lido = inter.interpretar_descricao('Pix - Baixada Comercio Ltda - 06/2026')
    assert lido.servico is None
    assert lido.nome == 'BAIXADA COMERCIO LTDA'


def test_competencia_de_um_digito_vira_mm_aaaa():
    lido = inter.interpretar_descricao('Pix - Alfa Ltda - 6/2026')
    assert lido.competencia == '06/2026'
