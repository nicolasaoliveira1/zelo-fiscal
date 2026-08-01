"""Importacao do extrato do Inter: normalizacao, status e agrupamento.

O que estes testes existem para provar:

1. O PDF entra pela MESMA porta do CSV e vira as mesmas `NotaNfse` — a decisao
   de formato sai do conteudo, nao da extensao;
2. A competencia do Inter e LITERAL (vem escrita no Pix), ao contrario da do
   CSV, que e derivada do vencimento. Trocar as duas erra o mes de toda nota;
3. Descricao que nao diz competencia nem servico NAO vira nota pronta;
4. Estorno vira PROPOSTA, nunca abatimento automatico, e segura as linhas fora
   da fila enquanto o operador nao responde.
"""
import os
import sys
from decimal import Decimal

import pytest

from app import db
from app.models import (
    Empresa,
    NotaNfse,
    ServicoNfse,
    StatusNotaNfse,
)
from app.services import nfse_grupos, nfse_import as imp, nfse_service

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'fixtures'))
import extrato_inter as fixture_pdf  # noqa: E402

CSV_UMA_LINHA = ('"13/07/2026";"ALFA COMERCIO LTDA";"0001443038";"062623";'
                 '"05/07/2026";"811,00";"0,00";"0,00";"811,00";"COBRANCA SIMPLES"')


@pytest.fixture()
def banco(app):
    """Schema limpo por teste, sem os dados semeados pelo fixture `ids`."""
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def pdf():
    return fixture_pdf.gerar(None)


def _empresa(nome, cnpj):
    empresa = Empresa(nome=nome, cnpj=cnpj, cidade='Imbé', estado='RS')
    db.session.add(empresa)
    db.session.commit()
    return empresa


def _notas(lote):
    return NotaNfse.query.filter_by(lote_id=lote.id).order_by(NotaNfse.id).all()


def _por_nome(notas, trecho):
    return next(n for n in notas if trecho.upper() in (n.nome_csv or ''))


# --- entrada pela mesma porta ----------------------------------------------

def test_pdf_e_csv_entram_pela_mesma_importacao(banco, pdf):
    """Uma selecao pode misturar os dois formatos."""
    lote = imp.importar([('extrato.pdf', pdf), ('cobrancas.csv', CSV_UMA_LINHA)])
    notas = _notas(lote)

    origens = {n.origem_extrato for n in notas}
    assert origens == {imp.ORIGEM_INTER, imp.ORIGEM_CSV}
    # 6 entradas de honorarios no PDF + 1 linha do CSV
    assert len(notas) == 7


def test_so_entram_linhas_da_categoria_com_entrada(banco, pdf):
    """Saida nao vira nota, e credito fora da categoria tambem nao."""
    lote = imp.importar(pdf, 'extrato.pdf')
    notas = _notas(lote)

    assert len(notas) == 6
    assert all(n.valor_final > 0 for n in notas)
    # o estorno da Gama e a guia da Caixa sao saidas: nenhuma virou nota
    assert not any('ESTORNO' in (n.descricao_extrato or '') for n in notas)
    assert not any('fgts' in (n.descricao_extrato or '').lower() for n in notas)


def test_categoria_configuravel_filtra_as_linhas(banco, pdf):
    """Se o operador renomear a categoria no app do banco, o filtro acompanha."""
    from app.services import nfse_config
    config = nfse_config.get_config_nfse()
    config.categoria_extrato = 'OUTRA COISA'
    db.session.commit()

    lote = imp.importar(pdf, 'extrato.pdf')
    assert _notas(lote) == []


def test_arquivo_que_nao_e_extrato_recusa_a_importacao_inteira(banco, pdf):
    with pytest.raises(imp.ArquivoInvalidoError) as erro:
        imp.importar([('extrato.pdf', pdf), ('errado.pdf', b'%PDF-1.4 lixo')])
    assert 'errado.pdf' in str(erro.value)
    # nada persistido: nem lote parcial
    assert NotaNfse.query.count() == 0


