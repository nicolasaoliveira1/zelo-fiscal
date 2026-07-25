"""Testes do fluxo CNDT/Trabalhista (spec 07, TRAB-01/04/05).

Sem Selenium/rede: `_localizar`, `_clicar_submit`, `_aguardar_sucesso` e o núcleo
`captcha_img.resolver_captcha_imagem` são mockados. Foco no retry + contrato.
"""
from unittest.mock import MagicMock, patch

import pytest

from app.automation import trabalhista


@pytest.fixture(autouse=True)
def _stub_espera_imagem(monkeypatch):
    # A espera real usa WebDriverWait (bloquearia 8s com mocks). Stub p/ os testes
    # do resolver; o predicado _imagem_carregada e testado direto abaixo.
    monkeypatch.setattr(trabalhista, '_esperar_imagem_carregada', lambda *a, **k: True)


def _patches(resolver_ret, submit_ret=True, aguardar_side=None, aguardar_ret=True):
    """Contexto com os pontos de I/O mockados. resolver_ret pode ser um valor
    (mesmo em toda tentativa) ou uma lista (side_effect por tentativa)."""
    p_loc = patch.object(trabalhista, '_localizar', return_value=MagicMock())
    if isinstance(resolver_ret, list):
        p_res = patch.object(trabalhista.captcha_img, 'resolver_captcha_imagem', side_effect=resolver_ret)
    else:
        p_res = patch.object(trabalhista.captcha_img, 'resolver_captcha_imagem', return_value=resolver_ret)
    p_sub = patch.object(trabalhista, '_clicar_submit', return_value=submit_ret)
    if aguardar_side is not None:
        p_agu = patch.object(trabalhista, '_aguardar_sucesso', side_effect=aguardar_side)
    else:
        p_agu = patch.object(trabalhista, '_aguardar_sucesso', return_value=aguardar_ret)
    return p_loc, p_res, p_sub, p_agu


def test_sucesso_primeira_tentativa():
    p_loc, p_res, p_sub, p_agu = _patches((True, 'ABC123'), aguardar_ret=True)
    with p_loc, p_res as res, p_sub as sub, p_agu:
        ok, msg = trabalhista.resolver_captcha_e_submeter(
            MagicMock(), {}, houve_sucesso=lambda: True, espera_pos_submit=0)
    assert (ok, msg) == (True, None)
    assert res.call_count == 1
    assert sub.call_count == 1


def test_sucesso_na_segunda_apos_captcha_recusado():
    # 1ª tentativa: captcha resolvido mas sem download (recusado) -> retry;
    # 2ª tentativa: sucesso.
    p_loc, p_res, p_sub, p_agu = _patches((True, 'ABC123'), aguardar_side=[False, True])
    with p_loc, p_res as res, p_sub, p_agu:
        ok, msg = trabalhista.resolver_captcha_e_submeter(
            MagicMock(), {}, houve_sucesso=lambda: True, tentativas=2, espera_pos_submit=0)
    assert (ok, msg) == (True, None)
    assert res.call_count == 2


def test_falha_infra_captcha_aborta_sem_retry():
    # Falha de infraestrutura (2captcha vazio) NÃO dispara retry e não submete.
    p_loc, p_res, p_sub, p_agu = _patches((False, 'Resposta do 2captcha vazia.'))
    with p_loc, p_res as res, p_sub as sub, p_agu as agu:
        ok, msg = trabalhista.resolver_captcha_e_submeter(
            MagicMock(), {}, houve_sucesso=lambda: True, tentativas=2, espera_pos_submit=0)
    assert ok is False
    assert msg == 'Resposta do 2captcha vazia.'
    assert res.call_count == 1
    sub.assert_not_called()
    agu.assert_not_called()


def test_captcha_recusado_em_todas_retorna_erro_acionavel():
    p_loc, p_res, p_sub, p_agu = _patches((True, 'ABC123'), aguardar_ret=False)
    with p_loc, p_res as res, p_sub, p_agu:
        ok, msg = trabalhista.resolver_captcha_e_submeter(
            MagicMock(), {}, houve_sucesso=lambda: False, tentativas=2, espera_pos_submit=0)
    assert ok is False
    assert msg == 'Captcha do CNDT recusado (tentativa 2).'
    assert res.call_count == 2


def test_falha_ao_clicar_submit_retorna_erro():
    p_loc, p_res, p_sub, p_agu = _patches((True, 'ABC123'), submit_ret=False)
    with p_loc, p_res, p_sub, p_agu:
        ok, msg = trabalhista.resolver_captcha_e_submeter(
            MagicMock(), {}, houve_sucesso=lambda: True, espera_pos_submit=0)
    assert ok is False
    assert msg == 'Não foi possível clicar em Emitir Certidão (CNDT).'


def test_aguardar_sucesso_avalia_uma_vez_com_timeout_zero():
    assert trabalhista._aguardar_sucesso(lambda: True, 0) is True
    assert trabalhista._aguardar_sucesso(lambda: False, 0) is False


def test_aguardar_sucesso_trata_excecao_do_callback_como_nao():
    def boom():
        raise RuntimeError('x')
    assert trabalhista._aguardar_sucesso(boom, 0) is False


def test_imagem_carregada_true_quando_naturalwidth_positivo():
    el = MagicMock()
    el.get_attribute.return_value = '300'
    assert trabalhista._imagem_carregada(el) is True


def test_imagem_carregada_false_quando_nao_carregou():
    el = MagicMock()
    el.get_attribute.return_value = '0'
    assert trabalhista._imagem_carregada(el) is False


def test_imagem_carregada_false_para_none_ou_erro():
    assert trabalhista._imagem_carregada(None) is False
    el = MagicMock()
    el.get_attribute.side_effect = RuntimeError('stale')
    assert trabalhista._imagem_carregada(el) is False
