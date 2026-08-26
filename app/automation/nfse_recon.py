"""Inventário estrutural e sanitizado das etapas da NFS-e.

Este módulo observa somente metadados declarados no DOM. Ele não recebe a nota,
não acessa banco de dados e não transforma a tela observada em uma sessão
fiscal. O HTML produzido aqui é reconstruído a partir das dataclasses seguras.
"""

from __future__ import annotations

import json
import time
import unicodedata
from dataclasses import dataclass, field
from html import escape
from typing import Any, Iterable
from urllib.parse import urlparse

from selenium.common.exceptions import WebDriverException


MAX_CONTROLES_ETAPA = 500
MAX_OPCOES_CONTROLE = 5000
MAX_ROTULO = 500
MAX_IDENTIFICADOR = 200
MAX_VALOR_OPCAO = 190

_ETAPAS_POR_PATH = {
    "/EmissorNacional/DPS/Pessoas": "pessoas",
    "/EmissorNacional/DPS/Servico": "servico",
    "/EmissorNacional/DPS/Tributacao": "tributacao",
    "/EmissorNacional/DPS/EmitirNFSe": "revisao",
}
_CLASSES_FUNCIONAIS = frozenset({"select2-hidden-accessible", "form-chosen"})
_MARCADORES_CARREGAMENTO = (
    '[aria-busy="true"]',
    ".loading",
    ".carregando",
    ".select2-results__option--loading",
    '[data-loading="true"]',
)


class InventarioExcedidoError(RuntimeError):
    """Indica que o inventário ultrapassou um limite de segurança."""


class InventarioInconclusivoError(RuntimeError):
    """Indica que um metadado não permite uma observação segura."""


@dataclass(frozen=True)
class OpcaoInventariada:
    """Valor e rótulo declarados por uma opção, sem estado de seleção."""

    valor: str
    rotulo: str
    ordem: int = field(default=0, compare=False, repr=False)


@dataclass(frozen=True)
class ControleInventariado:
    """Metadados estruturais de um controle da etapa."""

    chave_semantica: str
    etapa: str
    tag: str
    tipo: str
    id: str
    name: str
    rotulo: str
    seletor_tipo: str
    seletor: str
    obrigatorio: bool
    desabilitado: bool
    somente_leitura: bool
    visivel: bool
    interacao: str
    classes_funcionais: tuple[str, ...] = ()
    opcoes: tuple[OpcaoInventariada, ...] = ()
    ordem_relativa: int = field(default=0, compare=False, repr=False)


@dataclass(frozen=True)
class InventarioEtapa:
    """Resultado de uma observação, que pode ser conhecida ou desconhecida."""

    etapa: str
    controles: tuple[ControleInventariado, ...] = ()
    estado: str = "ok"
    motivo: str | None = None

    @classmethod
    def desconhecido(cls, etapa: str, motivo: str = "observação inconclusiva"):
        return cls(etapa=etapa, estado="desconhecida", motivo=motivo)

    @property
    def conhecida(self) -> bool:
        return self.estado == "ok"


def etapa_da_url(url: str) -> str | None:
    """Retorna a etapa reconhecida pelo path, ignorando querystring e fragmento."""

    try:
        caminho = urlparse(str(url)).path.rstrip("/")
    except (TypeError, ValueError):
        return None
    for trecho, etapa in _ETAPAS_POR_PATH.items():
        if caminho == trecho or caminho.endswith(trecho):
            return etapa
    return None


