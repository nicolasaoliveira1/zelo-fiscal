"""Spike de recon do portal Federal (RFB/PGFN) — spec 07 / COV-01.

Rodada 3: o gate confirmado e a REPUTACAO da sessao no hCaptcha invisivel
(perfil novo do uc -> 023; Chrome do dia a dia -> emite). Este spike aponta o uc
para um perfil configuravel (por env) para testar se um perfil COM reputacao
(ex.: copia do perfil real do Chrome) passa do erro 023.

NAO emite em lote nem salva nada. Abre o navegador, deixa voce emitir manualmente
o CNPJ real e coleta se apareceu 023 ou nao.

Variaveis de ambiente (opcionais):
    FEDERAL_PROFILE_DIR   pasta do user-data-dir (default: chrome-profile-federal-recon)
    FEDERAL_PROFILE_NAME  nome do profile-directory (default: Federal)
    CHROME_UC_VERSION_MAIN  major do Chrome, se a auto-deteccao falhar

Como rodar:
    python scripts/recon_federal.py
"""
import os
import time

URL_FEDERAL = "https://servicos.receitafederal.gov.br/servico/certidoes/#/home/cnpj"

DEFAULT_PROFILE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "chrome-profile-federal-recon")
)
PROFILE_DIR = os.environ.get("FEDERAL_PROFILE_DIR") or DEFAULT_PROFILE_DIR
PROFILE_NAME = os.environ.get("FEDERAL_PROFILE_NAME") or "Federal"


def detectar_version_main():
    env = (os.environ.get("CHROME_UC_VERSION_MAIN") or "").strip()
    if env.isdigit():
        return int(env)
    try:
        import winreg
    except ImportError:
        return None
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, r"Software\Google\Chrome\BLBeacon") as chave:
                versao, _ = winreg.QueryValueEx(chave, "version")
        except OSError:
            continue
        major = str(versao).split(".")[0]
        if major.isdigit():
            return int(major)
    return None


DIAG_JS = r"""
const out = {};
out.url = location.href;
out.webdriver = navigator.webdriver;
out.captchaResponse = Array.from(document.querySelectorAll(
    'textarea[name="h-captcha-response"], textarea[name="g-recaptcha-response"]'))
    .map(t => ({ name: t.name, len: (t.value || '').length }));
const bt = (document.body ? document.body.innerText : "");
const m = bt.match(/Mensagem de Aviso[\s\S]{0,200}/i);
out.avisoMsg = m ? m[0].replace(/\s+/g, ' ').trim() : null;
out.temErro023 = /\b023\b/.test(bt) && /nao foi possivel|não foi possível/i.test(bt);
out.cookies = document.cookie.split(';').map(c => c.trim().split('=')[0]).filter(Boolean);
return out;
"""


def dump(driver, rotulo):
    print(f"\n=== DIAGNOSTICO [{rotulo}] ===")
    try:
        data = driver.execute_script(DIAG_JS)
    except Exception as exc:
        print(f"  (falha ao coletar: {exc})")
        return
    print(f"  url            : {data.get('url')}")
    print(f"  webdriver      : {data.get('webdriver')}")
    print(f"  captchaResponse: {data.get('captchaResponse')}")
    print(f"  temErro023     : {data.get('temErro023')}")
    print(f"  avisoMsg       : {data.get('avisoMsg')}")
    print(f"  cookies        : {data.get('cookies')}")


def _auto_aceitar_cookies(driver):
    try:
        botoes = driver.find_elements("xpath",
            "//button[normalize-space()='Aceitar' or contains(., 'Aceitar')]")
        for b in botoes:
            try:
                if b.is_displayed():
                    b.click()
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def main():
    print("Spike recon Federal (rodada 3 — reputacao de perfil) — spec 07")
    print(f"Perfil: {PROFILE_DIR} (nome={PROFILE_NAME})")
    try:
        import undetected_chromedriver as uc
    except ImportError:
        print("ERRO: undetected-chromedriver nao instalado no venv.")
        return

    os.makedirs(PROFILE_DIR, exist_ok=True)
    options = uc.ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument(f"--user-data-dir={PROFILE_DIR}")
    options.add_argument(f"--profile-directory={PROFILE_NAME}")

    version_main = detectar_version_main()
    print(f"version_main detectado: {version_main}")

    driver = None
    try:
        driver = uc.Chrome(options=options, version_main=version_main)
        driver.get(URL_FEDERAL)
        print("\nAguardando 8s o SPA renderizar...")
        time.sleep(8)
        _auto_aceitar_cookies(driver)
        dump(driver, "logo apos carregar")

        print("\n" + "=" * 64)
        print("NA TELA: digite o CNPJ REAL e clique em 'Emitir Certidao'.")
        print("Espere aparecer o 023 OU a certidao. So entao tecle ENTER.")
        print("=" * 64)
        input("\n>> ENTER para coletar o resultado...")
        dump(driver, "apos emitir")
        veredito = driver.execute_script(
            "return /\b023\b/.test(document.body.innerText) ? 'DEU 023' : 'SEM 023 (verifique se emitiu)';")
        print(f"\n>>> VEREDITO: {veredito}")
        input("\n>> ENTER para fechar.")
    except Exception as exc:
        print(f"\nERRO ao iniciar/dirigir o uc: {exc}")
        print("Se for versao do ChromeDriver, defina CHROME_UC_VERSION_MAIN e rode de novo.")
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    main()
