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
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By

from app.services import nfse_contrato
from app.services.execution_logger import log_event

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
# Elemento que EMITE de fato. So o P3 (opt-in) clica nele; P1 e P2 param antes.
# Nao e <button>: e uma <a href="/EmissorNacional/DPS/NFSe?idr=...">, ou seja,
# navega em vez de submeter — quem for automatizar o P3 nao deve esperar spinner
# de submit, e sim a troca de path.
ID_BTN_EMITIR = 'btnProsseguir'

# --- sinais de "ja emitiu" -------------------------------------------------
# A tela de confirmacao traz DOIS sinais independentes: o botao de baixar o
# DANFSe e o alerta verde. Aceitar qualquer um dos dois e deliberado — o lote
# assistido fica parado esperando este sinal, e se o portal mexer no layout de
# um deles a espera trava ate o timeout em vez de reconhecer a nota emitida.
ID_BTN_DANFSE = 'btnDownloadDANFSE'
SEL_ALERTA_SUCESSO = 'div.alert-success'
TEXTO_EMISSAO_OK = 'gerada com sucesso'

# Quanto esperar a tela de revisao aparecer depois do ultimo "Avancar".
TIMEOUT_REVISAO = 20

# Quanto esperar o portal preencher sozinho emitente e tomador.
# Modulo-level para os testes reduzirem sem esperar o tempo real.
TIMEOUT_AUTOPREENCHIMENTO = 15


class InteracaoPortalError(RuntimeError):
    """Interacao com o portal nao produziu o efeito esperado.

    Existe para transformar falha silenciosa em erro alto: um select que nao
    pegou o valor precisa parar a emissao, nao seguir e gerar nota errada."""


# --- primitivas de interacao ----------------------------------------------

# Nenhum select do assistente e manipulavel por Select() do Selenium: o portal
# usa DOIS plugins que escondem o <select> real de formas diferentes — Select2
# (classe `select2-hidden-accessible`) no municipio e no codigo de tributacao,
# Chosen (`form-chosen` + display:none) no item da NBS. Esta via serve os dois:
# `change` atualiza Select2 e o select nativo, `chosen:updated` atualiza o
# Chosen; disparar o evento do outro plugin e inofensivo.
_JS_SELECIONAR = """
var el = document.getElementById(arguments[0]);
if (!el) { return 'ausente'; }
var valor = arguments[1];
var jq = window.jQuery || window.$;
if (jq) {
  jq(el).val(valor).trigger('change');
  jq(el).trigger('chosen:updated');
} else {
  el.value = valor;
  el.dispatchEvent(new Event('change', {bubbles: true}));
}
return el.value;
"""


def _selecionar(driver, elemento_id, valor):
    """Escolhe uma opcao de select, seja ele Select2, Chosen ou nativo.

    Confere o valor apos setar e levanta se nao pegou. Sem essa checagem, um
    seletor renomeado no portal — ou um codigo que nao existe mais na lista —
    passaria despercebido e a nota sairia com o campo em branco (ND-008)."""
    resultado = driver.execute_script(_JS_SELECIONAR, elemento_id, str(valor))
    if resultado == 'ausente':
        raise InteracaoPortalError(
            f'Campo "{elemento_id}" nao existe na pagina. O portal pode ter '
            'mudado o formulario; refaca a recon.')
    if str(resultado) != str(valor):
        raise InteracaoPortalError(
            f'Campo "{elemento_id}" nao aceitou o valor "{valor}" '
            f'(ficou "{resultado}"). O codigo pode nao existir mais na lista do '
            'portal — confira a configuracao da NFSe.')
    return resultado


def _marcar_radio(driver, name, valor):
    """Marca a opcao de um grupo de radio por (name, value).

    NUNCA por id: no portal os tres radios do mesmo grupo compartilham o id."""
    seletor = f'input[name="{name}"][value="{valor}"]'
    # radios sao CSS-hidden por desenho: nao exigir visibilidade (ver _localizar)
    elemento = _localizar(driver, By.CSS_SELECTOR, seletor, exigir_visivel=False)
    if elemento is None:
        raise InteracaoPortalError(
            f'Opcao "{valor}" do grupo "{name}" nao esta disponivel nesta tela.')
    driver.execute_script('arguments[0].click();', elemento)
    return elemento


def _preencher(driver, elemento_id, valor, sair=True):
    """Limpa e preenche um input de texto, saindo do campo ao final.

    `sair` simula um clique fora do campo depois de digitar, por dois motivos
    observados no portal:

    - o campo de data abre um datepicker que fica POR CIMA do proximo campo, e
      o clique seguinte falha com "element not interactable";
    - o portal so processa o valor quando o campo perde o foco — e e isso que
      dispara o preenchimento automatico do emitente e do tomador.

    Por teclado nao funciona: o TAB leva o foco ao botao "Abrir calendario" ao
    lado da data (o campo nunca sai de foco) e o ESC ABRE o datepicker em vez
    de fechar. Ver `_JS_SAIR`.
    """
    if not _esperar_interagivel(driver, elemento_id):
        raise InteracaoPortalError(
            f'O campo "{elemento_id}" nao ficou disponivel para preenchimento. '
            'A tela pode nao ter terminado de carregar ou o campo depende de '
            'uma escolha anterior que nao foi aplicada.')
    try:
        elemento = _localizar(driver, By.ID, elemento_id)
        elemento.clear()
        elemento.send_keys(str(valor))
    except (WebDriverException, AttributeError) as exc:
        raise InteracaoPortalError(
            f'Nao foi possivel preencher o campo "{elemento_id}": '
            f'{(str(exc).strip().splitlines() or [""])[0]}') from exc
    if sair:
        _sair_do_campo(elemento)
    return elemento


# Simula "clicar fora do campo", que e o que o operador faz na mao: confirma o
# valor digitado, fecha o datepicker e libera os campos seguintes. O mousedown
# no documento e a peca essencial — e nele que o datepicker fecha.
_JS_SAIR = (
    "var el = arguments[0];"
    "el.dispatchEvent(new Event('input', {bubbles: true}));"
    "el.dispatchEvent(new Event('change', {bubbles: true}));"
    "el.blur();"
    "el.dispatchEvent(new Event('focusout', {bubbles: true}));"
    "document.body.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));"
    "document.body.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));"
    "document.body.click();"
)


def _sair_do_campo(elemento):
    """Sai do campo como um clique fora faria, para o portal processar o valor.

    NAO usa teclado. Duas tentativas anteriores falharam por motivos opostos:
    o TAB leva o foco para o botao "Abrir calendario" que fica ao lado da data
    (o campo nunca sai de foco), e o ESC **abre** o datepicker em vez de fechar.
    O que funciona na mao e clicar fora — daqui sai o mousedown no documento,
    que e o evento em que o datepicker se fecha.
    """
    try:
        elemento.parent.execute_script(_JS_SAIR, elemento)
    except WebDriverException:
        pass


class ContratoNfseIncompativelError(InteracaoPortalError):
    """O contrato não consegue orientar o campo com segurança."""


