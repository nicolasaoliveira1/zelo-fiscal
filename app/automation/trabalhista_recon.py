"""Inventário estrutural e passivo do portal Trabalhista.

O observador recebe um driver já posicionado e lê somente metadados declarados.
Não navega, não preenche campos, não resolve captcha e não executa ações.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from selenium.common.exceptions import WebDriverException


ESPERA_ENTRE_FOTOS_S = 0.4
MAX_FORMULARIOS = 50
MAX_ELEMENTOS = 300
MAX_ROTULO = 500
MAX_IDENTIFICADOR = 500


@dataclass(frozen=True)
class FormularioInventariado:
    id: str
    name: str
    metodo: str
    acao_caminho: str
    ordem: int
    assinatura: str


@dataclass(frozen=True)
class ElementoInventariado:
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


JS_INVENTARIO_TRABALHISTA = r"""
return (function () {
  function textoEstatico(elemento) {
    return String((elemento && elemento.textContent) || '')
      .replace(/\s+/g, ' ').trim();
  }

  function rotuloDo(elemento) {
    var id = elemento.getAttribute('id') || '';
    var labels = document.getElementsByTagName('label');
    var i;
    if (id) {
      for (i = 0; i < labels.length; i += 1) {
        if (labels[i].getAttribute('for') === id) {
          return textoEstatico(labels[i]);
        }
      }
    }
    var ancestral = elemento.closest ? elemento.closest('label') : null;
    if (ancestral) { return textoEstatico(ancestral); }
    return String(
      elemento.getAttribute('aria-label')
      || elemento.getAttribute('placeholder')
      || elemento.getAttribute('alt')
      || elemento.getAttribute('title')
      || textoEstatico(elemento)
      || ''
    ).replace(/\s+/g, ' ').trim();
  }

  function visivel(elemento) {
    if (elemento.hasAttribute('hidden')) { return false; }
    var estilo = window.getComputedStyle ? window.getComputedStyle(elemento) : null;
    if (estilo && (estilo.display === 'none' || estilo.visibility === 'hidden')) {
      return false;
    }
    var caixa = elemento.getBoundingClientRect
      ? elemento.getBoundingClientRect() : {width: 1, height: 1};
    return !(caixa.width === 0 && caixa.height === 0);
  }

  function caminhoSeguro(endereco) {
    if (!endereco) { return {caminho: '', tem_query: false}; }
    try {
      var url = new URL(endereco, document.location.origin);
      return {caminho: String(url.pathname || ''), tem_query: !!url.search};
    } catch (e) {
      return {caminho: '', tem_query: true};
    }
  }

  var formulariosDom = Array.prototype.slice.call(
    document.querySelectorAll('form'));
  var formularios = formulariosDom.map(function (formulario, ordem) {
    var acao = caminhoSeguro(formulario.getAttribute('action') || '');
    return {
      id: formulario.getAttribute('id') || '',
      name: formulario.getAttribute('name') || '',
      metodo: String(formulario.getAttribute('method') || 'get').toLowerCase(),
      acao_caminho: acao.caminho,
      acao_tem_query: acao.tem_query,
      ordem: ordem
    };
  });

  var elementos = [];
  var candidatos = document.querySelectorAll('a, input, select, textarea, button, img');
  for (var ordem = 0; ordem < candidatos.length; ordem += 1) {
    var elemento = candidatos[ordem];
    var tag = String(elemento.tagName || '').toLowerCase();
    var tipo = String(elemento.getAttribute('type') || tag).toLowerCase();
    if (tag === 'input' && tipo === 'hidden') { continue; }
    if (tag === 'img') {
      var marca = [
        elemento.getAttribute('id'), elemento.getAttribute('name'),
        elemento.getAttribute('class'), elemento.getAttribute('alt'),
        elemento.getAttribute('title')
      ].join(' ').toLowerCase();
      if (marca.indexOf('captcha') === -1) { continue; }
    }
    var formulario = elemento.closest ? elemento.closest('form') : null;
    var formularioOrdem = formulario ? formulariosDom.indexOf(formulario) : -1;
    var href = tag === 'a'
      ? caminhoSeguro(elemento.getAttribute('href') || '')
      : {caminho: '', tem_query: false};
    elementos.push({
      tag: tag,
      tipo: tipo,
      id: elemento.getAttribute('id') || '',
      name: elemento.getAttribute('name') || '',
      rotulo: rotuloDo(elemento),
      formulario_ordem: formularioOrdem,
      ordem: ordem,
      obrigatorio: elemento.hasAttribute('required')
        || elemento.getAttribute('aria-required') === 'true',
      desabilitado: elemento.hasAttribute('disabled'),
      somente_leitura: elemento.hasAttribute('readonly'),
      visivel: visivel(elemento),
      href_caminho: href.caminho,
      href_tem_query: href.tem_query
    });
  }

  var estruturaInacessivel = !!document.querySelector('iframe');
  var todos = document.querySelectorAll('*');
  for (var n = 0; n < todos.length && !estruturaInacessivel; n += 1) {
    if (todos[n].shadowRoot || String(todos[n].tagName || '').indexOf('-') !== -1) {
      estruturaInacessivel = true;
    }
  }

  return {
    estado: 'ok',
    protocolo: String(document.location.protocol || ''),
    host: String(document.location.hostname || ''),
    porta: String(document.location.port || ''),
    caminho: String(document.location.pathname || ''),
    tem_query: !!document.location.search,
    tem_fragmento: !!document.location.hash,
    estrutura_inacessivel: estruturaInacessivel,
    formularios: formularios,
    elementos: elementos
  };
})();
"""


class _InventarioInvalidoError(ValueError):
    pass


def _texto(valor: Any, *, limite: int = MAX_IDENTIFICADOR) -> str:
    texto = ' '.join(str(valor or '').split())
    if len(texto) > limite:
        raise _InventarioInvalidoError('limite de metadado excedido')
    return texto


def _host_canonico(host: Any, porta: Any = '') -> str:
    texto = str(host or '').strip().rstrip('.').lower()
    try:
        canonico = texto.encode('idna').decode('ascii')
    except UnicodeError as erro:
        raise _InventarioInvalidoError('host inválido') from erro
    porta_texto = str(porta or '').strip()
    if porta_texto and porta_texto != '443':
        canonico = f'{canonico}:{porta_texto}'
    return canonico


def _assinatura_formulario(dados: dict[str, Any]) -> str:
    estrutura = {
        'id': dados['id'],
        'name': dados['name'],
        'metodo': dados['metodo'],
        'acao_caminho': dados['acao_caminho'],
        'ordem': dados['ordem'],
    }
    bruto = json.dumps(
        estrutura, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(bruto).hexdigest()


def _carregar_payload(resposta: Any) -> dict[str, Any]:
    if isinstance(resposta, str):
        try:
            resposta = json.loads(resposta)
        except (TypeError, ValueError) as erro:
            raise _InventarioInvalidoError('payload inválido') from erro
    if not isinstance(resposta, dict):
        raise _InventarioInvalidoError('payload inválido')
    return resposta


def _normalizar_payload(
    resposta: Any,
    *,
    host_esperado: str,
    rota_esperada: str,
    etapa: str,
) -> InventarioPortal:
    payload = _carregar_payload(resposta)
    if payload.get('estado') != 'ok':
        raise _InventarioInvalidoError('observação inconclusiva')
    if payload.get('estrutura_inacessivel'):
        raise _InventarioInvalidoError('estrutura inacessível')
    if payload.get('tem_query') or payload.get('tem_fragmento'):
        raise _InventarioInvalidoError('rota contém query ou fragmento não aprovado')
    if str(payload.get('protocolo') or '').lower() != 'https:':
        raise _InventarioInvalidoError('protocolo não aprovado')

    host = _host_canonico(payload.get('host'), payload.get('porta'))
    if host != _host_canonico(host_esperado):
        raise _InventarioInvalidoError('host diferente do aprovado')
    rota = str(payload.get('caminho') or '')
    if rota != rota_esperada:
        raise _InventarioInvalidoError('rota diferente da aprovada')

    formularios_payload = payload.get('formularios')
    elementos_payload = payload.get('elementos')
    if not isinstance(formularios_payload, list) or not isinstance(elementos_payload, list):
        raise _InventarioInvalidoError('payload inválido')
    if len(formularios_payload) > MAX_FORMULARIOS:
        raise _InventarioInvalidoError('limite de formulários excedido')
    if len(elementos_payload) > MAX_ELEMENTOS:
        raise _InventarioInvalidoError('limite de elementos excedido')

    formularios = []
    for ordem, item in enumerate(formularios_payload):
        if not isinstance(item, dict) or item.get('acao_tem_query'):
            raise _InventarioInvalidoError('formulário com ação não aprovada')
        dados = {
            'id': _texto(item.get('id')),
            'name': _texto(item.get('name')),
            'metodo': _texto(item.get('metodo'), limite=10).lower(),
            'acao_caminho': _texto(item.get('acao_caminho')),
            'ordem': int(item.get('ordem', ordem)),
        }
        formularios.append(FormularioInventariado(
            **dados, assinatura=_assinatura_formulario(dados)))

    assinatura_sem_formulario = hashlib.sha256(
        b'sem-formulario').hexdigest()
    elementos = []
    for ordem, item in enumerate(elementos_payload):
        if not isinstance(item, dict) or item.get('href_tem_query'):
            raise _InventarioInvalidoError('elemento com destino não aprovado')
        formulario_ordem = int(item.get('formulario_ordem', -1))
        if formulario_ordem < -1 or formulario_ordem >= len(formularios):
            raise _InventarioInvalidoError('estrutura de formulário inválida')
        identificador = _texto(item.get('id'))
        nome = _texto(item.get('name'))
        seletor_tipo = 'id' if identificador else ('name' if nome else 'nenhum')
        seletor = identificador or nome
        elementos.append(ElementoInventariado(
            tag=_texto(item.get('tag'), limite=30).lower(),
            tipo=_texto(item.get('tipo'), limite=50).lower(),
            id=identificador,
            name=nome,
            rotulo=_texto(item.get('rotulo'), limite=MAX_ROTULO),
            seletor_tipo=seletor_tipo,
            seletor=seletor,
            assinatura_formulario=(
                formularios[formulario_ordem].assinatura
                if formulario_ordem >= 0 else assinatura_sem_formulario),
            ordem_relativa=int(item.get('ordem', ordem)),
            obrigatorio=bool(item.get('obrigatorio', False)),
            desabilitado=bool(item.get('desabilitado', False)),
            somente_leitura=bool(item.get('somente_leitura', False)),
            visivel=bool(item.get('visivel', False)),
            href_caminho=_texto(item.get('href_caminho')),
        ))

    estrutura = {
        'host': host,
        'rota': rota,
        'etapa': etapa,
        'formularios': [asdict(item) for item in formularios],
        'elementos': [asdict(item) for item in elementos],
    }
    artefato = json.dumps(
        estrutura, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'))
    return InventarioPortal(
        host=host, rota=rota, etapa=etapa,
        formularios=tuple(formularios), elementos=tuple(elementos),
        artefato_sanitizado=artefato)


def _assinatura_inventario(inventario: InventarioPortal) -> str:
    return hashlib.sha256(
        inventario.artefato_sanitizado.encode('utf-8')).hexdigest()


def inventariar(
    driver,
    *,
    host_esperado: str,
    rota_esperada: str,
    etapa: str,
) -> InventarioPortal:
    """Coleta duas fotos passivas e só aceita um DOM estruturalmente estável."""
    try:
        primeira = _normalizar_payload(
            driver.execute_script(JS_INVENTARIO_TRABALHISTA),
            host_esperado=host_esperado,
            rota_esperada=rota_esperada,
            etapa=etapa)
        # Respiro entre as fotos: coladas, as duas perdem igualmente o
        # conteudo que chega depois do onload (captcha buscado por XHR, por
        # exemplo) e o par identico-porem-incompleto passa como "estavel" —
        # esvaziando o teste que ele existe para fazer.
        time.sleep(ESPERA_ENTRE_FOTOS_S)
        segunda = _normalizar_payload(
            driver.execute_script(JS_INVENTARIO_TRABALHISTA),
            host_esperado=host_esperado,
            rota_esperada=rota_esperada,
            etapa=etapa)
        if _assinatura_inventario(primeira) != _assinatura_inventario(segunda):
            return InventarioPortal.desconhecido(
                etapa, 'DOM instável durante a observação')
        return segunda
    except WebDriverException:
        return InventarioPortal.desconhecido(
            etapa, 'falha do driver durante a observação')
    except (KeyError, TypeError, ValueError, _InventarioInvalidoError) as erro:
        return InventarioPortal.desconhecido(etapa, str(erro))
