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


def _driver_na_etapa(etapa="servico"):
    """Driver falso cuja URL confirma a etapa.

    A guarda de `_observar_fronteira_contrato` só compara o que a URL confirma
    ser a etapa: um `MagicMock` cru tem `current_url` que não é etapa nenhuma,
    e a observação (corretamente) não acontece.
    """

    caminho = {
        "pessoas": "Pessoas", "servico": "Servico",
        "tributacao": "Tributacao", "revisao": "EmitirNFSe",
    }[etapa]
    driver = MagicMock()
    driver.current_url = f"https://www.nfse.gov.br/EmissorNacional/DPS/{caminho}"
    return driver


def _contrato(campos=(), contrato_id=0):
    return SimpleNamespace(contrato_id=contrato_id, campos=tuple(campos))


def test_fronteira_desconhecida_bloqueia_sem_persistir_incidente(monkeypatch):
    inventariar = MagicMock(
        return_value=InventarioEtapa.desconhecido("servico", "DOM sintético")
    )
    registrar = MagicMock()
    monkeypatch.setattr(nfse_service.nfse_recon, "inventariar", inventariar)
    monkeypatch.setattr(nfse_service.nfse_contrato, "registrar_incidentes", registrar)
    monkeypatch.setattr(nfse_service, "log_event", MagicMock())

    driver = _driver_na_etapa()
    with pytest.raises(nfse_service.NfseDriftError, match="observar"):
        nfse_service._observar_fronteira_contrato(
            driver,
            _contrato(contrato_id=71),
            "servico",
            "entrada",
            execution_id="execucao-sintetica",
        )

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
    monkeypatch.setattr(
        nfse_service.nfse_contrato, "salvar_artefato_sanitizado", salvar
    )

    resultado = nfse_service._observar_fronteira_contrato(
        _driver_na_etapa(),
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


def test_fronteira_opcional_bloqueia_no_modo_automatico(monkeypatch):
    inventario = InventarioEtapa(
        etapa="servico",
        controles=(_controle(obrigatorio=False),),
    )
    monkeypatch.setattr(nfse_service.nfse_recon, "inventariar", lambda *_: inventario)

    with pytest.raises(nfse_service.NfseDriftError) as erro:
        nfse_service._observar_fronteira_contrato(
            _driver_na_etapa(),
            _contrato(),
            "servico",
            "entrada",
            modo="automatico",
        )

    assert erro.value.pausar_lote is True


def test_drift_incompatível_gera_artefato_sanitizado(monkeypatch):
    inventario = InventarioEtapa(
        etapa="servico",
        controles=(_controle(obrigatorio=True),),
    )
    salvar = MagicMock()
    capturar = MagicMock()
    registrar = MagicMock()
    monkeypatch.setattr(nfse_service.nfse_recon, "inventariar", lambda *_: inventario)
    monkeypatch.setattr(
        nfse_service.nfse_contrato, "salvar_artefato_sanitizado", salvar
    )
    monkeypatch.setattr(nfse_service, "capturar_contexto_falha", capturar)
    monkeypatch.setattr(nfse_service.nfse_contrato, "registrar_incidentes", registrar)

    with pytest.raises(nfse_service.NfseDriftError) as erro:
        nfse_service._observar_fronteira_contrato(
            _driver_na_etapa(),
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
    # Este artefato e do proprio `_registrar_validacao_portal`, nao do nucleo
    # compartilhado: e a captura da mensagem do portal quando a nota falha.
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


def _campo_esperado(chave="campo.contratado"):
    return CampoComparavel(
        chave_semantica=chave,
        etapa="servico",
        rotulo="Campo contratado",
        tipo="text",
        interacao="texto",
        obrigatorio=True,
        seletor_tipo="name",
        seletor=chave,
    )


def test_ausencia_provisoria_nao_bloqueia_o_modo_automatico(monkeypatch):
    """O automático é conservador de propósito, mas conservadorismo que nunca
    deixa concluir não protege nada: a etapa é um formulário progressivo e "o
    campo ainda não apareceu" acontece em toda nota."""

    monkeypatch.setattr(
        nfse_service.nfse_recon, "inventariar",
        lambda *_: InventarioEtapa(etapa="servico", controles=()),
    )
    registrar = MagicMock()
    monkeypatch.setattr(nfse_service.nfse_contrato, "registrar_incidentes", registrar)

    resultado = nfse_service._observar_fronteira_contrato(
        _driver_na_etapa(), _contrato([_campo_esperado()], contrato_id=71),
        "servico", "entrada", modo="automatico",
    )

    assert resultado["estado"] == "compativel"
    # E, sobretudo: nada vira incidente. Um incidente aberto aqui fecharia o
    # gate do automático para sempre, por um fato que não aconteceu.
    registrar.assert_not_called()


def test_ausencia_na_observacao_final_continua_bloqueando_o_automatico(monkeypatch):
    """Percorrida a etapa inteira, o campo que não apareceu sumiu de verdade."""

    monkeypatch.setattr(
        nfse_service.nfse_recon, "inventariar",
        lambda *_: InventarioEtapa(etapa="servico", controles=()),
    )
    monkeypatch.setattr(
        nfse_service.nfse_contrato, "registrar_incidentes", MagicMock()
    )

    with pytest.raises(nfse_service.NfseDriftError):
        nfse_service._observar_fronteira_contrato(
            _driver_na_etapa(), _contrato([_campo_esperado()], contrato_id=71),
            "servico", "pre_avancar", modo="automatico",
        )


def test_url_que_nao_e_etapa_conhecida_nao_vira_observacao(monkeypatch):
    """Sessão expirada leva o driver ao login. Comparar a tela de login com o
    contrato transforma todo campo contratado em remoção crítica e enche a
    Central de incidentes falsos contra a versão ativa."""

    inventariar = MagicMock()
    registrar = MagicMock()
    monkeypatch.setattr(nfse_service.nfse_recon, "inventariar", inventariar)
    monkeypatch.setattr(nfse_service.nfse_contrato, "registrar_incidentes", registrar)
    driver = MagicMock()
    driver.current_url = "https://www.nfse.gov.br/EmissorNacional/Login"

    resultado = nfse_service._observar_fronteira_contrato(
        driver, _contrato([_campo_esperado()], contrato_id=71),
        "servico", "pre_avancar", modo="automatico",
    )

    assert resultado["estado"] == "ignorada"
    inventariar.assert_not_called()
    registrar.assert_not_called()


def test_current_url_indisponivel_tambem_nao_observa(monkeypatch):
    inventariar = MagicMock()
    monkeypatch.setattr(nfse_service.nfse_recon, "inventariar", inventariar)

    class DriverMudo:
        @property
        def current_url(self):
            raise RuntimeError("sessão encerrada")

    resultado = nfse_service._observar_fronteira_contrato(
        DriverMudo(), _contrato([_campo_esperado()], contrato_id=71),
        "servico", "entrada",
    )

    assert resultado["estado"] == "ignorada"
    inventariar.assert_not_called()