# Chaves semânticas do contrato inicial. O valor do seletor continua vindo do
# snapshot; estas constantes são apenas a identidade estável usada pelo fluxo.
CHAVE_DATA_COMPETENCIA = 'DataCompetencia'
CHAVE_REGIME_APURACAO = 'SimplesNacional_RegimeApuracaoTributosSN'
CHAVE_DOMICILIO_TOMADOR = 'Tomador.LocalDomicilio'
CHAVE_INSCRICAO_TOMADOR = 'Tomador_Inscricao'
CHAVE_INSCRICAO_PRESTADOR = 'Prestador_Inscricao'
CHAVE_NOME_TOMADOR = 'Tomador_Nome'
CHAVE_MUNICIPIO_SERVICO = 'LocalPrestacao_CodigoMunicipioPrestacao'
CHAVE_PAIS_SERVICO = 'LocalPrestacao_CodigoPaisPrestacao'
CHAVE_TRIBUTACAO_NACIONAL = 'ServicoPrestado_CodigoTributacaoNacional'
CHAVE_EXPORTACAO_IMUNIDADE = 'ServicoPrestado.HaExportacaoImunidadeNaoIncidencia'
CHAVE_DESCRICAO_SERVICO = 'ServicoPrestado_Descricao'
CHAVE_ITEM_NBS = 'ServicoPrestado_CodigoNBS'
CHAVE_VALOR_SERVICO = 'Valores_ValorServico'
CHAVE_RETENCAO_ISSQN = 'ISSQN.HaRetencao'
CHAVE_PISCOFINS_SITUACAO = 'TributacaoFederal_PISCofins_SituacaoTributaria'
CHAVE_PISCOFINS_RETENCAO = 'TributacaoFederal_PISCofins_TipoRetencao'


def _contrato_execucao(contrato):
    return contrato or nfse_contrato.contrato_inicial_execucao()


def _campo_contrato(contrato, chave):
    try:
        return _contrato_execucao(contrato).campo(chave)
    except (AttributeError, nfse_contrato.ContratoNfseError) as exc:
        raise ContratoNfseIncompativelError(
            f'O campo de contrato "{chave}" não está disponível.'
        ) from exc


def _separar_pausa_contrato(pausa, contrato):
    """Aceita a ordem legada (pausa) e a nova chamada posicional (contrato)."""

    if contrato is None and pausa is not None and not callable(pausa):
        return None, pausa
    return pausa, contrato


def _observar_fronteira(observar, driver, etapa, momento):
    if observar is not None:
        observar(driver, etapa, momento)


def _visivel(elemento):
    try:
        return bool(elemento.is_displayed() and elemento.is_enabled())
    except WebDriverException:
        return False


def _localizar(driver, by, alvo, exigir_visivel=True):
    """Elemento visivel que casa; com `exigir_visivel=False`, o primeiro que casar.

    Preferir o visivel importa porque o portal reaproveita identificadores em
    partes ocultas do formulario, e o primeiro do DOM pode estar numa delas.

    Mas `exigir_visivel` NAO serve para radio: no portal os `<input type=radio>`
    sao escondidos por CSS e o que aparece na tela e um label estilizado por
    cima. **Nenhum** radio do assistente e visivel para o Selenium — nem os que
    ja estao marcados — entao exigir visibilidade neles nao acha nada. Eles sao
    clicados por JS, que funciona em elemento de tamanho zero.
    """
    try:
        candidatos = driver.find_elements(by, alvo)
    except WebDriverException:
        return None

    for elemento in candidatos:
        if _visivel(elemento):
            return elemento
    if exigir_visivel:
        return None
    return candidatos[0] if candidatos else None


def _esperar_interagivel(driver, elemento_id, timeout=None, intervalo=0.2):
    """Espera o campo estar visivel e habilitado.

    Existir no DOM nao basta: partes do formulario so sao reveladas depois de
    outra escolha (os campos do tomador aparecem apos marcar "Brasil"), e
    digitar antes disso levanta "element not interactable".
    """
    timeout = TIMEOUT_AUTOPREENCHIMENTO if timeout is None else timeout

    return esperar(lambda: _localizar(driver, By.ID, elemento_id) is not None,
                   timeout=timeout, intervalo=intervalo)


def _esperar_preenchido(driver, elemento_id, timeout=None, intervalo=0.3):
    """Espera o PORTAL preencher um campo sozinho.

    Ancora a espera num efeito observavel em vez de dormir um tempo fixo: o
    emitente aparece depois da data, e os dados do tomador depois do CNPJ.
    """
    timeout = TIMEOUT_AUTOPREENCHIMENTO if timeout is None else timeout

    def tem_valor():
        try:
            return bool((driver.find_element(By.ID, elemento_id)
                         .get_attribute('value') or '').strip())
        except WebDriverException:
            return False

    return esperar(tem_valor, timeout=timeout, intervalo=intervalo)


# --- select2 com busca no servidor ----------------------------------------
# Municipio e codigo de tributacao nascem SEM opcoes: o portal so as busca
# depois que o operador digita. Definir o valor por jQuery nunca funciona
# nesses dois — nao ha <option> para selecionar. Ha que dirigir o widget como
# gente: clicar, digitar, esperar a busca, escolher.

SEL_SELECT2_BUSCA = 'input.select2-search__field'
SEL_SELECT2_OPCAO = 'li.select2-results__option'


def _sem_acento(texto):
    import unicodedata
    return (unicodedata.normalize('NFKD', str(texto or ''))
            .encode('ascii', 'ignore').decode())


def _chave(texto):
    return ' '.join(_sem_acento(texto).upper().split())


def _tentativas_de_busca(termo):
    """Prefixos a tentar, do mais especifico ao mais curto.

    Comeca pelo termo inteiro sem acento e vai encurtando: o portal pode nao
    casar acento ('Imbe' x 'Imbe'), e um prefixo curto ('Imb') filtra igual —
    e e o que o operador digita na mao. Encurtar evita ter que adivinhar como o
    servidor normaliza a busca."""
    base = _sem_acento(termo).split('/')[0].strip()
    vistos = []
    for tamanho in (len(base), 4, 3):
        pedaco = base[:tamanho].strip()
        if len(pedaco) >= 2 and pedaco not in vistos:
            vistos.append(pedaco)
    return vistos


def _valor_atual(driver, elemento_id):
    try:
        return driver.execute_script(
            "var el = document.getElementById(arguments[0]);"
            "return el ? el.value : null;", elemento_id)
    except WebDriverException:
        return None


def _selecionar_com_busca(driver, elemento_id, valor, rotulo_esperado):
    """Escolhe num Select2 que busca as opcoes no servidor.

    `rotulo_esperado` e o texto da opcao (ex.: 'Imbe/RS'); dele sai o termo
    digitado e por ele a opcao e reconhecida na lista."""
    if _valor_atual(driver, elemento_id) == str(valor):
        return valor  # ja escolhido; nao reabre o widget a toa

    seletor_caixa = f'span.select2-selection[aria-labelledby="select2-{elemento_id}-container"]'
    if not esperar(lambda: _localizar(driver, By.CSS_SELECTOR, seletor_caixa) is not None,
                   timeout=TIMEOUT_AUTOPREENCHIMENTO, intervalo=0.2):
        raise InteracaoPortalError(
            f'O campo "{rotulo_esperado}" nao apareceu nesta tela. A etapa pode '
            'nao ter terminado de carregar ou o portal mudou o formulario.')

    for termo in _tentativas_de_busca(rotulo_esperado):
        if _buscar_e_escolher(driver, seletor_caixa, termo, rotulo_esperado):
            break

    atual = _valor_atual(driver, elemento_id)
    if str(atual) != str(valor):
        raise InteracaoPortalError(
            f'Nao consegui escolher "{rotulo_esperado}" no campo '
            f'"{elemento_id}" (ficou "{atual}"). O portal busca as opcoes ao '
            'digitar; confira se o nome esta como aparece na lista dele.')
    return atual


