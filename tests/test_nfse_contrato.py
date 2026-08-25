"""Serviço persistente do contrato adaptativo, com dados sintéticos."""

from dataclasses import FrozenInstanceError
from datetime import datetime
from types import SimpleNamespace

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


def test_fontes_disponiveis_e_resolver_valor_usam_catalogo_fechado():
    nota = SimpleNamespace(
        documento="DOCUMENTO-SINTÉTICO",
        valor_final=123,
        descricao_servico="Descrição sintética",
        competencia="08/2026",
    )
    config = SimpleNamespace(
        regime_apuracao_sn="1",
        municipio_servico_codigo="0000000",
        municipio_servico_nome="Município sintético",
        codigo_tributacao="00.00.00",
        item_nbs="000000000",
        piscofins_situacao="0",
        piscofins_tipo_retencao="0",
        descricao_template="Descrição {competencia}",
    )
    regra = {"origem": "nota", "fonte": "documento"}

    catalogo = nfse_contrato.fontes_disponiveis()

    assert {item["origem"] for item in catalogo} == {
        "fixo", "nota", "derivado", "configuracao", "padrao_portal", "intocavel"
    }
    assert nfse_contrato.resolver_valor(regra, nota, config, datetime(2026, 8, 25)) == (
        "DOCUMENTO-SINTÉTICO"
    )
    assert nfse_contrato.resolver_valor(
        {"origem": "fixo", "valor_fixo": "A"}, nota, config, None
    ) == "A"
    assert nfse_contrato.resolver_valor(
        {"origem": "padrao_portal", "fonte": None}, nota, config, None
    ) is None
    with pytest.raises(nfse_contrato.ConfiguracaoContratoInvalidaError):
        nfse_contrato.resolver_valor(
            {"origem": "nao_permitida", "fonte": "__import__"},
            nota,
            config,
            None,
        )


def test_configurar_incidente_copia_ativo_e_altera_somente_campo_coberto(
    app, ids, monkeypatch
):
    monkeypatch.setattr(nfse_contrato.auditoria, "registrar", lambda *args, **kwargs: None)
    with app.app_context():
        ativo = nfse_contrato.garantir_contrato_inicial()
        campos_ativos = {
            campo.chave_semantica: (campo.origem, campo.fonte, campo.valor_fixo)
            for campo in ativo.campos
        }
        incidente = nfse_contrato.registrar_incidentes(
            ativo.id, [_diferenca()], agora=datetime(2026, 8, 25, 12, 0)
        )[0]

        candidato = nfse_contrato.configurar_incidente(
            incidente.id,
            {"origem": "fixo", "valor_fixo": "A"},
            usuario_id=None,
        )
        db.session.expire_all()
        ativo_depois = db.session.get(type(ativo), ativo.id)

        assert candidato.estado == "candidata"
        assert candidato.elegivel_automatico is False
        assert candidato.id != ativo.id
        assert any(campo.chave_semantica == "campo.novo" for campo in candidato.campos)
        assert {
            campo.chave_semantica: (campo.origem, campo.fonte, campo.valor_fixo)
            for campo in ativo_depois.campos
        } == campos_ativos
        assert incidente.estado == "configurado"
        assert incidente.contrato_candidato_id == candidato.id


def test_configurar_recusa_opcao_fixa_ausente_sem_criar_candidata(app, ids):
    with app.app_context():
        ativo = nfse_contrato.garantir_contrato_inicial()
        incidente = nfse_contrato.registrar_incidentes(
            ativo.id, [_diferenca()], agora=datetime(2026, 8, 25, 12, 0)
        )[0]

        with pytest.raises(nfse_contrato.ConfiguracaoContratoInvalidaError):
            nfse_contrato.configurar_incidente(
                incidente.id, {"origem": "fixo", "valor_fixo": "B"}
            )

        assert nfse_contrato.ContratoNfse.query.count() == 1
        assert incidente.estado == "aberto"


def test_padrao_portal_obrigatorio_fica_dependente_de_prova(app, ids, monkeypatch):
    monkeypatch.setattr(nfse_contrato.auditoria, "registrar", lambda *args, **kwargs: None)
    with app.app_context():
        ativo = nfse_contrato.garantir_contrato_inicial()
        diferenca = _diferenca()
        diferenca = type(diferenca)(
            **{**diferenca.__dict__, "chave_observada": "campo.padrao"}
        )
        incidente = nfse_contrato.registrar_incidentes(ativo.id, [diferenca])[0]
        candidato = nfse_contrato.configurar_incidente(
            incidente.id, {"origem": "padrao_portal", "fonte": None}
        )

        assert candidato.elegivel_automatico is False
        assert candidato.erro_validacao == "aguarda prova assistida de avanço"


def test_falha_de_auditoria_nao_desfaz_candidata(app, ids, monkeypatch):
    with app.app_context():
        ativo = nfse_contrato.garantir_contrato_inicial()
        incidente = nfse_contrato.registrar_incidentes(ativo.id, [_diferenca()])[0]

        def falhar_auditoria(*args, **kwargs):
            raise RuntimeError("falha sintética")

        monkeypatch.setattr(nfse_contrato.auditoria, "registrar", falhar_auditoria)
        candidato = nfse_contrato.configurar_incidente(
            incidente.id, {"origem": "fixo", "valor_fixo": "A"}
        )

        assert db.session.get(type(candidato), candidato.id) is not None
