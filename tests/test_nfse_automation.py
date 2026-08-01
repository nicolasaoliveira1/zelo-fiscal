"""Primitivas de interacao com o Emissor Nacional (NFSE-13).

Driver falso (MagicMock), no estilo de tests/test_steps.py. Os testes assertam
o EFEITO de cada primitiva — o seletor usado e o valor enviado — porque as duas
armadilhas do portal produzem falha silenciosa:

- os radios do mesmo grupo compartilham o id, entao localizar por id marca a
  opcao errada sem erro nenhum;
- os selects ficam ocultos atras do plugin Chosen, entao mexer no elemento
  oculto muda o DOM sem a tela reagir.

Testar "nao levantou excecao" nao pegaria nenhum dos dois.
"""
from unittest.mock import MagicMock

import pytest
from selenium.common.exceptions import NoSuchElementException, WebDriverException
from selenium.webdriver.common.by import By

from app.automation import nfse


@pytest.fixture(autouse=True)
def _espera_curta(monkeypatch):
    """Encurta a espera pelo portal: os casos de falha segurariam a suite pelo
    timeout real de 15s cada, e teste lento e teste que ninguem roda."""
    monkeypatch.setattr(nfse, 'TIMEOUT_AUTOPREENCHIMENTO', 0.05)


def _driver(url='', valor_chosen=None, elemento=None, falha_find=False):
    """Driver falso.

    Implementa `find_elements` (plural) alem do singular: o modulo procura
    TODOS os elementos que casam e escolhe entre eles (o visivel, quando ha
    mais de um — os radios de um grupo compartilham o mesmo id).
    """
    driver = MagicMock()
    driver.current_url = url
    driver.execute_script.return_value = valor_chosen
    if falha_find:
        driver.find_element.side_effect = NoSuchElementException('nao achou')
        driver.find_elements.return_value = []
    elif elemento is not None:
        driver.find_element.return_value = elemento
        driver.find_elements.return_value = [elemento]
    else:
        driver.find_elements.return_value = [MagicMock()]
    return driver


# --- selects: uma via so para Select2, Chosen e nativo ---------------------

def test_selecionar_envia_id_e_valor_para_o_script():
    driver = _driver(valor_chosen='113022100')
    nfse._selecionar(driver, 'ServicoPrestado_CodigoNBS', '113022100')

    script, elemento_id, valor = driver.execute_script.call_args[0]
    assert elemento_id == 'ServicoPrestado_CodigoNBS'
    assert valor == '113022100'


def test_selecionar_avisa_os_dois_plugins():
    """O portal usa DOIS plugins: Select2 (municipio, codigo de tributacao,
    escondidos por classe) e Chosen (item da NBS, display:none inline). Nenhum
    dos dois e manipulavel por Select() do Selenium. `change` atualiza Select2 e
    o select nativo; `chosen:updated` atualiza o Chosen."""
    driver = _driver(valor_chosen='1')
    nfse._selecionar(driver, 'SimplesNacional_RegimeApuracaoTributosSN', '1')

    script = driver.execute_script.call_args[0][0]
    assert 'chosen:updated' in script, 'sem isso o Chosen nao atualiza'
    assert "trigger('change')" in script, 'sem isso o Select2 nao atualiza'


def test_selecionar_funciona_sem_jquery():
    """Fallback nativo: sem jQuery ainda seta o valor e dispara change."""
    driver = _driver(valor_chosen='1')
    nfse._selecionar(driver, 'Campo', '1')
    script = driver.execute_script.call_args[0][0]
    assert 'el.value = valor' in script
    assert "new Event('change'" in script


def test_selecionar_levanta_quando_o_valor_nao_pegou():
    """O caso perigoso: o DOM aceita, a tela ignora, a nota sai em branco.
    Tambem cobre um codigo que saiu da lista do portal."""
    driver = _driver(valor_chosen='')
    with pytest.raises(nfse.InteracaoPortalError) as exc:
        nfse._selecionar(driver, 'ServicoPrestado_CodigoNBS', '113022100')
    assert '113022100' in str(exc.value)


