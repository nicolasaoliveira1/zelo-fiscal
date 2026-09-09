"""Comparação pura e determinística dos contratos de portais."""
from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from typing import Any

from app.automation.trabalhista_recon import (
    ElementoInventariado,
    InventarioPortal,
)


COMPATIVEL = 'compativel'
AUTOATIVAVEL = 'autoativavel'
REVISAO = 'revisao'
DESCONHECIDA = 'desconhecida'

_ROTULOS_GENERICOS = frozenset({'CAMPO', 'CONTROLE', 'DIGITE', 'BOTAO', 'BOTÃO'})
_PAPEIS_PROIBIDOS = frozenset({'captcha', 'navegacao', 'submissao', 'download'})
_ACOES_PROIBIDAS = frozenset({'navegar', 'avancar', 'submeter', 'download'})
_TRECHOS_CHAVE_PROIBIDOS = (
    'captcha', 'submeter', 'submit', 'baixar', 'download', 'abrir', 'avancar')


@dataclass(frozen=True)
class ElementoContratoComparavel:
    chave: str
    etapa: str
    papel: str
    acao: str
    seletor_tipo: str
    seletor: str
    tag: str
    tipo: str
    rotulo: str
    assinatura_formulario: str
    ordem_relativa: int
    obrigatorio: bool
    visivel: bool
    somente_leitura: bool
    desabilitado: bool = False
    autoajuste_seletor: bool = False


@dataclass(frozen=True)
class ContratoComparavel:
    host: str
    rota: str
    etapa: str
    elementos: tuple[ElementoContratoComparavel, ...]


@dataclass(frozen=True)
class DiferencaPortal:
    tipo: str
    severidade: str
    etapa: str
    elemento_chave: str | None
    esperado: str | None
    observado: str | None
    evidencias: tuple[str, ...]
    assinatura: str


@dataclass(frozen=True)
class RemapeamentoSeletor:
    chave: str
    seletor_tipo_anterior: str
    seletor_anterior: str
    seletor_tipo_novo: str
    seletor_novo: str


@dataclass(frozen=True)
class ResultadoComparacaoPortal:
    classificacao: str
    diferencas: tuple[DiferencaPortal, ...] = ()
    remapeamentos: tuple[RemapeamentoSeletor, ...] = ()


def normalizar_rotulo(valor: Any) -> str:
    texto = unicodedata.normalize('NFKD', str(valor or ''))
    sem_acento = ''.join(caractere for caractere in texto
                         if not unicodedata.combining(caractere))
    return ' '.join(sem_acento.upper().split())


