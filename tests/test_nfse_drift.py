"""Comparação pura de contratos, com estruturas sintéticas e sem banco."""

from dataclasses import replace

from app.automation.nfse_recon import (
    ControleInventariado,
    InventarioEtapa,
    OpcaoInventariada,
)
from app.services.nfse_drift import (
    AVISO,
    COMPATIVEL,
    CONTROLE_NOVO,
    CONTROLE_REMOVIDO,
    DESCONHECIDA,
    INCOMPATIVEL,
    OPCOES_ALTERADAS,
    CampoComparavel,
    assinar_incidente,
    comparar,
    normalizar_chave,
    recomendar_remapeamentos,
)


def _opcoes(*valores):
    return tuple(OpcaoInventariada(valor, f"Rótulo {valor}", ordem) for ordem, valor in enumerate(valores))


def _contrato(
    chave="campo.nome",
    *,
    rotulo="Descrição do serviço",
    tipo="text",
    interacao="text",
    obrigatorio=False,
    opcoes=(),
):
    return CampoComparavel(
        chave_semantica=chave,
        etapa="servico",
        rotulo=rotulo,
        tipo=tipo,
        interacao=interacao,
        obrigatorio=obrigatorio,
        seletor_tipo="name",
        seletor=chave,
        opcoes=tuple(opcoes),
    )


def _inventariado(
    chave="campo.nome",
    *,
    rotulo="Descrição do serviço",
    tipo="text",
    interacao="text",
    obrigatorio=False,
    opcoes=(),
    classes=(),
    ordem=0,
):
    return ControleInventariado(
        chave_semantica=chave,
        etapa="servico",
        tag="input",
        tipo=tipo,
        id=f"id-{chave}",
        name=chave,
        rotulo=rotulo,
        seletor_tipo="name",
        seletor=chave,
        obrigatorio=obrigatorio,
        desabilitado=False,
        somente_leitura=False,
        visivel=True,
        interacao=interacao,
        classes_funcionais=tuple(classes),
        opcoes=tuple(opcoes),
        ordem_relativa=ordem,
    )


def _resultado(campos, controles, *, observacao_final=True):
    return comparar(
        "servico",
        campos,
        InventarioEtapa("servico", tuple(controles)),
        observacao_final=observacao_final,
    )


def test_normalizacao_reutiliza_regra_da_nfse():
    assert normalizar_chave("  Código   Tributário  ") == "CODIGO TRIBUTARIO"
    assert normalizar_chave("código tributário") == normalizar_chave("CODIGO TRIBUTARIO")


def test_ordem_e_classes_funcionais_decorativas_nao_criam_diferenca():
    contrato = [_contrato("a", rotulo="Campo A"), _contrato("b", rotulo="Campo B")]
    atual = [
        _inventariado("b", rotulo="Campo B", classes=("form-chosen",), ordem=99),
        _inventariado("a", rotulo="Campo A", classes=(), ordem=2),
    ]

    resultado = _resultado(contrato, atual)

    assert resultado.compatibilidade == COMPATIVEL
    assert resultado.diferencas == ()


def test_campo_novo_opcional_eh_aviso_e_obrigatorio_eh_incompativel():
    opcional = _resultado([], [_inventariado("novo", rotulo="Novo campo")])
    obrigatorio = _resultado([], [_inventariado("novo", rotulo="Novo campo", obrigatorio=True)])

    assert opcional.compatibilidade == AVISO
    assert opcional.diferencas[0].tipo == CONTROLE_NOVO
    assert obrigatorio.compatibilidade == INCOMPATIVEL


def test_campo_removido_fica_separado_do_campo_novo():
    resultado = _resultado(
        [_contrato("antigo", rotulo="Campo antigo")],
        [_inventariado("novo", rotulo="Campo novo")],
    )

    assert [d.tipo for d in resultado.diferencas] == [CONTROLE_REMOVIDO, CONTROLE_NOVO]
    assert resultado.diferencas[0].chave_esperada == "antigo"
    assert resultado.diferencas[1].chave_observada == "novo"


