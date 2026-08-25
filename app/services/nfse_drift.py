"""Comparação determinística entre contrato e inventário da NFS-e.

O comparador é deliberadamente puro: não acessa banco, Selenium, sessão fiscal
ou valores da nota. Ele trabalha apenas com a estrutura declarada pelo
contrato e pelo inventário sanitizado.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Iterable, Mapping

from app.automation.nfse_recon import InventarioEtapa, OpcaoInventariada


COMPATIVEL = "compativel"
AVISO = "aviso"
INCOMPATIVEL = "incompativel"
DESCONHECIDA = "desconhecida"

CONTROLE_NOVO = "controle_novo"
CONTROLE_REMOVIDO = "controle_removido"
TIPO_ALTERADO = "tipo_alterado"
OBRIGATORIEDADE_ALTERADA = "obrigatoriedade_alterada"
OPCOES_ALTERADAS = "opcoes_alteradas"
INTERACAO_ALTERADA = "interacao_alterada"
VISIBILIDADE_ALTERADA = "visibilidade_alterada"


def _sem_acento(texto: Any) -> str:
    return (
        unicodedata.normalize("NFKD", str(texto or ""))
        .encode("ascii", "ignore")
        .decode()
    )


def normalizar_chave(texto: Any) -> str:
    """Mantém a normalização textual usada pelos seletores atuais da NFS-e."""

    return " ".join(_sem_acento(texto).upper().split())


@dataclass(frozen=True)
class CampoComparavel:
    """Visão segura de um campo, compartilhada por contrato e inventário."""

    chave_semantica: str
    etapa: str
    rotulo: str
    tipo: str
    interacao: str
    obrigatorio: bool
    visivel: bool | None = None
    seletor_tipo: str = ""
    seletor: str = ""
    opcoes: tuple[OpcaoInventariada, ...] = ()
    classes_funcionais: tuple[str, ...] = field(default=(), compare=False, repr=False)
    ordem: int = field(default=0, compare=False, repr=False)


@dataclass(frozen=True)
class Diferenca:
    """Uma dimensão semântica diferente entre contrato e inventário."""

    etapa: str
    tipo: str
    severidade: str
    chave_esperada: str | None = None
    chave_observada: str | None = None
    esperado: CampoComparavel | None = None
    observado: CampoComparavel | None = None
    mensagem: str = ""

    @property
    def rotulo(self) -> str:
        campo = self.observado or self.esperado
        return campo.rotulo if campo else ""


@dataclass(frozen=True)
class ResultadoComparacao:
    """Resultado completo e imutável da comparação de uma etapa."""

    etapa: str
    compatibilidade: str
    diferencas: tuple[Diferenca, ...] = ()
    evidencias: tuple[str, ...] = ()

    @property
    def compatibilidade_normalizada(self) -> str:
        return self.compatibilidade


@dataclass(frozen=True)
class Recomendacao:
    """Sugestão de associação, que nunca aplica uma decisão sozinha."""

    etapa: str
    chave_esperada: str
    chave_observada: str | None
    confianca: str
    evidencias: tuple[str, ...] = ()
    candidatos: tuple[str, ...] = ()
    ambigua: bool = False

    @property
    def inequivoca(self) -> bool:
        return not self.ambigua and self.chave_observada is not None


def _ler(objeto: Any, nome: str, padrao: Any = None) -> Any:
    if isinstance(objeto, Mapping):
        return objeto.get(nome, padrao)
    return getattr(objeto, nome, padrao)


def _opcoes_de(objeto: Any) -> tuple[OpcaoInventariada, ...]:
    resultado = []
    for ordem, opcao in enumerate(_ler(objeto, "opcoes", ()) or ()):
        valor = _ler(opcao, "valor", "")
        rotulo = _ler(opcao, "rotulo", "")
        resultado.append(OpcaoInventariada(str(valor or ""), str(rotulo or ""), ordem))
    return tuple(resultado)


def _campo_de(objeto: Any, etapa_padrao: str) -> CampoComparavel:
    etapa = str(_ler(objeto, "etapa", etapa_padrao) or etapa_padrao)
    chave = str(_ler(objeto, "chave_semantica", "") or "")
    tipo = str(_ler(objeto, "tipo", "") or "")
    interacao = str(_ler(objeto, "interacao", "") or "")
    rotulo = str(_ler(objeto, "rotulo", "") or "")
    visivel = _ler(objeto, "visivel", None)
    if visivel is not None:
        visivel = bool(visivel)
    return CampoComparavel(
        chave_semantica=chave,
        etapa=etapa,
        rotulo=rotulo,
        tipo=tipo,
        interacao=interacao,
        obrigatorio=bool(_ler(objeto, "obrigatorio", False)),
        visivel=visivel,
        seletor_tipo=str(_ler(objeto, "seletor_tipo", "") or ""),
        seletor=str(_ler(objeto, "seletor", "") or ""),
        opcoes=_opcoes_de(objeto),
        classes_funcionais=tuple(_ler(objeto, "classes_funcionais", ()) or ()),
        ordem=int(_ler(objeto, "ordem", _ler(objeto, "ordem_relativa", 0)) or 0),
    )


def _chave_opcao(opcao: OpcaoInventariada) -> tuple[str, str]:
    return normalizar_chave(opcao.valor), normalizar_chave(opcao.rotulo)


def _opcoes_canonicas(campo: CampoComparavel) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(_chave_opcao(opcao) for opcao in campo.opcoes))


def _mesma_opcao(campo_a: CampoComparavel, campo_b: CampoComparavel) -> bool:
    # Selects cuja lista é carregada pelo portal não têm catálogo inicial no
    # contrato. Sem uma declaração esperada, não há base segura para acusar
    # alteração; opções explicitamente contratadas continuam sendo comparadas.
    if not campo_a.opcoes:
        return True
    return _opcoes_canonicas(campo_a) == _opcoes_canonicas(campo_b)


def _diferenca(
    etapa: str,
    tipo: str,
    severidade: str,
    esperado: CampoComparavel | None,
    observado: CampoComparavel | None,
    mensagem: str,
) -> Diferenca:
    return Diferenca(
        etapa=etapa,
        tipo=tipo,
        severidade=severidade,
        chave_esperada=esperado.chave_semantica if esperado else None,
        chave_observada=observado.chave_semantica if observado else None,
        esperado=esperado,
        observado=observado,
        mensagem=mensagem,
    )


def _comparar_campo(esperado: CampoComparavel, observado: CampoComparavel):
    diferencas = []
    if normalizar_chave(esperado.tipo) != normalizar_chave(observado.tipo):
        diferencas.append(
            _diferenca(
                esperado.etapa,
                TIPO_ALTERADO,
                "fiscal",
                esperado,
                observado,
                "O tipo do controle mudou.",
            )
        )
    if esperado.obrigatorio != observado.obrigatorio:
        diferencas.append(
            _diferenca(
                esperado.etapa,
                OBRIGATORIEDADE_ALTERADA,
                "critica",
                esperado,
                observado,
                "A obrigatoriedade do controle mudou.",
            )
        )
    if not _mesma_opcao(esperado, observado):
        diferencas.append(
            _diferenca(
                esperado.etapa,
                OPCOES_ALTERADAS,
                "fiscal",
                esperado,
                observado,
                "O conjunto de opções declaradas mudou.",
            )
        )
    if esperado.interacao and observado.interacao and esperado.interacao != observado.interacao:
        diferencas.append(
            _diferenca(
                esperado.etapa,
                INTERACAO_ALTERADA,
                "fiscal",
                esperado,
                observado,
                "O adaptador de interação do controle mudou.",
            )
        )
    if esperado.visivel is not None and esperado.visivel != observado.visivel:
        diferencas.append(
            _diferenca(
                esperado.etapa,
                VISIBILIDADE_ALTERADA,
                "fiscal",
                esperado,
                observado,
                "A visibilidade condicional do controle mudou.",
            )
        )
    return diferencas


def comparar(
    etapa: str,
    campos_contrato: Iterable[Any],
    inventario: InventarioEtapa,
) -> ResultadoComparacao:
    """Compara uma etapa, sem decidir remapeamentos ou alterar configurações."""

    if inventario.estado != "ok" or inventario.etapa != etapa:
        return ResultadoComparacao(
            etapa=etapa,
            compatibilidade=DESCONHECIDA,
            evidencias=("não foi possível observar a etapa com segurança",),
        )

    esperados = tuple(
        _campo_de(campo, etapa)
        for campo in campos_contrato
        if _campo_de(campo, etapa).etapa == etapa
    )
    observados = tuple(
        _campo_de(campo, etapa)
        for campo in inventario.controles
        if _campo_de(campo, etapa).etapa == etapa
    )
    esperados_por_chave = {campo.chave_semantica: campo for campo in esperados}
    observados_por_chave = {campo.chave_semantica: campo for campo in observados}
    diferencas = []

    for chave in sorted(esperados_por_chave.keys() - observados_por_chave.keys()):
        esperado = esperados_por_chave[chave]
        diferencas.append(
            _diferenca(
                etapa,
                CONTROLE_REMOVIDO,
                "critica",
                esperado,
                None,
                "O controle esperado não foi encontrado.",
            )
        )
    for chave in sorted(observados_por_chave.keys() - esperados_por_chave.keys()):
        observado = observados_por_chave[chave]
        severidade = "critica" if observado.obrigatorio else "informativa"
        diferencas.append(
            _diferenca(
                etapa,
                CONTROLE_NOVO,
                severidade,
                None,
                observado,
                "Foi encontrado um controle que não existe no contrato.",
            )
        )
    for chave in sorted(esperados_por_chave.keys() & observados_por_chave.keys()):
        diferencas.extend(
            _comparar_campo(esperados_por_chave[chave], observados_por_chave[chave])
        )

    if not diferencas:
        compatibilidade = COMPATIVEL
    elif any(diferenca.severidade in {"fiscal", "critica"} for diferenca in diferencas):
        compatibilidade = INCOMPATIVEL
    else:
        compatibilidade = AVISO
    return ResultadoComparacao(
        etapa=etapa,
        compatibilidade=compatibilidade,
        diferencas=tuple(diferencas),
        evidencias=tuple(diferenca.mensagem for diferenca in diferencas),
    )


def _canonico_campo(campo: CampoComparavel | None) -> dict[str, Any] | None:
    if campo is None:
        return None
    return {
        "chave": campo.chave_semantica,
        "etapa": campo.etapa,
        "rotulo": normalizar_chave(campo.rotulo),
        "tipo": normalizar_chave(campo.tipo),
        "interacao": normalizar_chave(campo.interacao),
        "obrigatorio": campo.obrigatorio,
        "visivel": campo.visivel,
        "seletor_tipo": normalizar_chave(campo.seletor_tipo),
        "seletor": campo.seletor,
        "opcoes": _opcoes_canonicas(campo),
    }


def assinar_incidente(contrato_id: int | str, diferenca: Diferenca) -> str:
    """Gera uma assinatura estável para a dimensão semântica observada."""

    observado = _canonico_campo(diferenca.observado)
    forma_observada = json.dumps(
        observado,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assinatura_observada = hashlib.sha256(forma_observada.encode("utf-8")).hexdigest()
    forma = "\x1f".join(
        (
            str(contrato_id),
            diferenca.etapa,
            diferenca.tipo,
            diferenca.chave_esperada or "",
            assinatura_observada,
            _canonico_campo(diferenca.esperado).__repr__(),
        )
    )
    return hashlib.sha256(forma.encode("utf-8")).hexdigest()


_ROTULOS_GENERICO = {
    "",
    "CAMPO",
    "CAMPO NOVO",
    "VALOR",
    "OPCAO",
    "SELECIONE",
    "SELECIONAR",
    "ITEM",
    "DADO",
}


def _rotulo_generico(rotulo: str) -> bool:
    return normalizar_chave(rotulo) in _ROTULOS_GENERICO


def _similaridade_rotulo(a: str, b: str) -> float:
    a_norm = normalizar_chave(a)
    b_norm = normalizar_chave(b)
    if not a_norm or not b_norm or _rotulo_generico(a) or _rotulo_generico(b):
        return 0.0
    if a_norm == b_norm:
        return 1.0
    tokens_a, tokens_b = set(a_norm.split()), set(b_norm.split())
    intersecao = len(tokens_a & tokens_b)
    uniao = len(tokens_a | tokens_b)
    jaccard = intersecao / uniao if uniao else 0.0
    sequencia = SequenceMatcher(None, a_norm, b_norm).ratio()
    return max(jaccard, sequencia)


def _candidatos_compativeis(
    removido: Diferenca,
    novos: Iterable[Diferenca],
) -> list[tuple[float, Diferenca]]:
    esperado = removido.esperado
    if esperado is None or _rotulo_generico(esperado.rotulo):
        return []
    resultado = []
    for novo in novos:
        observado = novo.observado
        if observado is None or _rotulo_generico(observado.rotulo):
            continue
        if esperado.etapa != observado.etapa:
            continue
        if normalizar_chave(esperado.tipo) != normalizar_chave(observado.tipo):
            continue
        if esperado.obrigatorio != observado.obrigatorio:
            continue
        if not _mesma_opcao(esperado, observado):
            continue
        similaridade = _similaridade_rotulo(esperado.rotulo, observado.rotulo)
        if similaridade >= 0.55:
            resultado.append((similaridade, novo))
    return sorted(
        resultado,
        key=lambda item: (-item[0], item[1].chave_observada or ""),
    )


def recomendar_remapeamentos(resultado: ResultadoComparacao) -> list[Recomendacao]:
    """Sugere associações inequívocas ou explicita a ambiguidade encontrada."""

    removidos = [
        diferenca
        for diferenca in resultado.diferencas
        if diferenca.tipo == CONTROLE_REMOVIDO
    ]
    novos = [
        diferenca
        for diferenca in resultado.diferencas
        if diferenca.tipo == CONTROLE_NOVO
    ]
    recomendacoes = []
    for removido in removidos:
        candidatos = _candidatos_compativeis(removido, novos)
        if not candidatos:
            continue
        chaves = tuple(
            candidato.chave_observada
            for _, candidato in candidatos
            if candidato.chave_observada
        )
        if len(candidatos) > 1:
            recomendacoes.append(
                Recomendacao(
                    etapa=resultado.etapa,
                    chave_esperada=removido.chave_esperada or "",
                    chave_observada=None,
                    confianca="ambigua",
                    evidencias=(
                        "etapa, tipo, obrigatoriedade e opções compatíveis",
                        "há mais de um controle plausível",
                    ),
                    candidatos=chaves,
                    ambigua=True,
                )
            )
            continue
        score, candidato = candidatos[0]
        recomendacoes.append(
            Recomendacao(
                etapa=resultado.etapa,
                chave_esperada=removido.chave_esperada or "",
                chave_observada=candidato.chave_observada,
                confianca="alta" if score >= 0.8 else "media",
                evidencias=(
                    "etapa, tipo, obrigatoriedade e opções compatíveis",
                    "rótulos suficientemente semelhantes",
                ),
                candidatos=chaves,
            )
        )
    return recomendacoes


__all__ = [
    "AVISO",
    "COMPATIVEL",
    "CONTROLE_NOVO",
    "CONTROLE_REMOVIDO",
    "DESCONHECIDA",
    "Diferenca",
    "INCOMPATIVEL",
    "INTERACAO_ALTERADA",
    "OBRIGATORIEDADE_ALTERADA",
    "OPCOES_ALTERADAS",
    "Recomendacao",
    "ResultadoComparacao",
    "TIPO_ALTERADO",
    "VISIBILIDADE_ALTERADA",
    "assinar_incidente",
    "comparar",
    "normalizar_chave",
    "recomendar_remapeamentos",
]
