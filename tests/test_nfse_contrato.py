"""Serviço persistente do contrato adaptativo, com dados sintéticos."""

from dataclasses import FrozenInstanceError
from datetime import datetime

import pytest

from app import db
from app.automation.nfse_recon import OpcaoInventariada
from app.models import IncidenteContratoNfse
from app.services import nfse_contrato
from app.services.nfse_drift import CampoComparavel, Diferenca


def _diferenca():
    observado = CampoComparavel(
        chave_semantica="campo.novo",
        etapa="servico",
        rotulo="Campo sintético novo",
        tipo="select",
        interacao="chosen",
        obrigatorio=True,
        opcoes=(OpcaoInventariada("A", "Opção A", 0),),
    )
    return Diferenca(
        etapa="servico",
        tipo="controle_novo",
        severidade="critica",
        chave_observada="campo.novo",
        observado=observado,
        mensagem="Controle sintético requer decisão.",
    )


def test_garantir_contrato_inicial_eh_idempotente_e_nao_abre_portal(app, ids):
    with app.app_context():
        primeiro = nfse_contrato.garantir_contrato_inicial()
        segundo = nfse_contrato.garantir_contrato_inicial()

        assert segundo.id == primeiro.id
        assert primeiro.estado == "ativa"
        assert primeiro.elegivel_automatico is True
        assert len(primeiro.campos) == len(nfse_contrato.CONTRATO_INICIAL)
        assert [campo.ordem for campo in primeiro.campos] == list(range(len(primeiro.campos)))
        assert {campo.etapa for campo in primeiro.campos} == {
            "pessoas", "servico", "tributacao", "revisao"
        }


def test_carregar_execucao_materializa_opcoes_e_eh_frozen(app, ids):
    with app.app_context():
        contrato = nfse_contrato.garantir_contrato_inicial()
        execucao = nfse_contrato.carregar_execucao(contrato.id)
        campo = execucao.campo("Tomador.LocalDomicilio")

        assert execucao.contrato_id == contrato.id
        assert tuple(opcao.valor for opcao in campo.opcoes) == ("0", "1", "2")
        assert campo.origem == "fixo"
        with pytest.raises(FrozenInstanceError):
            execucao.versao = 99
        with pytest.raises(nfse_contrato.CampoContratoDesconhecidoError):
            execucao.campo("campo.inexistente")


def test_registrar_incidentes_faz_upsert_e_preserva_primeira_observacao(app, ids, monkeypatch):
    eventos = []
    monkeypatch.setattr(
        nfse_contrato,
        "log_event",
        lambda evento, **campos: eventos.append((evento, campos)),
    )
    monkeypatch.setattr(nfse_contrato.auditoria, "registrar", lambda *args, **kwargs: None)
    primeira_hora = datetime(2026, 8, 25, 12, 0)
    segunda_hora = datetime(2026, 8, 25, 12, 5)

    with app.app_context():
        contrato = nfse_contrato.garantir_contrato_inicial()
        primeira = nfse_contrato.registrar_incidentes(
            contrato.id, [_diferenca()], agora=primeira_hora
        )
        segunda = nfse_contrato.registrar_incidentes(
            contrato.id, [_diferenca()], agora=segunda_hora
        )
        incidente = db.session.get(IncidenteContratoNfse, primeira[0].id)

        assert len(primeira) == len(segunda) == 1
        assert segunda[0].id == primeira[0].id
        assert incidente.primeira_observacao_em == primeira_hora
        assert incidente.ultima_observacao_em == segunda_hora
        assert incidente.observacoes == 2
        assert len(incidente.opcoes) == 1
        assert {"campo.novo", "Controle sintético"}.isdisjoint(
            {eventos[-1][1].get("valor"), eventos[-1][1].get("rotulo")}
        )
        assert set(eventos[-1][1]) == {
            "contrato_id", "incidente_id", "etapa", "tipo", "assinatura"
        }


def test_comparacao_desconhecida_nao_persiste_incidente(app, ids):
    with app.app_context():
        contrato = nfse_contrato.garantir_contrato_inicial()
        resultado = type("Resultado", (), {"diferencas": (), "compatibilidade": "desconhecida"})()

        assert nfse_contrato.registrar_incidentes(contrato.id, resultado) == []
        assert IncidenteContratoNfse.query.count() == 0


def test_falha_de_persistencia_levanta_bloqueio_e_limpa_transacao(app, ids, monkeypatch):
    with app.app_context():
        contrato = nfse_contrato.garantir_contrato_inicial()

        def falhar_commit():
            raise RuntimeError("falha sintética")

        monkeypatch.setattr(db.session, "commit", falhar_commit)
        with pytest.raises(nfse_contrato.PersistenciaContratoError):
            nfse_contrato.registrar_incidentes(contrato.id, [_diferenca()])

        assert not db.session().in_transaction()
