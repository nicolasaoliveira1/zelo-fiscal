"""Adaptador de contratos para portais municipais.

O cadastro de Município continua sendo a fonte da automação municipal. Este
módulo somente traduz a configuração existente para o protocolo genérico de
contratos e devolve uma cópia dos seletores quando um snapshot ativo governa a
execução. Nunca grava `config_automacao`.

Uma entrada do registry representa uma tela: município e variante, não uma
família de portais. A observação reaproveita o dry-run existente para chegar à
fronteira segura e, depois, coleta somente metadados estruturais da tela.
"""
from __future__ import annotations

import copy
import hashlib
import json
import time
from dataclasses import asdict, dataclass
from types import SimpleNamespace
from urllib.parse import urlsplit

from selenium.common.exceptions import WebDriverException

from app.services import dryrun_municipio
from app.services.contrato_portal_drift import (
    ContratoComparavel,
    ElementoContratoComparavel,
)
from app.services.contrato_portal_preflight import (
    ContratoPortalBloqueadoError,
    SnapshotContratoPortal,
)
from app.services.contrato_portal_protocol import (
    ElementoInventariado,
    FormularioInventariado,
    InventarioPortal,
)
from app.services.contrato_portal_registry import AdaptadorRecon
from app.utils import normalizar_cidade


FLUXO_CONTRATO = 'municipal'
ETAPA_CONTRATO = 'formulario'
_ESPERA_ENTRE_FOTOS_S = 0.1
_MAX_FORMULARIOS = 50
_MAX_ELEMENTOS = 300
_MAX_TEXTO = 500
_MAX_IDENTIFICADOR = 500


_JS_INVENTARIO = r"""
return (function (seletores) {
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
    if (!endereco) { return ''; }
    try {
      return String(new URL(endereco, document.location.origin).pathname || '');
    } catch (e) {
      return '';
    }
  }

  function corresponde(elemento, item) {
    var tipo = item.tipo;
    var seletor = item.seletor || '';
    try {
      if (tipo === 'id') { return elemento.id === seletor; }
      if (tipo === 'name') { return elemento.getAttribute('name') === seletor; }
      if (tipo === 'class_name') {
        return elemento.classList && elemento.classList.contains(seletor);
      }
      if (tipo === 'css_selector') { return elemento.matches(seletor); }
      if (tipo === 'xpath') {
        var resultado = document.evaluate(
          seletor, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
        for (var i = 0; i < resultado.snapshotLength; i += 1) {
          if (resultado.snapshotItem(i) === elemento) { return true; }
        }
      }
    } catch (e) {
      return false;
    }
    return false;
  }

  function identidadeDo(elemento) {
    for (var i = 0; i < seletores.length; i += 1) {
      if (corresponde(elemento, seletores[i])) {
        return {tipo: seletores[i].tipo, seletor: seletores[i].seletor};
      }
    }
    var id = elemento.getAttribute('id') || '';
    var name = elemento.getAttribute('name') || '';
    return id
      ? {tipo: 'id', seletor: id}
      : (name ? {tipo: 'name', seletor: name}
             : {tipo: 'nenhum', seletor: ''});
  }

  var formulariosDom = Array.prototype.slice.call(
    document.querySelectorAll('form'));
  var formularios = formulariosDom.map(function (formulario, ordem) {
    return {
      id: formulario.getAttribute('id') || '',
      name: formulario.getAttribute('name') || '',
      metodo: String(formulario.getAttribute('method') || 'get').toLowerCase(),
      acao_caminho: caminhoSeguro(formulario.getAttribute('action') || ''),
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
    var identidade = identidadeDo(elemento);
    elementos.push({
      tag: tag,
      tipo: tipo,
      id: elemento.getAttribute('id') || '',
      name: elemento.getAttribute('name') || '',
      rotulo: rotuloDo(elemento),
      seletor_tipo: identidade.tipo,
      seletor: identidade.seletor,
      formulario_ordem: formularioOrdem,
      ordem: ordem,
      obrigatorio: elemento.hasAttribute('required')
        || elemento.getAttribute('aria-required') === 'true',
      desabilitado: elemento.hasAttribute('disabled'),
      somente_leitura: elemento.hasAttribute('readonly'),
      visivel: visivel(elemento),
      href_caminho: tag === 'a'
        ? caminhoSeguro(elemento.getAttribute('href') || '') : ''
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
    consulta: String(document.location.search || ''),
    fragmento: String(document.location.hash || ''),
    estrutura_inacessivel: estruturaInacessivel,
    formularios: formularios,
    elementos: elementos
  };
})(arguments[0] || []);
"""