def test_selecionar_levanta_quando_o_campo_sumiu_do_portal():
    driver = _driver(valor_chosen='ausente')
    with pytest.raises(nfse.InteracaoPortalError) as exc:
        nfse._selecionar(driver, 'CampoQueSumiu', '1')
    assert 'recon' in str(exc.value).lower()


def test_selecionar_aceita_valor_nao_string():
    driver = _driver(valor_chosen='1')
    nfse._selecionar(driver, 'SimplesNacional_RegimeApuracaoTributosSN', 1)


# --- radio: por (name, value), NUNCA por id --------------------------------

def test_marcar_radio_localiza_por_name_e_value_nao_por_id():
    """No portal os tres radios do grupo tem o mesmo id: By.ID pegaria o
    primeiro ("Tomador nao informado") em vez de "Brasil"."""
    elemento = MagicMock()
    driver = _driver(elemento=elemento)
    nfse._marcar_radio(driver, 'Tomador.LocalDomicilio', '1')

    by, seletor = driver.find_elements.call_args[0]
    assert by == By.CSS_SELECTOR, 'radio nao pode ser localizado por id'
    assert seletor == 'input[name="Tomador.LocalDomicilio"][value="1"]'


def test_marcar_radio_clica_no_elemento_encontrado():
    elemento = MagicMock()
    driver = _driver(elemento=elemento)
    nfse._marcar_radio(driver, 'ISSQN.HaRetencao', '0')
    assert driver.execute_script.called
    assert elemento in driver.execute_script.call_args[0]


def test_marcar_radio_levanta_quando_a_opcao_nao_existe():
    driver = _driver(falha_find=True)
    with pytest.raises(nfse.InteracaoPortalError) as exc:
        nfse._marcar_radio(driver, 'ISSQN.HaRetencao', '9')
    assert 'ISSQN.HaRetencao' in str(exc.value)


# --- preencher -------------------------------------------------------------

def test_preencher_limpa_escreve_e_sai_do_campo():
    """Sair do campo faz parte do preenchimento, nao e um extra: o portal so
    processa o valor quando o campo perde o foco."""
    elemento = MagicMock()
    driver = _driver(elemento=elemento)
    elemento.parent = driver
    nfse._preencher(driver, 'Tomador_Inscricao', '33.684.001/0001-51')

    assert elemento.clear.called
    enviados = [chamada.args[0] for chamada in elemento.send_keys.call_args_list]
    assert enviados == ['33.684.001/0001-51']


def test_sair_do_campo_nao_usa_teclado():
    """Duas tentativas por teclado falharam por motivos opostos: o TAB leva o
    foco ao botao "Abrir calendario" ao lado da data (o campo nunca sai de
    foco), e o ESC ABRE o datepicker em vez de fechar. O que funciona na mao e
    clicar fora — e o datepicker fecha no mousedown do documento."""
    from selenium.webdriver.common.keys import Keys
    elemento = MagicMock()
    driver = _driver(elemento=elemento)
    elemento.parent = driver
    nfse._preencher(driver, 'DataCompetencia', '28/07/2026')

    enviados = [chamada.args[0] for chamada in elemento.send_keys.call_args_list]
    assert Keys.TAB not in enviados
    assert Keys.ESCAPE not in enviados

    script = driver.execute_script.call_args[0][0]
    assert 'blur' in script and 'change' in script
    assert 'mousedown' in script, 'sem o mousedown no documento o calendario nao fecha'


def test_preencher_sem_sair_nao_manda_teclas():
    elemento = MagicMock()
    driver = _driver(elemento=elemento)
    nfse._preencher(driver, 'Tomador_Inscricao', '123', sair=False)
    elemento.send_keys.assert_called_once_with('123')