# --- competencia: literal no Inter, derivada no CSV ------------------------

def test_competencia_vem_escrita_na_descricao(banco, pdf):
    lote = imp.importar(pdf, 'extrato.pdf')
    alfa = _por_nome(_notas(lote), 'ALFA')
    # Pix pago em 06/07/2026 com '06/2026' escrito: vale o escrito, e nao o
    # mes anterior ao pagamento (que daria 06/2026 por coincidencia aqui) nem
    # o mes do pagamento (07/2026)
    assert alfa.competencia == '06/2026'


def test_servico_sem_competencia_escrita_usa_o_mes_do_pagamento(banco, pdf):
    """Serve para agrupar e filtrar — mas nao entra no texto da nota."""
    from app.services import nfse_config

    lote = imp.importar(pdf, 'extrato.pdf')
    epsilon = _por_nome(_notas(lote), 'EPSILON')
    assert epsilon.descricao_servico == 'BAIXA DE EMPRESA'
    assert epsilon.competencia == '07/2026'          # Pix de 23/07/2026

    config = nfse_config.get_config_nfse()
    assert nfse_config.descricao_da_nota(config, epsilon) == 'BAIXA DE EMPRESA'
    assert '07/2026' not in nfse_config.descricao_da_nota(config, epsilon)


def test_honorarios_seguem_usando_o_template_com_competencia(banco, pdf):
    from app.services import nfse_config

    lote = imp.importar(pdf, 'extrato.pdf')
    alfa = _por_nome(_notas(lote), 'ALFA')
    descricao = nfse_config.descricao_da_nota(nfse_config.get_config_nfse(), alfa)
    assert '06/2026' in descricao
    assert 'HONOR' in descricao.upper()


# --- descricao pendente ----------------------------------------------------

def test_descricao_sem_competencia_nem_servico_fica_pendente(banco, pdf):
    _empresa('GAMA SAUDE PRODUTOS LTDA', '22.222.222/0001-22')
    lote = imp.importar(pdf, 'extrato.pdf')
    nota = _por_nome(_notas(lote), 'GAMA SAUDE PRODUTOS')

    # o documento resolveu, mas nao ha o que escrever na nota
    assert nota.documento == '22.222.222/0001-22'
    assert nota.descricao_pendente is True
    assert nota.status == StatusNotaNfse.DESCRICAO_PENDENTE
    assert nfse_service.emitivel(nota) is False


def test_empresa_pendente_vence_descricao_pendente(banco, pdf):
    """Documento primeiro: e o erro mais caro de desfazer."""
    lote = imp.importar(pdf, 'extrato.pdf')
    nota = _por_nome(_notas(lote), 'GAMA SAUDE PRODUTOS')
    assert nota.descricao_pendente is True
    assert nota.status == StatusNotaNfse.EMPRESA_PENDENTE


def test_memoria_de_servico_resolve_no_import_seguinte(banco, pdf):
    """A chave gravada e a descricao SEM o prefixo 'Pix' — a mesma forma que a
    busca usa. Gravar a descricao crua faria a memoria nunca casar."""
    db.session.add(ServicoNfse(termo_norm='GAMA SAUDE PRODUTOS LTDA',
                               descricao='CONSULTORIA'))
    db.session.commit()

    lote = imp.importar(pdf, 'extrato.pdf')
    nota = _por_nome(_notas(lote), 'GAMA SAUDE PRODUTOS')
    assert nota.descricao_pendente is False
    assert nota.descricao_servico == 'CONSULTORIA'
    # a descricao inteira aprendida NAO consome o nome do tomador: sem isso a
    # nota ficaria sem a quem emitir
    assert nota.nome_csv == 'GAMA SAUDE PRODUTOS LTDA'


# --- proposta de agrupamento -----------------------------------------------

