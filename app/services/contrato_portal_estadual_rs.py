"""Adaptador do contrato adaptativo para o portal estadual do RS.

O RS tem uma preparação própria: o navegador autentica com certificado antes de
chegar à tela de consulta. O contrato governa somente essa tela observável —
CNPJ, desafio ALTCHA e envio — e nunca tenta autoajustar o desafio ou a ação
que pode gerar a certidão.
"""
from __future__ import annotations

import time
from urllib.parse import urlsplit

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By

from app.automation import trabalhista_recon
from app.automation.batch_state import RS_BATCH_LOCK, RS_BATCH_STATE
from app.automation.sites import SITES_CERTIDOES
from app.services import circuit_breaker, contrato_portal_preflight
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
    InventarioPortal,
    PortalObservacaoTemporariamenteIndisponivelError,
)
from app.services.contrato_portal_registry import AdaptadorRecon


FLUXO_CONTRATO = 'estadual'
ALVO_CONTRATO = 'rs'
ETAPA_CONTRATO = 'formulario'
_CONTROLES_CONTRATO = ('cnpj', 'desafio', 'enviar')
_TEMPO_MAXIMO_CARREGAMENTO_S = 30
_ESPERA_ENTRE_FOTOS_S = 0.4


def _configuracao_legada():
    return (SITES_CERTIDOES.get('ESTADUAL', {}).get('RS') or {})


def _endpoint(url):
    try:
        partes = urlsplit(str(url or ''))
        porta = partes.port
    except ValueError as erro:
        raise ValueError('URL estadual RS inválida') from erro
    if (
        partes.scheme.lower() != 'https'
        or not partes.hostname
        or partes.username
        or partes.password
        or partes.query
        or partes.fragment
    ):
        raise ValueError('URL estadual RS não aprovada')
    host = partes.hostname.rstrip('.').lower()
    if porta and porta != 443:
        host = f'{host}:{porta}'
    return host, partes.path or '/'


def _urls_legadas():
    configuracao = _configuracao_legada()
    formulario = _endpoint(configuracao.get('url'))
    login = _endpoint(configuracao.get('login_cert_url'))
    return formulario, login


def _url_do_contrato(contrato):
    return f'https://{contrato.host}{contrato.rota}'


def definicao_baseline() -> ContratoComparavel:
    """Declara a identidade e a política; fatos vêm da observação passiva."""
    (host, rota), _login = _urls_legadas()
    dados = (
        ('cnpj', 'entrada', 'preencher', 'name', 'campoCnpj',
         'input', 'text', 'CNPJ', True),
        ('desafio', 'captcha', 'observar', 'css_selector', 'altcha-widget',
         'altcha-widget', 'altcha-widget', 'ALTCHA', False),
        ('enviar', 'submissao', 'submeter', 'id', 'btnEnviar',
         'button', 'submit', 'Enviar', False),
    )
    elementos = tuple(ElementoContratoComparavel(
        chave=chave,
        etapa=ETAPA_CONTRATO,
        papel=papel,
        acao=acao,
        seletor_tipo=seletor_tipo,
        seletor=seletor,
        tag=tag,
        tipo=tipo,
        rotulo=rotulo,
        assinatura_formulario='',
        ordem_relativa=ordem,
        obrigatorio=False,
        visivel=True,
        somente_leitura=False,
        autoajuste_seletor=autoajuste,
    ) for ordem, (
        chave, papel, acao, seletor_tipo, seletor, tag, tipo, rotulo,
        autoajuste,
    ) in enumerate(dados))
    return ContratoComparavel(
        host=host,
        rota=rota,
        etapa=ETAPA_CONTRATO,
        elementos=elementos,
    )