# O script nunca acessa propriedades de estado dos campos. `getAttribute` em
# `option` lê somente a declaração do markup, que é diferente da seleção atual.
JS_INVENTARIO_SEGURO = r"""
(function () {
  var classesPermitidas = {
    'select2-hidden-accessible': true,
    'form-chosen': true
  };

  function textoEstatico(elemento) {
    if (!elemento) { return ''; }
    return String(elemento.textContent || '').replace(/\s+/g, ' ').trim();
  }

  function rotuloDo(elemento) {
    var identificador = elemento.getAttribute('id');
    var labels = document.getElementsByTagName('label');
    var i;
    if (identificador) {
      for (i = 0; i < labels.length; i += 1) {
        if (labels[i].getAttribute('for') === identificador) {
          return textoEstatico(labels[i]);
        }
      }
    }
    var ancestral = elemento.closest ? elemento.closest('label') : null;
    if (ancestral) { return textoEstatico(ancestral); }
    var grupo = elemento.closest ? elemento.closest('fieldset') : null;
    var legenda = grupo ? grupo.querySelector('legend') : null;
    if (legenda) { return textoEstatico(legenda); }
    return (elemento.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
  }

  function visivel(elemento) {
    if (elemento.hasAttribute('hidden')) { return false; }
    if (window.getComputedStyle) {
      var estilo = window.getComputedStyle(elemento);
      if (estilo.display === 'none' || estilo.visibility === 'hidden') {
        return false;
      }
    }
    if (elemento.getBoundingClientRect) {
      var caixa = elemento.getBoundingClientRect();
      if (caixa.width === 0 && caixa.height === 0) { return false; }
    }
    return true;
  }

  function classesFuncionais(elemento) {
    var resultado = [];
    var classes = elemento.classList || [];
    for (var i = 0; i < classes.length; i += 1) {
      if (classesPermitidas[classes[i]]) { resultado.push(classes[i]); }
    }
    return resultado;
  }

  var marcadores = document.querySelectorAll(
    '[aria-busy="true"], .loading, .carregando, ' +
    '.select2-results__option--loading, [data-loading="true"]'
  );
  if (marcadores.length) { return {estado: 'carregando'}; }

  var controles = [];
  var elementos = document.querySelectorAll('input, select, textarea');
  for (var n = 0; n < elementos.length; n += 1) {
    var elemento = elementos[n];
    var tag = String(elemento.tagName || '').toLowerCase();
    var tipo = (elemento.getAttribute('type') || tag).toLowerCase();
    if (tag === 'input' && {
      hidden: true, submit: true, button: true, reset: true, file: true, image: true
    }[tipo]) { continue; }
    var opcoes = [];
    if (tag === 'select') {
      var declaracoes = elemento.querySelectorAll('option');
      for (var o = 0; o < declaracoes.length; o += 1) {
        var declaracao = declaracoes[o];
        opcoes.push({
          valor: declaracao.getAttribute('value') || '',
          rotulo: textoEstatico(declaracao)
        });
      }
    } else if (tipo === 'radio' || tipo === 'checkbox') {
      opcoes.push({
        valor: elemento.getAttribute('value') || '',
        rotulo: rotuloDo(elemento)
      });
    }
    controles.push({
      tag: tag,
      tipo: tipo,
      id: elemento.getAttribute('id') || '',
      name: elemento.getAttribute('name') || '',
      rotulo: rotuloDo(elemento),
      obrigatorio: elemento.hasAttribute('required'),
      desabilitado: elemento.hasAttribute('disabled'),
      somente_leitura: elemento.hasAttribute('readonly'),
      visivel: visivel(elemento),
      classes_funcionais: classesFuncionais(elemento),
      opcoes: opcoes
    });
  }
  return {estado: 'ok', controles: controles};
}())
"""


JS_MENSAGENS_VALIDACAO = r"""
(function () {
  var seletores = [
    '[role="alert"]',
    '.invalid-feedback',
    '.validation-summary-errors',
    '.text-danger'
  ];
  var vistos = [];
  var resultado = [];
  for (var s = 0; s < seletores.length; s += 1) {
    var elementos = document.querySelectorAll(seletores[s]);
    for (var i = 0; i < elementos.length; i += 1) {
      var texto = String(elementos[i].textContent || '').replace(/\s+/g, ' ').trim();
      if (texto && vistos.indexOf(texto) === -1) {
        vistos.push(texto);
        resultado.push(texto);
      }
    }
  }
  return resultado;
}())
"""


def _normalizar_texto(valor: Any, limite: int, nome: str) -> str:
    texto = unicodedata.normalize("NFKC", str(valor or ""))
    texto = " ".join(texto.split())
    if len(texto) > limite:
        raise InventarioInconclusivoError(
            f"inventário inconclusivo: {nome} excede o limite permitido"
        )
    return texto


def _normalizar_identificador(valor: Any) -> str:
    return _normalizar_texto(valor, MAX_IDENTIFICADOR, "identificador")


def _normalizar_opcao(valor: Any, rotulo: Any, ordem: int) -> OpcaoInventariada:
    return OpcaoInventariada(
        valor=_normalizar_texto(valor, MAX_VALOR_OPCAO, "valor de opção"),
        rotulo=_normalizar_texto(rotulo, MAX_ROTULO, "rótulo"),
        ordem=ordem,
    )


def _estado_payload(driver: Any) -> dict[str, Any] | None:
    try:
        payload = driver.execute_script(JS_INVENTARIO_SEGURO)
    except WebDriverException:
        return None
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return None
    return payload if isinstance(payload, dict) else None


def _interacao(tag: str, tipo: str, classes: tuple[str, ...]) -> str:
    if "select2-hidden-accessible" in classes:
        return "select_busca"
    if "form-chosen" in classes:
        return "chosen"
    if tag == "select":
        return "select_direto"
    if tipo in {"radio", "checkbox"}:
        return tipo
    if tag == "textarea":
        return "textarea"
    if tipo in {"text", "date", "number", "email", "tel"}:
        return "texto"
    return tipo or tag


