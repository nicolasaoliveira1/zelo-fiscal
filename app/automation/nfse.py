"""Automacao do Emissor Nacional de NFS-e (nfse.gov.br).

Todos os seletores foram capturados na recon assistida (T0) contra o portal
real; nenhum e suposto. Ver `.specs/features/nfse-honorarios/recon.md`.

Duas armadilhas do portal ditam o desenho deste modulo:

1. **Os radios repetem o mesmo `id` entre as opcoes do grupo.** `Tomador.LocalDomicilio`
   aparece tres vezes com o id `Tomador_LocalDomicilio` ("nao informado", "Brasil",
   "Exterior"). Localizar por id pega o primeiro, quase sempre o errado. Por isso
   `_marcar_radio` localiza por `input[name=...][value=...]`, sempre.

2. **Os selects ficam ocultos atras do plugin Chosen** (`class="form-chosen"`,
   `style="display:none"`), que desenha a UI real. `Select().select_by_value()`
   no elemento oculto altera o DOM mas nao dispara o comportamento da tela:
   falha SILENCIOSA, o pior modo de falha possivel quando o resultado e um
   documento fiscal. `_set_chosen` usa a API do plugin e **confere o valor
   depois de setar** (ND-008).
"""
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By

BASE = 'https://www.nfse.gov.br/EmissorNacional'
URL_LOGIN = f'{BASE}/Login?ReturnUrl=%2fEmissorNacional'
URL_CONFIGURACAO = f'{BASE}/Perfil/Configuracao'
URL_NOVA_DPS = f'{BASE}/DPS/Pessoas'

# Trecho de path que identifica cada etapa. A deteccao usa o PATH, nunca o
# token `idr` da querystring (opaco e por rascunho).
PATH_DASHBOARD = '/EmissorNacional/Dashboard'
PATH_ETAPA_PESSOAS = '/DPS/Pessoas'
PATH_ETAPA_SERVICO = '/DPS/Servico'
PATH_ETAPA_TRIBUTACAO = '/DPS/Tributacao'
PATH_REVISAO = '/DPS/EmitirNFSe'
PATH_CONFIRMACAO = '/DPS/NFSe'

SEL_LINK_CERTIFICADO = 'a.img-certificado'
ID_ALIQUOTA_SN = 'AliquotaSimplesNacional'
ID_BTN_AVANCAR = 'btnAvancar'
# Botao que EMITE de fato. So o P3 (opt-in) clica nele; P1 e P2 param antes.
ID_BTN_EMITIR = 'btnProsseguir'
# Presente so na tela de confirmacao: e o sinal de "nota emitida" do lote (P2).
ID_BTN_DANFSE = 'btnDownloadDANFSE'


class InteracaoPortalError(RuntimeError):
    """Interacao com o portal nao produziu o efeito esperado.

    Existe para transformar falha silenciosa em erro alto: um select que nao
    pegou o valor precisa parar a emissao, nao seguir e gerar nota errada."""


# --- primitivas de interacao ----------------------------------------------

_JS_CHOSEN = """
var el = document.getElementById(arguments[0]);
if (!el) { return 'ausente'; }
var jq = window.jQuery || window.$;
if (!jq) { return 'sem-jquery'; }
jq(el).val(arguments[1]).trigger('chosen:updated').trigger('change');
return el.value;
"""


def _set_chosen(driver, elemento_id, valor):
    """Escolhe uma opcao de select oculto atras do plugin Chosen.

    Confere o valor apos setar e levanta se nao pegou: sem essa checagem, um
    seletor que mudou de nome no portal passaria despercebido e a nota sairia
    com o campo em branco (ND-008)."""
    resultado = driver.execute_script(_JS_CHOSEN, elemento_id, str(valor))
    if resultado == 'ausente':
        raise InteracaoPortalError(
            f'Campo "{elemento_id}" nao existe na pagina. O portal pode ter '
            'mudado o formulario; refaca a recon.')
    if resultado == 'sem-jquery':
        raise InteracaoPortalError(
            f'jQuery indisponivel na pagina ao preencher "{elemento_id}".')
    if str(resultado) != str(valor):
        raise InteracaoPortalError(
            f'Campo "{elemento_id}" nao aceitou o valor "{valor}" '
            f'(ficou "{resultado}"). Confira a configuracao da NFSe.')
    return resultado


def _marcar_radio(driver, name, valor):
    """Marca a opcao de um grupo de radio por (name, value).

    NUNCA por id: no portal os tres radios do mesmo grupo compartilham o id."""
    seletor = f'input[name="{name}"][value="{valor}"]'
    try:
        elemento = driver.find_element(By.CSS_SELECTOR, seletor)
    except WebDriverException as exc:
        raise InteracaoPortalError(
            f'Opcao "{valor}" do grupo "{name}" nao encontrada na pagina.') from exc
    driver.execute_script('arguments[0].click();', elemento)
    return elemento


def _preencher(driver, elemento_id, valor):
    """Limpa e preenche um input de texto comum."""
    try:
        elemento = driver.find_element(By.ID, elemento_id)
    except WebDriverException as exc:
        raise InteracaoPortalError(
            f'Campo "{elemento_id}" nao encontrado na pagina.') from exc
    elemento.clear()
    elemento.send_keys(str(valor))
    return elemento


# --- deteccao de etapa ----------------------------------------------------

def _tem_elemento(driver, elemento_id):
    try:
        driver.find_element(By.ID, elemento_id)
        return True
    except WebDriverException:
        return False


def _url(driver):
    try:
        return driver.current_url or ''
    except WebDriverException:
        return ''


def esperar_revisao(driver):
    """True quando o navegador esta na tela de revisao, com o botao de emitir.

    Exige path E elemento: so a URL nao basta porque uma pagina de erro no
    mesmo path passaria."""
    return PATH_REVISAO in _url(driver) and _tem_elemento(driver, ID_BTN_EMITIR)


def detectar_emitida(driver):
    """True quando a nota foi emitida (tela de confirmacao).

    E o sinal que faz o lote assistido avancar para a proxima nota (P2). Exige
    o botao de download do DANFSe, que so existe depois da emissao."""
    return PATH_CONFIRMACAO in _url(driver) and _tem_elemento(driver, ID_BTN_DANFSE)