@dataclass(frozen=True)
class ContextoMunicipal:
    """Contexto imutável de uma tela municipal/variante."""

    municipio: object
    rotulo: str
    config: dict
    view: object

    @property
    def cidade(self):
        return getattr(self.view, 'nome', None) or getattr(self.municipio, 'nome', '')

    @property
    def variante(self):
        return _slug_variante(self.rotulo)

    @property
    def alvo(self):
        cidade = normalizar_cidade(self.cidade)
        return f'municipio:{cidade.lower()}:{self.variante}'

    @property
    def chave_health(self):
        from app.services import circuit_breaker

        return (normalizar_cidade(self.cidade)
                or circuit_breaker.ALVO_MUNICIPAL_GENERICO)


def _slug_variante(rotulo):
    return normalizar_cidade(rotulo).lower() if rotulo else 'padrao'


def _texto(valor, *, limite=_MAX_IDENTIFICADOR):
    texto = ' '.join(str(valor or '').split())
    if len(texto) > limite:
        raise ValueError('metadado municipal excedeu o limite permitido')
    return texto


def _host_canonico(host, porta=''):
    texto = str(host or '').strip().rstrip('.').lower()
    if not texto:
        raise ValueError('portal municipal sem host')
    try:
        texto = texto.encode('idna').decode('ascii')
    except UnicodeError as erro:
        raise ValueError('host municipal inválido') from erro
    porta_texto = str(porta or '').strip()
    if porta_texto and porta_texto != '443':
        texto = f'{texto}:{porta_texto}'
    return texto


def _endpoint(url):
    partes = urlsplit(str(url or ''))
    if partes.scheme.lower() != 'https' or partes.username or partes.password:
        raise ValueError('URL municipal não aprovada')
    host = _host_canonico(partes.hostname, partes.port or '')
    rota = partes.path or '/'
    if partes.query:
        rota += f'?{partes.query}'
    if partes.fragment:
        rota += f'#{partes.fragment}'
    return host, rota


def _url_do_contexto(contexto):
    return getattr(contexto.view, 'url_certidao', None)