def test_preencher_levanta_quando_o_campo_nao_existe():
    driver = _driver(falha_find=True)
    with pytest.raises(nfse.InteracaoPortalError):
        nfse._preencher(driver, 'CampoInexistente', 'x')


# --- deteccao de etapa: path E elemento ------------------------------------

URL_REVISAO = 'https://www.nfse.gov.br/EmissorNacional/DPS/EmitirNFSe?idr=RXN1Q0x5'
URL_CONFIRMACAO = 'https://www.nfse.gov.br/EmissorNacional/DPS/NFSe?idr=RXN1Q0x5'


def test_revisao_exige_path_e_botao():
    assert nfse.na_revisao(_driver(URL_REVISAO, elemento=MagicMock()))


def test_revisao_falsa_quando_o_botao_nao_esta_na_pagina():
    # mesmo path, mas pagina de erro: nao pode ser tratada como revisao
    assert not nfse.na_revisao(_driver(URL_REVISAO, falha_find=True))


def test_revisao_falsa_em_outra_etapa():
    url = 'https://www.nfse.gov.br/EmissorNacional/DPS/Tributacao?idr=X'
    assert not nfse.na_revisao(_driver(url, elemento=MagicMock()))


def test_esperar_revisao_espera_a_pagina_carregar():
    """O ultimo "Avancar" pode ser clicado por JS, que nao bloqueia ate a
    navegacao terminar. Uma leitura unica reprovaria uma nota corretamente
    preenchida — e reprovar aqui marca FALHA numa nota que esta certa."""
    driver = _driver(URL_REVISAO, elemento=MagicMock())
    telas = ['https://www.nfse.gov.br/EmissorNacional/DPS/Tributacao?idr=X',
             'https://www.nfse.gov.br/EmissorNacional/DPS/Tributacao?idr=X',
             URL_REVISAO]
    type(driver).current_url = property(lambda self: telas.pop(0) if telas else URL_REVISAO)

    assert nfse.esperar_revisao(driver, timeout=2)


def test_esperar_revisao_desiste_no_prazo():
    url = 'https://www.nfse.gov.br/EmissorNacional/DPS/Tributacao?idr=X'
    assert not nfse.esperar_revisao(_driver(url, elemento=MagicMock()), timeout=0.3)


def test_emitida_exige_path_e_botao_do_danfse():
    assert nfse.detectar_emitida(_driver(URL_CONFIRMACAO, elemento=MagicMock()))


def test_emitida_falsa_sem_o_botao_do_danfse():
    assert not nfse.detectar_emitida(_driver(URL_CONFIRMACAO, falha_find=True))


def test_tela_de_revisao_NAO_conta_como_emitida():
    """Colisao de substring que causaria emissao fantasma.

    '/DPS/EmitirNFSe' contem 'NFSe'. Se a deteccao de 'emitida' procurasse so
    por 'NFSe', a tela de REVISAO — onde a nota ainda nao foi emitida — seria
    lida como confirmacao, e o lote marcaria a nota como emitida e pularia para
    a proxima sem ninguem ter clicado em nada.
    """
    driver = _driver(URL_REVISAO, elemento=MagicMock())
    assert not nfse.detectar_emitida(driver)


def test_deteccao_nao_levanta_com_driver_morto():
    driver = MagicMock()
    type(driver).current_url = property(
        lambda self: (_ for _ in ()).throw(WebDriverException('sessao morta')))
    assert not nfse.na_revisao(driver)
    assert not nfse.detectar_emitida(driver)


def test_paths_de_revisao_e_confirmacao_sao_distintos():
    assert nfse.PATH_REVISAO != nfse.PATH_CONFIRMACAO
    assert nfse.PATH_CONFIRMACAO not in nfse.PATH_REVISAO


# --- espera com cancelamento ----------------------------------------------

def test_esperar_retorna_true_assim_que_a_condicao_vira():
    chamadas = []

    def condicao():
        chamadas.append(1)
        return len(chamadas) >= 2

    assert nfse.esperar(condicao, timeout=5, intervalo=0)
    assert len(chamadas) == 2