def _buscar_e_escolher(driver, seletor_caixa, termo, rotulo_esperado):
    """Uma tentativa: abre, digita, espera a busca e clica na opcao."""
    caixa = _localizar(driver, By.CSS_SELECTOR, seletor_caixa)
    if caixa is None:
        return False
    try:
        caixa.click()
    except WebDriverException:
        driver.execute_script('arguments[0].click();', caixa)

    campo = _localizar(driver, By.CSS_SELECTOR, SEL_SELECT2_BUSCA)
    if campo is None:
        return False
    try:
        campo.clear()
        campo.send_keys(termo)
    except WebDriverException:
        return False

    alvo = _chave(rotulo_esperado)

    def achou():
        return _opcao_correspondente(driver, alvo) is not None

    if not esperar(achou, timeout=TIMEOUT_AUTOPREENCHIMENTO, intervalo=0.3):
        return False

    opcao = _opcao_correspondente(driver, alvo)
    try:
        opcao.click()
    except WebDriverException:
        driver.execute_script('arguments[0].click();', opcao)
    return True


def _opcao_correspondente(driver, alvo):
    """Opcao da lista cujo texto casa com o rotulo esperado.

    Compara sem acento e sem caixa; `startswith` cobre o caso do codigo de
    tributacao, cuja opcao traz a descricao inteira depois do codigo."""
    try:
        opcoes = driver.find_elements(By.CSS_SELECTOR, SEL_SELECT2_OPCAO)
    except WebDriverException:
        return None
    for opcao in opcoes:
        try:
            texto = _chave(opcao.text)
        except WebDriverException:
            continue
        if not texto or 'CARREGANDO' in texto or 'SEARCHING' in texto:
            continue
        if texto == alvo or texto.startswith(alvo):
            return opcao
    return None


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


def na_revisao(driver):
    """True quando o navegador ESTA na tela de revisao, com o botao de emitir.

    Exige path E elemento: so a URL nao basta porque uma pagina de erro no
    mesmo path passaria."""
    return PATH_REVISAO in _url(driver) and _tem_elemento(driver, ID_BTN_EMITIR)


def esperar_revisao(driver, timeout=None):
    """Espera o portal chegar a tela de revisao. False se estourar o prazo.

    Espera de verdade, ao contrario de uma leitura unica: o ultimo "Avancar"
    pode ser clicado por JS (o fallback de `_clicar`), e clique por JS nao
    bloqueia ate a navegacao terminar. Conferir na hora reprova uma nota
    corretamente preenchida so porque a pagina ainda estava carregando — e
    reprovar aqui marca FALHA numa nota que esta certa esperando no portal."""
    timeout = TIMEOUT_REVISAO if timeout is None else timeout
    return esperar(lambda: na_revisao(driver), timeout=timeout)


def _tem_alerta_sucesso(driver):
    """True se a pagina mostra o alerta verde de nota gerada.

    Confere o TEXTO, nao so a classe: `alert-success` e generico e aparece em
    outras confirmacoes do portal."""
    try:
        alertas = driver.find_elements(By.CSS_SELECTOR, SEL_ALERTA_SUCESSO)
    except WebDriverException:
        return False
    for alerta in alertas:
        try:
            if TEXTO_EMISSAO_OK in (alerta.text or '').lower():
                return True
        except WebDriverException:
            continue
    return False


def detectar_emitida(driver):
    """True quando a nota foi emitida (tela de confirmacao).

    E o sinal que faz o lote assistido avancar para a proxima nota e o modo
    individual fechar o navegador. O path e obrigatorio (ancora), e basta um
    dos dois sinais de emissao acontecer dentro dele."""
    if PATH_CONFIRMACAO not in _url(driver):
        return False
    return _tem_elemento(driver, ID_BTN_DANFSE) or _tem_alerta_sucesso(driver)


# --- espera generica -------------------------------------------------------

def esperar(condicao, timeout=30, intervalo=0.5, cancelado=None):
    """Espera `condicao()` virar verdadeira. False se estourar o timeout.

    Nao usa WebDriverWait de proposito: precisa aceitar um callback
    `cancelado()` para responder a pausar/parar do lote enquanto aguarda a
    confirmacao humana (P2), e ser exercitavel com driver falso.
    """
    import time
    limite = time.monotonic() + timeout
    while True:
        if cancelado is not None and cancelado():
            return False
        if condicao():
            return True
        if time.monotonic() >= limite:
            return False
        time.sleep(intervalo)


# --- login por certificado -------------------------------------------------

class LoginNfseError(RuntimeError):
    """Nao foi possivel autenticar no portal com o certificado.

    Erro acionavel: nenhuma nota e preenchida depois disso (NFSE-11)."""

    def __init__(self, mensagem, acao=None):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.acao = acao


def logado(driver):
    """True quando o navegador chegou ao painel.

    E o alvo do LOGIN — "entrou e caiu no Dashboard". Nao serve para perguntar
    "a sessao ainda vale?" depois, porque o operador sai do painel na primeira
    acao: ler a aliquota vai para /Perfil/Configuracao e preencher uma nota
    termina em /DPS/... Para isso existe `sessao_valida`."""
    return PATH_DASHBOARD in _url(driver)


def sessao_valida(driver):
    """True enquanto o navegador continua autenticado no Emissor Nacional.

    Vale em qualquer tela de dentro do sistema, nao so no painel. O portal
    devolve para /Login quando a sessao cai, entao e a presenca dessa tela que
    denuncia a perda — nao a ausencia do Dashboard.

    A distincao nao e cosmetica: usar `logado` aqui fazia o proximo `garantir()`
    fechar o Chrome e pedir o certificado outra vez a cada nota, que e
    exatamente o que a sessao persistente existe para evitar."""
    url = _url(driver)
    if not url or '/EmissorNacional' not in url:
        return False
    return '/Login' not in url


def login_certificado(driver, timeout=40, intervalo=0.5):
    """Abre o login e entra pelo certificado digital.

    O clique e no link de acesso por certificado; a escolha do certificado em
    si acontece FORA do DOM — e o dialogo nativo do Chrome, resolvido pela
    policy de registro aplicada antes de abrir o navegador (cert_policy).
    Sem a policy, o dialogo trava aqui ate o timeout.
    """
    driver.get(URL_LOGIN)
    try:
        driver.find_element(By.CSS_SELECTOR, SEL_LINK_CERTIFICADO).click()
    except WebDriverException as exc:
        raise LoginNfseError(
            'Link de acesso por certificado nao encontrado na tela de login.',
            acao='O portal pode ter mudado a tela; refaca a recon.') from exc

    if not esperar(lambda: logado(driver), timeout=timeout, intervalo=intervalo):
        raise LoginNfseError(
            'O portal nao chegou ao painel apos o acesso por certificado.',
            acao='Confira se o certificado da NFSe esta instalado e valido e se '
                 'as variaveis NFSE_CERT_AUTOSELECT_* apontam para o e-CNPJ '
                 'correto (o do RS e um e-CPF diferente).')
    return True


def ler_aliquota_simples(driver):
    """Le a aliquota do Simples configurada no perfil do emitente.

    Devolve o texto como esta no portal (ex.: '3,87') ou None se nao der para
    ler. None NAO e tratado como zero em lugar nenhum: quem chama pede
    confirmacao manual ao operador em vez de seguir calado (NFSE-12).
    """
    driver.get(URL_CONFIGURACAO)
    try:
        elemento = driver.find_element(By.ID, ID_ALIQUOTA_SN)
    except WebDriverException:
        return None
    try:
        valor = elemento.get_attribute('value')
    except WebDriverException:
        return None
    valor = (valor or '').strip()
    return valor or None


# --- etapas do assistente DPS ---------------------------------------------

