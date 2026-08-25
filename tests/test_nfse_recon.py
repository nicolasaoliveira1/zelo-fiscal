"""Testes do inventário sanitizado, sem navegador nem sessão fiscal."""

from __future__ import annotations

import json

import pytest

from app.automation.nfse_recon import (
    JS_INVENTARIO_SEGURO,
    InventarioExcedidoError,
    InventarioEtapa,
    etapa_da_url,
    inventariar,
    inventario_para_html,
    mensagens_validacao,
)


class DriverFalso:
    def __init__(self, inventarios, mensagens=None):
        self.inventarios = list(inventarios)
        self.mensagens = mensagens if mensagens is not None else []

    def execute_script(self, script):
        if "role=\"alert\"" in script:
            return self.mensagens
        payload = self.inventarios.pop(0) if len(self.inventarios) > 1 else self.inventarios[0]
        return json.dumps(payload)


def _controle(**sobrescritos):
    controle = {
        "tag": "input",
        "tipo": "text",
        "id": "campo-id",
        "name": "campo.nome",
        "rotulo": "Campo declarado",
        "obrigatorio": True,
        "desabilitado": False,
        "somente_leitura": False,
        "visivel": True,
        "classes_funcionais": ["classe-decorativa"],
        "opcoes": [],
        # Chaves desconhecidas representam propriedades que não fazem parte
        # do payload seguro e devem ser ignoradas pelo inventariador.
        "valor_atual": "SENTINELA-ATUAL",
        "checked": True,
        "selected": True,
    }
    controle.update(sobrescritos)
    return controle


def test_etapa_da_url_usa_path_e_ignora_querystring():
    assert etapa_da_url(
        "https://www.nfse.gov.br/EmissorNacional/DPS/Servico?idr=opaco"
    ) == "servico"
    assert etapa_da_url("/EmissorNacional/DPS/Tributacao/") == "tributacao"
    assert etapa_da_url("https://www.nfse.gov.br/EmissorNacional/Login") is None


def test_script_seguro_nao_acessa_estado_atual_dos_controles():
    assert ".value" not in JS_INVENTARIO_SEGURO
    assert "checked" not in JS_INVENTARIO_SEGURO
    assert "selected" not in JS_INVENTARIO_SEGURO


def test_inventario_omite_sentinelas_de_todos_os_tipos_e_reconstroi_html():
    sentinela = "SENTINELA-VALOR-ATUAL"
    payload = {
        "estado": "ok",
        "controles": [
            _controle(),
            _controle(
                id="campo-textarea",
                name="campo.descricao",
                tag="textarea",
                tipo="textarea",
                rotulo="Descrição declarada",
                valor_atual=sentinela,
            ),
            _controle(
                id="campo-select",
                name="campo.opcao",
                tag="select",
                tipo="select",
                opcoes=[
                    {"valor": "A", "rotulo": "Opção A", "selected": True},
                    {"valor": "B", "rotulo": "Opção B", "selected": False},
                ],
            ),
            _controle(
                id="radio-a",
                name="campo.radio",
                tipo="radio",
                opcoes=[{"valor": "a", "rotulo": "A", "checked": True}],
                checked=True,
            ),
            _controle(
                id="radio-b",
                name="campo.radio",
                tipo="radio",
                opcoes=[{"valor": "b", "rotulo": "B", "checked": False}],
                checked=False,
            ),
            _controle(
                id="check",
                name="campo.check",
                tipo="checkbox",
                opcoes=[{"valor": "sim", "rotulo": "Sim", "checked": True}],
                checked=True,
            ),
        ],
    }
    inventario = inventariar(DriverFalso([payload]), "servico")
    html = inventario_para_html(inventario)

    assert inventario.conhecida
    assert sentinela not in repr(inventario)
    assert sentinela not in html
    assert len([c for c in inventario.controles if c.name == "campo.radio"]) == 1
    radio = next(c for c in inventario.controles if c.name == "campo.radio")
    assert {opcao.valor for opcao in radio.opcoes} == {"a", "b"}
    assert "classe-decorativa" not in repr(inventario)
    assert "classe-decorativa" not in html


def test_inventario_preserva_todas_as_opcoes_sem_truncamento_em_quarenta():
    opcoes = [{"valor": str(i), "rotulo": f"Opção {i}"} for i in range(41)]
    payload = {"estado": "ok", "controles": [_controle(tag="select", tipo="select", opcoes=opcoes)]}

    inventario = inventariar(DriverFalso([payload]), "servico")

    assert len(inventario.controles[0].opcoes) == 41
    assert inventario.controles[0].opcoes[-1].valor == "40"


def test_carregamento_assincrono_espera_marcador_e_depois_inventaria():
    payload = {"estado": "ok", "controles": [_controle()]}
    driver = DriverFalso([{"estado": "carregando"}, payload])

    inventario = inventariar(driver, "pessoas", timeout=0.2, intervalo=0)

    assert inventario.conhecida
    assert len(driver.inventarios) == 1


def test_timeout_de_carregamento_retorna_estado_desconhecido():
    inventario = inventariar(
        DriverFalso([{"estado": "carregando"}]),
        "pessoas",
        timeout=0,
    )

    assert isinstance(inventario, InventarioEtapa)
    assert inventario.estado == "desconhecida"
    assert inventario.controles == ()


def test_limite_de_controles_levanta_sem_inventario_parcial():
    payload = {"estado": "ok", "controles": [_controle(id=f"id-{i}") for i in range(501)]}

    with pytest.raises(InventarioExcedidoError) as erro:
        inventariar(DriverFalso([payload]), "pessoas")

    assert "SENTINELA" not in str(erro.value)


def test_limite_de_opcoes_levanta_sem_truncar():
    opcoes = [{"valor": str(i), "rotulo": f"Opção {i}"} for i in range(5001)]
    payload = {"estado": "ok", "controles": [_controle(tag="select", tipo="select", opcoes=opcoes)]}

    with pytest.raises(InventarioExcedidoError):
        inventariar(DriverFalso([payload]), "servico")


def test_mensagens_de_validacao_descartam_sequencias_sensiveis():
    driver = DriverFalso(
        [{"estado": "ok", "controles": []}],
        mensagens=["Campo obrigatório", "Mensagem SENTINELA-VALOR-ATUAL"],
    )

    mensagens = mensagens_validacao(driver, ["SENTINELA-VALOR-ATUAL"])

    assert mensagens == ["Campo obrigatório"]
