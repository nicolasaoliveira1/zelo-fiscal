"""Adaptador do contrato adaptativo para o portal do FGTS.

O contrato do FGTS governa somente a tela automatizada e os quatro controles
que podem levar à consulta e ao PDF. Sem contrato ativo, o executor continua
usando o mapa legado de ``SITES_CERTIDOES``. Com contrato ativo, a execução só
recebe localizadores do snapshot fixado e falha fechada se ele estiver
incompleto.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By

from app.automation import trabalhista_recon
from app.automation.batch_state import FGTS_BATCH_LOCK
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
from app.services.contrato_portal_protocol import InventarioPortal
from app.services.contrato_portal_registry import AdaptadorRecon


FLUXO_CONTRATO = 'fgts'
ALVO_CONTRATO = 'fgts'
ETAPA_CONTRATO = 'formulario'
_CONTROLES_CONTRATO = ('cnpj', 'consultar', 'certificado', 'visualizar')
_TEMPO_MAXIMO_CARREGAMENTO_S = 30


def _url_legada():
    return (SITES_CERTIDOES.get('FGTS') or {}).get('url')


def _endpoint_legado():
    partes = urlsplit(str(_url_legada() or ''))
    if partes.scheme.lower() != 'https' or not partes.hostname:
        raise ValueError('URL do FGTS não aprovada')
    if partes.username or partes.password or partes.query or partes.fragment:
        raise ValueError('URL do FGTS possui destino não aprovado')
    porta = partes.port
    host = partes.hostname.rstrip('.').lower()
    if porta and porta != 443:
        host = f'{host}:{porta}'
    return host, partes.path or '/'


def _url_do_contrato(contrato):
    return f'https://{contrato.host}{contrato.rota}'


def definicao_baseline():
    """Declara identidade e política; fatos da tela vêm da observação."""
    host, rota = _endpoint_legado()
    dados = (
        ('cnpj', 'entrada', 'preencher', 'id', 'mainForm:txtInscricao1',
         'input', 'text', 'CNPJ', True),
        ('consultar', 'submissao', 'submeter', 'id', 'mainForm:btnConsultar',
         'button', 'submit', 'Consultar', False),
        ('certificado', 'submissao', 'submeter', 'id', 'mainForm:j_id76',
         'button', 'button', 'Certificado', False),
        ('visualizar', 'submissao', 'submeter', 'id', 'mainForm:btnVisualizar',
         'button', 'button', 'Visualizar', False),
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
        host=host, rota=rota, etapa=ETAPA_CONTRATO, elementos=elementos)


def inventariar(driver, *, host_esperado, rota_esperada):
    """Reusa o inventário sanitizado passivo já aprovado pelo piloto."""
    return trabalhista_recon.inventariar(
        driver,
        host_esperado=host_esperado,
        rota_esperada=rota_esperada,
        etapa=ETAPA_CONTRATO,
    )


def _observar_tela(driver, contrato):
    try:
        driver.set_page_load_timeout(_TEMPO_MAXIMO_CARREGAMENTO_S)
    except Exception:
        pass

    dryrun_municipio.bloquear_downloads(driver)
    try:
        driver.get(_url_do_contrato(contrato))
    except (TimeoutException, WebDriverException):
        return InventarioPortal.desconhecido(
            ETAPA_CONTRATO, 'falha ao abrir o portal do FGTS')
    except Exception:
        return InventarioPortal.desconhecido(
            ETAPA_CONTRATO, 'falha ao abrir o portal do FGTS')
    return inventariar(
        driver,
        host_esperado=contrato.host,
        rota_esperada=contrato.rota,
    )


def observar_passivo(driver, contrato):
    """Chega à tela do contrato sem preencher, clicar ou baixar."""
    return _observar_tela(driver, contrato)


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
        raise ContratoPortalBloqueadoError('seletor FGTS ausente')
    return by, elemento.seletor


def validar_snapshot(snapshot):
    if snapshot.fluxo != FLUXO_CONTRATO or snapshot.alvo != ALVO_CONTRATO:
        raise ContratoPortalBloqueadoError(
            'Snapshot FGTS fixado para outro fluxo ou alvo.')
    for chave in _CONTROLES_CONTRATO:
        localizador(snapshot, chave)


def preparar_execucao(driver, *, estado_lote=None, execution_id=None):
    """Fixa uma versão antes do CNPJ e a reutiliza no restante do lote."""
    if estado_lote is not None:
        fixado = estado_lote.get('contrato_snapshot')
        if fixado is not None:
            validar_snapshot(fixado)
            driver.get(_url_do_contrato(fixado))
            return fixado

    ativo = contrato_portal_preflight.buscar_ativo(
        FLUXO_CONTRATO, ALVO_CONTRATO, obrigatorio=False)
    if ativo is None:
        return None

    snapshot = contrato_portal_preflight.executar(
        fluxo=FLUXO_CONTRATO,
        alvo=ALVO_CONTRATO,
        observar=lambda contrato: observar_passivo(driver, contrato),
        alvo_breaker=circuit_breaker.ALVO_FGTS,
        execution_id=execution_id,
        contrato_ativo=ativo,
    )
    validar_snapshot(snapshot)
    if estado_lote is not None:
        estado_lote['contrato_snapshot'] = snapshot
    return snapshot


def adaptador_fgts():
    return AdaptadorRecon(
        fluxo=FLUXO_CONTRATO,
        alvo=ALVO_CONTRATO,
        nome='FGTS (Caixa)',
        chave_health=circuit_breaker.ALVO_FGTS,
        observar=observar_passivo,
        lock=FGTS_BATCH_LOCK,
        recon_passivo_seguro=True,
        definicao=definicao_baseline,
    )
