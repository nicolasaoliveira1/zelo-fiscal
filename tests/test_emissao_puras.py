"""Testes de funcoes puras de emissao.py — sem Selenium, sem rede.

Cobre suporte de lote municipal, normalizacao de texto, deteccao de
impedimento FGTS (driver falso) e o diff de downloads (snapshot/pick).
"""
import os
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from app.automation import emissao


def test_municipal_batch_suportado():
    assert emissao._municipal_batch_suportado('Imbé') is True
    assert emissao._municipal_batch_suportado('Tramandaí') is True
    assert emissao._municipal_batch_suportado('TRAMANDAI') is True
    assert emissao._municipal_batch_suportado('São Paulo') is False
    assert emissao._municipal_batch_suportado('') is False
    assert emissao._municipal_batch_suportado(None) is False


def test_fgts_normalizar_texto():
    assert emissao._fgts_normalizar_texto('Não   Cadastrado') == 'nao cadastrado'
    assert emissao._fgts_normalizar_texto('') == ''
    assert emissao._fgts_normalizar_texto(None) == ''


def _driver_com_texto(texto):
    driver = MagicMock()
    driver.find_element.return_value.text = texto
    return driver


def test_fgts_detectar_impedimento_insuficiente():
    driver = _driver_com_texto(
        'As informações disponíveis não são suficientes para a comprovação '
        'automática da regularidade do empregador perante o FGTS.'
    )
    msg = emissao._fgts_detectar_mensagem_impedimento(driver)
    assert msg is not None
    assert 'insuficientes' in msg.lower()


def test_fgts_detectar_impedimento_nao_cadastrado():
    driver = _driver_com_texto('Empregador não cadastrado.')
    assert emissao._fgts_detectar_mensagem_impedimento(driver) is not None


def test_fgts_detectar_impedimento_impedimentos_caixa():
    driver = _driver_com_texto(
        'Constam impedimentos na CAIXA para a comprovação da regularidade '
        'do empregador no FGTS.'
    )
    assert emissao._fgts_detectar_mensagem_impedimento(driver) is not None


def test_fgts_detectar_impedimento_fger0419():
    driver = _driver_com_texto('Erro FGER0419: operação não efetuada.')
    assert emissao._fgts_detectar_mensagem_impedimento(driver) is not None


def test_fgts_detectar_impedimento_texto_limpo():
    driver = _driver_com_texto('Certidão emitida com sucesso.')
    assert emissao._fgts_detectar_mensagem_impedimento(driver) is None


def test_fgts_status_por_data(app):
    with app.app_context():
        assert emissao._fgts_status_por_data(None) == 'status-cinza'
        assert emissao._fgts_status_por_data(date.today() + timedelta(days=365)) == 'status-verde'
        assert emissao._fgts_status_por_data(date.today() - timedelta(days=1)) == 'status-vermelho'


def test_snapshot_downloads_pdf(tmp_path, monkeypatch):
    downloads = tmp_path / 'Downloads'
    downloads.mkdir()
    (downloads / 'cert.pdf').write_bytes(b'%PDF-1.4 conteudo')
    (downloads / 'parcial.crdownload').write_bytes(b'x')
    (downloads / 'temp.tmp').write_bytes(b'x')
    (downloads / 'nota.txt').write_bytes(b'x')
    (downloads / 'subdir').mkdir()

    monkeypatch.setattr(os.path, 'expanduser', lambda _p: str(tmp_path))
    snap = emissao._snapshot_downloads_pdf()

    nomes = {os.path.basename(p) for p in snap}
    assert nomes == {'cert.pdf'}
    info = next(iter(snap.values()))
    assert 'mtime' in info and 'size' in info


def test_pick_changed_download_arquivo_novo(monkeypatch):
    antes = {'/dl/a.pdf': {'mtime': 100.0, 'size': 10}}
    agora = {
        '/dl/a.pdf': {'mtime': 100.0, 'size': 10},
        '/dl/b.pdf': {'mtime': 200.0, 'size': 20},
    }
    monkeypatch.setattr(emissao, '_snapshot_downloads_pdf', lambda: agora)
    assert emissao._pick_changed_download_pdf(antes) == '/dl/b.pdf'


def test_pick_changed_download_modificado(monkeypatch):
    antes = {'/dl/a.pdf': {'mtime': 100.0, 'size': 10}}
    agora = {'/dl/a.pdf': {'mtime': 150.0, 'size': 10}}
    monkeypatch.setattr(emissao, '_snapshot_downloads_pdf', lambda: agora)
    assert emissao._pick_changed_download_pdf(antes) == '/dl/a.pdf'


def test_pick_changed_download_sem_mudanca(monkeypatch):
    antes = {'/dl/a.pdf': {'mtime': 100.0, 'size': 10}}
    agora = {'/dl/a.pdf': {'mtime': 100.0, 'size': 10}}
    monkeypatch.setattr(emissao, '_snapshot_downloads_pdf', lambda: agora)
    assert emissao._pick_changed_download_pdf(antes) is None


def test_pick_changed_download_mais_recente(monkeypatch):
    antes = {}
    agora = {
        '/dl/a.pdf': {'mtime': 100.0, 'size': 10},
        '/dl/b.pdf': {'mtime': 300.0, 'size': 20},
        '/dl/c.pdf': {'mtime': 200.0, 'size': 30},
    }
    monkeypatch.setattr(emissao, '_snapshot_downloads_pdf', lambda: agora)
    assert emissao._pick_changed_download_pdf(antes) == '/dl/b.pdf'