def _assinar_formulario(dados):
    bruto = json.dumps({
        'id': dados['id'], 'name': dados['name'],
        'metodo': dados['metodo'], 'acao_caminho': dados['acao_caminho'],
        'ordem': dados['ordem'],
    }, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(bruto).hexdigest()


def _normalizar_payload(payload, *, host_esperado, rota_esperada, etapa,
                        seletores):
    if not isinstance(payload, dict) or payload.get('estado') != 'ok':
        raise ValueError('observação municipal inconclusiva')
    if payload.get('estrutura_inacessivel'):
        raise ValueError('estrutura municipal inacessível')
    if str(payload.get('protocolo') or '').lower() != 'https:':
        raise ValueError('protocolo municipal não aprovado')
    host = _host_canonico(payload.get('host'), payload.get('porta'))
    if host != _host_canonico(host_esperado):
        raise ValueError('host municipal diferente do aprovado')
    rota = str(payload.get('caminho') or '/')
    rota += str(payload.get('consulta') or '')
    rota += str(payload.get('fragmento') or '')
    if rota != rota_esperada:
        raise ValueError('rota municipal diferente da aprovada')

    formularios_payload = payload.get('formularios')
    elementos_payload = payload.get('elementos')
    if not isinstance(formularios_payload, list) or not isinstance(elementos_payload, list):
        raise ValueError('payload municipal inválido')
    if len(formularios_payload) > _MAX_FORMULARIOS:
        raise ValueError('limite de formulários municipais excedido')
    if len(elementos_payload) > _MAX_ELEMENTOS:
        raise ValueError('limite de elementos municipais excedido')

    formularios = []
    for ordem, item in enumerate(formularios_payload):
        if not isinstance(item, dict):
            raise ValueError('formulário municipal inválido')
        dados = {
            'id': _texto(item.get('id')),
            'name': _texto(item.get('name')),
            'metodo': _texto(item.get('metodo'), limite=10).lower(),
            'acao_caminho': _texto(item.get('acao_caminho')),
            'ordem': int(item.get('ordem', ordem)),
        }
        formularios.append(FormularioInventariado(
            **dados, assinatura=_assinar_formulario(dados)))

    sem_formulario = hashlib.sha256(b'sem-formulario').hexdigest()
    elementos = []
    for ordem, item in enumerate(elementos_payload):
        if not isinstance(item, dict):
            raise ValueError('elemento municipal inválido')
        formulario_ordem = int(item.get('formulario_ordem', -1))
        if formulario_ordem < -1 or formulario_ordem >= len(formularios):
            raise ValueError('formulário municipal inválido')
        seletor_tipo = _texto(item.get('seletor_tipo'), limite=20).lower()
        seletor = _texto(item.get('seletor'))
        if seletor_tipo not in {'id', 'name', 'css_selector', 'xpath',
                                'class_name', 'nenhum'}:
            raise ValueError('tipo de seletor municipal não permitido')
        elementos.append(ElementoInventariado(
            tag=_texto(item.get('tag'), limite=30).lower(),
            tipo=_texto(item.get('tipo'), limite=50).lower(),
            id=_texto(item.get('id')),
            name=_texto(item.get('name')),
            rotulo=_texto(item.get('rotulo'), limite=_MAX_TEXTO),
            seletor_tipo=seletor_tipo,
            seletor=seletor,
            assinatura_formulario=(
                formularios[formulario_ordem].assinatura
                if formulario_ordem >= 0 else sem_formulario),
            ordem_relativa=int(item.get('ordem', ordem)),
            obrigatorio=bool(item.get('obrigatorio', False)),
            desabilitado=bool(item.get('desabilitado', False)),
            somente_leitura=bool(item.get('somente_leitura', False)),
            visivel=bool(item.get('visivel', False)),
            href_caminho=_texto(item.get('href_caminho')),
        ))

    estrutura = {
        'host': host, 'rota': rota, 'etapa': etapa,
        'formularios': [asdict(item) for item in formularios],
        'elementos': [asdict(item) for item in elementos],
        'seletores_declarados': seletores,
    }
    artefato = json.dumps(
        estrutura, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'))
    return InventarioPortal(
        host=host, rota=rota, etapa=etapa,
        formularios=tuple(formularios), elementos=tuple(elementos),
        artefato_sanitizado=artefato)


def _assinatura_inventario(inventario):
    return hashlib.sha256(
        inventario.artefato_sanitizado.encode('utf-8')).hexdigest()


def inventariar(driver, *, url_esperada, etapa=ETAPA_CONTRATO, seletores=()):
    """Coleta duas fotos sanitizadas da tela municipal e exige estabilidade."""
    try:
        host, rota = _endpoint(url_esperada)
        declarados = [
            {'tipo': tipo, 'seletor': seletor}
            for tipo, seletor in seletores
        ]
        primeira = _normalizar_payload(
            driver.execute_script(_JS_INVENTARIO, declarados),
            host_esperado=host, rota_esperada=rota, etapa=etapa,
            seletores=declarados)
        time.sleep(_ESPERA_ENTRE_FOTOS_S)
        segunda = _normalizar_payload(
            driver.execute_script(_JS_INVENTARIO, declarados),
            host_esperado=host, rota_esperada=rota, etapa=etapa,
            seletores=declarados)
        if _assinatura_inventario(primeira) != _assinatura_inventario(segunda):
            return InventarioPortal.desconhecido(
                etapa, 'DOM municipal instável durante a observação')
        return segunda
    except WebDriverException:
        return InventarioPortal.desconhecido(
            etapa, 'falha do driver durante a observação municipal')
    except (KeyError, TypeError, ValueError, AttributeError) as erro:
        return InventarioPortal.desconhecido(etapa, str(erro))


def _tipo_passo(passo):
    return (passo or {}).get('tipo') or ''


def _alvo_passo(passo):
    return (passo or {}).get('by'), (passo or {}).get('locator')


def _parece_link(seletor):
    texto = str(seletor or '').lower()
    return (
        '<a' in texto or '//a' in texto or 'a[' in texto
        or texto.startswith('a.') or ' a.' in texto)


def _papel_acao_declarados(chave, passo=None, *, terminal=False):
    tipo = _tipo_passo(passo)
    locator = str((passo or {}).get('locator') or '').lower()
    if terminal:
        if _parece_link(locator):
            return 'navegacao', 'navegar'
        return 'submissao', 'submeter'
    if _parece_link(locator):
        return 'navegacao', 'navegar'
    if chave in {'cnpj', 'inscricao', 'pre_fill_click'}:
        return 'entrada', 'preencher'
    if tipo in {'fill', 'select', 'wait_for', 'press_tab'}:
        return 'entrada', 'preencher'
    if tipo in {'click', 'click_js'}:
        # Clicáveis identificados como input/radio são entradas; os demais
        # normalmente são botões e devem permanecer protegidos como ação de
        # submissão, ainda que a etapa os use apenas para navegar.
        if any(marca in locator for marca in ('input', 'radio', 'select')):
            return 'entrada', 'preencher'
        return 'submissao', 'submeter'
    return 'entrada', 'preencher'


def _chave_passo(etapa, indice):
    return f'{etapa}[{indice}]'


def _itens_declarados(contexto):
    """Retorna `(chave, by, locator, papel, ação, autoajuste)` da tela."""
    itens = []

    def adicionar(chave, by, locator, papel, acao, autoajuste=False):
        if not by or not locator:
            return
        identidade = (by, locator)
        if any((item[1], item[2]) == identidade for item in itens):
            return
        itens.append((chave, by, locator, papel, acao, autoajuste))

    antes = list(contexto.config.get('before_cnpj') or [])
    for indice, passo in enumerate(antes, start=1):
        by, locator = _alvo_passo(passo)
        papel, acao = _papel_acao_declarados(
            _chave_passo('before_cnpj', indice), passo,
            terminal=dryrun_municipio._passo_emite(
                passo, indice, len(antes),
                bool(contexto.config.get('after_cnpj')),
                bool(contexto.config.get('skip_cnpj_fill'))))
        adicionar(_chave_passo('before_cnpj', indice), by, locator,
                   papel, acao)

    view = contexto.view
    if not contexto.config.get('skip_cnpj_fill'):
        papel, acao = _papel_acao_declarados('cnpj')
        adicionar('cnpj', getattr(view, 'by', None),
                   getattr(view, 'cnpj_field_id', None), papel, acao, True)

    pre_by = getattr(view, 'pre_fill_click_by', None)
    pre_locator = getattr(view, 'pre_fill_click_id', None)
    if pre_locator and not contexto.config.get('skip_cnpj_fill'):
        papel, acao = _papel_acao_declarados('pre_fill_click')
        adicionar('pre_fill_click', pre_by or 'id', pre_locator,
                   papel, acao, True)

    inscricao = getattr(view, 'inscricao_field_id', None)
    if inscricao:
        papel, acao = _papel_acao_declarados('inscricao')
        adicionar('inscricao', getattr(view, 'inscricao_field_by', None),
                   inscricao, papel, acao, True)

    depois = list(contexto.config.get('after_cnpj') or [])
    for indice, passo in enumerate(depois, start=1):
        # O dry-run só observa o primeiro controle pós-CNPJ. Não declarar
        # passos que não podem ser alcançados evita uma baseline enganosa.
        if indice > 1:
            break
        by, locator = _alvo_passo(passo)
        papel, acao = _papel_acao_declarados(
            _chave_passo('after_cnpj', indice), passo,
            terminal=True)
        adicionar(_chave_passo('after_cnpj', indice), by, locator,
                   papel, acao)
    return tuple(itens)


def definicao_baseline(contexto):
    host, rota = _endpoint(_url_do_contexto(contexto))
    elementos = []
    for ordem, (chave, by, locator, papel, acao, autoajuste) in enumerate(
            _itens_declarados(contexto)):
        elementos.append(ElementoContratoComparavel(
            chave=chave, etapa=ETAPA_CONTRATO, papel=papel, acao=acao,
            seletor_tipo=by, seletor=locator,
            tag='input' if papel == 'entrada' else 'button',
            tipo='text' if papel == 'entrada' else 'submit',
            rotulo=chave, assinatura_formulario='', ordem_relativa=ordem,
            obrigatorio=False, visivel=True, somente_leitura=False,
            autoajuste_seletor=autoajuste,
        ))
    return ContratoComparavel(
        host=host, rota=rota, etapa=ETAPA_CONTRATO,
        elementos=tuple(elementos))


def _seletores(contexto):
    return tuple((by, locator) for _chave, by, locator, *_resto in
                 _itens_declarados(contexto))


def contexto(municipio, rotulo='', config=None, *, view=None):
    """Monta contexto sem tocar no objeto `Municipio` nem no config original."""
    if view is None:
        view = municipio
    return ContextoMunicipal(
        municipio=municipio, rotulo=rotulo,
        config=copy.deepcopy(config or {}), view=view)


def contexto_da_emissao(municipio, rotulo, config, info_site):
    """Cria a visão usada pelo preflight da emissão lote/individual."""
    view = SimpleNamespace(
        nome=getattr(municipio, 'nome', None),
        automacao_ativa=getattr(municipio, 'automacao_ativa', True),
        url_certidao=info_site.get('url'),
        cnpj_field_id=info_site.get('cnpj_field_id'),
        by=info_site.get('by'),
        pre_fill_click_id=info_site.get('pre_fill_click_id'),
        pre_fill_click_by=info_site.get('pre_fill_click_by'),
        inscricao_field_id=info_site.get('inscricao_field_id'),
        inscricao_field_by=info_site.get('inscricao_field_by'),
    )
    return contexto(municipio, rotulo, config, view=view)


def _contextos_municipio(municipio, config):
    return tuple(
        contexto(municipio, rotulo, cfg_var, view=muni_var)
        for rotulo, muni_var, cfg_var in dryrun_municipio.variantes(
            municipio, config)
    )


def _criar_driver_municipal(contexto):
    """Reusa o driver do lote e o perfil UC já usados pela automação municipal."""
    from app.automation.driver import (
        _criar_driver_uc,
        _municipal_profile_acquire,
        _municipal_profile_release,
    )
    from app.automation.sites import is_ipm_atende

    if is_ipm_atende(_url_do_contexto(contexto)):
        if not _municipal_profile_acquire(blocking=False):
            raise RuntimeError('Perfil municipal em uso.')
        try:
            driver = _criar_driver_uc()
        except Exception:
            _municipal_profile_release()
            raise
        return _DriverComPerfil(driver, _municipal_profile_release)

    # O ponto único da janela background continua sendo o criador do lote;
    # este import é lazy para não criar ciclo `routes -> emissão -> serviço`.
    from app.routes.lotes import _criar_driver_lote
    return _criar_driver_lote()


class _DriverComPerfil:
    """Libera o lock UC junto com o fechamento feito pelo núcleo de recon."""

    def __init__(self, driver, liberar):
        self._driver = driver
        self._liberar = liberar
        self._fechado = False

    def quit(self):
        try:
            return self._driver.quit()
        finally:
            if not self._fechado:
                self._fechado = True
                self._liberar()

    def __getattr__(self, nome):
        return getattr(self._driver, nome)


def _adaptador_contexto(contexto):
    def observar(driver, _contrato):
        return observar_passivo(driver, contexto)

    def definir():
        return definicao_baseline(contexto)

    return AdaptadorRecon(
        fluxo=FLUXO_CONTRATO,
        alvo=contexto.alvo,
        nome=(f'Municipal — {contexto.cidade} '
              f'({contexto.rotulo or "padrão"})'),
        chave_health=contexto.chave_health,
        observar=observar,
        lock=_lock_municipal(),
        recon_passivo_seguro=True,
        definicao=definir,
        criar_driver=lambda: _criar_driver_municipal(contexto),
    )


def _lock_municipal():
    from app.automation.batch_state import MUNICIPAL_BATCH_LOCK

    return MUNICIPAL_BATCH_LOCK


def adaptadores_municipais(municipios=None):
    """Cria adaptadores das linhas ativas com configuração válida."""
    if municipios is None:
        from app.models import Municipio

        municipios = (Municipio.query
                       .filter_by(automacao_ativa=True)
                       .order_by(Municipio.nome).all())
    adaptadores = []
    for municipio in municipios:
        if not getattr(municipio, 'automacao_ativa', True):
            continue
        if not normalizar_cidade(getattr(municipio, 'nome', None)):
            continue
        config = dryrun_municipio._carregar_config_municipio(municipio)
        if not isinstance(config, dict):
            continue
        contextos = _contextos_municipio(municipio, config)
        adaptadores.extend(
            _adaptador_contexto(ctx) for ctx in contextos
            if _url_aprovada(ctx.view) and _itens_declarados(ctx))
    return adaptadores


def adaptador_por_alvo(alvo, municipios=None):
    return next((item for item in adaptadores_municipais(municipios)
                 if item.alvo == alvo), None)


def _url_aprovada(view):
    try:
        _endpoint(getattr(view, 'url_certidao', None))
    except (TypeError, ValueError):
        return False
    return True


def municipios_cobertos_por_contrato(municipios=None):
    """Cidades em que TODAS as telas/variantes já têm contrato ativo.

    O dry-run legado só sai para uma cidade completamente coberta. Uma onda
    parcial mantém a verificação anterior da cidade inteira, evitando uma
    lacuna silenciosa em variantes ainda não ativadas.
    """
    from app.services import contrato_portal_preflight

    adaptadores = adaptadores_municipais(municipios)
    esperados = {}
    ativos = {}
    for adaptador in adaptadores:
        esperados.setdefault(adaptador.chave_health, set()).add(adaptador.alvo)
        if contrato_portal_preflight.buscar_ativo(
                adaptador.fluxo, adaptador.alvo, obrigatorio=False) is not None:
            ativos.setdefault(adaptador.chave_health, set()).add(adaptador.alvo)
    return {
        chave for chave, alvos in esperados.items()
        if alvos and ativos.get(chave, set()) >= alvos
    }


def observar_passivo(driver, contexto):
    """Observa a tela municipal sem preencher, submeter ou baixar."""
    dryrun_municipio.bloquear_downloads(driver)
    relatorio = dryrun_municipio.verificar_municipio(
        contexto.view, driver, config=copy.deepcopy(contexto.config),
        timeout=20, cnpj=dryrun_municipio.CNPJ_TESTE,
        rotulo=contexto.rotulo, modo_passivo=True)
    dryrun_municipio.registrar_resultado(relatorio)

    resultado = relatorio.get('resultado')
    if resultado == dryrun_municipio.PARCIAL:
        return InventarioPortal.desconhecido(
            ETAPA_CONTRATO,
            relatorio.get('mensagem') or 'observação municipal parcial')
    if resultado not in {dryrun_municipio.OK, dryrun_municipio.QUEBRADO}:
        return InventarioPortal.desconhecido(
            ETAPA_CONTRATO,
            relatorio.get('mensagem') or 'observação municipal inconclusiva')
    url_ok = any(
        item.get('etapa', '').split('/')[-1] == dryrun_municipio.ETAPA_URL
        and item.get('status') == dryrun_municipio.OK
        for item in relatorio.get('checagens') or [])
    if not url_ok:
        return InventarioPortal.desconhecido(
            ETAPA_CONTRATO, 'portal municipal não chegou à tela observável')
    return inventariar(
        driver, url_esperada=_url_do_contexto(contexto),
        etapa=ETAPA_CONTRATO, seletores=_seletores(contexto))


def aplicar_snapshot(snapshot: SnapshotContratoPortal, info_site, config):
    """Traduz o snapshot fixado para uma cópia executável dos seletores."""
    novo_info = copy.deepcopy(info_site or {})
    novo_config = copy.deepcopy(config or {})

    def elemento(chave):
        item = snapshot.elemento(chave)
        if item.seletor_tipo == 'nenhum' or not item.seletor:
            raise ContratoPortalBloqueadoError(
                'desconhecida', 'seletor municipal ausente')
        return item

    # O contrato só declara o que a observação passiva alcança: `after_cnpj`
    # além do primeiro passo nunca entra, e `_itens_declarados` deduplica por
    # `(by, locator)`, então um passo que repete um endereço já declarado
    # também não tem chave própria. Exigir aqui uma chave que a baseline jamais
    # teve bloqueava a emissão de qualquer município nessas condições; o
    # localizador não declarado segue o valor do cadastro, como antes.
    def declarado(chave):
        return chave in snapshot.elementos

    def aplicar_info(chave, campo_locator, campo_by):
        if not novo_info.get(campo_locator) or not declarado(chave):
            return
        item = elemento(chave)
        novo_info[campo_locator] = item.seletor
        novo_info[campo_by] = item.seletor_tipo

    aplicar_info('cnpj', 'cnpj_field_id', 'by')
    aplicar_info('pre_fill_click', 'pre_fill_click_id', 'pre_fill_click_by')
    aplicar_info('inscricao', 'inscricao_field_id', 'inscricao_field_by')

    for etapa in ('before_cnpj', 'after_cnpj'):
        passos = novo_config.get(etapa) or []
        for indice, passo in enumerate(passos, start=1):
            by, locator = _alvo_passo(passo)
            chave = _chave_passo(etapa, indice)
            if not by or not locator or not declarado(chave):
                continue
            item = elemento(chave)
            passo['by'] = item.seletor_tipo
            passo['locator'] = item.seletor
    return novo_info, novo_config


def preparar_execucao(driver, contexto, *, estado_lote=None, execution_id=None):
    """Fixa uma versão por tela antes do primeiro preenchimento."""
    from app.services import contrato_portal_preflight

    snapshots = None
    if estado_lote is not None:
        snapshots = estado_lote.setdefault('contrato_snapshots', {})
        fixado = snapshots.get(contexto.alvo)
        if fixado is not None:
            if fixado.alvo != contexto.alvo or fixado.fluxo != FLUXO_CONTRATO:
                raise ContratoPortalBloqueadoError(
                    'desconhecida',
                    'Snapshot municipal fixado para outro alvo.')
            return fixado

    ativo = contrato_portal_preflight.buscar_ativo(
        FLUXO_CONTRATO, contexto.alvo, obrigatorio=False)
    if ativo is None:
        return None

    snapshot = contrato_portal_preflight.executar(
        fluxo=FLUXO_CONTRATO,
        alvo=contexto.alvo,
        observar=lambda _contrato: observar_passivo(driver, contexto),
        alvo_breaker=contexto.chave_health,
        execution_id=execution_id,
        contrato_ativo=ativo,
    )
    # A observação passiva reusa o dry-run: ele navega para a URL e EXECUTA os
    # cliques pré-CNPJ (só o passo que emite fica de fora). Sem voltar à tela
    # inicial, a emissão repetiria esses mesmos passos sobre uma página já
    # avançada — clicando duas vezes ou não achando mais o elemento.
    driver.get(_url_do_contexto(contexto))
    if snapshots is not None:
        snapshots[contexto.alvo] = snapshot
    return snapshot