def test_tipo_obrigatoriedade_e_opcoes_sao_diferencas_tipadas():
    contrato = [_contrato(tipo="select", obrigatorio=False, opcoes=_opcoes("A", "B"))]
    atual = [_inventariado(tipo="radio", obrigatorio=True, opcoes=_opcoes("A", "C"))]

    resultado = _resultado(contrato, atual)
    tipos = {d.tipo for d in resultado.diferencas}

    assert resultado.compatibilidade == INCOMPATIVEL
    assert {"tipo_alterado", "obrigatoriedade_alterada", OPCOES_ALTERADAS} <= tipos


def test_inventario_desconhecido_nao_vira_compatibilidade():
    resultado = comparar(
        "servico",
        [_contrato()],
        InventarioEtapa.desconhecido("servico", "sessão indisponível"),
    )

    assert resultado.compatibilidade == DESCONHECIDA
    assert resultado.diferencas == ()


def test_remapeamento_inequivoco_exige_evidencias_estruturais():
    resultado = _resultado(
        [_contrato("campo.antigo", rotulo="Descrição do serviço")],
        [_inventariado("campo.novo", rotulo="Descrição do serviço")],
    )

    recomendacoes = recomendar_remapeamentos(resultado)

    assert len(recomendacoes) == 1
    assert recomendacoes[0].chave_esperada == "campo.antigo"
    assert recomendacoes[0].chave_observada == "campo.novo"
    assert recomendacoes[0].inequivoca
    assert not recomendacoes[0].ambigua


def test_rotulo_generico_nunca_produz_recomendacao_inequivoca():
    resultado = _resultado(
        [_contrato("campo.antigo", rotulo="Campo")],
        [_inventariado("campo.novo", rotulo="Campo")],
    )

    assert recomendar_remapeamentos(resultado) == []


def test_dois_candidatos_plausiveis_resultam_em_ambiguidade():
    resultado = _resultado(
        [_contrato("campo.antigo", rotulo="Descrição do serviço")],
        [
            _inventariado("campo.novo.a", rotulo="Descrição do serviço"),
            _inventariado("campo.novo.b", rotulo="Descrição do serviço"),
        ],
    )

    recomendacoes = recomendar_remapeamentos(resultado)

    assert len(recomendacoes) == 1
    assert recomendacoes[0].ambigua
    assert recomendacoes[0].chave_observada is None
    assert recomendacoes[0].candidatos == ("campo.novo.a", "campo.novo.b")


def test_tipo_incompativel_nao_gera_recomendacao():
    resultado = _resultado(
        [_contrato("campo.antigo", rotulo="Descrição do serviço", tipo="text")],
        [_inventariado("campo.novo", rotulo="Descrição do serviço", tipo="select")],
    )

    assert recomendar_remapeamentos(resultado) == []


def test_assinatura_e_deterministica_e_muda_em_dimensao_semantica():
    resultado = _resultado(
        [_contrato(opcoes=_opcoes("A", "B"))],
        [_inventariado(opcoes=_opcoes("A", "C"))],
    )
    diferenca = next(d for d in resultado.diferencas if d.tipo == OPCOES_ALTERADAS)
    original = assinar_incidente(7, diferenca)

    assert original == assinar_incidente(7, diferenca)
    assert original != assinar_incidente(8, diferenca)
    assert original != assinar_incidente(7, replace(diferenca, tipo="tipo_alterado"))
    # Obrigatoriedade e visibilidade mudam conforme a tela é preenchida: se
    # entrassem na assinatura, cada passe da recon criaria um incidente novo
    # para o mesmo controle em vez de atualizar o que já existe.
    observado = replace(diferenca.observado, obrigatorio=not diferenca.observado.obrigatorio)
    assert original == assinar_incidente(7, replace(diferenca, observado=observado))
    observado = replace(diferenca.observado, visivel=False)
    assert original == assinar_incidente(7, replace(diferenca, observado=observado))
    observado = replace(diferenca.observado, opcoes=_opcoes("A", "D"))
    assert original != assinar_incidente(7, replace(diferenca, observado=observado))