def _chave_semantica(name: str, identificador: str, rotulo: str, tipo: str) -> str:
    if name:
        return name
    if identificador:
        return identificador
    base = f"{rotulo.casefold()}|{tipo}" if rotulo else tipo
    return _normalizar_identificador(base)


def _controle_a_dataclass(raw: dict[str, Any], etapa: str, ordem: int):
    tag = _normalizar_identificador(raw.get("tag"))
    tipo = _normalizar_identificador(raw.get("tipo"))
    identificador = _normalizar_identificador(raw.get("id"))
    name = _normalizar_identificador(raw.get("name"))
    rotulo = _normalizar_texto(raw.get("rotulo"), MAX_ROTULO, "rótulo")
    chave = _chave_semantica(name, identificador, rotulo, tipo)
    classes = tuple(
        classe
        for classe in (
            _normalizar_identificador(valor)
            for valor in (raw.get("classes_funcionais") or ())
        )
        if classe in _CLASSES_FUNCIONAIS
    )
    opcoes = tuple(
        _normalizar_opcao(
            opcao.get("valor"), opcao.get("rotulo"), indice
        )
        for indice, opcao in enumerate(raw.get("opcoes") or ())
        if isinstance(opcao, dict)
    )
    if name:
        seletor_tipo, seletor = "name", name
    elif identificador:
        seletor_tipo, seletor = "id", identificador
    else:
        seletor_tipo, seletor = "css", tag
    return ControleInventariado(
        chave_semantica=chave,
        etapa=etapa,
        tag=tag,
        tipo=tipo,
        id=identificador,
        name=name,
        rotulo=rotulo,
        seletor_tipo=seletor_tipo,
        seletor=seletor,
        obrigatorio=bool(raw.get("obrigatorio")),
        desabilitado=bool(raw.get("desabilitado")),
        somente_leitura=bool(raw.get("somente_leitura")),
        visivel=bool(raw.get("visivel")),
        interacao=_interacao(tag, tipo, classes),
        classes_funcionais=classes,
        opcoes=opcoes,
        ordem_relativa=ordem,
    )


def _agrupar_radios(controles: list[ControleInventariado]):
    agrupados: list[ControleInventariado] = []
    indices: dict[tuple[str, str], int] = {}
    for controle in controles:
        chave_grupo = (
            controle.name,
            controle.tipo,
        ) if controle.tipo in {"radio", "checkbox"} and controle.name else None
        if chave_grupo is None or chave_grupo not in indices:
            if chave_grupo is not None:
                indices[chave_grupo] = len(agrupados)
            agrupados.append(controle)
            continue
        indice = indices[chave_grupo]
        anterior = agrupados[indice]
        opcoes = anterior.opcoes + tuple(
            opcao for opcao in controle.opcoes if opcao not in anterior.opcoes
        )
        classes = tuple(dict.fromkeys(anterior.classes_funcionais + controle.classes_funcionais))
        agrupados[indice] = ControleInventariado(
            **{
                **anterior.__dict__,
                "obrigatorio": anterior.obrigatorio or controle.obrigatorio,
                "desabilitado": anterior.desabilitado and controle.desabilitado,
                "visivel": anterior.visivel or controle.visivel,
                "classes_funcionais": classes,
                "opcoes": opcoes,
            }
        )
    return agrupados


def _inventario_do_payload(payload: dict[str, Any], etapa: str) -> InventarioEtapa:
    if payload.get("estado") != "ok":
        return InventarioEtapa.desconhecido(etapa)
    raws = payload.get("controles")
    if not isinstance(raws, list):
        return InventarioEtapa.desconhecido(etapa)
    if len(raws) > MAX_CONTROLES_ETAPA:
        raise InventarioExcedidoError("inventário excede o limite de controles da etapa")
    total_opcoes = sum(
        len(raw.get("opcoes") or ())
        for raw in raws
        if isinstance(raw, dict)
    )
    if total_opcoes > MAX_OPCOES_CONTROLE:
        raise InventarioExcedidoError("inventário excede o limite de opções da etapa")
    controles = [
        _controle_a_dataclass(raw, etapa, ordem)
        for ordem, raw in enumerate(raws)
        if isinstance(raw, dict)
    ]
    return InventarioEtapa(etapa=etapa, controles=tuple(_agrupar_radios(controles)))