# Campos que o portal ja traz corretos e que a automacao NAO deve tocar.
# Mexer neles reabre secoes condicionais do formulario e muda a nota.
CAMPOS_INTOCAVEIS = (
    'ISSQN.HaSuspensao',            # ja vem "Nao"
    'ISSQN.HaBeneficioMunicipal',   # ja vem "Nao"
    'ISSQN_TributacaoISSQN',        # ja vem "Operacao Tributavel"
    'ISSQN_RegimeEspecial',         # ja vem "Nenhum"
    'ValorTributos.TipoValorTributos',  # ja vem "Informar aliquota do Simples"
    'ISSQN_BaseDeCalculo',          # calculados e bloqueados pelo portal
    'ISSQN_Valor',
    'ISSQN_Aliquota',
)


def formatar_valor(valor):
    """Decimal('826.09') -> '826,09' (o portal usa o padrao brasileiro)."""
    return f'{valor:.2f}'.replace('.', ',')


def formatar_data(data):
    return data.strftime('%d/%m/%Y')


def abrir_nova_dps(driver):
    driver.get(URL_NOVA_DPS)


def _clicar(driver, localizadores, rotulo):
    """Clica no elemento visivel, rolando ate ele e caindo para clique por JS.

    Separa "nao achei" de "achei mas o clique falhou": mensagens iguais para as
    duas coisas ja mandaram investigar o lado errado. O botao de avancar fica no
    fim de um formulario longo, entao rolar ate ele importa; e se algo ficar por
    cima (aviso, rodape fixo), o clique por JS ainda resolve.
    """
    def achar():
        for by, alvo in localizadores:
            elemento = _localizar(driver, by, alvo)
            if elemento is not None:
                return elemento
        return None

    if not esperar(lambda: achar() is not None,
                   timeout=TIMEOUT_AUTOPREENCHIMENTO, intervalo=0.2):
        raise InteracaoPortalError(
            f'{rotulo} nao esta disponivel nesta tela. O portal pode ter mudado '
            'o formulario ou a etapa nao terminou de carregar.')

    elemento = achar()
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center'});", elemento)
    except WebDriverException:
        pass

    try:
        elemento.click()
        return elemento
    except WebDriverException:
        pass

    try:
        driver.execute_script('arguments[0].click();', elemento)
    except WebDriverException as exc:
        raise InteracaoPortalError(
            f'{rotulo} foi encontrado mas nao aceitou o clique: '
            f'{(str(exc).strip().splitlines() or [""])[0]}') from exc
    return elemento


# O botao "Avancar" so tem id na etapa 1; nas etapas 2 e 3 e o mesmo elemento
# sem identificador nenhum. As classes sao iguais nas tres, entao a busca vai do
# mais especifico ao mais generico e para no primeiro que aparecer.
LOCALIZADORES_AVANCAR = (
    (By.ID, ID_BTN_AVANCAR),
    (By.CSS_SELECTOR, 'button[type="submit"].direita.has-spin'),
    (By.XPATH, '//button[@type="submit"][.//span[normalize-space()="Avançar"]]'),
)


def _avancar(driver):
    """Avanca para a proxima etapa do assistente."""
    if not esperar(lambda: _achar_avancar(driver) is not None,
                   timeout=TIMEOUT_AUTOPREENCHIMENTO, intervalo=0.2):
        raise InteracaoPortalError(
            'O botao "Avancar" nao esta disponivel nesta tela. A etapa pode nao '
            'ter terminado de carregar ou o portal mudou o formulario.')

    elemento = _achar_avancar(driver)
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center'});", elemento)
    except WebDriverException:
        pass

    try:
        elemento.click()
        return elemento
    except WebDriverException:
        pass

    try:
        driver.execute_script('arguments[0].click();', elemento)
    except WebDriverException as exc:
        raise InteracaoPortalError(
            'O botao "Avancar" foi encontrado mas nao aceitou o clique: '
            f'{(str(exc).strip().splitlines() or [""])[0]}') from exc
    return elemento


def _achar_avancar(driver):
    for by, alvo in LOCALIZADORES_AVANCAR:
        elemento = _localizar(driver, by, alvo)
        if elemento is not None:
            return elemento
    return None


_ADAPTADORES_CONTRATO = frozenset({
    'texto', 'textarea', 'radio', 'select_direto', 'select_busca', 'chosen',
    'padrao_portal', 'intocavel',
})


def _valor_regra_contrato(regra, chave, padrao=None):
    if isinstance(regra, Mapping):
        return regra.get(chave, padrao)
    return getattr(regra, chave, padrao)


def _objeto_regra_contrato(regra):
    return SimpleNamespace(**regra) if isinstance(regra, Mapping) else regra


def _localizar_campo_contrato(driver, campo):
    by = {
        'id': By.ID,
        'name': By.NAME,
        'css': By.CSS_SELECTOR,
    }.get(campo.seletor_tipo)
    if by is None:
        raise ContratoNfseIncompativelError(
            f'O seletor do campo "{campo.chave_semantica}" não é aprovado.'
        )
    if campo.seletor_tipo == 'css':
        try:
            elementos = driver.find_elements(by, campo.seletor)
        except WebDriverException:
            elementos = []
        # "Inequivoco" e entre o que da para DIRIGIR, nao no DOM inteiro. O
        # portal repete `name`/`id` em partes ocultas do formulario — e por isso
        # que `_localizar` prefere o visivel —, entao exigir um unico elemento na
        # pagina recusava campo que so tem uma copia utilizavel. Ambiguidade de
        # verdade e duas VISIVEIS; ai continua recusando, porque escolher no
        # chute preencheria a copia errada de um documento fiscal.
        visiveis = [elemento for elemento in elementos if _visivel(elemento)]
        candidatos = visiveis or elementos
        if len(candidatos) != 1:
            raise ContratoNfseIncompativelError(
                f'O campo "{campo.chave_semantica}" não possui alvo inequívoco.'
            )
        elemento = candidatos[0]
    else:
        elemento = _localizar(driver, by, campo.seletor, exigir_visivel=False)
    if elemento is None:
        raise ContratoNfseIncompativelError(
            f'O campo "{campo.chave_semantica}" não está disponível.'
        )
    return elemento


def _id_campo_contrato(driver, campo):
    """Resolve o id real exigido pelos plugins sem presumir o seletor antigo."""

    if campo.seletor_tipo == 'id':
        return campo.seletor
    elemento = _localizar_campo_contrato(driver, campo)
    try:
        elemento_id = (elemento.get_attribute('id') or '').strip()
    except (WebDriverException, AttributeError):
        elemento_id = ''
    if not elemento_id:
        raise ContratoNfseIncompativelError(
            f'O campo "{campo.chave_semantica}" não possui id para o adaptador.'
        )
    return elemento_id


def _esperar_preenchido_contrato(driver, campo):
    if campo.seletor_tipo == 'id':
        return _esperar_preenchido(driver, campo.seletor)

    def tem_valor():
        try:
            elemento = _localizar_campo_contrato(driver, campo)
            return bool((elemento.get_attribute('value') or '').strip())
        except (ContratoNfseIncompativelError, WebDriverException, AttributeError):
            return False

    return esperar(
        tem_valor,
        timeout=TIMEOUT_AUTOPREENCHIMENTO,
        intervalo=0.3,
    )


def _texto_para_o_portal(valor):
    """O que digitar, no formato que o portal espera.

    `str()` cru entrega `649.00` para um Decimal e `2026-08-27` para uma data —
    o portal usa virgula decimal e dd/mm/aaaa. Os campos historicos ja passavam
    por `formatar_valor`/`formatar_data`; os campos NOVOS do contrato caiam
    direto no `str()`, entao configurar "Valor final da nota" num campo de texto
    digitava o numero com ponto.
    """
    if isinstance(valor, Decimal):
        return formatar_valor(valor)
    if isinstance(valor, datetime):
        return formatar_data(valor.date())
    if isinstance(valor, date):
        return formatar_data(valor)
    return str(valor)


