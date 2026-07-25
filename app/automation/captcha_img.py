"""Núcleo compartilhado de resolução de captcha de imagem (2captcha `.normal()`).

Extraído do fluxo IMBE (`emissao._imbe_resolver_captcha_2captcha`) para reuso entre
IMBE (município) e CNDT/Trabalhista, sem duplicar lógica (AD-021 / princípio de reuso
do ROADMAP). Recebe os WebElements já localizados — a busca de seletor fica com cada
consumidor (IMBE por heurística; CNDT por id fixo).
"""
import os
import tempfile

from flask import current_app

from app.captcha_solver import solve_normal_captcha


def resolver_captcha_imagem(imagem_el, campo_el, config=None, execution_id=None):
    """Resolve um captcha de imagem já localizado.

    Fluxo: screenshot do elemento de imagem -> 2captcha `.normal()` -> preenche o
    campo de texto (clear + click + send_keys).

    Args:
        imagem_el: WebElement da imagem do captcha (ou None se não localizado).
        campo_el: WebElement do input de texto do captcha (ou None se não localizado).
        config: mapa de config (default: `current_app.config`).
        execution_id: id de correlação opcional, propagado ao solver.

    Retorna `(True, codigo)` em sucesso ou `(False, mensagem_erro)` em falha.
    Nunca levanta — espelha o contrato best-effort do captcha_solver.
    """
    if imagem_el is None:
        return False, 'Imagem do captcha não encontrada.'
    if campo_el is None:
        return False, 'Campo do captcha não encontrado.'

    cfg = config if config is not None else current_app.config
    arquivo_tmp = None
    try:
        captcha_bytes = imagem_el.screenshot_as_png
        if not captcha_bytes:
            return False, 'Captcha sem imagem capturada.'

        with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp:
            tmp.write(captcha_bytes)
            arquivo_tmp = tmp.name

        resultado = solve_normal_captcha(cfg, image_path=arquivo_tmp, execution_id=execution_id)
        codigo = (resultado.get('code') or '').strip()
        if not codigo:
            return False, 'Resposta do 2captcha vazia.'

        campo_el.clear()
        campo_el.click()
        campo_el.send_keys(codigo)
        return True, codigo
    except Exception as exc:
        return False, f'Falha ao resolver captcha: {exc}'
    finally:
        if arquivo_tmp and os.path.exists(arquivo_tmp):
            try:
                os.remove(arquivo_tmp)
            except Exception:
                pass
