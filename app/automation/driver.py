"""Criação do WebDriver Chrome e política de auto-seleção de certificado (RS).

Extraído de routes.py (C1). Sem dependência do estado de lote.
"""
import os

try:
    import winreg
except ImportError:
    winreg = None

from flask import current_app
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService
from threading import Lock
from webdriver_manager.chrome import ChromeDriverManager

from app.automation import cert_policy
from app.automation.cert_policy import PoliticaCertificado
from app.services.execution_logger import log_event
from app.utils import get_config_value as _get_config_value, to_bool as _to_bool


def _get_chrome_profile_settings(profile_dir=None, profile_name=None):
    # Precedencia: argumento explicito > config/env > default. Os overrides
    # permitem um perfil dedicado (ex.: municipal) isolado do perfil Certidoes.
    if profile_dir is None or profile_name is None:
        try:
            cfg_dir = current_app.config.get('CHROME_PROFILE_DIR')
            cfg_name = current_app.config.get('CHROME_PROFILE_NAME')
        except RuntimeError:
            cfg_dir = os.environ.get('CHROME_PROFILE_DIR')
            cfg_name = os.environ.get('CHROME_PROFILE_NAME')
        if profile_dir is None:
            profile_dir = cfg_dir
        if profile_name is None:
            profile_name = cfg_name

    if not profile_dir:
        profile_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), '..', 'chrome-profile')
        )
    if not profile_name:
        profile_name = 'Certidoes'

    os.makedirs(profile_dir, exist_ok=True)
    return profile_dir, profile_name


def _politica_autoselect_rs():
    """Alvo do RS (pattern/indice/issuer/subject) a partir das envs RS_CERT_AUTOSELECT_*.

    Sem o gate de habilitado: a remocao do registro sempre valeu para o indice
    configurado, independente da flag (comportamento preservado — ND-002b).
    """
    return PoliticaCertificado(
        pattern=_get_config_value('RS_CERT_AUTOSELECT_PATTERN', 'https://www.sefaz.rs.gov.br'),
        indice=_get_config_value('RS_CERT_AUTOSELECT_POLICY_INDEX', '1'),
        issuer_cn=_get_config_value('RS_CERT_AUTOSELECT_ISSUER_CN', ''),
        subject_cn=_get_config_value('RS_CERT_AUTOSELECT_SUBJECT_CN', ''),
        prefixo_log='rs_autoselect',
    )


def _montar_politica_autoselect_rs():
    if not _to_bool(_get_config_value('RS_CERT_AUTOSELECT_ENABLED', False), False):
        return None

    return _politica_autoselect_rs().montar()


def _ativar_politica_autoselect_rs_temporaria():
    if not _montar_politica_autoselect_rs():
        return False

    return cert_policy.ativar(_politica_autoselect_rs())


def _desativar_politica_autoselect_rs_temporaria():
    cert_policy.desativar(_politica_autoselect_rs())


# Posicao usada para tirar a janela do lote do campo de visao. Nao e headless:
# o portal continua renderizando (medido — screenshot de elemento sai igual ao
# da janela visivel, inclusive em aba nova apos switch_to.window), so nao rouba
# o foco de quem esta trabalhando na maquina.
_POSICAO_FORA_DA_TELA = (-32000, -32000)
_TAMANHO_JANELA_BACKGROUND = (1920, 1080)

# Sem estas flags o Chrome trata janela invisivel como ociosa e estrangula
# renderizacao e timers. O lote espera ate 90s por download e tira screenshot de
# captcha; um renderizador em segundo plano transformaria isso em falha
# intermitente que so aparece com a janela escondida.
_FLAGS_SEM_THROTTLE_EM_BACKGROUND = (
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-background-timer-throttling",
)
# Uma flag --disable-features so: repetir a chave faz o Chrome honrar apenas a
# ultima, e a segunda apagaria silenciosamente a desativacao do DownloadBubble.
_FEATURES_DESLIGADAS = ("DownloadBubble", "DownloadBubbleV2")
_FEATURES_DESLIGADAS_BACKGROUND = ("CalculateNativeWinOcclusion",)


