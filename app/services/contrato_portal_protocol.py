"""Tipos compartilhados pelo recon adaptativo dos portais de certidões.

Este módulo descreve o contrato entre um adaptador de portal e o núcleo de
comparação. Não conhece Selenium, um portal específico ou uma política de
emissão: cada adaptador é responsável por observar a sua tela e devolver os
metadados já normalizados.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable


@dataclass(frozen=True)
class FormularioInventariado:
    """Metadados sanitizados de um formulário observado."""

    id: str
    name: str
    metodo: str
    acao_caminho: str
    ordem: int
    assinatura: str


@dataclass(frozen=True)
class ElementoInventariado:
    """Metadados sanitizados de um controle da etapa observada."""

    tag: str
    tipo: str
    id: str
    name: str
    rotulo: str
    seletor_tipo: str
    seletor: str
    assinatura_formulario: str
    ordem_relativa: int
    obrigatorio: bool
    desabilitado: bool
    somente_leitura: bool
    visivel: bool
    href_caminho: str = ''


@dataclass(frozen=True)
class InventarioPortal:
    """Resultado normalizado de uma observação passiva."""

    host: str
    rota: str
    etapa: str
    formularios: tuple[FormularioInventariado, ...] = ()
    elementos: tuple[ElementoInventariado, ...] = ()
    estado: str = 'ok'
    motivo: str | None = None
    artefato_sanitizado: str = field(default='{}', repr=False)

    @classmethod
    def desconhecido(cls, etapa: str, motivo: str):
        return cls(
            host='', rota='', etapa=etapa, estado='desconhecida',
            motivo=motivo)

    @property
    def conhecido(self) -> bool:
        return self.estado == 'ok'


@runtime_checkable
class AdaptadorPortal(Protocol):
    """Interface estrutural de um alvo que o núcleo pode observar.

    Um registro representa um alvo, não uma família inteira de portais. Isso
    permite que Município/variante, FGTS e cada UF declarem políticas próprias
    sem colocar decisões de portal dentro do comparador.
    """

    fluxo: str
    alvo: str
    nome: str
    chave_health: str
    observar: Callable[[Any, Any], InventarioPortal]
    lock: Any
    recon_passivo_seguro: bool
    definicao: Callable[[], Any] | None
    criar_driver: Callable[[], Any] | None
