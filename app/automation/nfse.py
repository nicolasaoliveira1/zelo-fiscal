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

    `sair` manda ESC + TAB depois de digitar, por dois motivos observados no
    portal:

    - o campo de data abre um datepicker que fica POR CIMA do proximo campo, e
      o clique seguinte falha com "element not interactable" (ESC fecha);
    - o portal so processa o valor quando o campo perde o foco — e e isso que
      dispara o preenchimento automatico do emitente e do tomador (TAB).
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


def _visivel(elemento):
    try:
        return bool(elemento.is_displayed() and elemento.is_enabled())
    except WebDriverException:
        return False


def _localizar(driver, by, alvo, exigir_visivel=True):
    """Elemento visivel que casa; com `exigir_visivel=False`, o primeiro que casar.

    Preferir o visivel importa porque o portal repete identificadores entre
    secoes do assistente, e o primeiro do DOM pode estar numa parte oculta.

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
    return PATH_DASHBOARD in _url(driver)


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


def _clicar(driver, by, alvo, rotulo):
    """Clica no elemento visivel, rolando ate ele e caindo para clique por JS.

    Separa "nao achei" de "achei mas o clique falhou": mensagens iguais para as
    duas coisas ja mandaram investigar o lado errado. O botao de avancar fica no
    fim de um formulario longo, entao rolar ate ele importa; e se algo ficar por
    cima (aviso, rodape fixo), o clique por JS ainda resolve.
    """
    if not esperar(lambda: _localizar(driver, by, alvo) is not None,
                   timeout=TIMEOUT_AUTOPREENCHIMENTO, intervalo=0.2):
        raise InteracaoPortalError(
            f'{rotulo} nao esta disponivel nesta tela. O portal pode ter mudado '
            'o formulario ou a etapa nao terminou de carregar.')

    elemento = _localizar(driver, by, alvo)
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


def _avancar(driver):
    _clicar(driver, By.ID, ID_BTN_AVANCAR, 'O botao "Avancar"')


def preencher_etapa_pessoas(driver, nota, config, data_competencia, pausa=None):
    """Etapa 1: competencia, regime de apuracao e tomador.

    `data_competencia` e a data da EMISSAO (hoje) — nao confundir com a
    competencia da descricao, que vem do vencimento. Emitente, nome do tomador
    e endereco sao preenchidos pelo proprio portal apos a data e o CNPJ; a
    automacao nao os toca.
    """
    _preencher(driver, 'DataCompetencia', formatar_data(data_competencia))

    # O portal so carrega o emitente depois que a data sai do foco, e os campos
    # seguintes ficam nao-interagiveis ate la. Esperar o CNPJ do emitente
    # aparecer e o sinal de que a etapa esta pronta.
    if not _esperar_preenchido(driver, 'Prestador_Inscricao'):
        raise InteracaoPortalError(
            'O portal nao carregou os dados do emitente apos a data de '
            'competencia. A tela pode estar lenta ou ter mudado.')
    if pausa:
        pausa()

    _selecionar(driver, 'SimplesNacional_RegimeApuracaoTributosSN',
                config.regime_apuracao_sn)

    # Marcar "Brasil" e o que REVELA os campos do tomador; o _preencher abaixo
    # ja espera o CNPJ ficar interagivel antes de digitar.
    _marcar_radio(driver, 'Tomador.LocalDomicilio', '1')
    _preencher(driver, 'Tomador_Inscricao', nota.documento)

    # mesma logica: o nome/endereco do tomador vem do portal apos o documento
    if not _esperar_preenchido(driver, 'Tomador_Nome'):
        raise InteracaoPortalError(
            f'O portal nao reconheceu o documento {nota.documento} do tomador. '
            'Confira se esta correto e ativo na Receita.')
    if pausa:
        pausa()
    _avancar(driver)


def preencher_etapa_servico(driver, nota, config, descricao, pausa=None):
    """Etapa 2: local, codigo de tributacao, descricao e item da NBS.

    Municipio e codigo de tributacao sao selects VISIVEIS (nao usam Chosen);
    o item da NBS e Chosen com 919 opcoes.
    """
    _selecionar(driver, 'LocalPrestacao_CodigoMunicipioPrestacao',
                config.municipio_servico_codigo)
    _selecionar(driver, 'ServicoPrestado_CodigoTributacaoNacional',
                config.codigo_tributacao)
    # "Nao" para imunidade/exportacao: ja e o default, mas marcar explicitamente
    # evita depender de o portal manter esse default.
    _marcar_radio(driver, 'ServicoPrestado.HaExportacaoImunidadeNaoIncidencia', '0')
    _preencher(driver, 'ServicoPrestado_Descricao', descricao)
    _selecionar(driver, 'ServicoPrestado_CodigoNBS', config.item_nbs)
    if pausa:
        pausa()
    _avancar(driver)


def preencher_etapa_tributacao(driver, nota, config, pausa=None):
    """Etapa 3: valor do servico e retencoes.

    A retencao do ISSQN NAO vem marcada (nem Sim nem Nao) — e obrigatoria.
    Os campos de base de calculo/valor/aliquota do ISSQN sao calculados e
    bloqueados pelo portal; a automacao nao os toca.
    """
    _preencher(driver, 'Valores_ValorServico', formatar_valor(nota.valor_final))
    _marcar_radio(driver, 'ISSQN.HaRetencao', '0')  # Nao
    _selecionar(driver, 'TributacaoFederal_PISCofins_SituacaoTributaria',
                config.piscofins_situacao)
    _selecionar(driver, 'TributacaoFederal_PISCofins_TipoRetencao',
                config.piscofins_tipo_retencao)
    if pausa:
        pausa()
    _avancar(driver)