def _preencher_campo_contrato(driver, campo, valor):
    if campo.seletor_tipo == 'id':
        return _preencher(driver, campo.seletor, _texto_para_o_portal(valor))
    elemento = _localizar_campo_contrato(driver, campo)
    try:
        elemento.clear()
        elemento.send_keys(_texto_para_o_portal(valor))
        _sair_do_campo(elemento)
    except (WebDriverException, AttributeError) as exc:
        raise ContratoNfseIncompativelError(
            f'O campo "{campo.chave_semantica}" não aceitou preenchimento.'
        ) from exc
    return elemento


def _selecionar_direto_contrato(driver, campo, valor):
    elemento = _localizar_campo_contrato(driver, campo)
    try:
        from selenium.webdriver.support.ui import Select

        Select(elemento).select_by_value(str(valor))
        atual = elemento.get_attribute('value')
    except (WebDriverException, AttributeError, ValueError) as exc:
        raise ContratoNfseIncompativelError(
            f'O select "{campo.chave_semantica}" não aceitou a opção.'
        ) from exc
    if str(atual) != str(valor):
        raise ContratoNfseIncompativelError(
            f'O select "{campo.chave_semantica}" não confirmou a opção.'
        )
    return atual


def _valor_busca_contrato(campo, valor):
    if isinstance(valor, (tuple, list)) and len(valor) == 2:
        return valor[0], valor[1]
    for opcao in getattr(campo, 'opcoes', ()) or ():
        if str(getattr(opcao, 'valor', '')) == str(valor):
            return valor, getattr(opcao, 'rotulo', valor)
    return valor, valor


def _marcar_radio_contrato(driver, campo, valor):
    if campo.seletor_tipo == 'name':
        return _marcar_radio(driver, campo.seletor, valor)
    # Seletor fora do conjunto aprovado e recusado, nunca adivinhado: cair em
    # CSS por omissao faria o clique procurar o grupo por uma sintaxe que nao e
    # a dele, e um clique errado em documento fiscal nao tem rollback.
    by = {
        'id': By.ID,
        'css': By.CSS_SELECTOR,
    }.get(campo.seletor_tipo)
    if by is None:
        raise ContratoNfseIncompativelError(
            f'O seletor do campo "{campo.chave_semantica}" não é aprovado.'
        )
    try:
        elementos = driver.find_elements(by, campo.seletor)
    except WebDriverException as exc:
        raise ContratoNfseIncompativelError(
            f'O grupo "{campo.chave_semantica}" não pôde ser localizado.'
        ) from exc
    candidatos = []
    for elemento in elementos:
        try:
            if str(elemento.get_attribute('value')) == str(valor):
                candidatos.append(elemento)
        except WebDriverException:
            continue
    if len(candidatos) != 1:
        raise ContratoNfseIncompativelError(
            f'O grupo "{campo.chave_semantica}" não possui uma opção inequívoca.'
        )
    driver.execute_script('arguments[0].click();', candidatos[0])
    return candidatos[0]


def aplicar_campos_adicionais(driver, regras, valores):
    """Aplica somente adaptadores aprovados e em ordem declarada."""

    valores = valores or {}
    regras_ordenadas = sorted(
        list(regras or ()),
        key=lambda regra: int(_valor_regra_contrato(regra, 'ordem', 0) or 0),
    )
    preparados = []
    for regra in regras_ordenadas:
        interacao = _valor_regra_contrato(regra, 'interacao', '')
        origem = _valor_regra_contrato(regra, 'origem', '')
        adaptador = (
            'padrao_portal' if origem == 'padrao_portal' else
            'intocavel' if origem == 'intocavel' else interacao
        )
        if adaptador not in _ADAPTADORES_CONTRATO:
            chave = _valor_regra_contrato(regra, 'chave_semantica', '')
            raise ContratoNfseIncompativelError(
                f'O adaptador do campo "{chave}" não é conhecido.'
            )
        condicao_chave = _valor_regra_contrato(regra, 'condicao_chave')
        if condicao_chave:
            if condicao_chave not in valores or valores[condicao_chave] is None:
                raise ContratoNfseIncompativelError(
                    f'A dependência "{condicao_chave}" do contrato não está disponível.'
                )
            condicao_valor = _valor_regra_contrato(regra, 'condicao_valor')
            if condicao_valor is not None and str(valores[condicao_chave]) != str(condicao_valor):
                continue
        chave = _valor_regra_contrato(regra, 'chave_semantica')
        if adaptador not in {'padrao_portal', 'intocavel'}:
            if chave not in valores or valores[chave] is None:
                raise ContratoNfseIncompativelError(
                    f'O valor do campo "{chave}" não foi resolvido.'
                )
        preparados.append((_objeto_regra_contrato(regra), adaptador, chave))

    for regra, adaptador, chave in preparados:
        if adaptador in {'padrao_portal', 'intocavel'}:
            continue
        campo = regra
        valor = valores[chave]
        if adaptador in {'texto', 'textarea'}:
            _preencher_campo_contrato(driver, campo, valor)
        elif adaptador == 'radio':
            _marcar_radio_contrato(driver, campo, valor)
        elif adaptador == 'select_direto':
            _selecionar_direto_contrato(driver, campo, valor)
        elif adaptador == 'chosen':
            _selecionar(driver, _id_campo_contrato(driver, campo), valor)
        elif adaptador == 'select_busca':
            valor_busca, rotulo = _valor_busca_contrato(campo, valor)
            _selecionar_com_busca(
                driver, _id_campo_contrato(driver, campo), valor_busca, rotulo
            )
    return True


def _valor_contrato(valores, chave, padrao):
    if valores is not None and chave in valores:
        return valores[chave]
    return padrao


def _aplicar_campo_contrato(driver, campo, valor):
    aplicar_campos_adicionais(
        driver, (campo,), {campo.chave_semantica: valor}
    )


def _liberadores_de_etapa(adicionais):
    """Campos do contrato capazes de destravar o resto do formulario.

    A reforma tributaria pos uma pergunta no topo da etapa de Pessoas
    ("Preencher as informacoes IBS/CBS?") que mantem os demais campos inertes
    ate ser respondida. Sao sempre a mesma forma: um clique com valor ja
    decidido no contrato — nada que dependa da nota, nada que digite texto.
    """

    return tuple(
        campo
        for campo in adicionais
        if _valor_regra_contrato(campo, 'interacao') == 'radio'
        and _valor_regra_contrato(campo, 'origem') == 'fixo'
    )


def _campos_adicionais_etapa(contrato, etapa, chaves_principais):
    return tuple(
        campo
        for campo in _contrato_execucao(contrato).campos
        if campo.etapa == etapa and campo.chave_semantica not in chaves_principais
    )


