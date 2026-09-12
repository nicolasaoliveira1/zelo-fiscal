"""Persistência atômica da consulta de NFS-e do portal."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models import (
    ConsultaEmitidaNfse,
    NotaEmitidaNfse,
    ObservacaoEmitidaNfse,
)
from app.services import nfse_emitidas


CHAVE_A = '0' * 50
CHAVE_B = '2' * 50
DOCUMENTO = '44.556.677/0001-86'


def _linha(chave, valor='1280.40'):
    return nfse_emitidas.LinhaEmitida(
        chave=chave,
        data_geracao=date(2026, 7, 31),
        documento=DOCUMENTO,
        nome_tomador='Tomador Sintético',
        competencia='06/2026',
        municipio='Município Sintético/RS',
        valor=Decimal(valor),
        situacao='P100_GERADA',
    )


def _raspagem_dublada(monkeypatch, linhas):
    import app.automation.nfse_emitidas as automacao
    import app.services.nfse_session as nfse_session

    monkeypatch.setattr(
        nfse_session,
        'SESSAO',
        SimpleNamespace(garantir=lambda: object()),
    )
    monkeypatch.setattr(
        automacao,
        'listar_periodo',
        lambda *_args, **_kwargs: list(linhas),
    )


def test_consultar_grava_observacao_e_espelho_com_retorno_publico(
        app, ids, monkeypatch):
    _raspagem_dublada(monkeypatch, [_linha(CHAVE_A)])

    with app.app_context():
        resultado = nfse_emitidas.consultar(
            date(2026, 7, 1), date(2026, 7, 31),
            execution_id='exec-consulta-sintetica',
        )
        observacao = ObservacaoEmitidaNfse.query.one()
        nota = NotaEmitidaNfse.query.one()

    assert resultado['periodo'] == (date(2026, 7, 1), date(2026, 7, 31))
    assert resultado['blocos'] == 1
    assert resultado['lidas'] == 1
    assert resultado['novas'] == 1
    assert resultado['atualizadas'] == 0
    assert resultado['consulta_id'] is not None
    assert observacao.fonte == 'portal'
    assert observacao.chave == CHAVE_A
    assert nota.chave == CHAVE_A
    assert nota.origem_autoritativa == 'portal'
    assert nota.situacao_fiscal == 'gerada'


def test_consultar_reverte_toda_a_unidade_se_a_conciliacao_falhar(
        app, ids, monkeypatch):
    _raspagem_dublada(monkeypatch, [_linha(CHAVE_A, '100.00')])
    inicio, fim = date(2026, 7, 1), date(2026, 7, 31)

    with app.app_context():
        nfse_emitidas.consultar(inicio, fim)

    _raspagem_dublada(monkeypatch, [
        _linha(CHAVE_A, '120.00'),
        _linha(CHAVE_B, '130.00'),
    ])

    def falhar(*_args, **_kwargs):
        raise RuntimeError('falha sintética na conciliação')

    monkeypatch.setattr(nfse_emitidas, 'conciliar', falhar)

    with app.app_context():
        with pytest.raises(RuntimeError, match='falha sintética'):
            nfse_emitidas.consultar(inicio, fim)

        observacoes = ObservacaoEmitidaNfse.query.all()
        notas = NotaEmitidaNfse.query.all()

        assert ConsultaEmitidaNfse.query.count() == 1
        assert len(observacoes) == 1
        assert observacoes[0].chave == CHAVE_A
        assert observacoes[0].valor == Decimal('100.00')
        assert len(notas) == 1
        assert notas[0].chave == CHAVE_A
        assert notas[0].valor == Decimal('100.00')
