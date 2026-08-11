"""Testes do circuit breaker por portal (spec 09, RESOP-02).

O breaker existe para NAO queimar credito de captcha contra um portal fora: 3
falhas seguidas do mesmo portal abrem, e ele fecha sozinho quando a janela
expira (sem reset manual).
"""
from datetime import datetime, timedelta

import pytest

from app.services import circuit_breaker, diagnostics


@pytest.fixture(autouse=True)
def _breaker_limpo():
    circuit_breaker.limpar()
    yield
    circuit_breaker.limpar()


def _falhar(alvo, vezes, mensagem='Timeout aguardando portal.'):
    return [circuit_breaker.registrar_falha(alvo, mensagem) for _ in range(vezes)]


def test_abre_exatamente_na_terceira_falha_seguida():
    aberturas = _falhar('FGTS', 3)
    assert aberturas == [False, False, True]
    assert circuit_breaker.aberto('FGTS') is True


def test_nao_abre_abaixo_do_limiar():
    _falhar('FGTS', 2)
    assert circuit_breaker.aberto('FGTS') is False


def test_alvos_diferentes_contam_separado():
    _falhar('Imbe', 3)
    _falhar('Tramandai', 1)
    assert circuit_breaker.aberto('Imbe') is True
    assert circuit_breaker.aberto('Tramandai') is False


def test_sucesso_fecha_o_breaker_antes_da_janela():
    _falhar('RS', 3)
    circuit_breaker.registrar_sucesso('RS')
    assert circuit_breaker.aberto('RS') is False


def test_sucesso_zera_a_contagem():
    """Depois de um sucesso, duas falhas novas nao podem reabrir (a contagem
    recomeca do zero — e a histerese que o portal oscilando exige)."""
    _falhar('RS', 2)
    circuit_breaker.registrar_sucesso('RS')
    aberturas = _falhar('RS', 2)
    assert aberturas == [False, False]
    assert circuit_breaker.aberto('RS') is False


def test_continua_aberto_dentro_da_janela(monkeypatch):
    agora = datetime(2026, 8, 10, 3, 0, 0)
    monkeypatch.setattr(circuit_breaker, '_agora', lambda: agora)
    _falhar('FGTS', 3)

    monkeypatch.setattr(circuit_breaker, '_agora', lambda: agora + timedelta(minutes=59))
    assert circuit_breaker.aberto('FGTS') is True


def test_fecha_sozinho_quando_a_janela_expira(monkeypatch):
    agora = datetime(2026, 8, 10, 3, 0, 0)
    monkeypatch.setattr(circuit_breaker, '_agora', lambda: agora)
    _falhar('FGTS', 3)

    monkeypatch.setattr(circuit_breaker, '_agora', lambda: agora + timedelta(minutes=61))
    assert circuit_breaker.aberto('FGTS') is False
    assert circuit_breaker.abertos() == []


def test_apos_fechar_pela_janela_a_contagem_recomeca(monkeypatch):
    """O ciclo seguinte tenta 'do zero': duas falhas novas nao reabrem."""
    agora = datetime(2026, 8, 10, 3, 0, 0)
    monkeypatch.setattr(circuit_breaker, '_agora', lambda: agora)
    _falhar('FGTS', 3)

    monkeypatch.setattr(circuit_breaker, '_agora', lambda: agora + timedelta(minutes=61))
    circuit_breaker.aberto('FGTS')

    assert _falhar('FGTS', 2) == [False, False]
    assert circuit_breaker.aberto('FGTS') is False


def test_falha_em_alvo_ja_aberto_nao_reabre():
    """So a ABERTURA devolve True — senao o alerta sairia a cada item do lote."""
    _falhar('FGTS', 3)
    assert circuit_breaker.registrar_falha('FGTS', 'de novo') is False


def test_abertos_lista_alvo_ocorrencias_e_motivo(monkeypatch):
    agora = datetime(2026, 8, 10, 3, 0, 0)
    monkeypatch.setattr(circuit_breaker, '_agora', lambda: agora)
    _falhar('Imbe', 3, mensagem='Timeout no portal do IMBE.')

    abertos = circuit_breaker.abertos()
    assert len(abertos) == 1
    assert abertos[0]['alvo'] == 'Imbe'
    assert abertos[0]['ocorrencias'] == 3
    assert abertos[0]['motivo'] == 'Timeout no portal do IMBE.'
    assert abertos[0]['desde'] == agora.isoformat()


def test_alvo_vazio_e_ignorado():
    assert circuit_breaker.registrar_falha(None, 'x') is False
    assert circuit_breaker.aberto(None) is False


def test_limiar_vem_da_config(app, monkeypatch):
    # setitem via monkeypatch (e nao atribuicao direta): a fixture `app` e
    # SESSION-scoped, entao mexer no config sem restaurar envenena o limiar de
    # todo teste seguinte que rode dentro de um app context.
    monkeypatch.setitem(app.config, 'BREAKER_LIMIAR', 2)
    with app.app_context():
        assert _falhar('FGTS', 2) == [False, True]


def test_abertura_e_fechamento_sao_logados(app, monkeypatch):
    """RESOP-02.11: abrir e fechar precisam aparecer no log estruturado."""
    agora = datetime(2026, 8, 10, 3, 0, 0)
    monkeypatch.setattr(circuit_breaker, '_agora', lambda: agora)
    diagnostics.limpar()
    with app.app_context():
        _falhar('FGTS', 3)
        monkeypatch.setattr(circuit_breaker, '_agora', lambda: agora + timedelta(minutes=61))
        circuit_breaker.aberto('FGTS')

    eventos = [e.get('event') for e in diagnostics.eventos_recentes(limite=20)]
    assert 'breaker_aberto' in eventos
    assert 'breaker_fechado' in eventos


def test_abertos_nao_lista_breaker_com_janela_vencida(monkeypatch):
    """O painel nao pode mostrar 'pausado' para sempre: `abertos()` tem de
    respeitar a mesma janela que `aberto()` — senao a tela contradiz o e-mail
    que promete que o bloqueio expira sozinho."""
    agora = datetime(2026, 8, 11, 3, 0, 0)
    monkeypatch.setattr(circuit_breaker, '_agora', lambda: agora)
    _falhar('FGTS', 3)
    assert len(circuit_breaker.abertos()) == 1

    monkeypatch.setattr(circuit_breaker, '_agora', lambda: agora + timedelta(hours=4))

    assert circuit_breaker.abertos() == []


def test_abertos_mantem_o_que_ainda_esta_na_janela(monkeypatch):
    agora = datetime(2026, 8, 11, 3, 0, 0)
    monkeypatch.setattr(circuit_breaker, '_agora', lambda: agora)
    _falhar('FGTS', 3)
    _falhar('Imbe', 3)

    monkeypatch.setattr(circuit_breaker, '_agora', lambda: agora + timedelta(minutes=59))

    assert {b['alvo'] for b in circuit_breaker.abertos()} == {'FGTS', 'Imbe'}