def test_esperar_retorna_false_no_timeout():
    assert not nfse.esperar(lambda: False, timeout=0, intervalo=0)


def test_esperar_desiste_quando_cancelado():
    """Base do pausar/parar durante a espera pela confirmacao humana (P2).

    Precisa medir o TEMPO, nao so o retorno: com um timeout longo, "cancelou"
    e "estourou o timeout" devolvem False do mesmo jeito, e a assercao passaria
    mesmo se o cancelamento fosse ignorado.
    """
    import time
    inicio = time.monotonic()
    assert not nfse.esperar(lambda: False, timeout=30, intervalo=0.1,
                            cancelado=lambda: True)
    assert time.monotonic() - inicio < 1, 'desistiu por timeout, nao por cancelamento'


def test_esperar_checa_cancelamento_antes_da_condicao():
    condicao = MagicMock(return_value=True)
    assert not nfse.esperar(condicao, timeout=9, intervalo=0, cancelado=lambda: True)
    assert not condicao.called


# --- login por certificado -------------------------------------------------

def _driver_login(urls):
    """Driver cujo current_url percorre a sequencia dada a cada leitura."""
    driver = MagicMock()
    sequencia = list(urls)

    def proxima(_self):
        return sequencia.pop(0) if len(sequencia) > 1 else sequencia[0]

    type(driver).current_url = property(proxima)
    return driver


def test_login_abre_o_portal_e_clica_no_link_do_certificado():
    link = MagicMock()
    driver = _driver_login([nfse.URL_LOGIN, 'https://x/EmissorNacional/Dashboard'])
    driver.find_element.return_value = link

    nfse.login_certificado(driver, timeout=1, intervalo=0)

    driver.get.assert_called_once_with(nfse.URL_LOGIN)
    by, seletor = driver.find_element.call_args[0]
    assert (by, seletor) == (By.CSS_SELECTOR, nfse.SEL_LINK_CERTIFICADO)
    assert link.click.called


def test_login_levanta_quando_nao_chega_ao_painel():
    driver = _driver_login(['https://x/EmissorNacional/Login'])
    driver.find_element.return_value = MagicMock()
    with pytest.raises(nfse.LoginNfseError) as exc:
        nfse.login_certificado(driver, timeout=0, intervalo=0)
    # a acao precisa citar o e-CNPJ: confundir com o e-CPF do RS ja aconteceu
    assert 'CNPJ' in (exc.value.acao or '')


def test_login_levanta_quando_o_link_do_certificado_sumiu():
    driver = _driver_login(['https://x/EmissorNacional/Login'])
    driver.find_element.side_effect = NoSuchElementException('sumiu')
    with pytest.raises(nfse.LoginNfseError):
        nfse.login_certificado(driver, timeout=0, intervalo=0)


def test_login_nao_preenche_nada_quando_falha():
    driver = _driver_login(['https://x/EmissorNacional/Login'])
    driver.find_element.return_value = MagicMock()
    with pytest.raises(nfse.LoginNfseError):
        nfse.login_certificado(driver, timeout=0, intervalo=0)
    # so navegou para o login: nenhuma outra pagina foi aberta
    assert driver.get.call_count == 1


# --- leitura da aliquota ---------------------------------------------------

def test_le_a_aliquota_do_perfil():
    elemento = MagicMock()
    elemento.get_attribute.return_value = '3,87'
    driver = _driver(elemento=elemento)
    assert nfse.ler_aliquota_simples(driver) == '3,87'
    driver.get.assert_called_once_with(nfse.URL_CONFIGURACAO)


def test_aliquota_ausente_devolve_none_e_nao_levanta():
    """None nunca pode virar zero: quem chama pede confirmacao manual."""
    assert nfse.ler_aliquota_simples(_driver(falha_find=True)) is None