# O inventário comum já sanitiza campos, rótulos, formulários e rotas. O RS
# acrescenta apenas o host do widget ALTCHA, que é um componente customizado;
# seu conteúdo interno nunca é lido. A presença do host é suficiente para
# manter o desafio protegido, e qualquer mudança nele continua bloqueante.
_JS_INVENTARIO_RS = r"""
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
  var candidatos = document.querySelectorAll(
    'a, input, select, textarea, button, img, altcha-widget');
  for (var ordem = 0; ordem < candidatos.length; ordem += 1) {
    var elemento = candidatos[ordem];
    var tag = String(elemento.tagName || '').toLowerCase();
    var tipo = String(elemento.getAttribute('type') || tag).toLowerCase();
    var customizado = tag === 'altcha-widget';
    var nome = elemento.getAttribute('name') || '';
    var identificador = elemento.getAttribute('id') || '';
    if (tag === 'input' && tipo === 'hidden'
        && nome.toLowerCase().indexOf('altcha') === -1
        && identificador.toLowerCase().indexOf('altcha') === -1) {
      continue;
    }
    var formulario = elemento.closest ? elemento.closest('form') : null;
    var formularioOrdem = formulario ? formulariosDom.indexOf(formulario) : -1;
    var href = tag === 'a'
      ? caminhoSeguro(elemento.getAttribute('href') || '')
      : {caminho: '', tem_query: false};
    var seletorTipo = identificador
      ? 'id' : (nome ? 'name' : (customizado ? 'css_selector' : 'nenhum'));
    var seletor = identificador || nome || (customizado ? 'altcha-widget' : '');
    elementos.push({
      tag: tag,
      tipo: tipo,
      id: identificador,
      name: nome,
      rotulo: customizado ? 'ALTCHA' : rotuloDo(elemento),
      seletor_tipo: seletorTipo,
      seletor: seletor,
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
    var tagTodo = String(todos[n].tagName || '').toLowerCase();
    if (todos[n].shadowRoot && tagTodo !== 'altcha-widget') {
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


def inventariar(driver, *, host_esperado, rota_esperada):
    """Coleta duas fotos estruturais, sem preencher ou submeter."""
    try:
        primeira = trabalhista_recon._normalizar_payload(
            driver.execute_script(_JS_INVENTARIO_RS),
            host_esperado=host_esperado,
            rota_esperada=rota_esperada,
            etapa=ETAPA_CONTRATO)
        time.sleep(_ESPERA_ENTRE_FOTOS_S)
        segunda = trabalhista_recon._normalizar_payload(
            driver.execute_script(_JS_INVENTARIO_RS),
            host_esperado=host_esperado,
            rota_esperada=rota_esperada,
            etapa=ETAPA_CONTRATO)
        if trabalhista_recon._assinatura_inventario(primeira) != (
                trabalhista_recon._assinatura_inventario(segunda)):
            return InventarioPortal.desconhecido(
                ETAPA_CONTRATO, 'DOM RS instável durante a observação')
        return segunda
    except WebDriverException:
        return InventarioPortal.desconhecido(
            ETAPA_CONTRATO, 'falha do driver durante a observação RS')
    except (KeyError, TypeError, ValueError, AttributeError) as erro:
        return InventarioPortal.desconhecido(ETAPA_CONTRATO, str(erro))
    except Exception:
        return InventarioPortal.desconhecido(
            ETAPA_CONTRATO, 'falha inesperada durante a observação RS')


def _entrar_na_tela(driver, contrato):
    """Autentica a sessão e termina na rota contratada, sem emitir."""
    from app.automation.emissao import _login_certificado_rs

    _login_certificado_rs(
        driver,
        _configuracao_legada().get('login_cert_url'),
        _url_do_contrato(contrato),
        timeout=_TEMPO_MAXIMO_CARREGAMENTO_S,
    )


def _observar_tela(driver, contrato):
    try:
        driver.set_page_load_timeout(_TEMPO_MAXIMO_CARREGAMENTO_S)
    except Exception:
        pass
    try:
        _entrar_na_tela(driver, contrato)
    except (TimeoutException, WebDriverException) as erro:
        raise PortalObservacaoTemporariamenteIndisponivelError(
            'Portal Estadual RS temporariamente indisponível durante a observação.') from erro
    except Exception:
        return InventarioPortal.desconhecido(
            ETAPA_CONTRATO, 'falha ao autenticar ou abrir o portal estadual RS')
    inventario = inventariar(
        driver,
        host_esperado=contrato.host,
        rota_esperada=contrato.rota,
    )
    if (
        inventario.estado == 'desconhecida'
        and str(inventario.motivo or '').startswith('falha do driver')
    ):
        raise PortalObservacaoTemporariamenteIndisponivelError(
            f'falha ao observar o portal Estadual RS: {inventario.motivo}')
    return inventario


def observar_passivo(driver, contrato):
    """Observa a tela do RS sem preencher CNPJ, resolver ALTCHA ou enviar.

    O bloqueio de download vive AQUI, não em `_observar_tela`: este é o caminho
    do recon, com driver descartável. O preflight da emissão usa o mesmo
    observador no driver do lote, e `Page.setDownloadBehavior: deny` fica
    valendo pela sessão inteira — a certidão do RS chega por download de
    verdade, então bloquear ali fazia todo item do lote estourar os 180s de
    espera sem nunca receber o PDF.
    """
    dryrun_municipio.bloquear_downloads(driver)
    try:
        return _observar_tela(driver, contrato)
    except PortalObservacaoTemporariamenteIndisponivelError as erro:
        return InventarioPortal.desconhecido(ETAPA_CONTRATO, str(erro))


_BY_SNAPSHOT = {
    'id': By.ID,
    'name': By.NAME,
    'css_selector': By.CSS_SELECTOR,
    'xpath': By.XPATH,
    'class_name': By.CLASS_NAME,
}


def localizador(snapshot: SnapshotContratoPortal, chave: str):
    elemento = snapshot.elemento(chave)
    by = _BY_SNAPSHOT.get(elemento.seletor_tipo)
    if by is None or not elemento.seletor:
        raise ContratoPortalBloqueadoError(
            'desconhecida', 'seletor estadual RS ausente')
    return by, elemento.seletor


def validar_snapshot(snapshot):
    if snapshot.fluxo != FLUXO_CONTRATO or snapshot.alvo != ALVO_CONTRATO:
        raise ContratoPortalBloqueadoError(
            'desconhecida',
            'Snapshot estadual RS fixado para outro fluxo ou alvo.')
    for chave in _CONTROLES_CONTRATO:
        localizador(snapshot, chave)


def preparar_execucao(driver, *, estado_lote=None, execution_id=None):
    """Fixa uma versão antes do CNPJ e reutiliza a sessão no lote."""
    if estado_lote is not None:
        fixado = estado_lote.get('contrato_snapshot')
        if fixado is not None:
            validar_snapshot(fixado)
            # Compara o OBJETO, não `id()`: o CPython reaproveita endereço, e um
            # driver recriado no meio do lote podia cair no mesmo id do que
            # acabou de morrer — a sessão seria dada como autenticada sem nunca
            # ter passado pelo certificado.
            if estado_lote.get('contrato_sessao_driver') is not driver:
                _entrar_na_tela(driver, fixado)
                estado_lote['contrato_sessao_driver'] = driver
            return fixado

    ativo = contrato_portal_preflight.buscar_ativo(
        FLUXO_CONTRATO, ALVO_CONTRATO, obrigatorio=False)
    if ativo is None:
        return None

    snapshot = contrato_portal_preflight.executar(
        fluxo=FLUXO_CONTRATO,
        alvo=ALVO_CONTRATO,
        observar=lambda contrato: _observar_tela(driver, contrato),
        alvo_breaker=circuit_breaker.ALVO_ESTADUAL_RS,
        execution_id=execution_id,
        contrato_ativo=ativo,
    )
    validar_snapshot(snapshot)
    if estado_lote is not None:
        estado_lote['contrato_snapshot'] = snapshot
        estado_lote['contrato_sessao_driver'] = driver
    return snapshot


def adaptador_estadual_rs():
    return AdaptadorRecon(
        fluxo=FLUXO_CONTRATO,
        alvo=ALVO_CONTRATO,
        nome='Estadual RS (SEFAZ)',
        chave_health=circuit_breaker.ALVO_ESTADUAL_RS,
        observar=observar_passivo,
        lock=RS_BATCH_LOCK,
        preflight_state=RS_BATCH_STATE,
        recon_passivo_seguro=True,
        definicao=definicao_baseline,
    )