def test_estorno_vira_proposta_e_nao_abatimento(banco, pdf):
    """684,00 + 2.000,00 - 1.784,00 = 900,00, proposto e nao aplicado."""
    _empresa('GAMA SAUDE', '22.222.222/0001-22')
    lote = imp.importar(pdf, 'extrato.pdf')
    notas = _notas(lote)

    do_grupo = [n for n in notas if n.grupo_sugerido]
    assert len(do_grupo) == 2
    assert {n.valor_final for n in do_grupo} == {Decimal('684.00'), Decimal('2000.00')}

    lider = next(n for n in do_grupo if n.grupo_valor_liquido is not None)
    assert lider.grupo_valor_liquido == Decimal('900.00')
    assert '1.784,00' in lider.grupo_detalhe
    assert '900,00' in lider.grupo_detalhe


def test_proposta_pendente_segura_a_nota_fora_da_fila(banco, pdf):
    _empresa('GAMA SAUDE', '22.222.222/0001-22')
    lote = imp.importar(pdf, 'extrato.pdf')
    do_grupo = [n for n in _notas(lote) if n.grupo_sugerido]

    assert all(nfse_service.emitivel(n) is False for n in do_grupo)
    with pytest.raises(nfse_service.NotaNaoEmitivelError):
        nfse_service._pode_emitir(do_grupo[0])


def test_confirmar_o_grupo_deixa_uma_nota_com_o_liquido(banco, pdf):
    _empresa('GAMA SAUDE', '22.222.222/0001-22')
    lote = imp.importar(pdf, 'extrato.pdf')
    token = next(n.grupo_sugerido for n in _notas(lote) if n.grupo_sugerido)

    lider = nfse_grupos.confirmar(token)

    assert lider.valor_final == Decimal('900.00')
    assert lider.valor_ajustado is False
    # o servico estava escrito na linha de 684,00, nao na de 2.000,00
    assert lider.descricao_servico == 'ALTERAÇÃO CONTRATUAL'
    assert nfse_service.emitivel(lider) is True

    absorvidas = NotaNfse.query.filter_by(status=StatusNotaNfse.AGRUPADA).all()
    assert len(absorvidas) == 1
    assert absorvidas[0].agrupada_em_id == lider.id


def test_valor_do_grupo_pode_ser_corrigido_e_fica_marcado(banco, pdf):
    _empresa('GAMA SAUDE', '22.222.222/0001-22')
    lote = imp.importar(pdf, 'extrato.pdf')
    token = next(n.grupo_sugerido for n in _notas(lote) if n.grupo_sugerido)

    lider = nfse_grupos.confirmar(token, Decimal('950.00'))
    assert lider.valor_final == Decimal('950.00')
    assert lider.valor_ajustado is True


def test_confirmar_com_o_valor_sugerido_nao_marca_como_ajustado(banco, pdf):
    """A tela manda o campo sempre preenchido com a sugestao; marcar por isso
    poria o selo de 'valor mexido' em toda nota agrupada."""
    _empresa('GAMA SAUDE', '22.222.222/0001-22')
    lote = imp.importar(pdf, 'extrato.pdf')
    token = next(n.grupo_sugerido for n in _notas(lote) if n.grupo_sugerido)

    lider = nfse_grupos.confirmar(token, Decimal('900.00'))
    assert lider.valor_final == Decimal('900.00')
    assert lider.valor_ajustado is False


def test_desfazer_devolve_o_valor_do_extrato(banco, pdf):
    _empresa('GAMA SAUDE', '22.222.222/0001-22')
    lote = imp.importar(pdf, 'extrato.pdf')
    notas = _notas(lote)
    token = next(n.grupo_sugerido for n in notas if n.grupo_sugerido)
    do_grupo = {n.id: n.valor_final for n in notas if n.grupo_sugerido}

    nfse_grupos.confirmar(token, Decimal('950.00'))
    lider = nfse_grupos.desfazer(token)

    # o valor volta do `valor_extrato`, que nunca muda — nenhuma sequencia de
    # juntar/desfazer pode faze-lo derivar do numero que esta no PDF do banco
    assert lider.valor_final == do_grupo[lider.id]
    assert lider.valor_ajustado is False
    assert nfse_grupos.tem_proposta_pendente(lider) is True

    for nota in _notas(lote):
        if nota.id in do_grupo:
            assert nota.valor_final == do_grupo[nota.id]
            assert nota.status != StatusNotaNfse.AGRUPADA
            assert nota.agrupada_em_id is None


