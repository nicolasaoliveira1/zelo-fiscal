"""Testes do nucleo compartilhado `certidao_service.finalizar_federal` (COV-01b).

Sem Selenium/rede/PDF real: `pdf.classificar_e_tratar_positivo` e
`pdf.extrair_validade_federal` sao mockados. Foco no contrato + persistencia
(validade do PDF / fallback 180d / positiva->pendente / erro / excecao).
"""
from datetime import date, timedelta
from unittest.mock import patch

from app import db
from app.models import Certidao, TipoCertidao
from app.services import certidao_service


def _federal_id():
    return Certidao.query.filter_by(tipo=TipoCertidao.FEDERAL).first().id


def _run(app, *, classificacao_ret, validade_ret=None):
    """Executa finalizar_federal com pdf.* mockado e devolve (res, estado)."""
    with app.app_context(), \
            patch.object(certidao_service.pdf, 'classificar_e_tratar_positivo',
                         return_value=classificacao_ret), \
            patch.object(certidao_service.pdf, 'extrair_validade_federal',
                         return_value=validade_ret):
        cert = db.session.get(Certidao, _federal_id())
        res = certidao_service.finalizar_federal(cert, '/tmp/federal.pdf')
        cert = db.session.get(Certidao, _federal_id())
        estado = (cert.data_validade, cert.status_especial)
    return res, estado


def test_finalizar_negativa_com_data_do_pdf(app, ids):
    # FED-02: negativa + PDF com "Valida ate" -> grava exatamente a data do PDF.
    data_pdf = date(2026, 12, 31)
    res, (validade, status) = _run(
        app, classificacao_ret=('negativa', None), validade_ret=data_pdf)
    assert res['ok'] is True and res['pendente'] is False
    assert res['data_validade'] == data_pdf
    assert validade == data_pdf
    assert status is None


def test_finalizar_negativa_sem_data_usa_fallback_180d(app, ids):
    # FED-03: negativa sem data legivel -> fallback hoje+180 (mata mutante do fallback).
    res, (validade, status) = _run(
        app, classificacao_ret=('negativa', None), validade_ret=None)
    assert res['ok'] is True and res['pendente'] is False
    assert validade == date.today() + timedelta(days=180)
    assert status is None


def test_finalizar_efeito_negativa_sem_data_fallback_180d(app, ids):
    # FED-03/04: efeito-de-negativa e valida (nao positiva) -> tambem entra no ciclo.
    res, (validade, _status) = _run(
        app, classificacao_ret=('efeito_negativa', None), validade_ret=None)
    assert res['ok'] is True and res['pendente'] is False
    assert validade == date.today() + timedelta(days=180)


def test_finalizar_desconhecida_concede_180d(app, ids):
    # Edge (AD-021): PDF ilegivel -> desconhecida ainda concede 180d (convencao RS/municipal).
    res, (validade, _status) = _run(
        app, classificacao_ret=('desconhecida', None), validade_ret=None)
    assert res['ok'] is True
    assert validade == date.today() + timedelta(days=180)


def test_finalizar_positiva_marca_pendente_sem_validade(app, ids):
    # FED-04: positiva -> pendente, sem validade. O helper reusado ja tratou o estado;
    # o nucleo apenas propaga o contrato (nao grava validade).
    res, (validade, _status) = _run(
        app, classificacao_ret=('positiva', 'Federal POSITIVA: pendente.'))
    assert res['ok'] is True and res['pendente'] is True
    assert res['data_validade'] is None
    assert res['message'] == 'Federal POSITIVA: pendente.'
    assert validade is None  # nao caiu no branch que grava validade


def test_finalizar_erro_de_classificacao_retorna_grave(app, ids):
    # FED-05: erro ao marcar PENDENTE (DB) -> ok=False, grave, sem gravar validade.
    res, (validade, _status) = _run(
        app, classificacao_ret=('erro', 'Erro ao marcar PENDENTE no banco.'))
    assert res['ok'] is False and res['grave'] is True
    assert res['message'] == 'Erro ao marcar PENDENTE no banco.'
    assert validade is None


def test_finalizar_excecao_faz_rollback_e_erro_acionavel(app, ids):
    # FED-05: excecao no fluxo -> rollback + mensagem acionavel, sem inconsistencia.
    with app.app_context(), \
            patch.object(certidao_service.pdf, 'classificar_e_tratar_positivo',
                         side_effect=RuntimeError('boom')):
        cert = db.session.get(Certidao, _federal_id())
        res = certidao_service.finalizar_federal(cert, '/tmp/federal.pdf')
        cert = db.session.get(Certidao, _federal_id())
        validade = cert.data_validade
    assert res['ok'] is False and res['grave'] is True
    assert res['message']
    assert validade is None
