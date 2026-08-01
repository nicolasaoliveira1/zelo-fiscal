"""Quebra do periodo consultado na tela de Notas Emitidas.

Funcao pura, sem banco e sem Selenium. O que estes testes garantem:

1. o intervalo pedido e coberto INTEIRO, sem buraco e sem sobreposicao — um
   buraco aqui vira nota faltando no total do mes, silenciosamente;
2. o corte respeita o mes civil, para o total de cada mes sair de uma consulta
   so (e o trabalho manual que a feature elimina);
3. nenhum bloco passa do limite que o portal aceita, qualquer que seja ele.
"""
from datetime import date, timedelta

import pytest

from app.services import nfse_emitidas as emit


def _cobre_tudo(blocos, inicio, fim):
    """Os blocos, em ordem, cobrem [inicio, fim] sem buraco nem sobreposicao."""
    assert blocos, 'nenhum bloco gerado'
    assert blocos[0][0] == inicio
    assert blocos[-1][1] == fim
    for (_, fim_anterior), (inicio_seguinte, _) in zip(blocos, blocos[1:]):
        assert inicio_seguinte == fim_anterior + timedelta(days=1)
    for comeco, termino in blocos:
        assert comeco <= termino


def _dentro_do_limite(blocos, limite):
    for comeco, termino in blocos:
        assert (termino - comeco).days + 1 <= limite


# --- mes fechado -----------------------------------------------------------

def test_mes_de_31_dias_cabe_num_bloco_quando_o_portal_permite():
    blocos = emit.dividir_periodo(date(2026, 7, 1), date(2026, 7, 31), limite_dias=31)
    assert blocos == [(date(2026, 7, 1), date(2026, 7, 31))]


def test_mes_de_31_dias_e_subdividido_se_o_portal_cortar_em_30():
    """O caso que so a recon confirma: julho tem 31 dias."""
    blocos = emit.dividir_periodo(date(2026, 7, 1), date(2026, 7, 31), limite_dias=30)
    assert blocos == [(date(2026, 7, 1), date(2026, 7, 30)),
                      (date(2026, 7, 31), date(2026, 7, 31))]
    _cobre_tudo(blocos, date(2026, 7, 1), date(2026, 7, 31))
    _dentro_do_limite(blocos, 30)


def test_fevereiro_bissexto():
    blocos = emit.dividir_periodo(date(2028, 2, 1), date(2028, 2, 29))
    assert blocos == [(date(2028, 2, 1), date(2028, 2, 29))]


# --- varios meses ----------------------------------------------------------

def test_tres_meses_viram_um_bloco_por_mes():
    """Pedido do usuario: escolher um periodo maior e o sistema consulta mes a
    mes. O corte por mes civil e o que faz o total de cada mes sair inteiro."""
    inicio, fim = date(2026, 5, 1), date(2026, 7, 31)
    blocos = emit.dividir_periodo(inicio, fim)

    assert blocos == [
        (date(2026, 5, 1), date(2026, 5, 31)),
        (date(2026, 6, 1), date(2026, 6, 30)),
        (date(2026, 7, 1), date(2026, 7, 31)),
    ]
    _cobre_tudo(blocos, inicio, fim)


def test_periodo_que_comeca_e_termina_no_meio_do_mes():
    inicio, fim = date(2026, 5, 20), date(2026, 7, 10)
    blocos = emit.dividir_periodo(inicio, fim)

    assert blocos == [
        (date(2026, 5, 20), date(2026, 5, 31)),
        (date(2026, 6, 1), date(2026, 6, 30)),
        (date(2026, 7, 1), date(2026, 7, 10)),
    ]
    _cobre_tudo(blocos, inicio, fim)


def test_virada_de_ano():
    inicio, fim = date(2026, 12, 1), date(2027, 1, 31)
    blocos = emit.dividir_periodo(inicio, fim)
    assert blocos == [(date(2026, 12, 1), date(2026, 12, 31)),
                      (date(2027, 1, 1), date(2027, 1, 31))]
    _cobre_tudo(blocos, inicio, fim)


def test_um_dia_so():
    dia = date(2026, 7, 15)
    assert emit.dividir_periodo(dia, dia) == [(dia, dia)]


def test_ano_inteiro_com_limite_apertado_continua_cobrindo_tudo():
    inicio, fim = date(2026, 1, 1), date(2026, 12, 31)
    for limite in (7, 15, 28, 30, 31):
        blocos = emit.dividir_periodo(inicio, fim, limite_dias=limite)
        _cobre_tudo(blocos, inicio, fim)
        _dentro_do_limite(blocos, limite)
        # nenhum bloco atravessa a virada de mes
        assert all(c.month == t.month and c.year == t.year for c, t in blocos)


# --- entrada invalida ------------------------------------------------------

def test_data_inicial_depois_da_final_e_recusada():
    with pytest.raises(ValueError):
        emit.dividir_periodo(date(2026, 7, 31), date(2026, 7, 1))


def test_limite_nao_positivo_e_recusado():
    with pytest.raises(ValueError):
        emit.dividir_periodo(date(2026, 7, 1), date(2026, 7, 31), limite_dias=0)


# --- competencia -----------------------------------------------------------

def test_competencia_do_bloco_bate_com_a_da_nota():
    """Mesmo formato da NotaNfse.competencia: os dois lados sao confrontados
    sem conversao no meio."""
    assert emit.competencia_do_bloco(date(2026, 7, 1)) == '07/2026'
    assert emit.competencia_do_bloco(date(2026, 12, 31)) == '12/2026'
