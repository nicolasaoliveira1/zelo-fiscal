"""Barreira única de compatibilidade antes da automação de um portal."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable, Mapping

from sqlalchemy.exc import MultipleResultsFound
from sqlalchemy.orm import selectinload

from app.models import ContratoPortal
from app.services import circuit_breaker, contrato_portal
from app.services.contrato_portal_drift import (
    AUTOATIVAVEL,
    COMPATIVEL,
    ContratoComparavel,
    ElementoContratoComparavel,
    comparar,
)
from app.services.execution_logger import log_event


class PreflightContratoPortalError(RuntimeError):
    pass


class ContratoPortalAusenteError(PreflightContratoPortalError):
    pass


class ContratoPortalBloqueadoError(PreflightContratoPortalError):
    def __init__(self, classificacao: str):
        super().__init__(
            'A estrutura do portal mudou e a emissão foi bloqueada para revisão.')
        self.classificacao = classificacao


@dataclass(frozen=True)
class ElementoSnapshotPortal:
    chave: str
    etapa: str
    papel: str
    acao: str
    seletor_tipo: str
    seletor: str


@dataclass(frozen=True)
class SnapshotContratoPortal:
    contrato_id: int
    fluxo: str
    alvo: str
    versao: int
    fingerprint: str
    host: str
    rota: str
    elementos: Mapping[str, ElementoSnapshotPortal]

    def elemento(self, chave: str) -> ElementoSnapshotPortal:
        try:
            return self.elementos[chave]
        except KeyError as erro:
            raise ContratoPortalBloqueadoError('desconhecida') from erro


def _carregar_ativo(fluxo: str, alvo: str) -> ContratoPortal:
    try:
        ativo = ContratoPortal.query.options(
            selectinload(ContratoPortal.elementos)).filter_by(
                fluxo=fluxo, alvo=alvo, estado='ativa').one_or_none()
    except MultipleResultsFound as erro:
        raise ContratoPortalAusenteError(
            'O alvo possui mais de um contrato ativo.') from erro
    if ativo is None:
        raise ContratoPortalAusenteError(
            'O alvo não possui contrato ativo revisado.')
    return ativo


def buscar_ativo(
    fluxo: str,
    alvo: str,
    *,
    obrigatorio: bool = True,
) -> ContratoPortal | None:
    """Consulta a versão ativa; ausência só é tolerada no rollout explícito."""
    try:
        return _carregar_ativo(fluxo, alvo)
    except ContratoPortalAusenteError:
        if obrigatorio:
            raise
        return None


def comparavel(contrato: ContratoPortal) -> ContratoComparavel:
    """Traduz a versão persistida para a forma que o comparador entende."""
    elementos = tuple(ElementoContratoComparavel(
        chave=item.chave,
        etapa=item.etapa,
        papel=item.papel,
        acao=item.acao,
        seletor_tipo=item.seletor_tipo,
        seletor=item.seletor,
        tag=item.tag,
        tipo=item.tipo,
        rotulo=item.rotulo,
        assinatura_formulario=item.assinatura_formulario,
        ordem_relativa=item.ordem_relativa,
        obrigatorio=item.obrigatorio,
        visivel=item.visivel,
        somente_leitura=item.somente_leitura,
        autoajuste_seletor=item.autoajuste_seletor,
    ) for item in sorted(
        contrato.elementos,
        key=lambda elemento: (
            elemento.etapa, elemento.ordem_relativa, elemento.chave)))
    etapas = {item.etapa for item in elementos}
    if len(etapas) != 1:
        raise ContratoPortalAusenteError(
            'O contrato ativo possui etapas incompatíveis com o piloto.')
    return ContratoComparavel(
        host=contrato.host,
        rota=contrato.rota,
        etapa=next(iter(etapas)),
        elementos=elementos,
    )


def _snapshot(contrato: ContratoPortal) -> SnapshotContratoPortal:
    elementos = {
        item.chave: ElementoSnapshotPortal(
            chave=item.chave,
            etapa=item.etapa,
            papel=item.papel,
            acao=item.acao,
            seletor_tipo=item.seletor_tipo,
            seletor=item.seletor,
        )
        for item in contrato.elementos
    }
    return SnapshotContratoPortal(
        contrato_id=contrato.id,
        fluxo=contrato.fluxo,
        alvo=contrato.alvo,
        versao=contrato.versao,
        fingerprint=contrato.fingerprint,
        host=contrato.host,
        rota=contrato.rota,
        elementos=MappingProxyType(elementos),
    )


def executar(
    *,
    fluxo: str,
    alvo: str,
    observar: Callable[[ContratoPortal], object],
    alvo_breaker: str | None = None,
    execution_id: str | None = None,
    contrato_ativo: ContratoPortal | None = None,
) -> SnapshotContratoPortal:
    """Observa uma vez, decide e devolve somente uma versão pronta para fixar."""
    ativo = contrato_ativo or _carregar_ativo(fluxo, alvo)
    if (
        ativo.fluxo != fluxo
        or ativo.alvo != alvo
        or ativo.estado != 'ativa'
    ):
        raise ContratoPortalAusenteError(
            'O contrato fornecido não é a versão ativa do alvo.')
    inventario = observar(ativo)
    resultado = comparar(comparavel(ativo), inventario)

    if resultado.classificacao == AUTOATIVAVEL:
        ativo = contrato_portal.autoativar(
            ativo.id, resultado, fingerprint_base=ativo.fingerprint)
    elif resultado.classificacao != COMPATIVEL:
        contrato_portal.registrar_incidente(
            ativo.id,
            resultado,
            artefato_sanitizado=inventario.artefato_sanitizado or None,
        )
        mensagem = 'Estrutura do portal incompatível com o contrato ativo.'
        if alvo_breaker:
            circuit_breaker.registrar_falha(alvo_breaker, mensagem)
        log_event(
            'contrato_portal_bloqueado', level='WARNING',
            fluxo=fluxo, alvo=alvo, versao=ativo.versao,
            fingerprint=ativo.fingerprint[:12],
            resultado=resultado.classificacao, execution_id=execution_id,
        )
        raise ContratoPortalBloqueadoError(resultado.classificacao)

    snapshot = _snapshot(ativo)
    log_event(
        'contrato_portal_preflight', fluxo=fluxo, alvo=alvo,
        versao=snapshot.versao, fingerprint=snapshot.fingerprint[:12],
        resultado=resultado.classificacao, execution_id=execution_id,
    )
    return snapshot


def obter_ou_fixar_no_lote(
    estado: dict,
    executar_preflight: Callable[[], SnapshotContratoPortal],
) -> SnapshotContratoPortal:
    """Fixa o primeiro snapshot; retries e itens seguintes recebem o mesmo objeto."""
    fixado = estado.get('contrato_snapshot')
    if fixado is not None:
        return fixado
    snapshot = executar_preflight()
    estado['contrato_snapshot'] = snapshot
    return snapshot