def test_ausencia_so_e_remocao_quando_a_observacao_e_final():
    """Achado de UAT: a etapa de Pessoas só revela o Regime de apuração depois
    que a competência recarrega a tela. Tratar a ausência inicial como remoção
    bloqueava a automação por um fato que não aconteceu."""

    campos = [_contrato("antigo", rotulo="Campo antigo")]

    parcial = _resultado(campos, [], observacao_final=False)
    final = _resultado(campos, [], observacao_final=True)

    # Provisória: evidência, mas não pede decisão nem torna a tela incompatível.
    assert parcial.compatibilidade == COMPATIVEL
    assert parcial.diferencas[0].provisoria is True
    assert parcial.diferencas_acionaveis == ()
    assert "ainda não apareceu" in parcial.diferencas[0].mensagem

    assert final.compatibilidade == INCOMPATIVEL
    assert final.diferencas[0].provisoria is False
    assert final.diferencas[0].severidade == "critica"


def test_controle_que_o_operador_nao_preenche_nao_vira_incidente():
    """A etapa de Pessoas tem ~100 controles — endereço no exterior,
    intermediário, dados do próprio emitente — e quase nenhum é preenchível."""

    base = _inventariado("novo", rotulo="Novo campo", obrigatorio=True)
    for controle in (
        replace(base, visivel=False),
        replace(base, desabilitado=True),
        replace(base, somente_leitura=True),
    ):
        resultado = _resultado([], [controle])
        assert resultado.compatibilidade == COMPATIVEL
        assert resultado.diferencas == ()

    exigido = _resultado([], [base])
    assert exigido.compatibilidade == INCOMPATIVEL
    assert exigido.diferencas[0].severidade == "critica"


def test_contrato_por_id_casa_com_inventario_por_name():
    """O portal é ASP.NET MVC: `Tomador.Inscricao` por name, `Tomador_Inscricao` por id."""

    campo = replace(
        _contrato("Tomador_Inscricao", rotulo="CPF/CNPJ do tomador"),
        seletor_tipo="id", seletor="Tomador_Inscricao",
    )
    observado = replace(
        _inventariado("Tomador.Inscricao", rotulo="CPF/CNPJ do tomador"),
        id="Tomador_Inscricao", name="Tomador.Inscricao",
    )

    resultado = _resultado([campo], [observado])

    assert resultado.compatibilidade == COMPATIVEL
    assert resultado.diferencas == ()


def test_adaptador_de_politica_nao_e_comparado_com_a_forma_do_controle():
    """`intocavel` diz o que a automação faz (nada), não como o campo é desenhado."""

    campo = replace(_contrato("Prestador_Inscricao"), interacao="intocavel")
    observado = _inventariado("Prestador_Inscricao", interacao="texto")

    assert _resultado([campo], [observado]).compatibilidade == COMPATIVEL


def test_obrigatoriedade_de_campo_que_o_portal_preenche_nao_e_diferenca():
    """O nome do tomador é `readonly` e tem `*` no rótulo. O asterisco marca
    exigência do documento, não do operador."""

    campo = _contrato("Tomador.Nome", obrigatorio=False)
    observado = replace(
        _inventariado("Tomador.Nome", obrigatorio=True), somente_leitura=True
    )

    assert _resultado([campo], [observado]).compatibilidade == COMPATIVEL


def test_obrigatoriedade_continua_valendo_no_campo_que_o_operador_preenche():
    resultado = _resultado(
        [_contrato("Campo.Livre", obrigatorio=False)],
        [_inventariado("Campo.Livre", obrigatorio=True)],
    )

    assert [d.tipo for d in resultado.diferencas] == ["obrigatoriedade_alterada"]
    assert resultado.compatibilidade == INCOMPATIVEL
