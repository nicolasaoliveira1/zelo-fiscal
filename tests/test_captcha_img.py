"""Testes do núcleo compartilhado de captcha de imagem (spec 07, TRAB-01/05).

Núcleo extraído do fluxo IMBE (AD-021). Sem Selenium/rede: elementos falsos +
`solve_normal_captcha` mockado.
"""
from unittest.mock import MagicMock, patch

from app.automation import captcha_img


def _img(bytes_png=b'PNGDATA', natural_width='120'):
    el = MagicMock()
    el.screenshot_as_png = bytes_png
    el.get_attribute.return_value = natural_width  # naturalWidth > 0 => já carregou
    return el


def test_sucesso_resolve_e_preenche_campo():
    campo = MagicMock()
    with patch.object(captcha_img, 'solve_normal_captcha', return_value={'code': 'ABC123'}) as solver:
        ok, valor = captcha_img.resolver_captcha_imagem(_img(), campo, config={})
    assert ok is True
    assert valor == 'ABC123'
    campo.clear.assert_called_once()
    campo.send_keys.assert_called_once_with('ABC123')
    solver.assert_called_once()


def test_imagem_nao_encontrada_retorna_erro():
    ok, msg = captcha_img.resolver_captcha_imagem(None, MagicMock(), config={})
    assert ok is False
    assert msg == 'Imagem do captcha não encontrada.'


def test_campo_nao_encontrado_retorna_erro():
    ok, msg = captcha_img.resolver_captcha_imagem(_img(), None, config={})
    assert ok is False
    assert msg == 'Campo do captcha não encontrado.'


def test_imagem_sem_bytes_nao_chama_solver():
    campo = MagicMock()
    with patch.object(captcha_img, 'solve_normal_captcha') as solver:
        ok, msg = captcha_img.resolver_captcha_imagem(_img(bytes_png=b''), campo, config={})
    assert ok is False
    assert msg == 'Captcha sem imagem capturada.'
    solver.assert_not_called()
    campo.send_keys.assert_not_called()


def test_codigo_vazio_nao_preenche_campo():
    campo = MagicMock()
    with patch.object(captcha_img, 'solve_normal_captcha', return_value={'code': '   '}):
        ok, msg = captcha_img.resolver_captcha_imagem(_img(), campo, config={})
    assert ok is False
    assert msg == 'Resposta do 2captcha vazia.'
    campo.send_keys.assert_not_called()


def test_solver_levanta_retorna_erro_sem_propagar():
    with patch.object(captcha_img, 'solve_normal_captcha', side_effect=RuntimeError('boom')):
        ok, msg = captcha_img.resolver_captcha_imagem(_img(), MagicMock(), config={})
    assert ok is False
    assert msg.startswith('Falha ao resolver captcha:')
    assert 'boom' in msg


def test_imagem_carregada_true_quando_naturalwidth_positivo():
    el = MagicMock()
    el.get_attribute.return_value = '300'
    assert captcha_img._imagem_carregada(el) is True


def test_imagem_carregada_false_quando_nao_carregou():
    el = MagicMock()
    el.get_attribute.return_value = '0'
    assert captcha_img._imagem_carregada(el) is False


def test_imagem_carregada_false_para_none_ou_erro():
    assert captcha_img._imagem_carregada(None) is False
    el = MagicMock()
    el.get_attribute.side_effect = RuntimeError('stale')
    assert captcha_img._imagem_carregada(el) is False
