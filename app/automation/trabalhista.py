"""Fluxo de emissão da certidão Trabalhista (CNDT/TST).

O CNDT usa um captcha de imagem, um campo de resposta e um botão de emissão. Os
endereços vêm do snapshot quando existe contrato ativo. Este módulo resolve o captcha
reusando o núcleo compartilhado (`captcha_img`, extraído do IMBE — AD-021), submete e, em
captcha recusado, tenta de novo (o CNDT recarrega um novo captcha na mesma tela).

A detecção de sucesso é delegada ao chamador via callback `houve_sucesso` (ex.: novo PDF
nos Downloads) — assim o retry fica num único lugar, sem duplicar entre a emissão individual
e o lote, e sem acoplar este módulo ao `file_manager`.
"""
import time

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from app.automation import captcha_img, trabalhista_recon
from app.automation.sites import SITES_CERTIDOES
from app.services import circuit_breaker, contrato_portal_preflight
from app.services.contrato_portal_drift import (
    ContratoComparavel,
    ElementoContratoComparavel,
)
from app.services.execution_logger import log_event


FLUXO_CONTRATO = 'trabalhista'
ALVO_CONTRATO = 'cndt'
HOST_CNDT = 'cndt-certidao.tst.jus.br'
# Teto de carregamento de pagina. Sem ele, `driver.get` bloqueia sem limite:
# em 2026-09-09 um lote emitiu 4 itens e ficou 8 minutos parado no quinto, sem
# um unico evento no log. Portal que engasga tem de virar falha do item.
TIMEOUT_CARREGAMENTO_S = 30
ROTA_CNDT = '/gerarCertidao'


def definicao_baseline() -> ContratoComparavel:
    """Baseline declarada do CNDT atual; instalação exige ação admin em T8.

    A assinatura do formulário fica vazia de propósito, como no FGTS e no RS:
    ela é FATO da tela, e `montar_baseline_observada` a sobrescreve com o que a
    observação leu. Um hash escrito à mão aqui nunca chegava ao contrato.
    """
    formulario = ''
    dados = (
        ('documento', 'entrada', 'preencher', 'cpfCnpj', 'input', 'text',
         'CPF ou CNPJ', True, True),
        ('captcha_imagem', 'captcha', 'observar', 'captcha-imagem', 'img', 'img',
         'Captcha', False, False),
        ('captcha_resposta', 'entrada', 'preencher', 'captcha-resposta', 'input', 'text',
         'Código de verificação', True, False),
        ('submeter', 'submissao', 'submeter', 'botao-emitir', 'button', 'submit',
         'Emitir Certidão', False, False),
    )
    elementos = tuple(ElementoContratoComparavel(
        chave=chave,
        etapa='formulario',
        papel=papel,
        acao=acao,
        seletor_tipo='id',
        seletor=seletor,
        tag=tag,
        tipo=tipo,
        rotulo=rotulo,
        assinatura_formulario=formulario,
        ordem_relativa=ordem,
        obrigatorio=obrigatorio,
        visivel=True,
        somente_leitura=False,
        autoajuste_seletor=autoajuste,
    ) for ordem, (
        chave, papel, acao, seletor, tag, tipo, rotulo, obrigatorio, autoajuste,
    ) in enumerate(dados))
    return ContratoComparavel(
        host=HOST_CNDT,
        rota=ROTA_CNDT,
        etapa='formulario',
        elementos=elementos,
    )


class PortalNaoRespondeuError(RuntimeError):
    """O portal nao terminou de carregar dentro do teto.

    E falha do PORTAL: alimenta o circuit breaker (spec 09), diferente de
    drive de rede, permissao e banco.

    NAO herda de `TimeoutException` de proposito, embora seja o que a origem
    sugere: `TimeoutException` e subclasse de `WebDriverException`, que
    `_erro_indica_navegador_fechado` trata como sessao morta — e sessao morta e
    GRAVE_FATAL, que aborta o lote inteiro E pula o breaker. Portal lento nao e
    navegador morto: e falha do item, contada no breaker.
    """


