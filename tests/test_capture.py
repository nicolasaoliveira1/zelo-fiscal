"""Testes da captura de contexto na falha Selenium (screenshot + HTML)."""
import os
import inspect
import time

from app.automation import capture


class _FakeDriver:
    def __init__(self, fail_screenshot=False, fail_html=False):
        self.page_source = '<html><body>erro</body></html>'
        self._fail_shot = fail_screenshot
        self._fail_html = fail_html

    def save_screenshot(self, caminho):
        if self._fail_shot:
            raise RuntimeError('sem screenshot')
        with open(caminho, 'wb') as f:
            f.write(b'\x89PNG\r\n')
        return True

    @property
    def page_source(self):
        if self._fail_html:
            raise RuntimeError('sem html')
        return self._page

    @page_source.setter
    def page_source(self, v):
        self._page = v


def test_captura_salva_screenshot_e_html(app, tmp_path):
    with app.app_context():
        app.config['SELENIUM_CAPTURE_DIR'] = str(tmp_path)
        app.config['SELENIUM_CAPTURE_ENABLED'] = True
        out = capture.capturar_contexto_falha(_FakeDriver(), 'fgts', certidao_id=7)

    assert 'screenshot' in out and 'html' in out
    assert os.path.exists(out['screenshot']) and os.path.exists(out['html'])
    assert 'cert7' in os.path.basename(out['screenshot'])
    assert open(out['html'], encoding='utf-8').read().startswith('<html>')


def test_captura_sem_driver_retorna_vazio(app, tmp_path):
    with app.app_context():
        app.config['SELENIUM_CAPTURE_DIR'] = str(tmp_path)
        assert capture.capturar_contexto_falha(None, 'fgts') == {}


def test_captura_desabilitada_retorna_vazio(app, tmp_path):
    with app.app_context():
        app.config['SELENIUM_CAPTURE_DIR'] = str(tmp_path)
        app.config['SELENIUM_CAPTURE_ENABLED'] = False
        out = capture.capturar_contexto_falha(_FakeDriver(), 'fgts')
    assert out == {}
    assert not list(tmp_path.iterdir())


def test_captura_resiliente_a_falha_de_screenshot(app, tmp_path):
    # screenshot falha mas o HTML ainda e salvo
    with app.app_context():
        app.config['SELENIUM_CAPTURE_DIR'] = str(tmp_path)
        app.config['SELENIUM_CAPTURE_ENABLED'] = True
        out = capture.capturar_contexto_falha(_FakeDriver(fail_screenshot=True), 'rs')
    assert 'screenshot' not in out
    assert 'html' in out and os.path.exists(out['html'])


def test_salvar_artefato_sanitizado_grava_exatamente_html_recebido(app, tmp_path):
    html_seguro = '<!doctype html><p>Metadado sintético</p>'
    with app.app_context():
        app.config['SELENIUM_CAPTURE_DIR'] = str(tmp_path)
        app.config['SELENIUM_CAPTURE_ENABLED'] = True
        caminho = capture.salvar_artefato_sanitizado(
            'nfse-drift', html_seguro, execution_id='execucao-sintetica')

    assert caminho is not None
    assert caminho.endswith('.html')
    assert open(caminho, encoding='utf-8').read() == html_seguro
    assert 'driver' not in inspect.signature(
        capture.salvar_artefato_sanitizado).parameters
    assert 'screenshot' not in inspect.signature(
        capture.salvar_artefato_sanitizado).parameters
    assert 'page_source' not in inspect.signature(
        capture.salvar_artefato_sanitizado).parameters


def test_salvar_artefato_sanitizado_falha_sem_mascarar_erro(app, tmp_path):
    destino_que_e_arquivo = tmp_path / 'não-é-diretório'
    destino_que_e_arquivo.write_text('ocupado', encoding='utf-8')
    with app.app_context():
        app.config['SELENIUM_CAPTURE_DIR'] = str(destino_que_e_arquivo)
        app.config['SELENIUM_CAPTURE_ENABLED'] = True

        assert capture.salvar_artefato_sanitizado('nfse-drift', '<p>seguro</p>') is None


def test_prune_remove_antigos_mantem_recentes(tmp_path):
    antigo = tmp_path / 'velho.png'
    novo = tmp_path / 'novo.html'
    antigo.write_bytes(b'x')
    novo.write_text('y', encoding='utf-8')
    # envelhece o arquivo "antigo" em 30 dias
    velho_ts = time.time() - 30 * 86400
    os.utime(antigo, (velho_ts, velho_ts))

    removidos = capture.prune_capturas(14, base_dir=str(tmp_path))

    assert removidos == 1
    assert not antigo.exists()
    assert novo.exists()


def test_prune_retencao_invalida_nao_remove(tmp_path):
    (tmp_path / 'a.png').write_bytes(b'x')
    assert capture.prune_capturas(0, base_dir=str(tmp_path)) == 0
    assert capture.prune_capturas('abc', base_dir=str(tmp_path)) == 0
