"""Inventário estrutural e sanitizado das etapas da NFS-e.

Este módulo observa somente metadados declarados no DOM. Ele não recebe a nota,
não acessa banco de dados e não transforma a tela observada em uma sessão
fiscal. O HTML produzido aqui é reconstruído a partir das dataclasses seguras.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from html import escape
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse

from selenium.common.exceptions import WebDriverException

from app.services.execution_logger import log_event


MAX_CONTROLES_ETAPA = 500
MAX_OPCOES_CONTROLE = 5000
MAX_ROTULO = 500
# Casado com a largura das colunas `chave_*` (String(100)): identificador
# maior passaria no SQLite e estouraria `DataError` no MySQL.
MAX_IDENTIFICADOR = 100
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
    revela_bloco: bool = False
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
return (function () {
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
    // O Bootstrap do portal escreve `.form-group > label` sem `for`: sem este
    // salto a maioria dos campos fica sem rotulo e o incidente vira inacionavel.
    var caixa = elemento.closest
      ? elemento.closest('.form-group, .mb-3, .form-floating')
      : null;
    var rotuloCaixa = caixa ? caixa.querySelector('label') : null;
    if (rotuloCaixa) { return textoEstatico(rotuloCaixa); }
    var grupo = elemento.closest ? elemento.closest('fieldset') : null;
    var legenda = grupo ? grupo.querySelector('legend') : null;
    if (legenda) { return textoEstatico(legenda); }
    var aria = (elemento.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
    if (aria) { return aria; }
    return (
      elemento.getAttribute('placeholder') || elemento.getAttribute('title') || ''
    ).replace(/\s+/g, ' ').trim();
  }

  // Num grupo de radio/checkbox, `rotuloDo` devolve o rotulo da PRIMEIRA opcao
  // ("Sim", "Brasil"), nunca a pergunta. O campo obrigatorio da reforma virava
  // um incidente chamado "Sim", ilegivel. A pergunta mora no `legend` do
  // fieldset ou no label do bloco, FORA do `.form-check`/`.radiobutton` de
  // cada opcao.
  function rotuloDeGrupo(elemento) {
    if (!elemento.closest) { return ''; }
    var fieldset = elemento.closest('fieldset');
    var legenda = fieldset ? fieldset.querySelector('legend') : null;
    if (legenda) { return textoEstatico(legenda); }
    var caixa = elemento.closest('.form-group, .mb-3, fieldset');
    if (!caixa) { return ''; }
    var labels = caixa.querySelectorAll('label');
    var i;
    for (i = 0; i < labels.length; i += 1) {
      if (!labels[i].closest('.form-check, .form-check-inline, .radiobutton, .checkbox')
          && !labels[i].getAttribute('for')) {
        return textoEstatico(labels[i]);
      }
    }
    for (i = 0; i < labels.length; i += 1) {
      if (!labels[i].closest('.form-check, .form-check-inline, .radiobutton, .checkbox')) {
        return textoEstatico(labels[i]);
      }
    }
    return '';
  }

  // Checkbox que revela um bloco ("Informar endereco") e botao, nao dado.
  function revelaBloco(elemento) {
    return !!(
      elemento.getAttribute('data-bs-toggle')
      || elemento.getAttribute('data-toggle')
    );
  }

  // A validacao unobtrusive do ASP.NET declara exigencia em `data-val-required`,
  // nunca no atributo `required`. E a maior parte da tela nao declara nem isso:
  // marca a exigencia com asterisco no rotulo, unico sinal que o operador ve.
  function exigido(elemento, rotulo) {
    if (elemento.hasAttribute('required')) { return true; }
    if (elemento.hasAttribute('data-val-required')) { return true; }
    if (elemento.getAttribute('aria-required') === 'true') { return true; }
    return /\*\s*$/.test(rotulo || '');
  }

  // O Chosen e o select2 desenham a UI real e injetam campos proprios (a caixa
  // de busca do dropdown). Sao cromo do plugin: nao tem `name`, nao vao no POST
  // e nao fazem parte do documento fiscal.
  function cromoDePlugin(elemento) {
    if (!elemento.closest) { return false; }
    return !!elemento.closest(
      '.chosen-container, .chosen-drop, .select2-container, .select2-dropdown'
    );
  }

  function pintado(elemento) {
    if (!elemento) { return false; }
    if (elemento.hasAttribute && elemento.hasAttribute('hidden')) { return false; }
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

  // O portal desenha radio e checkbox com um `<span class="cr">` e esconde o
  // `<input>` atras. Medir o input diz "invisivel" para um controle que o
  // operador ve e clica — e some justamente com os obrigatorios da tela.
  function visivel(elemento) {
    if (pintado(elemento)) { return true; }
    var tag = String(elemento.tagName || '').toLowerCase();
    var tipo = (elemento.getAttribute('type') || tag).toLowerCase();
    if (tipo !== 'radio' && tipo !== 'checkbox') { return false; }
    if (!elemento.closest) { return false; }
    return pintado(elemento.closest(
      'label, .radiobutton, .form-check, .radio-options, .checkbox'
    ));
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
  // Overlay de loader costuma existir no markup o tempo todo, oculto. So o
  // marcador visivel indica que a tela ainda esta carregando.
  for (var m = 0; m < marcadores.length; m += 1) {
    if (visivel(marcadores[m])) { return {estado: 'carregando'}; }
  }

  var controles = [];
  var elementos = document.querySelectorAll('input, select, textarea');
  for (var n = 0; n < elementos.length; n += 1) {
    var elemento = elementos[n];
    var tag = String(elemento.tagName || '').toLowerCase();
    var tipo = (elemento.getAttribute('type') || tag).toLowerCase();
    if (tag === 'input' && {
      hidden: true, submit: true, button: true, reset: true, file: true, image: true
    }[tipo]) { continue; }
    if (cromoDePlugin(elemento)) { continue; }
    // Controle sem `name` e sem `id` nao e enderecavel nem submetido: nao ha o
    // que contratar nele.
    if (!elemento.getAttribute('name') && !elemento.getAttribute('id')) { continue; }
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
    var rotuloOpcao = rotuloDo(elemento);
    var agrupado = tipo === 'radio' || tipo === 'checkbox';
    var rotulo = agrupado ? (rotuloDeGrupo(elemento) || rotuloOpcao) : rotuloOpcao;
    controles.push({
      tag: tag,
      tipo: tipo,
      id: elemento.getAttribute('id') || '',
      name: elemento.getAttribute('name') || '',
      // O asterisco e marcador de exigencia, nao parte do nome do campo.
      rotulo: rotulo.replace(/\s*\*\s*$/, ''),
      revela_bloco: revelaBloco(elemento),
      obrigatorio: exigido(elemento, rotulo),
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
return (function () {
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


def _interacao(tag: str, tipo: str, classes: tuple[str, ...], revela=False) -> str:
    # Controle que revela um bloco e botao, nao dado: perguntar "origem do
    # valor" para ele nao faz sentido.
    if revela and tipo in {"radio", "checkbox"}:
        return "acao"
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
        interacao=_interacao(tag, tipo, classes, bool(raw.get("revela_bloco"))),
        revela_bloco=bool(raw.get("revela_bloco")),
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
        return InventarioEtapa.desconhecido(
            etapa, "a observação da tela não terminou em estado utilizável"
        )
    raws = payload.get("controles")
    if not isinstance(raws, list):
        return InventarioEtapa.desconhecido(
            etapa, "a observação da tela não trouxe a lista de controles"
        )
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
            return InventarioEtapa.desconhecido(
                etapa, "a janela do portal não respondeu à observação"
            )
        if payload.get("estado") != "carregando":
            try:
                return _inventario_do_payload(payload, etapa)
            except InventarioExcedidoError:
                raise
            except InventarioInconclusivoError:
                raise
            except (AttributeError, TypeError, ValueError):
                return InventarioEtapa.desconhecido(
                    etapa, "a observação da tela veio em formato inesperado"
                )
        if time.monotonic() >= prazo:
            return InventarioEtapa.desconhecido(etapa, "carregamento não terminou")
        time.sleep(min(max(0.0, intervalo), max(0.0, prazo - time.monotonic())))


# ATENÇÃO — este é o único script da recon que toca `.value`, e o contrato dele
# é estreito de propósito: devolve SOMENTE booleanos e a identidade do controle.
# Conteúdo de campo é dado do cliente e nunca sai daqui. O inventário estrutural
# (`JS_INVENTARIO_SEGURO`) continua proibido de ler estado, e é ele que alimenta
# a comparação; este aqui só responde "tem alguma coisa escrito?".
JS_PREENCHIMENTO_SEGURO = r"""
return (function () {
  function cheio(elemento) {
    var tag = String(elemento.tagName || '').toLowerCase();
    var tipo = (elemento.getAttribute('type') || tag).toLowerCase();
    if (tipo === 'radio' || tipo === 'checkbox') { return elemento.checked === true; }
    // So o comprimento sai daqui: o texto em si nunca e lido nem devolvido.
    return String(elemento.value || '').trim().length > 0;
  }

  var resultado = [];
  var elementos = document.querySelectorAll('input, select, textarea');
  for (var n = 0; n < elementos.length; n += 1) {
    var elemento = elementos[n];
    var identificador = elemento.getAttribute('id') || '';
    var nome = elemento.getAttribute('name') || '';
    if (!identificador && !nome) { continue; }
    if (elemento.closest && elemento.closest(
      '.chosen-container, .chosen-drop, .select2-container, .select2-dropdown'
    )) { continue; }
    resultado.push({
      id: identificador,
      name: nome,
      preenchido: cheio(elemento) === true
    });
  }
  return resultado;
}())
"""


def preenchimento(driver: Any) -> dict[str, bool]:
    """Diz quais controles têm algo escrito, sem jamais devolver o que está escrito.

    A chave é a identidade do controle (`name` e `id`, as duas formas do portal);
    o valor é um booleano. Qualquer coisa que não seja booleano é descartada — o
    contrato do script é estreito, e a checagem aqui é a segunda tranca.
    """

    try:
        bruto = driver.execute_script(JS_PREENCHIMENTO_SEGURO)
    except WebDriverException:
        return {}
    if isinstance(bruto, str):
        try:
            bruto = json.loads(bruto)
        except (TypeError, ValueError):
            return {}
    if not isinstance(bruto, (list, tuple)):
        return {}
    estado: dict[str, bool] = {}
    for item in bruto:
        if not isinstance(item, dict):
            continue
        marca = item.get("preenchido")
        if not isinstance(marca, bool):
            continue
        for chave in (item.get("name"), item.get("id")):
            texto = _normalizar_identificador(chave)
            if texto:
                estado[texto] = marca
    return estado


def rascunho_da_url(url: str) -> str:
    """Token opaco do rascunho (`idr`), que identifica a DPS em edição.

    Serve só para saber que ainda é a mesma nota: o acumulado de uma DPS não
    pode ser reaproveitado em outra, sob pena de fundir duas estruturas.
    """

    try:
        consulta = parse_qs(urlparse(str(url)).query)
    except (TypeError, ValueError):
        return ""
    valores = consulta.get("idr") or ()
    return _normalizar_identificador(valores[0]) if valores else ""


def _identidades(controle: ControleInventariado) -> tuple[str, ...]:
    """Nomes pelos quais o portal permite endereçar o mesmo controle.

    O Emissor é ASP.NET MVC: o mesmo campo aparece como `name="Tomador.Inscricao"`
    e `id="Tomador_Inscricao"`. Casar por uma só das duas formas perde o campo.
    """

    vistas: list[str] = []
    for valor in (controle.name, controle.id, controle.chave_semantica):
        texto = str(valor or "").strip()
        if texto and texto not in vistas:
            vistas.append(texto)
    return tuple(vistas)


def _mesclar_controle(
    anterior: ControleInventariado, atual: ControleInventariado
) -> ControleInventariado:
    """Funde duas aparições do mesmo controle em passes diferentes."""

    # As opções de um select podem chegar por AJAX depois do primeiro passe: a
    # lista mais recente que não está vazia é a que vale. Visibilidade e
    # exigência são OR — apareceu visível uma vez, é visível na etapa.
    return ControleInventariado(
        **{
            **anterior.__dict__,
            "rotulo": atual.rotulo or anterior.rotulo,
            "obrigatorio": anterior.obrigatorio or atual.obrigatorio,
            "visivel": anterior.visivel or atual.visivel,
            "desabilitado": anterior.desabilitado and atual.desabilitado,
            "somente_leitura": anterior.somente_leitura and atual.somente_leitura,
            "revela_bloco": anterior.revela_bloco or atual.revela_bloco,
            # A posicao vem do passe mais recente: o DOM do fim da etapa e o
            # mais completo, e e nele que a ordem da tela esta certa.
            "ordem_relativa": atual.ordem_relativa,
            "classes_funcionais": tuple(
                dict.fromkeys(anterior.classes_funcionais + atual.classes_funcionais)
            ),
            "opcoes": atual.opcoes or anterior.opcoes,
        }
    )


def unir(anterior: InventarioEtapa | None, atual: InventarioEtapa | None) -> InventarioEtapa:
    """Une dois inventários da mesma etapa, porque o formulário é progressivo.

    A tela de Pessoas revela campos conforme é preenchida (o Regime de apuração
    só existe depois que a competência recarrega a página). Um instantâneo em
    t=0 não pode enxergá-los, e tratar a ausência como remoção é chute.
    """

    if anterior is None or not anterior.conhecida:
        return atual if atual is not None else InventarioEtapa.desconhecido("")
    if atual is None or not atual.conhecida:
        return anterior
    if anterior.etapa != atual.etapa:
        raise ValueError("não se une inventário de etapas diferentes")

    posicoes: dict[str, int] = {}
    resultado: list[ControleInventariado] = []
    for controle in anterior.controles + atual.controles:
        posicao = next(
            (
                posicoes[identidade]
                for identidade in _identidades(controle)
                if identidade in posicoes
            ),
            None,
        )
        if posicao is None:
            posicao = len(resultado)
            resultado.append(controle)
        else:
            resultado[posicao] = _mesclar_controle(resultado[posicao], controle)
        for identidade in _identidades(resultado[posicao]):
            posicoes[identidade] = posicao
    if len(resultado) > MAX_CONTROLES_ETAPA:
        raise InventarioExcedidoError("inventário excede o limite de controles da etapa")
    return InventarioEtapa(etapa=anterior.etapa, controles=tuple(resultado))


@dataclass(frozen=True)
class SugestaoPreenchimento:
    """O que os passes sugerem sobre um controle, sem decidir nada por ele."""

    chave: str
    rotulo: str
    interacao: str
    obrigatorio: bool
    sugestao: str
    motivo: str


class AcumuladorRecon:
    """Guarda a união dos passes da recon assistida, por rascunho e etapa.

    Não persiste nada e não sobrevive ao processo: é memória de uma sessão de
    observação. Trocar de rascunho descarta tudo, porque estrutura de outra
    nota não é evidência desta.

    Guarda também, por controle, se ele estava com algo escrito no primeiro e no
    último passe — só isso, dois booleanos. É o que permite reconhecer o bloco
    que o portal preenche sozinho quando o CPF/CNPJ do tomador entra.
    """

    def __init__(self):
        self._rascunho = None
        self._por_etapa: dict[str, InventarioEtapa] = {}
        self._passes: dict[str, int] = {}
        self._preenchimento: dict[str, dict[str, bool]] = {}

    def acumular(
        self,
        rascunho: str,
        inventario: InventarioEtapa,
        preenchimento: dict[str, bool] | None = None,
    ) -> InventarioEtapa:
        if rascunho != self._rascunho:
            self.descartar()
            self._rascunho = rascunho
        etapa = inventario.etapa
        uniao = unir(self._por_etapa.get(etapa), inventario)
        self._por_etapa[etapa] = uniao
        self._passes[etapa] = self._passes.get(etapa, 0) + 1
        if preenchimento:
            marcas = self._preenchimento.setdefault(etapa, {})
            for chave, cheio in preenchimento.items():
                if not isinstance(cheio, bool):
                    continue
                # O primeiro passe fica congelado; o ultimo e sempre o de agora.
                marcas.setdefault(f"primeiro:{chave}", cheio)
                marcas[f"ultimo:{chave}"] = cheio
        return uniao

    def passes(self, etapa: str) -> int:
        return self._passes.get(etapa, 0)

    def sugestoes(self, etapa: str) -> tuple[SugestaoPreenchimento, ...]:
        """Propõe — nunca decide — o que fazer com cada controle observado.

        Dois sinais, e só eles: o controle que estava vazio e passou a ter algo
        sem o contrato mandar preencher é candidato a `intocavel` (o portal
        preenche); o que continua vazio, é exigido e o operador consegue
        alcançar é candidato a entrar no contrato.
        """

        inventario = self._por_etapa.get(etapa)
        marcas = self._preenchimento.get(etapa) or {}
        if inventario is None or not inventario.conhecida or not marcas:
            return ()
        sugestoes = []
        for controle in inventario.controles:
            primeiro = None
            ultimo = None
            for identidade in _identidades(controle):
                if primeiro is None:
                    primeiro = marcas.get(f"primeiro:{identidade}")
                if ultimo is None:
                    ultimo = marcas.get(f"ultimo:{identidade}")
            if ultimo is None:
                continue
            alcancavel = (
                controle.visivel
                and not controle.desabilitado
                and not controle.somente_leitura
            )
            if primeiro is False and ultimo is True:
                sugestoes.append(
                    SugestaoPreenchimento(
                        chave=controle.chave_semantica,
                        rotulo=controle.rotulo,
                        interacao=controle.interacao,
                        obrigatorio=controle.obrigatorio,
                        sugestao="intocavel",
                        motivo="ficou preenchido entre os passes; o portal parece preencher",
                    )
                )
            elif ultimo is False and controle.obrigatorio and alcancavel:
                sugestoes.append(
                    SugestaoPreenchimento(
                        chave=controle.chave_semantica,
                        rotulo=controle.rotulo,
                        interacao=controle.interacao,
                        obrigatorio=True,
                        sugestao="preencher",
                        motivo="continua vazio e o portal exige",
                    )
                )
        return tuple(sugestoes)

    def descartar(self):
        self._rascunho = None
        self._por_etapa.clear()
        self._passes.clear()
        self._preenchimento.clear()


# Vocabulário de uma mensagem de validação do portal. A mensagem precisa ter ao
# menos um destes termos para ser aceita.
_TERMOS_DE_VALIDACAO = (
    "obrigat", "inv\u00e1lid", "invalid", "preench", "informe", "informar",
    "selecion", "deve ", "n\u00e3o pode", "nao pode", "maior", "menor",
    "formato", "caracter", "campo", "requer", "exced", "permitid",
)

# Siglas que aparecem legitimamente em caixa alta numa mensagem do portal.
# Qualquer OUTRA palavra em caixa alta com 3+ letras e tratada como nome
# autopreenchido (razao social do tomador) e reprova a mensagem.
_SIGLAS_ESPERADAS = frozenset({
    "NFS", "NFSE", "DPS", "CPF", "CNPJ", "NIF", "IBS", "CBS", "ISS", "ISSQN",
    "NBS", "IRRF", "CSLL", "PIS", "COFINS", "INSS", "CEP", "UF", "SN", "RPS",
    "IM", "IE", "CNAE", "LC", "ART",
})

_CAIXA_ALTA = re.compile(r"\b[A-Z\u00c0-\u00dc]{3,}\b")
_DIGITOS_LONGOS = re.compile(r"\d{6,}")


def _tem_forma_de_validacao(texto: str) -> bool:
    """A mensagem parece uma validação do portal, e só isso?

    Lista de PERMISSÃO, não de recusa: aceitar tudo que não casa com os poucos
    segredos conhecidos deixava passar o que o portal autopreenche — o nome do
    tomador não está em `valores_sensiveis`, porque campo `intocavel` resolve
    para `None`.

    Garante: nada com corrida de 6+ dígitos (documento, chave, inscrição, CEP),
    nada com `@`, e nada com palavra em caixa alta que não seja sigla conhecida
    — que é a forma da razão social vinda do portal.

    NÃO garante: nome próprio em caixa e baixa ainda passa. Por isso o destino
    continua sendo `logs/`, local e gitignored, nunca arquivo versionado.
    """

    comparavel = texto.casefold()
    if not any(termo in comparavel for termo in _TERMOS_DE_VALIDACAO):
        return False
    if "@" in texto or _DIGITOS_LONGOS.search(texto):
        return False
    return all(
        palavra in _SIGLAS_ESPERADAS for palavra in _CAIXA_ALTA.findall(texto)
    )


def mensagens_validacao(driver: Any, valores_sensiveis: Iterable[str]) -> list[str]:
    """Lê mensagens de validação e mantém somente as que têm forma de validação."""

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
    descartadas = 0
    for mensagem in mensagens:
        # Truncar, nunca levantar: este leitor roda no tratamento de erro do
        # preenchimento, e uma exceção aqui apagaria o erro original da nota e
        # derrubaria o lote. Um resumo do ASP.NET concatena várias e passa de
        # 500 fácil.
        texto = " ".join(
            unicodedata.normalize("NFKC", str(mensagem or "")).split()
        )[:MAX_ROTULO]
        if not texto:
            continue
        comparavel = texto.casefold()
        if any(valor in comparavel for valor in sensiveis):
            continue
        if not _tem_forma_de_validacao(texto):
            descartadas += 1
            continue
        if texto not in resultado:
            resultado.append(texto)
    if descartadas:
        # Descarte silencioso esconderia o motivo de uma nota falhar sem
        # evidência nenhuma; a contagem não carrega o conteúdo.
        log_event(
            "nfse_mensagem_validacao_descartada",
            level="WARNING",
            quantidade=descartadas,
        )
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