def test_classificar_grave_browser_closed_e_fatal():
    # RESIL-04: excecao que indica navegador/sessao morta vira GRAVE_FATAL, para
    # que o lote automatico pare em vez de repetir os proximos itens com o driver
    # morto. (_erro_indica_navegador_fechado casa pelo marcador de texto.)
    from app.services.batch_engine import GRAVE_FATAL
    exc = Exception('chrome not reachable')
    assert emissao._classificar_grave(exc) == GRAVE_FATAL


def test_classificar_grave_comum_e_true():
    # RESIL-01: um erro grave "comum" (nao relacionado a driver morto) continua
    # sendo True — no lote automatico vira falha por-item e o loop segue.
    exc = ValueError('timeout aguardando download')
    assert emissao._classificar_grave(exc) is True


def test_imbe_captcha_delega_ao_nucleo_sucesso(monkeypatch):
    # T2/AD-021: o IMBE delega ao nucleo compartilhado (captcha_img) e mapeia o
    # sucesso para o contrato historico (True, None), passando os elementos ja
    # localizados pelas heuristicas do IMBE.
    imagem, campo = MagicMock(), MagicMock()
    monkeypatch.setattr(emissao, '_imbe_encontrar_captcha_imagem', lambda drv: imagem)
    monkeypatch.setattr(emissao, '_imbe_encontrar_campo_captcha', lambda drv: campo)
    with patch.object(emissao.captcha_img, 'resolver_captcha_imagem', return_value=(True, 'XYZ9')) as core:
        resultado = emissao._imbe_resolver_captcha_2captcha(MagicMock(), execution_id='e1')
    assert resultado == (True, None)
    core.assert_called_once_with(imagem, campo, execution_id='e1')


def test_imbe_captcha_delega_ao_nucleo_falha(monkeypatch):
    # Falha do nucleo propaga a mensagem no contrato (False, mensagem).
    monkeypatch.setattr(emissao, '_imbe_encontrar_captcha_imagem', lambda drv: MagicMock())
    monkeypatch.setattr(emissao, '_imbe_encontrar_campo_captcha', lambda drv: MagicMock())
    with patch.object(
        emissao.captcha_img, 'resolver_captcha_imagem',
        return_value=(False, 'Resposta do 2captcha vazia.'),
    ):
        resultado = emissao._imbe_resolver_captcha_2captcha(MagicMock())
    assert resultado == (False, 'Resposta do 2captcha vazia.')


def test_imbe_captcha_sem_imagem_nao_chama_nucleo(monkeypatch):
    # Fast-fail preservado: sem imagem, retorna a mensagem historica e NAO chama o nucleo.
    monkeypatch.setattr(emissao, '_imbe_encontrar_captcha_imagem', lambda drv: None)
    with patch.object(emissao.captcha_img, 'resolver_captcha_imagem') as core:
        resultado = emissao._imbe_resolver_captcha_2captcha(MagicMock())
    assert resultado == (False, 'Imagem do captcha não encontrada.')
    core.assert_not_called()


def test_imbe_captcha_sem_campo_nao_chama_nucleo(monkeypatch):
    monkeypatch.setattr(emissao, '_imbe_encontrar_captcha_imagem', lambda drv: MagicMock())
    monkeypatch.setattr(emissao, '_imbe_encontrar_campo_captcha', lambda drv: None)
    with patch.object(emissao.captcha_img, 'resolver_captcha_imagem') as core:
        resultado = emissao._imbe_resolver_captcha_2captcha(MagicMock())
    assert resultado == (False, 'Campo do captcha não encontrado.')
    core.assert_not_called()


def test_imbe_encontrar_captcha_uma_unica_espera(monkeypatch):
    # Correcao de latencia: a imagem do captcha do IMBE e localizada numa UNICA
    # espera com uniao de seletores, em vez de 4 esperas seriais que gastavam ate
    # `timeout` cada uma antes de casar. Sem serie -> extracao rapida como no CNDT.
    esperas = {'n': 0}
    loc = {}

    class _FakeWait:
        def __init__(self, driver, timeout):
            esperas['n'] += 1

        def until(self, cond):
            return 'ELEMENTO'

    monkeypatch.setattr(emissao, 'WebDriverWait', _FakeWait)
    monkeypatch.setattr(
        emissao.EC, 'presence_of_element_located',
        lambda locator: loc.__setitem__('locator', locator),
    )

    res = emissao._imbe_encontrar_captcha_imagem(MagicMock())

    assert res == 'ELEMENTO'
    assert esperas['n'] == 1  # uma unica espera, nao 4 seriais
    _by, xpath = loc['locator']
    assert xpath.count(' | ') == 3  # uniao dos 4 seletores num so XPath
    assert 'palavra' in xpath and 'captcha' in xpath and 'verificacao' in xpath


def test_emitir_trabalhista_inexistente(app):
    # Guarda do lote: id sem certidao -> falha soft, sem abrir driver.
    with app.app_context():
        ok, grave, msg = emissao._emitir_trabalhista_certidao(999999)
    assert (ok, grave) == (False, False)
    assert msg == 'Certidão não encontrada.'


def test_emitir_trabalhista_tipo_errado(app, ids):
    # Guarda de tipo (TRAB-08/09): uma certidao FGTS nao passa pelo fluxo Trabalhista.
    with app.app_context():
        ok, grave, msg = emissao._emitir_trabalhista_certidao(ids['fgts'])
    assert (ok, grave) == (False, False)
    assert msg == 'Certidão não pertence ao fluxo Trabalhista.'