def _build_chrome_options(anonimo=True, usar_perfil=False, profile_dir=None,
                          profile_name=None, background=False):
    """Monta as opcoes do Chrome. `background=True` abre a janela fora da tela.

    Usado pelos lotes, que rodam sem operador junto: o chromedriver traz a
    janela para a frente a cada `switch_to.window` (`Target.activateTarget`) e
    na abertura, o que interrompia quem estava usando a maquina. Fora da tela a
    ativacao continua acontecendo e simplesmente nao aparece. Fluxos que pedem o
    operador (emissao individual, dry-run, sessao NFSe) NAO usam isto."""
    chrome_options = Options()
    features_off = list(_FEATURES_DESLIGADAS)
    if background:
        chrome_options.add_argument(
            "--window-position={},{}".format(*_POSICAO_FORA_DA_TELA))
        chrome_options.add_argument(
            "--window-size={},{}".format(*_TAMANHO_JANELA_BACKGROUND))
        for flag in _FLAGS_SEM_THROTTLE_EM_BACKGROUND:
            chrome_options.add_argument(flag)
        features_off.extend(_FEATURES_DESLIGADAS_BACKGROUND)
    else:
        chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument("--disable-features=" + ",".join(features_off))
    chrome_options.add_argument("--no-first-run")
    chrome_options.add_argument("--no-default-browser-check")

    downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")
    chrome_options.add_experimental_option('prefs', {
        'download.default_directory': downloads_dir,
        'download.prompt_for_download': False,
        'download.directory_upgrade': True,
        'download.open_pdf_in_system_reader': False,
        'profile.default_content_setting_values.automatic_downloads': 1,
        'safebrowsing.enabled': True,
        'plugins.always_open_pdf_externally': True,
    })

    if anonimo:
        chrome_options.add_argument("--incognito")

    if usar_perfil:
        profile_dir, profile_name = _get_chrome_profile_settings(profile_dir, profile_name)
        if profile_dir:
            chrome_options.add_argument(f"--user-data-dir={profile_dir}")
        if profile_name:
            chrome_options.add_argument(f"--profile-directory={profile_name}")

    return chrome_options


def _configurar_download_automatico_chrome(driver):
    downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")

    try:
        driver.execute_cdp_cmd('Page.setDownloadBehavior', {
            'behavior': 'allow',
            'downloadPath': downloads_dir,
        })
    except Exception:
        pass

    try:
        driver.execute_cdp_cmd('Browser.setDownloadBehavior', {
            'behavior': 'allow',
            'downloadPath': downloads_dir,
            'eventsEnabled': False,
        })
    except Exception:
        pass

    try:
        info = driver.execute_cdp_cmd('Target.getTargetInfo', {})
        target_info = (info or {}).get('targetInfo') or {}
        browser_context_id = target_info.get('browserContextId')
        if browser_context_id:
            driver.execute_cdp_cmd('Browser.setDownloadBehavior', {
                'behavior': 'allow',
                'downloadPath': downloads_dir,
                'eventsEnabled': False,
                'browserContextId': browser_context_id,
            })
    except Exception:
        pass


def _criar_driver_chrome(anonimo=True, usar_perfil=False, background=False):
    chrome_options = _build_chrome_options(
        anonimo=anonimo, usar_perfil=usar_perfil, background=background)
    driver = webdriver.Chrome(service=ChromeService(
        ChromeDriverManager().install()), options=chrome_options)

    if background:
        # Com perfil persistente o Chrome restaura a ultima geometria e ignora
        # --window-position; reposiciona explicitamente (mesmo motivo do
        # maximize_window no driver uc). Best-effort: janela na frente incomoda,
        # nao quebra o lote.
        try:
            driver.set_window_position(*_POSICAO_FORA_DA_TELA)
        except Exception as exc:
            log_event('janela_background_falhou', level='WARNING', error=str(exc))

    try:
        _configurar_download_automatico_chrome(driver)
    except Exception as exc:
        log_event('download_config_chrome_failed', level='WARNING', error=str(exc))

    return driver


class UcIndisponivelError(RuntimeError):
    """undetected-chromedriver indisponivel: nao instalado ou nao foi possivel
    iniciar o navegador (ex.: versao do Chrome incompativel com o ChromeDriver).
    Carrega mensagem e acao acionaveis para o frontend."""

    def __init__(self, message, acao=None):
        super().__init__(message)
        self.message = message
        self.acao = acao