def _diferenca(
    tipo: str,
    etapa: str,
    elemento_chave: str | None,
    esperado: Any,
    observado: Any,
    *evidencias: str,
) -> DiferencaPortal:
    esperado_texto = None if esperado is None else str(esperado)
    observado_texto = None if observado is None else str(observado)
    conteudo = {
        'tipo': tipo,
        'etapa': etapa,
        'elemento_chave': elemento_chave,
        'esperado': esperado_texto,
        'observado': observado_texto,
        'evidencias': tuple(evidencias),
    }
    assinatura = hashlib.sha256(json.dumps(
        conteudo, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode('utf-8')).hexdigest()
    return DiferencaPortal(
        tipo=tipo,
        severidade='bloqueante',
        etapa=etapa,
        elemento_chave=elemento_chave,
        esperado=esperado_texto,
        observado=observado_texto,
        evidencias=tuple(evidencias),
        assinatura=assinatura,
    )


def _papel_acao(elemento: ElementoInventariado) -> tuple[str, str]:
    if elemento.tag == 'img':
        return 'captcha', 'observar'
    if elemento.tag == 'a':
        return 'navegacao', 'navegar'
    if elemento.tag == 'button' or elemento.tipo in {'submit', 'button', 'image'}:
        return 'submissao', 'submeter'
    return 'entrada', 'preencher'


def _mesma_identidade(
    esperado: ElementoContratoComparavel,
    observado: ElementoInventariado,
) -> bool:
    return (
        esperado.seletor_tipo == observado.seletor_tipo
        and esperado.seletor == observado.seletor
    )


def _rotulo_ambiguo(rotulo: str) -> bool:
    normalizado = normalizar_rotulo(rotulo)
    return len(normalizado) < 3 or normalizado in _ROTULOS_GENERICOS


def _candidatos(
    esperado: ElementoContratoComparavel,
    observados: tuple[ElementoInventariado, ...],
    disponiveis: set[int],
) -> list[int]:
    rotulo = normalizar_rotulo(esperado.rotulo)
    por_rotulo = [
        indice for indice in disponiveis
        if normalizar_rotulo(observados[indice].rotulo) == rotulo
    ]
    estritos = []
    for indice in por_rotulo:
        observado = observados[indice]
        papel, acao = _papel_acao(observado)
        if (
            observado.assinatura_formulario == esperado.assinatura_formulario
            and observado.tag == esperado.tag
            and observado.tipo == esperado.tipo
            and papel == esperado.papel
            and acao == esperado.acao
            and observado.ordem_relativa == esperado.ordem_relativa
            and observado.obrigatorio == esperado.obrigatorio
            and observado.somente_leitura == esperado.somente_leitura
            and observado.desabilitado == esperado.desabilitado
        ):
            estritos.append(indice)
    if estritos:
        return estritos
    if por_rotulo:
        return por_rotulo
    return [
        indice for indice in disponiveis
        if observados[indice].ordem_relativa == esperado.ordem_relativa
    ]


def _escolher_unico_visivel(
    indices: list[int],
    observados: tuple[ElementoInventariado, ...],
) -> tuple[int | None, set[int], bool]:
    visiveis = [indice for indice in indices if observados[indice].visivel]
    if len(visiveis) > 1:
        return None, set(indices), True
    if len(visiveis) == 1:
        escolhido = visiveis[0]
        identidade = (
            observados[escolhido].seletor_tipo,
            observados[escolhido].seletor,
        )
        duplicatas_ocultas = {
            indice for indice in indices
            if not observados[indice].visivel
            and (
                observados[indice].seletor_tipo,
                observados[indice].seletor,
            ) == identidade
        }
        return escolhido, {escolhido, *duplicatas_ocultas}, False
    if len(indices) == 1:
        return indices[0], {indices[0]}, False
    return None, set(indices), len(indices) > 1


def _comparar_elemento(
    esperado: ElementoContratoComparavel,
    observado: ElementoInventariado,
) -> list[DiferencaPortal]:
    diferencas = []
    papel, acao = _papel_acao(observado)
    dimensoes = (
        ('seletor_alterado',
         (esperado.seletor_tipo, esperado.seletor),
         (observado.seletor_tipo, observado.seletor)),
        ('formulario_alterado', esperado.assinatura_formulario,
         observado.assinatura_formulario),
        ('tag_alterada', esperado.tag, observado.tag),
        ('tipo_alterado', esperado.tipo, observado.tipo),
        ('papel_alterado', esperado.papel, papel),
        ('acao_alterada', esperado.acao, acao),
        ('rotulo_alterado', normalizar_rotulo(esperado.rotulo),
         normalizar_rotulo(observado.rotulo)),
        ('sequencia_alterada', esperado.ordem_relativa,
         observado.ordem_relativa),
        ('obrigatoriedade_alterada', esperado.obrigatorio,
         observado.obrigatorio),
        ('visibilidade_alterada', esperado.visivel, observado.visivel),
        ('somente_leitura_alterada', esperado.somente_leitura,
         observado.somente_leitura),
        ('habilitacao_alterada', esperado.desabilitado,
         observado.desabilitado),
    )
    for tipo, valor_esperado, valor_observado in dimensoes:
        if valor_esperado != valor_observado:
            diferencas.append(_diferenca(
                tipo, esperado.etapa, esperado.chave,
                valor_esperado, valor_observado))
    return diferencas


def _autoajuste_permitido(elemento: ElementoContratoComparavel) -> bool:
    chave = normalizar_rotulo(elemento.chave).lower().replace(' ', '_')
    return (
        elemento.autoajuste_seletor
        and elemento.papel not in _PAPEIS_PROIBIDOS
        and elemento.acao not in _ACOES_PROIBIDAS
        and not any(trecho in chave for trecho in _TRECHOS_CHAVE_PROIBIDOS)
    )


def comparar(
    contrato: ContratoComparavel,
    inventario: InventarioPortal,
) -> ResultadoComparacaoPortal:
    """Compara toda a estrutura antes de decidir; nunca promove parcialmente."""
    if not inventario.conhecido:
        return ResultadoComparacaoPortal(
            classificacao=DESCONHECIDA,
            diferencas=(_diferenca(
                'observacao_desconhecida', contrato.etapa, None,
                'ok', inventario.motivo),),
        )

    diferencas = []
    for tipo, esperado, observado in (
        ('host_alterado', contrato.host, inventario.host),
        ('rota_alterada', contrato.rota, inventario.rota),
        ('etapa_alterada', contrato.etapa, inventario.etapa),
    ):
        if esperado != observado:
            diferencas.append(_diferenca(
                tipo, contrato.etapa, None, esperado, observado))

    observados = inventario.elementos
    disponiveis = set(range(len(observados)))
    remapeamentos_possiveis = []

    for esperado in contrato.elementos:
        identicos = [
            indice for indice in disponiveis
            if _mesma_identidade(esperado, observados[indice])
        ]
        escolhido, consumidos, ambiguo = _escolher_unico_visivel(
            identicos, observados)
        if escolhido is None and not ambiguo:
            candidatos = _candidatos(esperado, observados, disponiveis)
            escolhido, consumidos, ambiguo = _escolher_unico_visivel(
                candidatos, observados)
            if escolhido is not None and _rotulo_ambiguo(esperado.rotulo):
                diferencas.append(_diferenca(
                    'rotulo_ambiguo', esperado.etapa, esperado.chave,
                    esperado.rotulo, observados[escolhido].rotulo))
        if ambiguo:
            diferencas.append(_diferenca(
                'ambiguidade', esperado.etapa, esperado.chave,
                esperado.seletor, len(consumidos), 'candidato não é único'))
            disponiveis.difference_update(consumidos)
            continue
        if escolhido is None:
            diferencas.append(_diferenca(
                'elemento_ausente', esperado.etapa, esperado.chave,
                esperado.seletor, None))
            continue

        observado = observados[escolhido]
        disponiveis.difference_update(consumidos)
        diferencas_elemento = _comparar_elemento(esperado, observado)
        diferencas.extend(diferencas_elemento)
        if (
            len(diferencas_elemento) == 1
            and diferencas_elemento[0].tipo == 'seletor_alterado'
            and _autoajuste_permitido(esperado)
            and not _rotulo_ambiguo(esperado.rotulo)
        ):
            remapeamentos_possiveis.append(RemapeamentoSeletor(
                chave=esperado.chave,
                seletor_tipo_anterior=esperado.seletor_tipo,
                seletor_anterior=esperado.seletor,
                seletor_tipo_novo=observado.seletor_tipo,
                seletor_novo=observado.seletor,
            ))

    for indice in sorted(disponiveis):
        observado = observados[indice]
        diferencas.append(_diferenca(
            'elemento_novo', contrato.etapa, None, None,
            observado.seletor or observado.rotulo))

    if not diferencas:
        return ResultadoComparacaoPortal(classificacao=COMPATIVEL)
    if (
        len(diferencas) == 1
        and diferencas[0].tipo == 'seletor_alterado'
        and len(remapeamentos_possiveis) == 1
    ):
        return ResultadoComparacaoPortal(
            classificacao=AUTOATIVAVEL,
            diferencas=tuple(diferencas),
            remapeamentos=tuple(remapeamentos_possiveis),
        )
    return ResultadoComparacaoPortal(
        classificacao=REVISAO,
        diferencas=tuple(diferencas),
    )


class BaselineNaoObservavelError(ValueError):
    """A tela observada não sustenta a baseline declarada."""

    def __init__(self, mensagem, faltantes=()):
        super().__init__(mensagem)
        self.faltantes = tuple(faltantes)


def montar_baseline_observada(
    declaracao: ContratoComparavel,
    inventario,
) -> ContratoComparavel:
    """Junta a DECLARAÇÃO (identidade e política) com a OBSERVAÇÃO (os fatos).

    Uma baseline escrita à mão não tem como saber a assinatura do formulário nem
    a ordem relativa dos controles — só a página sabe. Inventar esses valores faz
    a primeira comparação acusar `formulario_alterado` em todo elemento e o
    portal ficar bloqueado para sempre, mesmo com os seletores certos (achado
    real de 2026-09-09).

    O que continua vindo do código, e nunca do DOM:

    - **quais** controles existem (`chave`) e onde entram (`host`/`rota`/`etapa`);
    - **qual** o papel e a ação esperados de cada um;
    - **onde** ele deve estar (`seletor`);
    - **o que pode autoajustar** depois (`autoajuste_seletor`).

    O que vem da observação são só os fatos daqueles controles: assinatura do
    formulário, ordem relativa, tag, tipo, rótulo e flags de estado.

    Se um controle declarado não está na tela, ou está com outro papel, **nada é
    montado**: levanta `BaselineNaoObservavelError` com as chaves que não
    fecharam. Aprovar uma tela que não é a esperada é exatamente o erro que o
    contrato existe para impedir.
    """
    if (inventario.host, inventario.rota) != (declaracao.host, declaracao.rota):
        raise BaselineNaoObservavelError(
            'A tela observada não é a rota declarada para este portal.')

    por_identidade = {
        (item.seletor_tipo, item.seletor): item for item in inventario.elementos
    }

    elementos = []
    faltantes = []
    for esperado in declaracao.elementos:
        observado = por_identidade.get((esperado.seletor_tipo, esperado.seletor))
        if observado is None:
            faltantes.append(esperado.chave)
            continue
        papel, acao = _papel_acao(observado)
        if (papel, acao) != (esperado.papel, esperado.acao):
            # O controle existe naquele endereço, mas não é o que se esperava
            # dele — tratar como encontrado aprovaria um botão no lugar de um
            # campo, e a política de autoajuste é por papel.
            faltantes.append(esperado.chave)
            continue
        elementos.append(ElementoContratoComparavel(
            chave=esperado.chave,
            etapa=esperado.etapa,
            papel=papel,
            acao=acao,
            seletor_tipo=esperado.seletor_tipo,
            seletor=esperado.seletor,
            tag=observado.tag,
            tipo=observado.tipo,
            rotulo=observado.rotulo,
            assinatura_formulario=observado.assinatura_formulario,
            ordem_relativa=observado.ordem_relativa,
            obrigatorio=observado.obrigatorio,
            visivel=observado.visivel,
            somente_leitura=observado.somente_leitura,
            desabilitado=observado.desabilitado,
            autoajuste_seletor=esperado.autoajuste_seletor,
        ))

    if faltantes:
        raise BaselineNaoObservavelError(
            'A tela observada não tem os controles declarados: '
            + ', '.join(faltantes),
            faltantes=faltantes)
    return ContratoComparavel(
        host=declaracao.host,
        rota=declaracao.rota,
        etapa=declaracao.etapa,
        elementos=tuple(elementos),
    )