def inventariar(
    driver: Any,
    etapa: str,
    timeout: float = 5.0,
    intervalo: float = 0.05,
) -> InventarioEtapa:
    """Observa a etapa, aguardando um marcador de carregamento desaparecer.

    Falhas de sessão, script ou timeout não produzem inventário parcial: o
    resultado é explicitamente desconhecido. Limites estruturais continuam
    sendo erros próprios, pois indicam uma observação que deve bloquear.
    """

    if etapa not in _ETAPAS_POR_PATH.values():
        return InventarioEtapa.desconhecido(etapa, "etapa não reconhecida")
    prazo = time.monotonic() + max(0.0, timeout)
    while True:
        payload = _estado_payload(driver)
        if payload is None:
            return InventarioEtapa.desconhecido(etapa)
        if payload.get("estado") != "carregando":
            try:
                return _inventario_do_payload(payload, etapa)
            except InventarioExcedidoError:
                raise
            except InventarioInconclusivoError:
                raise
            except (AttributeError, TypeError, ValueError):
                return InventarioEtapa.desconhecido(etapa)
        if time.monotonic() >= prazo:
            return InventarioEtapa.desconhecido(etapa, "carregamento não terminou")
        time.sleep(min(max(0.0, intervalo), max(0.0, prazo - time.monotonic())))


def mensagens_validacao(driver: Any, valores_sensiveis: Iterable[str]) -> list[str]:
    """Lê mensagens de validação e descarta qualquer uma que contenha segredo conhecido."""

    sensiveis = {
        unicodedata.normalize("NFKC", str(valor)).strip().casefold()
        for valor in valores_sensiveis
        if str(valor).strip()
    }
    try:
        mensagens = driver.execute_script(JS_MENSAGENS_VALIDACAO)
    except WebDriverException:
        return []
    if isinstance(mensagens, str):
        try:
            mensagens = json.loads(mensagens)
        except (TypeError, ValueError):
            return []
    if not isinstance(mensagens, (list, tuple)):
        return []
    resultado = []
    for mensagem in mensagens:
        texto = _normalizar_texto(mensagem, MAX_ROTULO, "mensagem")
        if not texto:
            continue
        comparavel = texto.casefold()
        if any(valor in comparavel for valor in sensiveis):
            continue
        if texto not in resultado:
            resultado.append(texto)
    return resultado


def _atributo(nome: str, valor: Any) -> str:
    return f' {nome}="{escape(str(valor), quote=True)}"'


def inventario_para_html(inventario: InventarioEtapa) -> str:
    """Reconstrói um HTML seguro somente com os metadados do inventário."""

    etapa = escape(inventario.etapa, quote=True)
    estado = escape(inventario.estado, quote=True)
    partes = [
        "<!doctype html><html lang=\"pt-BR\"><head><meta charset=\"utf-8\">",
        f"<title>Inventário sanitizado — {etapa}</title></head><body>",
        f"<h1>Inventário da etapa {etapa}</h1><p data-estado=\"{estado}\">Estado: {estado}</p>",
    ]
    if inventario.motivo:
        partes.append(f"<p>{escape(inventario.motivo)}</p>")
    partes.append("<ol>")
    for controle in inventario.controles:
        partes.append(
            "<li><fieldset"
            + _atributo("data-chave", controle.chave_semantica)
            + _atributo("data-tag", controle.tag)
            + _atributo("data-tipo", controle.tipo)
            + "><legend>"
            + escape(controle.rotulo)
            + "</legend><dl>"
        )
        metadados = (
            ("id", controle.id),
            ("name", controle.name),
            ("seletor", controle.seletor),
            ("interação", controle.interacao),
            ("obrigatório", "sim" if controle.obrigatorio else "não"),
            ("desabilitado", "sim" if controle.desabilitado else "não"),
            ("somente leitura", "sim" if controle.somente_leitura else "não"),
            ("visível", "sim" if controle.visivel else "não"),
        )
        for nome, valor in metadados:
            partes.append(f"<dt>{escape(nome)}</dt><dd>{escape(str(valor))}</dd>")
        if controle.classes_funcionais:
            partes.append(
                "<dt>classes funcionais</dt><dd>"
                + escape(", ".join(controle.classes_funcionais))
                + "</dd>"
            )
        if controle.opcoes:
            partes.append("<dt>opções declaradas</dt><dd><ul>")
            for opcao in controle.opcoes:
                partes.append(
                    "<li>"
                    + escape(opcao.rotulo)
                    + " <code>"
                    + escape(opcao.valor)
                    + "</code></li>"
                )
            partes.append("</ul></dd>")
        partes.append("</dl></fieldset></li>")
    partes.extend(("</ol>", "</body></html>"))
    return "".join(partes)


__all__ = [
    "ControleInventariado",
    "InventarioEtapa",
    "InventarioExcedidoError",
    "InventarioInconclusivoError",
    "JS_INVENTARIO_SEGURO",
    "MAX_CONTROLES_ETAPA",
    "MAX_OPCOES_CONTROLE",
    "OpcaoInventariada",
    "etapa_da_url",
    "inventariar",
    "inventario_para_html",
    "mensagens_validacao",
]
