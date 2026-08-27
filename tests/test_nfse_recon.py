"""Testes do inventário sanitizado, sem navegador nem sessão fiscal."""

from __future__ import annotations

import json

import pytest

from app.automation.nfse_recon import (
    JS_INVENTARIO_SEGURO,
    JS_MENSAGENS_VALIDACAO,
    JS_PREENCHIMENTO_SEGURO,
    AcumuladorRecon,
    InventarioExcedidoError,
    InventarioEtapa,
    etapa_da_url,
    inventariar,
    inventario_para_html,
    mensagens_validacao,
    preenchimento,
    rascunho_da_url,
    unir,
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


def _inventario(*controles):
    return inventariar(
        DriverFalso([{"estado": "ok", "controles": list(controles)}]), "pessoas"
    )


@pytest.mark.parametrize("script", [JS_INVENTARIO_SEGURO, JS_MENSAGENS_VALIDACAO])
def test_script_devolve_valor_no_contrato_do_selenium(script):
    """execute_script embrulha o texto no corpo de uma função: uma IIFE que seja
    só expressão avalia e descarta o resultado, devolvendo undefined. A recon
    nunca funcionou, em tela nenhuma, por causa disso."""

    assert script.lstrip().startswith("return ")


def test_inventario_estrutural_continua_proibido_de_ler_estado():
    assert ".value" not in JS_INVENTARIO_SEGURO
    assert ".checked" not in JS_INVENTARIO_SEGURO


def test_inventario_sem_resposta_do_portal_explica_o_motivo():
    class DriverSemResposta:
        def execute_script(self, script):
            return None

    inventario = inventariar(DriverSemResposta(), "pessoas")

    assert inventario.estado == "desconhecida"
    assert "portal" in (inventario.motivo or "")


def test_payload_sem_controles_explica_o_motivo():
    inventario = inventariar(DriverFalso([{"estado": "ok"}]), "pessoas")

    assert inventario.estado == "desconhecida"
    assert "controles" in (inventario.motivo or "")


def test_marcador_de_carregamento_oculto_nao_bloqueia_a_observacao():
    """Regressao: o overlay de loader existe no markup o tempo todo, oculto."""

    assert "if (visivel(marcadores[m]))" in JS_INVENTARIO_SEGURO


def test_script_ignora_cromo_de_plugin_e_controle_sem_identidade():
    """O Chosen injeta a própria caixa de busca: cromo do plugin, não campo."""

    assert "cromoDePlugin" in JS_INVENTARIO_SEGURO
    assert ".chosen-container" in JS_INVENTARIO_SEGURO
    assert (
        "if (!elemento.getAttribute('name') && !elemento.getAttribute('id')) { continue; }"
        in JS_INVENTARIO_SEGURO
    )


def test_script_le_exigencia_do_asterisco_e_nao_a_deixa_no_rotulo():
    """O portal marca exigência com `*` no rótulo, não em atributo do input."""

    assert "data-val-required" in JS_INVENTARIO_SEGURO
    assert r"/\*\s*$/.test(rotulo" in JS_INVENTARIO_SEGURO
    assert r"rotulo.replace(/\s*\*\s*$/, '')" in JS_INVENTARIO_SEGURO


def test_rotulo_do_grupo_de_radio_vem_da_pergunta_nao_da_primeira_opcao():
    """Achado de UAT: o campo obrigatório da reforma virou um incidente chamado
    "Sim", porque o rótulo saía da primeira opção do grupo."""

    assert "function rotuloDeGrupo(" in JS_INVENTARIO_SEGURO
    assert "fieldset.querySelector('legend')" in JS_INVENTARIO_SEGURO
    assert ".form-check, .form-check-inline, .radiobutton, .checkbox" in JS_INVENTARIO_SEGURO
    assert "agrupado ? (rotuloDeGrupo(elemento) || rotuloOpcao)" in JS_INVENTARIO_SEGURO


def test_radio_desenhado_com_input_escondido_continua_visivel():
    """O portal esconde o `<input>` e desenha o controle num `<span class="cr">`.
    Medir o input some com os obrigatórios da tela — o IBS/CBS é um desses."""

    assert "function pintado(" in JS_INVENTARIO_SEGURO
    assert "if (pintado(elemento)) { return true; }" in JS_INVENTARIO_SEGURO
    assert "'label, .radiobutton, .form-check, .radio-options, .checkbox'" in (
        JS_INVENTARIO_SEGURO
    )


def test_checkbox_que_revela_bloco_e_acao_nao_dado():
    """"Informar endereço" abre uma seção: não faz sentido pedir origem do valor."""

    inventario = _inventario(
        _controle(
            id="Tomador_InformarEndereco",
            name="Tomador.InformarEndereco",
            tipo="checkbox",
            rotulo="Informar endereço",
            revela_bloco=True,
            opcoes=[{"valor": "true", "rotulo": "Informar endereço"}],
        ),
        _controle(id="Campo", name="Campo", tipo="checkbox", rotulo="Um dado"),
    )
    por_chave = {c.chave_semantica: c for c in inventario.controles}

    assert por_chave["Tomador.InformarEndereco"].interacao == "acao"
    assert por_chave["Campo"].interacao == "checkbox"


def test_uniao_soma_o_campo_que_so_aparece_no_segundo_passe():
    """Achado de UAT: o Regime de apuração só existe depois que a data recarrega."""

    inicial = _inventario(_controle(id="DataCompetencia", name="DataCompetencia"))
    depois = _inventario(
        _controle(id="DataCompetencia", name="DataCompetencia"),
        _controle(id="Regime", name="Regime", rotulo="Regime de apuração"),
    )

    chaves = {c.chave_semantica for c in unir(inicial, depois).controles}

    assert chaves == {"DataCompetencia", "Regime"}


def test_uniao_casa_o_mesmo_controle_por_id_e_por_name():
    """`name="Tomador.Inscricao"` e `id="Tomador_Inscricao"` são o mesmo campo."""

    passe_a = _inventario(_controle(id="Tomador_Inscricao", name=""))
    passe_b = _inventario(_controle(id="Tomador_Inscricao", name="Tomador.Inscricao"))

    assert len(unir(passe_a, passe_b).controles) == 1


def test_uniao_mantem_visibilidade_e_exigencia_ja_observadas():
    visivel = _inventario(_controle(visivel=True, obrigatorio=True))
    oculto = _inventario(_controle(visivel=False, obrigatorio=False))

    uniao = unir(visivel, oculto)

    assert uniao.controles[0].visivel is True
    assert uniao.controles[0].obrigatorio is True


def test_uniao_prefere_a_lista_de_opcoes_carregada_depois():
    """Select populado por AJAX chega vazio no primeiro passe."""

    vazio = _inventario(_controle(tag="select", tipo="select", opcoes=[]))
    cheio = _inventario(
        _controle(tag="select", tipo="select",
                  opcoes=[{"valor": "1", "rotulo": "Opção 1"}])
    )

    assert unir(vazio, cheio).controles[0].opcoes[0].valor == "1"
    assert unir(cheio, vazio).controles[0].opcoes[0].valor == "1"


def test_uniao_com_desconhecido_nao_contamina_o_que_ja_foi_observado():
    conhecido = _inventario(_controle())
    desconhecido = InventarioEtapa.desconhecido("pessoas", "portal mudo")

    assert unir(conhecido, desconhecido) is conhecido
    assert unir(desconhecido, conhecido) is conhecido


def test_rascunho_sai_do_idr_e_ignora_o_resto_da_url():
    assert rascunho_da_url(
        "https://www.nfse.gov.br/EmissorNacional/DPS/Pessoas?idr=opaco-123"
    ) == "opaco-123"
    assert rascunho_da_url("https://www.nfse.gov.br/EmissorNacional/DPS/Pessoas") == ""


def test_acumulador_zera_ao_trocar_de_rascunho():
    """Estrutura de outra nota não é evidência desta."""

    acumulador = AcumuladorRecon()
    acumulador.acumular("rascunho-a", _inventario(_controle(id="A", name="A")))
    acumulador.acumular("rascunho-a", _inventario(_controle(id="B", name="B")))
    assert acumulador.passes("pessoas") == 2

    uniao = acumulador.acumular("rascunho-b", _inventario(_controle(id="C", name="C")))

    assert acumulador.passes("pessoas") == 1
    assert {c.chave_semantica for c in uniao.controles} == {"C"}


def test_preenchimento_devolve_so_booleano_e_identidade():
    class DriverComConteudo:
        def execute_script(self, script):
            return [
                {"id": "Tomador_Nome", "name": "Tomador.Nome", "preenchido": True},
                {"id": "Tomador_Email", "name": "", "preenchido": False},
                # Conteudo nao pertence a este contrato: e descartado.
                {"id": "vazado", "name": "vazado", "preenchido": "SENTINELA-VALOR"},
                {"id": "", "name": "", "preenchido": True},
            ]

    estado = preenchimento(DriverComConteudo())

    assert estado == {
        "Tomador_Nome": True, "Tomador.Nome": True, "Tomador_Email": False,
    }
    assert "SENTINELA-VALOR" not in str(estado)


def test_script_de_preenchimento_nao_devolve_conteudo():
    """O único script que toca `.value` só deixa sair o comprimento."""

    assert "value: " not in JS_PREENCHIMENTO_SEGURO
    assert "String(elemento.value || '').trim().length > 0" in JS_PREENCHIMENTO_SEGURO
    assert "preenchido: cheio(elemento) === true" in JS_PREENCHIMENTO_SEGURO


def test_sugere_intocavel_para_o_que_o_portal_preencheu_sozinho():
    """Achado de UAT: o bloco do tomador chega preenchido junto com o CNPJ."""

    acumulador = AcumuladorRecon()
    controles = [
        _controle(id="Tomador_Nome", name="Tomador.Nome", rotulo="Nome"),
        _controle(id="Tomador_Email", name="Tomador.Email", rotulo="E-mail",
                  obrigatorio=True),
    ]
    acumulador.acumular("r1", _inventario(*controles),
                        {"Tomador.Nome": False, "Tomador.Email": False})
    acumulador.acumular("r1", _inventario(*controles),
                        {"Tomador.Nome": True, "Tomador.Email": False})

    por_chave = {i.chave: i for i in acumulador.sugestoes("pessoas")}

    assert por_chave["Tomador.Nome"].sugestao == "intocavel"
    assert por_chave["Tomador.Email"].sugestao == "preencher"


def test_nao_sugere_preencher_o_que_o_operador_nao_alcanca():
    acumulador = AcumuladorRecon()
    acumulador.acumular("r1", _inventario(
        _controle(id="A", name="A", obrigatorio=True, desabilitado=True),
        _controle(id="B", name="B", obrigatorio=True, visivel=False),
    ), {"A": False, "B": False})

    assert acumulador.sugestoes("pessoas") == ()


def _driver_com_mensagens(*mensagens):
    class DriverMensagens:
        def execute_script(self, script):
            if 'role=\"alert\"' in script:
                return list(mensagens)
            return json.dumps({"estado": "ok", "controles": []})

    return DriverMensagens()


def test_so_passa_o_que_tem_forma_de_mensagem_de_validacao():
    """Lista de permissão, não de recusa: o nome do tomador não está em
    `valores_sensiveis`, porque campo `intocavel` resolve para `None`."""

    assert mensagens_validacao(
        _driver_com_mensagens(
            "O campo Nome/Razão Social é obrigatório.",
            "Informe um CNPJ válido.",
        ), [],
    ) == ["O campo Nome/Razão Social é obrigatório.", "Informe um CNPJ válido."]


def test_descarta_mensagem_que_ecoa_dado_autopreenchido_pelo_portal():
    assert mensagens_validacao(
        _driver_com_mensagens(
            "O campo é obrigatório para PAPELARIA CENTRAL LTDA.",
            "CNPJ 11222333000181 inválido para esta operação.",
            "Informe um e-mail válido: contato@exemplo.com.br",
            "PAPELARIA CENTRAL LTDA",
        ), [],
    ) == []


def test_mensagem_longa_e_truncada_nunca_fatal():
    """Este leitor roda no tratamento de erro: levantar aqui apagaria o erro
    original da nota e derrubaria o lote inteiro."""

    longa = "O campo é obrigatório. " + ("detalhe " * 200)

    aceitas = mensagens_validacao(_driver_com_mensagens(longa), [])

    assert len(aceitas) == 1
    assert len(aceitas[0]) <= 500