def preencher_etapa_pessoas(
    driver, nota, config, data_competencia, pausa=None, contrato=None, observar=None,
    valores_contrato=None,
):
    """Etapa 1: competencia, regime de apuracao e tomador.

    `data_competencia` e a data da EMISSAO (hoje) — nao confundir com a
    competencia da descricao, que vem do vencimento. Emitente, nome do tomador
    e endereco sao preenchidos pelo proprio portal apos a data e o CNPJ; a
    automacao nao os toca.
    """
    pausa, contrato = _separar_pausa_contrato(pausa, contrato)
    campo_data = _campo_contrato(contrato, CHAVE_DATA_COMPETENCIA)
    campo_prestador = _campo_contrato(contrato, CHAVE_INSCRICAO_PRESTADOR)
    campo_regime = _campo_contrato(contrato, CHAVE_REGIME_APURACAO)
    campo_domicilio = _campo_contrato(contrato, CHAVE_DOMICILIO_TOMADOR)
    campo_inscricao = _campo_contrato(contrato, CHAVE_INSCRICAO_TOMADOR)
    campo_nome = _campo_contrato(contrato, CHAVE_NOME_TOMADOR)

    _observar_fronteira(observar, driver, 'pessoas', 'entrada')
    adicionais = _campos_adicionais_etapa(
        contrato,
        'pessoas',
        {
            CHAVE_DATA_COMPETENCIA,
            CHAVE_REGIME_APURACAO,
            CHAVE_DOMICILIO_TOMADOR,
            CHAVE_INSCRICAO_TOMADOR,
        },
    )
    # Perguntas fixas no topo da etapa podem ser porteiras: o portal deixa a
    # competência inerte até elas serem respondidas. Aplicá-las antes evita
    # gastar todo o timeout tentando preencher um campo que sabemos estar
    # bloqueado. O contrato já fixa a resposta; não há inferência sobre a nota.
    liberadores = _liberadores_de_etapa(adicionais)
    if liberadores:
        aplicar_campos_adicionais(driver, liberadores, valores_contrato)
        log_event('nfse_etapa_destravada', etapa='pessoas',
                  campos=len(liberadores))
    restantes = tuple(campo for campo in adicionais if campo not in liberadores)

    valor_data = _valor_contrato(
        valores_contrato, CHAVE_DATA_COMPETENCIA, data_competencia
    )
    if hasattr(valor_data, 'strftime'):
        valor_data = formatar_data(valor_data)
    _aplicar_campo_contrato(driver, campo_data, valor_data)

    # O portal so carrega o emitente depois que a data sai do foco, e os campos
    # seguintes ficam nao-interagiveis ate la. Esperar o CNPJ do emitente
    # aparecer e o sinal de que a etapa esta pronta.
    if not _esperar_preenchido_contrato(driver, campo_prestador):
        raise InteracaoPortalError(
            'O portal nao carregou os dados do emitente apos a data de '
            'competencia. A tela pode estar lenta ou ter mudado.')
    if pausa:
        pausa()

    _aplicar_campo_contrato(
        driver,
        campo_regime,
        _valor_contrato(
            valores_contrato, CHAVE_REGIME_APURACAO, config.regime_apuracao_sn
        ),
    )

    # Marcar "Brasil" e o que REVELA os campos do tomador; o _preencher abaixo
    # ja espera o CNPJ ficar interagivel antes de digitar.
    _aplicar_campo_contrato(
        driver,
        campo_domicilio,
        _valor_contrato(valores_contrato, CHAVE_DOMICILIO_TOMADOR, '1'),
    )
    _observar_fronteira(observar, driver, 'pessoas', 'dependencias')
    _aplicar_campo_contrato(
        driver,
        campo_inscricao,
        _valor_contrato(valores_contrato, CHAVE_INSCRICAO_TOMADOR, nota.documento),
    )

    # mesma logica: o nome/endereco do tomador vem do portal apos o documento
    if not _esperar_preenchido_contrato(driver, campo_nome):
        raise InteracaoPortalError(
            f'O portal nao reconheceu o documento {nota.documento} do tomador. '
            'Confira se esta correto e ativo na Receita.')
    if pausa:
        pausa()
    aplicar_campos_adicionais(driver, restantes, valores_contrato)
    _observar_fronteira(observar, driver, 'pessoas', 'pre_avancar')
    _avancar(driver)
    _observar_fronteira(observar, driver, 'pessoas', 'pos_avancar')


def preencher_etapa_servico(
    driver, nota, config, descricao, pausa=None, contrato=None, observar=None,
    valores_contrato=None,
):
    """Etapa 2: local, codigo de tributacao, descricao e item da NBS.

    Municipio e codigo de tributacao sao selects VISIVEIS (nao usam Chosen);
    o item da NBS e Chosen com 919 opcoes.
    """
    pausa, contrato = _separar_pausa_contrato(pausa, contrato)
    campo_municipio = _campo_contrato(contrato, CHAVE_MUNICIPIO_SERVICO)
    campo_tributacao = _campo_contrato(contrato, CHAVE_TRIBUTACAO_NACIONAL)
    campo_exportacao = _campo_contrato(contrato, CHAVE_EXPORTACAO_IMUNIDADE)
    campo_descricao = _campo_contrato(contrato, CHAVE_DESCRICAO_SERVICO)
    campo_nbs = _campo_contrato(contrato, CHAVE_ITEM_NBS)

    _observar_fronteira(observar, driver, 'servico', 'entrada')
    # Estes dois buscam as opções no servidor conforme se digita; o item da NBS
    # abaixo ja vem com a lista inteira carregada e usa a via direta.
    _aplicar_campo_contrato(
        driver,
        campo_municipio,
        _valor_contrato(
            valores_contrato,
            CHAVE_MUNICIPIO_SERVICO,
            (config.municipio_servico_codigo, config.municipio_servico_nome),
        ),
    )
    _aplicar_campo_contrato(
        driver,
        campo_tributacao,
        _valor_contrato(
            valores_contrato,
            CHAVE_TRIBUTACAO_NACIONAL,
            (config.codigo_tributacao, config.codigo_tributacao),
        ),
    )
    # "Nao" para imunidade/exportacao: ja e o default, mas marcar explicitamente
    # evita depender de o portal manter esse default.
    _aplicar_campo_contrato(
        driver,
        campo_exportacao,
        _valor_contrato(valores_contrato, CHAVE_EXPORTACAO_IMUNIDADE, '0'),
    )
    _observar_fronteira(observar, driver, 'servico', 'dependencias')
    _aplicar_campo_contrato(
        driver,
        campo_descricao,
        _valor_contrato(valores_contrato, CHAVE_DESCRICAO_SERVICO, descricao),
    )
    _aplicar_campo_contrato(
        driver,
        campo_nbs,
        _valor_contrato(valores_contrato, CHAVE_ITEM_NBS, config.item_nbs),
    )
    if pausa:
        pausa()
    adicionais = _campos_adicionais_etapa(
        contrato,
        'servico',
        {
            CHAVE_MUNICIPIO_SERVICO,
            CHAVE_TRIBUTACAO_NACIONAL,
            CHAVE_EXPORTACAO_IMUNIDADE,
            CHAVE_DESCRICAO_SERVICO,
            CHAVE_ITEM_NBS,
        },
    )
    aplicar_campos_adicionais(driver, adicionais, valores_contrato)
    _observar_fronteira(observar, driver, 'servico', 'pre_avancar')
    _avancar(driver)
    _observar_fronteira(observar, driver, 'servico', 'pos_avancar')


