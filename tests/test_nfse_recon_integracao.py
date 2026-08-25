"""Integração da recon às fronteiras, usando somente objetos sintéticos."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.automation.nfse_recon import ControleInventariado, InventarioEtapa
from app.services import nfse_service
from app.services.nfse_drift import CampoComparavel


def _controle(
    chave="campo.sintetico", *, obrigatorio=False, tipo="text", interacao="texto"
):
    return ControleInventariado(
        chave_semantica=chave,
        etapa="servico",
        tag="input",
        tipo=tipo,
        id="campo-sintetico",
        name=chave,
        rotulo="Campo sintético",
        seletor_tipo="id",
        seletor="campo-sintetico",
        obrigatorio=obrigatorio,
        desabilitado=False,
        somente_leitura=False,
        visivel=True,
        interacao=interacao,
    )


def _contrato(campos=(), contrato_id=0):
    return SimpleNamespace(contrato_id=contrato_id, campos=tuple(campos))


def test_fronteira_desconhecida_nao_persiste_incidente(monkeypatch):
    inventariar = MagicMock(
        return_value=InventarioEtapa.desconhecido("servico", "DOM sintético")
    )
    registrar = MagicMock()
    monkeypatch.setattr(nfse_service.nfse_recon, "inventariar", inventariar)
    monkeypatch.setattr(nfse_service.nfse_contrato, "registrar_incidentes", registrar)
    monkeypatch.setattr(nfse_service, "log_event", MagicMock())

    driver = MagicMock()
    resultado = nfse_service._observar_fronteira_contrato(
        driver,
        _contrato(contrato_id=71),
        "servico",
        "entrada",
        execution_id="execucao-sintetica",
    )

    assert resultado["estado"] == "desconhecida"
    inventariar.assert_called_once_with(driver, "servico")
    registrar.assert_not_called()
    assert driver.get.call_count == 0
    assert driver.execute_script.call_count == 0


def test_fronteira_opcional_persiste_artefato_e_retorna_aviso(monkeypatch):
    inventario = InventarioEtapa(
        etapa="servico",
        controles=(_controle(obrigatorio=False),),
    )
    salvar = MagicMock()
    monkeypatch.setattr(nfse_service.nfse_recon, "inventariar", lambda *_: inventario)
    monkeypatch.setattr(nfse_service, "salvar_artefato_sanitizado", salvar)

    resultado = nfse_service._observar_fronteira_contrato(
        MagicMock(),
        _contrato(),
        "servico",
        "dependencias",
        execution_id="execucao-sintetica",
    )

    assert resultado == {
        "estado": "aviso",
        "etapa": "servico",
        "momento": "dependencias",
        "aviso": True,
    }
    salvar.assert_called_once()
    assert "Campo sintético" in salvar.call_args.args[1]


def test_drift_incompatível_gera_artefato_sanitizado(monkeypatch):
    inventario = InventarioEtapa(
        etapa="servico",
        controles=(_controle(obrigatorio=True),),
    )
    salvar = MagicMock()
    capturar = MagicMock()
    registrar = MagicMock()
    monkeypatch.setattr(nfse_service.nfse_recon, "inventariar", lambda *_: inventario)
    monkeypatch.setattr(nfse_service, "salvar_artefato_sanitizado", salvar)
    monkeypatch.setattr(nfse_service, "capturar_contexto_falha", capturar)
    monkeypatch.setattr(nfse_service.nfse_contrato, "registrar_incidentes", registrar)

    with pytest.raises(nfse_service.NfseDriftError) as erro:
        nfse_service._observar_fronteira_contrato(
            MagicMock(),
            _contrato(contrato_id=71),
            "servico",
            "pre_avancar",
        )

    assert erro.value.pausar_lote is True
    assert erro.value.html_seguro is not None
    assert "Campo sintético" in erro.value.html_seguro
    capturar.assert_not_called()


def test_mensagem_de_validacao_remove_sentinelas_antes_do_artefato(monkeypatch):
    salvar = MagicMock()
    registrar = MagicMock()
    monkeypatch.setattr(nfse_service, "salvar_artefato_sanitizado", salvar)
    monkeypatch.setattr(
        nfse_service.nfse_recon,
        "mensagens_validacao",
        lambda *_: ["Campo obrigatório", "Mensagem sem dados preenchidos"],
    )
    monkeypatch.setattr(nfse_service.nfse_contrato, "registrar_incidentes", registrar)

    nota = SimpleNamespace(
        documento="DOCUMENTO-SINTETICO",
        valor_final=Decimal("12.34"),
    )
    mensagens = nfse_service._registrar_validacao_portal(
        MagicMock(),
        _contrato(contrato_id=71),
        "servico",
        nota,
        "Descrição sintética",
        execution_id="execucao-sintetica",
    )

    html = salvar.call_args.args[1]
    assert mensagens == ["Campo obrigatório", "Mensagem sem dados preenchidos"]
    assert "DOCUMENTO-SINTETICO" not in html
    assert "12.34" not in html
    assert "Descrição sintética" not in html
    registrar.assert_called_once()


def test_select_sem_catalogo_declarado_nao_cria_drift_falso():
    esperado = CampoComparavel(
        chave_semantica="campo.select",
        etapa="servico",
        rotulo="Seleção sintética",
        tipo="select",
        interacao="select_busca",
        obrigatorio=True,
    )
    inventario = InventarioEtapa(
        etapa="servico",
        controles=(
            _controle(
                "campo.select",
                obrigatorio=True,
                tipo="select",
                interacao="select_busca",
            ),
        ),
    )

    from app.services.nfse_drift import comparar

    resultado = comparar("servico", [esperado], inventario)

    assert resultado.compatibilidade == "compativel"
    assert resultado.diferencas == ()