def _navegar(driver, url, *, execution_id=None):
    """Navega com teto de tempo e deixa rastro nos dois lados da chamada."""
    try:
        driver.set_page_load_timeout(TIMEOUT_CARREGAMENTO_S)
    except Exception:
        # Driver que nao aceita o ajuste nao justifica abortar a emissao; o
        # rastro abaixo continua dizendo onde ela estava.
        pass
    log_event('trabalhista_navegando', url=url, execution_id=execution_id)
    try:
        driver.get(url)
    except TimeoutException as erro:
        log_event(
            'trabalhista_portal_nao_respondeu', level='WARNING',
            url=url, timeout_s=TIMEOUT_CARREGAMENTO_S, execution_id=execution_id)
        raise PortalNaoRespondeuError(
            'O portal do CNDT não respondeu em '
            f'{TIMEOUT_CARREGAMENTO_S}s.') from erro
    log_event('trabalhista_navegado', url=url, execution_id=execution_id)


def _url_contrato(snapshot_ou_modelo):
    return f'https://{snapshot_ou_modelo.host}{snapshot_ou_modelo.rota}'


def observar_passivo(driver, contrato):
    """Observação passiva do CNDT para o recon agendado (AC-08.5).

    Chega à tela observável e inventaria: não preenche documento, não resolve
    captcha e não submete. É o único caminho pelo qual o agendador toca o
    portal.
    """
    _navegar(driver, _url_contrato(contrato))
    return trabalhista_recon.inventariar(
        driver,
        host_esperado=contrato.host,
        rota_esperada=contrato.rota,
        etapa='formulario',
    )


def _validar_snapshot(snapshot):
    try:
        for chave in (
            'documento', 'captcha_imagem', 'captcha_resposta', 'submeter',
        ):
            localizador(snapshot, chave)
    except contrato_portal_preflight.ContratoPortalBloqueadoError:
        circuit_breaker.registrar_falha(
            circuit_breaker.ALVO_TRABALHISTA,
            'Contrato Trabalhista ativo está incompleto.')
        raise


def preparar_execucao(
    driver,
    *,
    url_legada: str,
    estado_lote: dict | None = None,
    execution_id: str | None = None,
):
    """Navega e fixa o contrato antes de qualquer preenchimento ou captcha."""
    if estado_lote is not None:
        fixado = estado_lote.get('contrato_snapshot')
        if fixado is not None:
            _validar_snapshot(fixado)
            _navegar(driver, _url_contrato(fixado), execution_id=execution_id)
            return fixado

    ativo = contrato_portal_preflight.buscar_ativo(
        FLUXO_CONTRATO, ALVO_CONTRATO, obrigatorio=False)
    if ativo is None:
        _navegar(driver, url_legada, execution_id=execution_id)
        return None

    _navegar(driver, _url_contrato(ativo), execution_id=execution_id)

    def _observar(contrato):
        return trabalhista_recon.inventariar(
            driver,
            host_esperado=contrato.host,
            rota_esperada=contrato.rota,
            etapa='formulario',
        )

    snapshot = contrato_portal_preflight.executar(
        fluxo=FLUXO_CONTRATO,
        alvo=ALVO_CONTRATO,
        observar=_observar,
        alvo_breaker=circuit_breaker.ALVO_TRABALHISTA,
        execution_id=execution_id,
        contrato_ativo=ativo,
    )
    _validar_snapshot(snapshot)
    if estado_lote is not None:
        estado_lote['contrato_snapshot'] = snapshot
    return snapshot


_BY_CONTRATO = {
    'id': By.ID,
    'name': By.NAME,
    'css_selector': By.CSS_SELECTOR,
    'xpath': By.XPATH,
}