def preencher_etapa_tributacao(
    driver, nota, config, pausa=None, contrato=None, observar=None,
    valores_contrato=None,
):
    """Etapa 3: valor do servico e retencoes.

    A retencao do ISSQN NAO vem marcada (nem Sim nem Nao) — e obrigatoria.
    Os campos de base de calculo/valor/aliquota do ISSQN sao calculados e
    bloqueados pelo portal; a automacao nao os toca.
    """
    pausa, contrato = _separar_pausa_contrato(pausa, contrato)
    campo_valor = _campo_contrato(contrato, CHAVE_VALOR_SERVICO)
    campo_retencao = _campo_contrato(contrato, CHAVE_RETENCAO_ISSQN)
    campo_situacao = _campo_contrato(contrato, CHAVE_PISCOFINS_SITUACAO)
    campo_piscofins = _campo_contrato(contrato, CHAVE_PISCOFINS_RETENCAO)

    _observar_fronteira(observar, driver, 'tributacao', 'entrada')
    valor_servico = _valor_contrato(
        valores_contrato, CHAVE_VALOR_SERVICO, nota.valor_final
    )
    if not isinstance(valor_servico, str):
        valor_servico = formatar_valor(valor_servico)
    _aplicar_campo_contrato(driver, campo_valor, valor_servico)
    _aplicar_campo_contrato(
        driver,
        campo_retencao,
        _valor_contrato(valores_contrato, CHAVE_RETENCAO_ISSQN, '0'),
    )
    _aplicar_campo_contrato(
        driver,
        campo_situacao,
        _valor_contrato(
            valores_contrato, CHAVE_PISCOFINS_SITUACAO, config.piscofins_situacao
        ),
    )
    _aplicar_campo_contrato(
        driver,
        campo_piscofins,
        _valor_contrato(
            valores_contrato,
            CHAVE_PISCOFINS_RETENCAO,
            config.piscofins_tipo_retencao,
        ),
    )
    if pausa:
        pausa()
    adicionais = _campos_adicionais_etapa(
        contrato,
        'tributacao',
        {
            CHAVE_VALOR_SERVICO,
            CHAVE_RETENCAO_ISSQN,
            CHAVE_PISCOFINS_SITUACAO,
            CHAVE_PISCOFINS_RETENCAO,
        },
    )
    aplicar_campos_adicionais(driver, adicionais, valores_contrato)
    _observar_fronteira(observar, driver, 'tributacao', 'pre_avancar')
    _avancar(driver)
    _observar_fronteira(observar, driver, 'tributacao', 'pos_avancar')




# --- auto-revisao antes de emitir (NFSE-24) --------------------------------
#
# A tela de revisao mostra pares <dt>rotulo</dt><dd>valor</dd> agrupados por
# <h4 class="emissao-titulo">. Duas armadilhas ditam os seletores abaixo:
#
# 1. Ha DOIS "CNPJ:" na pagina — o do emitente (o escritorio) vem antes do
#    tomador. Ler sem ancorar na secao compara o CNPJ do escritorio com o do
#    cliente e nunca bate.
# 2. Ha DOIS rotulos de descricao dentro de "Servico Prestado", diferentes
#    apenas pela caixa de uma letra: "Descricao do servico:" e o codigo de
#    tributacao, "Descricao do Servico:" e o nosso texto livre. Depender dessa
#    diferenca seria fragil, entao a descricao e conferida contra TODOS os <dd>
#    da secao: o texto que pretendemos emitir precisa estar la.

# Titulos das secoes, EXATOS e com acento. Exatos porque "Servico Prestado" e
# substring de "Valores do Servico Prestado" — um `contains` casaria as duas e
# leria o campo da secao errada. Com acento porque e o texto real do DOM; o
# XPath vai como unicode e casa direto.
# Titulos como o portal os escreve HOJE, conferidos no esqueleto capturado da
# revisao — nao supostos. Dois deles ja mudaram sob os nossos pes:
# "Tomador do Serviço" virou "Tomador/Adquirente do Serviço".
SECAO_TOMADOR = 'Tomador/Adquirente do Serviço'
SECAO_SERVICO = 'Serviço Prestado'
SECAO_VALORES = 'Valores do Serviço Prestado'

# Dentro da secao do tomador so existe um destes; qual depende de o tomador ser
# pessoa juridica ou fisica. Na PAGINA inteira existem dois "CNPJ:" (emitente e
# tomador) — por isso a busca por secao e a que vale, e o fallback global
# recusa quando acha mais de um.
ROTULO_DOCUMENTO = "contains(.,'CNPJ') or contains(.,'CPF')"
# O rotulo do valor tambem mudou: hoje e "Valor da operação/serviço prestado:".
# As duas formas ficam aceitas — a nova e a antiga —, porque quem determina o
# desfecho e a igualdade do VALOR, nao a forma do rotulo.
ROTULO_VALOR = (
    "contains(.,'Valor da opera') or contains(.,'Valor do servi')"
)


class ResultadoAutorrevisao(list):
    """Lista compatível com a API antiga que também informa elegibilidade."""

    def __init__(
        self, divergencias=(), elegivel_automatico=False, avisos_assistidos=()
    ):
        super().__init__(divergencias)
        self.elegivel_automatico = bool(elegivel_automatico)
        self.avisos_assistidos = tuple(avisos_assistidos or ())

# Titulo EXATO, nunca `contains`: "Serviço Prestado" e substring de "Valores do
# Serviço Prestado", e um `contains` casaria as duas secoes — a descricao seria
# lida da secao errada. A tolerancia a renomeacao mora no fallback de
# `_dd_da_secao`, que procura o rotulo na pagina inteira e so aceita com um
# unico casamento; aqui, precisao.
_XP_SECAO = ("//h4[contains(@class,'emissao-titulo')][normalize-space()='{secao}']"
             "/following-sibling::div[contains(@class,'emissao-conteudo')][1]")


def _dd_da_secao(driver, secao, condicao_rotulo):
    """Valor do campo cujo <dt> satisfaz `condicao_rotulo`, dentro de `secao`.

    Dois caminhos, e a ordem importa. O primeiro exige a seção: `h4` com a
    classe certa e o título exato. É o mais preciso e continua sendo o
    preferido.

    O segundo existe porque o primeiro quebra por motivo que não é fiscal — o
    portal trocar `h4` por `h3`, mexer na classe, ou acrescentar uma palavra ao
    título. Quando isso acontece, a autorrevisão não erra a conferência: ela
    para de conferir, e "não consegui ler" vira divergência num documento que
    está certo. O caminho tolerante procura o `<dt>` na página inteira e só
    aceita se houver EXATAMENTE UM: duas seções com o mesmo rótulo (prestador e
    tomador têm as duas um CNPJ) continuam sendo recusa.

    O que NÃO afrouxa é a comparação. Ler com mais tolerância não aprova valor
    errado — quem decide continua sendo a igualdade lá em cima.
    """
    xpath = (_XP_SECAO.format(secao=secao)
             + f"//dt[{condicao_rotulo}]/following-sibling::dd[1]")
    elemento = _localizar(driver, By.XPATH, xpath, exigir_visivel=False)
    if elemento is not None:
        return (elemento.text or '').strip()

    try:
        candidatos = driver.find_elements(
            By.XPATH, f"//dt[{condicao_rotulo}]/following-sibling::dd[1]",
        )
    except WebDriverException:
        return None
    if len(candidatos) != 1:
        return None
    log_event('nfse_revisao_secao_ignorada', secao=secao)
    return (candidatos[0].text or '').strip()


def _dds_da_secao(driver, secao):
    xpath = _XP_SECAO.format(secao=secao) + '//dd'
    try:
        return [(e.text or '').strip() for e in driver.find_elements(By.XPATH, xpath)]
    except WebDriverException:
        return []


def _xpath_literal(texto):
    texto = str(texto or '')
    if "'" not in texto:
        return f"'{texto}'"
    if '"' not in texto:
        return f'"{texto}"'
    partes = texto.split("'")
    return "concat(" + ", \"'\", ".join(f"'{parte}'" for parte in partes) + ")"


def _dd_declarativo(driver, secao, rotulo):
    """Lê exatamente um par dt/dd de uma seção declarada."""
    secao_literal = _xpath_literal(secao)
    rotulo_literal = _xpath_literal(rotulo)
    xpath = (
        "//h4[contains(@class,'emissao-titulo')]"
        f"[normalize-space()={secao_literal}]"
        "/following-sibling::div[contains(@class,'emissao-conteudo')][1]"
        f"//dt[normalize-space()={rotulo_literal}]/following-sibling::dd[1]"
    )
    try:
        elementos = driver.find_elements(By.XPATH, xpath)
    except WebDriverException:
        return None, 'não foi possível ler a seção ou o rótulo declarados'
    if len(elementos) != 1:
        return None, 'seção ou rótulo da revisão ausente ou ambíguo'
    try:
        texto = (elementos[0].text or '').strip()
    except WebDriverException:
        texto = ''
    if not texto:
        return None, 'o valor da revisão está ilegível'
    return texto, None


