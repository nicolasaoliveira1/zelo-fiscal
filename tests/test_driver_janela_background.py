"""Janela do lote fora da tela (sem roubar o foco de quem usa a maquina).

O chromedriver traz a janela para a frente a cada `switch_to.window`
(Target.activateTarget) — o PDF em aba nova do Imbe, o fechamento de abas do
FGTS e do RS. Como o lote roda sem operador junto, a janela vai para fora da
tela. NAO e headless: o portal precisa renderizar (screenshot do captcha).
"""
import pytest

from app.automation import driver
from app.routes import lotes


def _args(**kwargs):
    return driver._build_chrome_options(**kwargs).arguments


# --- opcoes do Chrome -------------------------------------------------------

def test_background_tira_a_janela_da_tela():
    args = _args(background=True)
    assert any(a.startswith('--window-position=-32000,-32000') for a in args), args
    # maximizar traria a janela de volta para a area visivel
    assert '--start-maximized' not in args


def test_background_desliga_o_estrangulamento_de_janela_invisivel():
    """Sem isto o Chrome trata janela escondida como ociosa: o screenshot do
    captcha e a espera de 90s pelo download viram falha intermitente."""
    args = _args(background=True)
    for flag in ('--disable-backgrounding-occluded-windows',
                 '--disable-renderer-backgrounding',
                 '--disable-background-timer-throttling'):
        assert flag in args, (flag, args)


def test_uma_flag_disable_features_so():
    """O Chrome honra apenas a ULTIMA --disable-features; repetir a chave
    apagaria em silencio a desativacao do DownloadBubble (que abre popup de
    download no meio do lote)."""
    features = [a for a in _args(background=True) if a.startswith('--disable-features=')]
    assert len(features) == 1, features
    valores = features[0].split('=', 1)[1].split(',')
    assert 'DownloadBubble' in valores
    assert 'DownloadBubbleV2' in valores
    assert 'CalculateNativeWinOcclusion' in valores


def test_sem_background_nada_muda():
    """Emissao individual, dry-run e sessao NFSe seguem com janela visivel — la
    o operador precisa ver (e na NFSe a revisao humana e obrigatoria, ND-005)."""
    args = _args(background=False)
    assert '--start-maximized' in args
    assert not any(a.startswith('--window-position=') for a in args)
    assert not any(a.startswith('--disable-backgrounding') for a in args)
    features = [a for a in args if a.startswith('--disable-features=')]
    assert features == ['--disable-features=DownloadBubble,DownloadBubbleV2'], features


# --- ligacao dos lotes ------------------------------------------------------

@pytest.fixture()
def chamadas(monkeypatch):
    registro = []
    monkeypatch.setattr(lotes, '_criar_driver_chrome',
                        lambda **kw: registro.append(kw) or 'driver-falso')
    return registro


def test_lote_pede_janela_em_background(app, chamadas):
    with app.app_context():
        assert lotes._criar_driver_lote() == 'driver-falso'
    assert chamadas == [{'background': True}]


def test_lote_preserva_os_kwargs_do_chamador(app, chamadas):
    """O lote do RS usa perfil persistente e nao-anonimo; o background nao pode
    engolir esses argumentos."""
    with app.app_context():
        lotes._criar_driver_lote(anonimo=False, usar_perfil=True)
    assert chamadas == [{'background': True, 'anonimo': False, 'usar_perfil': True}]


def test_operador_pode_ver_o_lote_na_tela(app, chamadas):
    with app.app_context():
        anterior = app.config.get('LOTE_JANELA_BACKGROUND')
        app.config['LOTE_JANELA_BACKGROUND'] = False
        try:
            lotes._criar_driver_lote()
        finally:
            app.config['LOTE_JANELA_BACKGROUND'] = anterior
    assert chamadas == [{'background': False}]


def test_flag_existe_na_config_do_app(app):
    """Sem a chave declarada em config.py, LOTE_JANELA_BACKGROUND no .env nunca
    chegaria ao app.config e o desligamento seria ignorado em silencio."""
    assert 'LOTE_JANELA_BACKGROUND' in app.config
    assert app.config['LOTE_JANELA_BACKGROUND'] is True