def localizador(snapshot, chave):
    elemento = snapshot.elemento(chave)
    by = _BY_CONTRATO.get(elemento.seletor_tipo)
    if by is None or not elemento.seletor:
        raise contrato_portal_preflight.ContratoPortalBloqueadoError('desconhecida')
    return by, elemento.seletor


def _cfg_cndt():
    return SITES_CERTIDOES.get('TRABALHISTA', {})


def _localizar(driver, locator, timeout=10):
    try:
        return WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located(locator)
        )
    except Exception:
        return None


def _clicar_submit(driver, locator):
    try:
        botao = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(locator)
        )
        botao.click()
        return True
    except Exception:
        try:
            botao = driver.find_element(*locator)
            driver.execute_script('arguments[0].click();', botao)
            return True
        except Exception:
            return False


def _aguardar_sucesso(houve_sucesso, timeout):
    """Poll `houve_sucesso()` até `timeout` segundos. True se concluiu; False no esgotamento.

    Testável com timeout=0 (avalia uma vez e retorna sem dormir). Exceções no
    callback são tratadas como 'ainda não'."""
    deadline = time.time() + max(0, timeout)
    while True:
        try:
            if houve_sucesso():
                return True
        except Exception:
            pass
        if time.time() >= deadline:
            return False
        time.sleep(0.5)


def resolver_captcha_e_submeter(driver, config, houve_sucesso, execution_id=None,
                                tentativas=2, espera_pos_submit=8, snapshot=None):
    """Resolve o captcha de imagem do CNDT e clica em Emitir Certidão, com retry.

    Pré-condição: `driver` já está na tela de emissão com o CNPJ preenchido.

    Args:
        driver: WebDriver na tela de emissão do CNDT.
        config: mapa de config (passado ao solver 2captcha).
        houve_sucesso: callable() -> bool; True quando a emissão concluiu (ex.: novo
            PDF nos Downloads). Avaliado após cada submit.
        execution_id: id de correlação opcional.
        tentativas: nº máximo de tentativas de captcha (default 2).
        espera_pos_submit: segundos aguardando `houve_sucesso` após cada submit.

    Retorna `(True, None)` em sucesso ou `(False, mensagem_erro)`. Uma falha de
    infraestrutura do captcha (2captcha/elemento ausente) aborta sem repetir — só
    um captcha *recusado* (sem download) dispara nova tentativa.
    """
    cfg = _cfg_cndt()
    if snapshot is None:
        locator_imagem = (By.ID, cfg.get('captcha_img_id', 'captcha-imagem'))
        locator_resposta = (By.ID, cfg.get('captcha_input_id', 'captcha-resposta'))
        locator_submit = (By.ID, cfg.get('submit_id'))
    else:
        locator_imagem = localizador(snapshot, 'captcha_imagem')
        locator_resposta = localizador(snapshot, 'captcha_resposta')
        locator_submit = localizador(snapshot, 'submeter')

    ultimo_erro = 'Captcha do CNDT não resolvido.'
    for tentativa in range(1, max(1, tentativas) + 1):
        imagem = _localizar(driver, locator_imagem)
        campo = _localizar(driver, locator_resposta)
        # O núcleo captcha_img espera a imagem carregar (naturalWidth > 0) antes do
        # screenshot — reuso comum a IMBE e CNDT, sem duplicar aqui.
        ok, info = captcha_img.resolver_captcha_imagem(
            imagem, campo, config=config, execution_id=execution_id)
        if not ok:
            log_event('trabalhista_captcha_infra_fail', level='WARNING',
                      tentativa=tentativa, error=info)
            return False, info

        if not _clicar_submit(driver, locator_submit):
            return False, 'Não foi possível clicar em Emitir Certidão (CNDT).'

        if _aguardar_sucesso(houve_sucesso, espera_pos_submit):
            return True, None

        ultimo_erro = f'Captcha do CNDT recusado (tentativa {tentativa}).'
        log_event('trabalhista_captcha_recusado', level='WARNING', tentativa=tentativa)

    return False, ultimo_erro