# Era copia byte a byte de `_valor_regra_contrato`, no mesmo arquivo. Duas
# copias divergem; o nome fica por legibilidade no bloco da revisao.
_valor_regra_revisao = _valor_regra_contrato


def _regra_tem_leitor(regra):
    return bool(
        _valor_regra_revisao(regra, 'revisao_secao') and
        _valor_regra_revisao(regra, 'revisao_rotulo')
    )


def _regra_fiscal(regra):
    tipo = str(_valor_regra_revisao(regra, 'tipo', '') or '').lower()
    severidade = str(_valor_regra_revisao(regra, 'severidade', '') or '').lower()
    return bool(_valor_regra_revisao(regra, 'obrigatorio')) or severidade in {
        'fiscal', 'critica'
    } or tipo in {'select', 'radio', 'checkbox', 'revisao', 'decimal', 'valor'}


def _esperado_regra_revisao(regra):
    for chave in ('valor_esperado', 'esperado', 'revisao_esperada'):
        valor = _valor_regra_revisao(regra, chave)
        if valor is not None:
            return valor
    origem = _valor_regra_revisao(regra, 'origem')
    if origem == 'fixo':
        return _valor_regra_revisao(regra, 'valor_fixo')
    return None


def _comparar_revisao_declarativa(texto, esperado, tipo):
    if esperado is None:
        return True
    tipo = str(tipo or 'text').lower()
    if tipo in {'decimal', 'valor', 'number'}:
        esperado_decimal = _decimal_do_portal(esperado)
        observado_decimal = _decimal_do_portal(texto)
        return (
            esperado_decimal is not None and observado_decimal is not None and
            esperado_decimal == observado_decimal
        )
    if tipo in {'documento', 'cpf', 'cnpj'}:
        return _so_digitos(texto) == _so_digitos(esperado)
    return _comparavel(texto) == _comparavel(esperado)


def _regras_elegiveis_automatico(regras):
    for regra in regras or ():
        if not bool(_valor_regra_revisao(regra, 'conferivel_automatico', False)):
            return False
        origem = _valor_regra_revisao(regra, 'origem')
        obrigatorio = bool(_valor_regra_revisao(regra, 'obrigatorio'))
        prova = bool(_valor_regra_revisao(regra, 'prova_avanco', False))
        if origem == 'padrao_portal' and obrigatorio and not prova:
            return False
        if _regra_fiscal(regra) and not _regra_tem_leitor(regra):
            return False
    return True


def calcular_elegibilidade_automatica(regras=(), divergencias=()):
    """Calcula o gate técnico sem confundir ilegibilidade com compatibilidade."""

    return not list(divergencias or ()) and _regras_elegiveis_automatico(regras)


def _conferir_regras_adicionais(driver, regras):
    divergencias = []
    avisos_assistidos = []
    for regra in regras or ():
        chave = _valor_regra_revisao(regra, 'chave_semantica', 'campo sem chave')
        if not _regra_tem_leitor(regra):
            # SEM LEITOR nao existe conferencia automatica possivel, e o
            # `conferivel_automatico` do campo nao muda esse fato — ele so
            # afirmava uma capacidade que o contrato nao declarou. Enquanto a
            # flag mandava, o mesmo campo virava divergencia em TODA nota:
            # sempre a mesma, nao diz nada sobre esta nota, e fechava o gate do
            # automatico por um motivo que nao e do portal.
            if _regra_fiscal(regra):
                avisos_assistidos.append(
                    f'O contrato não declara onde conferir "{chave}" na '
                    'revisão; confira à vista.'
                )
            continue
        texto, erro = _dd_declarativo(
            driver,
            _valor_regra_revisao(regra, 'revisao_secao'),
            _valor_regra_revisao(regra, 'revisao_rotulo'),
        )
        if erro:
            divergencias.append(f'Não consegui conferir o campo contratado "{chave}" na revisão: {erro}.')
            continue
        esperado = _esperado_regra_revisao(regra)
        if esperado is not None and not _comparar_revisao_declarativa(
            texto, esperado, _valor_regra_revisao(regra, 'tipo')
        ):
            divergencias.append(
                f'O campo contratado "{chave}" diverge na revisão.'
            )
    return divergencias, avisos_assistidos


def _so_digitos(texto):
    return ''.join(c for c in str(texto or '') if c.isdigit())


def _decimal_do_portal(texto):
    """'R$ 826,09' -> Decimal('826.09'). None se nao der para ler."""
    from decimal import Decimal, InvalidOperation
    limpo = ''.join(c for c in str(texto or '') if c.isdigit() or c in ',.')
    limpo = limpo.replace('.', '').replace(',', '.')
    try:
        return Decimal(limpo)
    except (InvalidOperation, ValueError):
        return None


def _comparavel(texto):
    """Texto sem acento, sem caixa e com espacos colapsados."""
    return ' '.join(_sem_acento(texto).casefold().split())


def conferir_revisao(
    driver, documento, valor, descricao, regras_adicionais=()
):
    """Rele a tela de revisao e devolve as divergencias encontradas.

    Lista vazia significa "confere". Qualquer item na lista significa NAO
    EMITIR: o texto ja vem escrito para o operador.

    Campo ilegivel conta como divergencia, nunca como aprovacao. E a diferenca
    entre "conferi e esta certo" e "nao consegui conferir" — tratar as duas
    igual transformaria uma mudanca de layout do portal em emissao as cegas.
    """
    divergencias = []

    lido = _dd_da_secao(driver, SECAO_TOMADOR, ROTULO_DOCUMENTO)
    if lido is None:
        divergencias.append('Nao consegui ler o CPF/CNPJ do tomador na revisao.')
    elif _so_digitos(lido) != _so_digitos(documento):
        divergencias.append(
            f'O tomador na tela e {lido}, e a nota e de {documento}.')

    lido = _dd_da_secao(driver, SECAO_VALORES, ROTULO_VALOR)
    na_tela = _decimal_do_portal(lido)
    if lido is None or na_tela is None:
        divergencias.append('Nao consegui ler o valor do servico na revisao.')
    elif na_tela != valor:
        divergencias.append(
            f'O valor na tela e {lido}, e a nota e de {formatar_valor(valor)}.')

    # A descricao e conferida contra todos os <dd> da secao (ver o comentario do
    # bloco): o texto que pretendemos emitir precisa estar na tela.
    esperada = _comparavel(descricao)
    if not any(_comparavel(dd) == esperada for dd in _dds_da_secao(driver, SECAO_SERVICO)):
        divergencias.append(
            f'A descricao na tela nao e a esperada ("{descricao}"). '
            'Confira principalmente a competencia.')

    divergencias_adicionais, avisos_assistidos = _conferir_regras_adicionais(
        driver, regras_adicionais
    )
    divergencias.extend(divergencias_adicionais)
    return ResultadoAutorrevisao(
        divergencias,
        calcular_elegibilidade_automatica(regras_adicionais, divergencias),
        avisos_assistidos,
    )


def emitir(driver, timeout=None):
    """Clica em emitir e espera a confirmacao. True se a nota saiu.

    So o modo automatico (P3) chama isto. Devolver False NAO significa "nao
    emitiu": o clique pode ter dado certo e a confirmacao ter demorado, e quem
    chama precisa tratar isso como "nao sei", nunca como fracasso."""
    timeout = TIMEOUT_REVISAO if timeout is None else timeout
    _clicar(driver, ((By.ID, ID_BTN_EMITIR),), 'O botao "Emitir NFS-e"')
    return esperar(lambda: detectar_emitida(driver), timeout=timeout)