def test_aliquota_em_branco_devolve_none():
    elemento = MagicMock()
    elemento.get_attribute.return_value = '   '
    assert nfse.ler_aliquota_simples(_driver(elemento=elemento)) is None


# --- id duplicado entre etapas: usar o VISIVEL -----------------------------

def test_localiza_o_elemento_visivel_e_nao_o_primeiro_do_dom():
    """O portal repete id entre as etapas do assistente (ja confirmado nos
    radios). Pegar o primeiro do DOM entrega um elemento de uma etapa oculta:
    o clique falha, ou pior, acontece num campo que ninguem esta vendo."""
    oculto = MagicMock()
    oculto.is_displayed.return_value = False
    visivel = MagicMock()
    visivel.is_displayed.return_value = True
    visivel.is_enabled.return_value = True

    driver = MagicMock()
    driver.find_elements.return_value = [oculto, visivel]
    assert nfse._localizar(driver, By.ID, 'btnAvancar') is visivel


def test_elemento_so_desabilitado_tambem_e_ignorado():
    desabilitado = MagicMock()
    desabilitado.is_displayed.return_value = True
    desabilitado.is_enabled.return_value = False
    driver = MagicMock()
    driver.find_elements.return_value = [desabilitado]
    assert nfse._localizar(driver, By.ID, 'x') is None


def test_avancar_rola_ate_o_botao_antes_de_clicar():
    """O botao fica no fim de um formulario longo."""
    botao = MagicMock()
    botao.is_displayed.return_value = True
    botao.is_enabled.return_value = True
    driver = MagicMock()
    driver.find_elements.return_value = [botao]

    nfse._avancar(driver)
    scripts = [c[0][0] for c in driver.execute_script.call_args_list]
    assert any('scrollIntoView' in s for s in scripts)
    assert botao.click.called


def test_avancar_cai_para_clique_por_js_quando_o_nativo_falha():
    """Aviso ou rodape fixo por cima interceptam o clique nativo."""
    from selenium.common.exceptions import ElementClickInterceptedException
    botao = MagicMock()
    botao.is_displayed.return_value = True
    botao.is_enabled.return_value = True
    botao.click.side_effect = ElementClickInterceptedException('coberto')
    driver = MagicMock()
    driver.find_elements.return_value = [botao]

    nfse._avancar(driver)
    scripts = [c[0][0] for c in driver.execute_script.call_args_list]
    assert any('click' in s for s in scripts), 'sem fallback o avanco morreria aqui'


def test_avancar_sem_botao_visivel_da_mensagem_certa():
    """A mensagem antiga dizia "nao encontrado" tanto para ausencia quanto para
    clique falho — e mandava investigar o lado errado."""
    driver = MagicMock()
    driver.find_elements.return_value = []
    with pytest.raises(nfse.InteracaoPortalError) as exc:
        nfse._avancar(driver)
    assert 'nao esta disponivel' in str(exc.value)


# --- botao Avancar: o id so existe na etapa 1 ------------------------------

def _driver_avancar(com_id):
    """Driver cujo #btnAvancar existe ou nao, conforme a etapa.

    Na etapa 1 o botao e <button id="btnAvancar" ...>; nas etapas 2 e 3 e o
    mesmo elemento SEM identificador nenhum, so as classes.
    """
    botao = MagicMock()
    botao.is_displayed.return_value = True
    botao.is_enabled.return_value = True

    driver = MagicMock()
    driver.find_elements.side_effect = lambda by, alvo: (
        [botao] if (by == By.ID and com_id) or by != By.ID else []
    )
    return driver, botao


def test_avancar_acha_pelo_id_na_primeira_etapa():
    driver, botao = _driver_avancar(com_id=True)
    nfse._avancar(driver)
    assert botao.click.called
    primeira_busca = driver.find_elements.call_args_list[0][0]
    assert primeira_busca[0] == By.ID, 'o id e o localizador mais especifico'


