"""Captura de contexto na falha de automacao Selenium.

Quando um fluxo Selenium quebra (tipicamente porque um portal do governo mudou
de estrutura e um seletor parou de casar), salva um screenshot + o HTML da
pagina no momento da falha. Isso encurta o diagnostico: em vez de so um
stacktrace, fica a evidencia visual e o DOM real.

Best-effort por desenho: nenhuma funcao aqui levanta excecao para o chamador
(uma falha na captura nunca pode mascarar o erro original do fluxo).
"""
import os
import re
import time

from app.services.execution_logger import log_event
from app.utils import get_config_value, to_bool

_SLUG_RE = re.compile(r'[^a-zA-Z0-9_-]+')


def _slug(texto):
    s = _SLUG_RE.sub('-', str(texto or 'falha')).strip('-')
    return (s or 'falha')[:40]


def _capture_dir():
    destino = get_config_value('SELENIUM_CAPTURE_DIR')
    if destino:
        return destino
    return os.path.join(os.getcwd(), 'logs', 'selenium')


def _habilitado():
    return to_bool(get_config_value('SELENIUM_CAPTURE_ENABLED', True), default=True)


def capturar_contexto_falha(driver, contexto, certidao_id=None, execution_id=None):
    """Salva screenshot (.png) e HTML (.html) da pagina atual do driver.

    Retorna um dict {'screenshot': caminho, 'html': caminho} com o que conseguiu
    salvar (vazio se desabilitado, sem driver, ou se tudo falhar)."""
    if driver is None or not _habilitado():
        return {}

    destino = _capture_dir()
    try:
        os.makedirs(destino, exist_ok=True)
    except OSError as exc:
        log_event('selenium_capture_dir_error', level='WARNING', error=str(exc))
        return {}

    base = f"{time.strftime('%Y%m%d_%H%M%S')}_{_slug(contexto)}"
    if certidao_id:
        base += f"_cert{certidao_id}"

    resultado = {}

    caminho_png = os.path.join(destino, base + '.png')
    try:
        if driver.save_screenshot(caminho_png):
            resultado['screenshot'] = caminho_png
    except Exception:
        pass

    caminho_html = os.path.join(destino, base + '.html')
    try:
        with open(caminho_html, 'w', encoding='utf-8') as f:
            f.write(driver.page_source or '')
        resultado['html'] = caminho_html
    except Exception:
        pass

    if resultado:
        extra = {'execution_id': execution_id} if execution_id else {}
        log_event(
            'selenium_failure_capture', level='WARNING',
            contexto=str(contexto), certidao_id=certidao_id,
            **extra, **resultado,
        )
    return resultado


def salvar_artefato_sanitizado(contexto, html_seguro, execution_id=None):
    """Salva somente o HTML seguro já reconstruído pelo chamador.

    Este caminho não conhece Selenium e não tenta sanitizar ou completar o
    conteúdo recebido: a garantia de ausência de dados fica demonstrada no
    inventário que originou ``html_seguro``. Uma falha de diretório ou escrita
    retorna ``None`` para não substituir o erro original do fluxo.
    """
    if not _habilitado() or not isinstance(html_seguro, str):
        return None

    destino = _capture_dir()
    try:
        os.makedirs(destino, exist_ok=True)
    except OSError as exc:
        log_event(
            'selenium_sanitized_capture_dir_error',
            level='WARNING',
            error_type=type(exc).__name__,
        )
        return None

    base = f"{time.strftime('%Y%m%d_%H%M%S')}_{_slug(contexto)}_sanitizado"
    caminho = os.path.join(destino, base + '.html')
    try:
        with open(caminho, 'w', encoding='utf-8') as arquivo:
            arquivo.write(html_seguro)
    except Exception as exc:
        log_event(
            'selenium_sanitized_capture_write_error',
            level='WARNING',
            error_type=type(exc).__name__,
        )
        return None

    log_event(
        'selenium_sanitized_capture',
        level='WARNING',
        execution_id=execution_id,
        artefato=caminho,
    )
    return caminho


def prune_capturas(dias, base_dir=None):
    """Remove capturas (.png/.html) mais antigas que `dias` dias.
    Retorna a quantidade de arquivos removidos. Best-effort."""
    try:
        dias = int(dias)
    except (TypeError, ValueError):
        return 0
    if dias <= 0:
        return 0

    destino = base_dir or _capture_dir()
    if not os.path.isdir(destino):
        return 0

    limite = time.time() - dias * 86400
    removidos = 0
    try:
        nomes = os.listdir(destino)
    except OSError:
        return 0

    for nome in nomes:
        if not nome.lower().endswith(('.png', '.html')):
            continue
        caminho = os.path.join(destino, nome)
        try:
            if os.path.isfile(caminho) and os.path.getmtime(caminho) < limite:
                os.remove(caminho)
                removidos += 1
        except OSError:
            continue

    if removidos:
        log_event('selenium_capture_prune', removidos=removidos, retencao_dias=dias)
    return removidos
