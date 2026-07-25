"""Fluxo de emissão da certidão Trabalhista (CNDT/TST) — spec 07 COV-01a.

O CNDT usa um captcha de IMAGEM base64 (`img#idImgBase64`) com input `#idCampoResposta`
e botão de emitir `#gerarCertidaoForm:btnEmitirCertidao`. Este módulo resolve o captcha
reusando o núcleo compartilhado (`captcha_img`, extraído do IMBE — AD-021), submete e, em
captcha recusado, tenta de novo (o CNDT recarrega um novo captcha na mesma tela).

A detecção de sucesso é delegada ao chamador via callback `houve_sucesso` (ex.: novo PDF
nos Downloads) — assim o retry fica num único lugar, sem duplicar entre a emissão individual
e o lote, e sem acoplar este módulo ao `file_manager`.
"""
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from app.automation import captcha_img
from app.automation.sites import SITES_CERTIDOES
from app.services.execution_logger import log_event


def _cfg_cndt():
    return SITES_CERTIDOES.get('TRABALHISTA', {})


def _localizar(driver, locator, timeout=10):
    try:
        return WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.ID, locator))
        )
    except Exception:
        return None


def _imagem_carregada(imagem_el):
    """True se a <img> do captcha já carregou de fato (naturalWidth > 0).

    O elemento <img> pode existir no DOM antes do base64 chegar; tirar o
    screenshot antes disso manda uma imagem em branco/incompleta ao 2captcha."""
    if imagem_el is None:
        return False
    try:
        largura = imagem_el.get_attribute('naturalWidth')
        return bool(largura) and int(largura) > 0
    except Exception:
        return False


def _esperar_imagem_carregada(driver, imagem_el, timeout=8):
    """Aguarda a imagem do captcha carregar antes do screenshot. Best-effort:
    retorna False no timeout/erro (o screenshot ainda ocorre e, se sair em branco,
    o retry re-tenta com a imagem já carregada)."""
    if imagem_el is None:
        return False
    try:
        return bool(WebDriverWait(driver, timeout).until(
            lambda d: _imagem_carregada(imagem_el)))
    except Exception:
        return False


def _clicar_submit(driver, submit_id):
    try:
        botao = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, submit_id))
        )
        botao.click()
        return True
    except Exception:
        try:
            botao = driver.find_element(By.ID, submit_id)
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
                                tentativas=2, espera_pos_submit=8):
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
    img_id = cfg.get('captcha_img_id', 'idImgBase64')
    input_id = cfg.get('captcha_input_id', 'idCampoResposta')
    submit_id = cfg.get('submit_id')

    ultimo_erro = 'Captcha do CNDT não resolvido.'
    for tentativa in range(1, max(1, tentativas) + 1):
        imagem = _localizar(driver, img_id)
        campo = _localizar(driver, input_id)
        # Espera a imagem carregar de fato (naturalWidth > 0) antes do screenshot —
        # o <img> aparece no DOM antes do base64 chegar.
        _esperar_imagem_carregada(driver, imagem)
        ok, info = captcha_img.resolver_captcha_imagem(
            imagem, campo, config=config, execution_id=execution_id)
        if not ok:
            log_event('trabalhista_captcha_infra_fail', level='WARNING',
                      tentativa=tentativa, error=info)
            return False, info

        if not _clicar_submit(driver, submit_id):
            return False, 'Não foi possível clicar em Emitir Certidão (CNDT).'

        if _aguardar_sucesso(houve_sucesso, espera_pos_submit):
            return True, None

        ultimo_erro = f'Captcha do CNDT recusado (tentativa {tentativa}).'
        log_event('trabalhista_captcha_recusado', level='WARNING', tentativa=tentativa)

    return False, ultimo_erro