def test_avancar_acha_sem_id_nas_etapas_seguintes():
    """Regressao: nas etapas 2 e 3 o botao nao tem id, e procurar so por
    #btnAvancar travava o assistente depois de preencher tudo."""
    driver, botao = _driver_avancar(com_id=False)
    nfse._avancar(driver)
    assert botao.click.called

    usados = [c[0] for c in driver.find_elements.call_args_list]
    assert any(by == By.ID for by, _ in usados), 'tentou o id primeiro'
    assert any(by != By.ID for by, _ in usados), 'caiu para o localizador sem id'


def test_localizadores_do_avancar_cobrem_classe_e_texto():
    """Se o portal trocar as classes, ainda resta achar pelo texto do botao."""
    porBy = {by: alvo for by, alvo in nfse.LOCALIZADORES_AVANCAR}
    assert porBy[By.ID] == 'btnAvancar'
    assert 'has-spin' in porBy[By.CSS_SELECTOR]
    assert 'Avançar' in porBy[By.XPATH]


def test_avancar_sem_nenhum_localizador_da_erro_acionavel():
    driver = MagicMock()
    driver.find_elements.return_value = []
    with pytest.raises(nfse.InteracaoPortalError) as exc:
        nfse._avancar(driver)
    assert 'Avancar' in str(exc.value)


# --- emissao confirmada: dois sinais independentes -------------------------

def _driver_confirmacao(url=URL_CONFIRMACAO, tem_danfse=False, alerta=None):
    """Driver da tela de confirmacao, com controle fino de cada sinal.

    `alerta` e o texto do <div class="alert-success">, ou None para nenhum.
    """
    driver = MagicMock()
    driver.current_url = url

    if tem_danfse:
        driver.find_element.return_value = MagicMock()
    else:
        driver.find_element.side_effect = NoSuchElementException('nao achou')

    caixas = []
    if alerta is not None:
        caixa = MagicMock()
        caixa.text = alerta
        caixas.append(caixa)
    driver.find_elements.return_value = caixas
    return driver


def test_emitida_pelo_alerta_de_sucesso_sem_o_botao_do_danfse():
    """O alerta sozinho confirma a emissao: se o portal mudar o botao de
    download, a espera do lote nao pode ficar presa ate o timeout."""
    driver = _driver_confirmacao(alerta='A NFS-e foi gerada com sucesso')
    assert nfse.detectar_emitida(driver)


def test_emitida_pelo_botao_mesmo_sem_alerta():
    assert nfse.detectar_emitida(_driver_confirmacao(tem_danfse=True))


def test_alerta_verde_de_outro_assunto_nao_conta_como_emissao():
    """`alert-success` e generico no portal. So a classe marcaria como emitida
    qualquer confirmacao verde — e o lote pularia uma nota nao emitida."""
    driver = _driver_confirmacao(alerta='Rascunho salvo com sucesso')
    assert not nfse.detectar_emitida(driver)


def test_alerta_de_sucesso_fora_da_tela_de_confirmacao_nao_conta():
    """O path e ancora obrigatoria: alerta verde na tela de revisao nao emite."""
    driver = _driver_confirmacao(url=URL_REVISAO,
                                 alerta='A NFS-e foi gerada com sucesso')
    assert not nfse.detectar_emitida(driver)


def test_nenhum_dos_dois_sinais_nao_e_emissao():
    assert not nfse.detectar_emitida(_driver_confirmacao())


def test_alerta_ilegivel_nao_derruba_a_deteccao():
    """Elemento que sumiu do DOM entre o find e o .text (stale) e comum na
    tela recem-carregada; nao pode virar excecao no meio da espera."""
    caixa = MagicMock()
    type(caixa).text = property(
        lambda self: (_ for _ in ()).throw(WebDriverException('stale')))
    driver = MagicMock()
    driver.current_url = URL_CONFIRMACAO
    driver.find_element.side_effect = NoSuchElementException('nao achou')
    driver.find_elements.return_value = [caixa]
    assert not nfse.detectar_emitida(driver)