def test_juntar_e_desfazer_varias_vezes_nao_deriva_o_valor(banco, pdf):
    _empresa('GAMA SAUDE', '22.222.222/0001-22')
    lote = imp.importar(pdf, 'extrato.pdf')
    token = next(n.grupo_sugerido for n in _notas(lote) if n.grupo_sugerido)

    for _ in range(3):
        lider = nfse_grupos.confirmar(token, Decimal('950.00'))
        assert lider.valor_final == Decimal('950.00')
        lider = nfse_grupos.desfazer(token)
        assert lider.valor_final == Decimal('684.00')


def test_descartar_devolve_cada_linha_como_nota_propria(banco, pdf):
    _empresa('GAMA SAUDE', '22.222.222/0001-22')
    lote = imp.importar(pdf, 'extrato.pdf')
    token = next(n.grupo_sugerido for n in _notas(lote) if n.grupo_sugerido)

    notas = nfse_grupos.descartar(token)

    assert len(notas) == 2
    assert all(n.grupo_descartado is True for n in notas)
    assert all(nfse_grupos.tem_proposta_pendente(n) is False for n in notas)
    # os valores brutos ficam como estavam: descartar nao mexe em dinheiro
    assert {n.valor_final for n in notas} == {Decimal('684.00'), Decimal('2000.00')}


def test_sem_estorno_e_sem_servico_nao_ha_proposta(banco, pdf):
    """Varias entradas do mesmo cliente no mes nao bastam para propor."""
    lote = imp.importar(pdf, 'extrato.pdf')
    alfa = _por_nome(_notas(lote), 'ALFA')
    assert alfa.grupo_sugerido is None


# --- duplicidade -----------------------------------------------------------

def test_reimportar_o_mesmo_pdf_nao_duplica_linha_identica(banco, pdf):
    lote = imp.importar([('a.pdf', pdf), ('b.pdf', pdf)])
    assert len(_notas(lote)) == 6
    assert lote.ignoradas_duplicadas == 6


def test_servico_e_honorarios_no_mesmo_mes_nao_sao_duplicata(banco):
    """A chave de duplicidade inclui o servico: sao duas notas legitimas."""
    assert (imp.chave_duplicidade('11.111.111/0001-11', '07/2026', 'BAIXA DE EMPRESA')
            != imp.chave_duplicidade('11.111.111/0001-11', '07/2026', None))


def test_nota_cancelada_ocupa_a_competencia(banco, pdf):
    """Reimportar nao pode ressuscitar como pronta o que o operador dispensou."""
    _empresa('ALFA COMERCIO LTDA', '11.111.111/0001-11')
    lote = imp.importar(pdf, 'extrato.pdf')
    alfa = _por_nome(_notas(lote), 'ALFA')
    alfa.status = StatusNotaNfse.CANCELADA
    db.session.commit()

    lote2 = imp.importar(pdf, 'outro-mes.pdf')
    de_novo = _por_nome(_notas(lote2), 'ALFA')
    assert de_novo.status == StatusNotaNfse.DUPLICATA
    assert de_novo.duplicata_de_id == alfa.id


# --- recalcular_status ------------------------------------------------------

def test_recalcular_status_poe_documento_antes_da_descricao(banco):
    nota = NotaNfse(documento=None, descricao_pendente=True)
    assert imp.recalcular_status(nota) == StatusNotaNfse.EMPRESA_PENDENTE

    nota.documento = '11.111.111/0001-11'
    assert imp.recalcular_status(nota) == StatusNotaNfse.DESCRICAO_PENDENTE

    nota.descricao_pendente = False
    nota.empresa_id = 1
    assert imp.recalcular_status(nota) == StatusNotaNfse.PRONTA
