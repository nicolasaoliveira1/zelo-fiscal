"""Registro neutro dos adaptadores de contratos de portais."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from app.services.contrato_portal_protocol import AdaptadorPortal


@dataclass(frozen=True)
class AdaptadorRecon:
    """Declara como um alvo pode ser observado com segurança."""

    fluxo: str
    alvo: str
    nome: str
    chave_health: str
    observar: Callable[[Any, Any], Any]
    lock: Any = None
    recon_passivo_seguro: bool = False
    # Baseline declarada no código, nunca derivada do DOM: a primeira versão
    # ativa é decisão humana revisável (AC-01.5).
    definicao: Callable[[], Any] | None = None


class RegistroAdaptadores:
    """Índice imutável por `(fluxo, alvo)`, sem conhecer portais concretos."""

    def __init__(self, adaptadores: Iterable[AdaptadorPortal] = ()):
        self._adaptadores = tuple(adaptadores)
        self._por_chave = {}
        for adaptador in self._adaptadores:
            chave = (adaptador.fluxo, adaptador.alvo)
            if chave in self._por_chave:
                raise ValueError(
                    'Já existe adaptador para o fluxo e alvo informados.')
            self._por_chave[chave] = adaptador

    def todos(self) -> tuple[AdaptadorPortal, ...]:
        """Devolve todos os adaptadores na ordem declarada."""
        return self._adaptadores

    def seguros(self) -> tuple[AdaptadorPortal, ...]:
        """Devolve apenas alvos que declararam recon passivo seguro."""
        return tuple(
            adaptador for adaptador in self._adaptadores
            if adaptador.recon_passivo_seguro
        )

    def por_alvo(self, fluxo: str, alvo: str) -> AdaptadorPortal | None:
        """Retorna `None` explicitamente quando não há adaptador registrado."""
        return self._por_chave.get((fluxo, alvo))