def _detectar_chrome_version_main():
    """Major version do Chrome instalado, para o uc baixar o ChromeDriver
    compativel (evita 'This version of ChromeDriver only supports Chrome X').

    Precedencia: env CHROME_UC_VERSION_MAIN > registro do Windows (BLBeacon) >
    None (deixa o uc tentar auto-detectar)."""
    env = (os.environ.get('CHROME_UC_VERSION_MAIN') or '').strip()
    if env.isdigit():
        return int(env)

    if os.name == 'nt' and winreg is not None:
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, r'Software\Google\Chrome\BLBeacon') as chave:
                    versao, _ = winreg.QueryValueEx(chave, 'version')
            except OSError:
                continue
            major = str(versao).split('.')[0]
            if major.isdigit():
                return int(major)

    return None


_MUNICIPAL_PROFILE_LOCK = Lock()


def _municipal_profile_acquire(blocking=False):
    """Adquire o lock do perfil municipal (um Chrome por vez nesse perfil).
    Retorna True se adquiriu, False se ja estava em uso (blocking=False)."""
    return _MUNICIPAL_PROFILE_LOCK.acquire(blocking=blocking)


def _municipal_profile_release():
    """Libera o lock do perfil municipal. Idempotente: liberar sem ter
    adquirido nao lanca (seguro de chamar em finally)."""
    try:
        _MUNICIPAL_PROFILE_LOCK.release()
    except RuntimeError:
        pass


def _get_municipal_profile_settings():
    """Resolve (pasta, nome) do perfil dedicado aos fluxos municipais IPM.

    Precedencia da pasta: env CHROME_PROFILE_MUNICIPAL_DIR > default sibling
    'chrome-profile-municipal'. Nome fixo 'Municipal', isolado do perfil
    'Certidoes' usado por RS/Federal (evita disputa de lock e contaminacao)."""
    profile_dir = (os.environ.get('CHROME_PROFILE_MUNICIPAL_DIR') or '').strip()
    if not profile_dir:
        profile_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), '..', 'chrome-profile-municipal')
        )
    return _get_chrome_profile_settings(profile_dir=profile_dir, profile_name='Municipal')


def _criar_driver_uc(profile_dir=None, profile_name=None):
    """Cria um Chrome via undetected-chromedriver com perfil persistente
    dedicado ao municipal, para elevar o score anti-bot do IPM Atende.Net e
    reaproveitar o cookie de clearance entre execucoes.

    Levanta UcIndisponivelError quando o pacote nao e importavel ou o
    navegador nao pode ser iniciado (fail-fast, sem fallback para incognito)."""
    try:
        import undetected_chromedriver as uc
    except ImportError as exc:
        raise UcIndisponivelError(
            'Driver anti-bloqueio nao instalado: instale undetected-chromedriver e setuptools no servidor.',
            acao='Rode iniciar.bat ou "pip install -r requirements.txt" no venv e tente de novo.',
        ) from exc

    if profile_dir is None and profile_name is None:
        profile_dir, profile_name = _get_municipal_profile_settings()

    options = _build_chrome_options(
        anonimo=False, usar_perfil=True,
        profile_dir=profile_dir, profile_name=profile_name,
    )

    try:
        driver = uc.Chrome(options=options, version_main=_detectar_chrome_version_main())
    except Exception as exc:
        log_event('uc_driver_start_failed', level='WARNING', error=str(exc))
        primeira_linha = (str(exc).strip().splitlines() or [''])[0]
        raise UcIndisponivelError(
            'Driver anti-bloqueio nao pode iniciar o Chrome: ' + primeira_linha,
            acao='Verifique a versao do Chrome instalada (o ChromeDriver pode estar incompativel). '
                 'Defina CHROME_UC_VERSION_MAIN se necessario. Detalhes no log pelo req_id.',
        ) from exc

    try:
        _configurar_download_automatico_chrome(driver)
    except Exception as exc:
        log_event('download_config_chrome_failed', level='WARNING', error=str(exc))

    # Perfil persistente restaura o ultimo tamanho da janela e ignora
    # --start-maximized; forca a maximizacao explicitamente.
    try:
        driver.maximize_window()
    except Exception as exc:
        log_event('uc_maximize_failed', level='WARNING', error=str(exc))

    return driver
