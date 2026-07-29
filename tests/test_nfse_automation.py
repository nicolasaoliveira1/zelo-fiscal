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
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By

from app.automation import nfse


def _driver(url='', valor_chosen=None, elemento=None, falha_find=False):
    driver = MagicMock()
    driver.current_url = url
    driver.execute_script.return_value = valor_chosen
    if falha_find:
        driver.find_element.side_effect = NoSuchElementException('nao achou')
    elif elemento is not None:
        driver.find_element.return_value = elemento
    return driver


# --- Chosen: o valor tem de ser conferido depois de setar ------------------

def test_set_chosen_envia_id_e_valor_para_o_script():
    driver = _driver(valor_chosen='113022100')
    nfse._set_chosen(driver, 'ServicoPrestado_CodigoNBS', '113022100')

    script, elemento_id, valor = driver.execute_script.call_args[0]
    assert elemento_id == 'ServicoPrestado_CodigoNBS'
    assert valor == '113022100'
    # a API do plugin, nao Select() do Selenium
    assert 'chosen:updated' in script
    assert 'change' in script


def test_set_chosen_levanta_quando_o_valor_nao_pegou():
    """O caso perigoso: o DOM aceita, a tela ignora, a nota sai em branco."""
    driver = _driver(valor_chosen='')
    with pytest.raises(nfse.InteracaoPortalError) as exc:
        nfse._set_chosen(driver, 'ServicoPrestado_CodigoNBS', '113022100')
    assert '113022100' in str(exc.value)


def test_set_chosen_levanta_quando_o_campo_sumiu_do_portal():
    driver = _driver(valor_chosen='ausente')
    with pytest.raises(nfse.InteracaoPortalError) as exc:
        nfse._set_chosen(driver, 'CampoQueSumiu', '1')
    assert 'recon' in str(exc.value).lower()


def test_set_chosen_levanta_sem_jquery():
    driver = _driver(valor_chosen='sem-jquery')
    with pytest.raises(nfse.InteracaoPortalError):
        nfse._set_chosen(driver, 'ServicoPrestado_CodigoNBS', '113022100')


def test_set_chosen_aceita_valor_nao_string():
    driver = _driver(valor_chosen='1')
    nfse._set_chosen(driver, 'SimplesNacional_RegimeApuracaoTributosSN', 1)


# --- radio: por (name, value), NUNCA por id --------------------------------

def test_marcar_radio_localiza_por_name_e_value_nao_por_id():
    """No portal os tres radios do grupo tem o mesmo id: By.ID pegaria o
    primeiro ("Tomador nao informado") em vez de "Brasil"."""
    elemento = MagicMock()
    driver = _driver(elemento=elemento)
    nfse._marcar_radio(driver, 'Tomador.LocalDomicilio', '1')

    by, seletor = driver.find_element.call_args[0]
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

def test_preencher_limpa_antes_de_escrever():
    elemento = MagicMock()
    driver = _driver(elemento=elemento)
    nfse._preencher(driver, 'Tomador_Inscricao', '33.684.001/0001-51')
    assert elemento.clear.called
    elemento.send_keys.assert_called_once_with('33.684.001/0001-51')


def test_preencher_levanta_quando_o_campo_nao_existe():
    driver = _driver(falha_find=True)
    with pytest.raises(nfse.InteracaoPortalError):
        nfse._preencher(driver, 'CampoInexistente', 'x')


# --- deteccao de etapa: path E elemento ------------------------------------

URL_REVISAO = 'https://www.nfse.gov.br/EmissorNacional/DPS/EmitirNFSe?idr=RXN1Q0x5'
URL_CONFIRMACAO = 'https://www.nfse.gov.br/EmissorNacional/DPS/NFSe?idr=RXN1Q0x5'


def test_revisao_exige_path_e_botao():
    assert nfse.esperar_revisao(_driver(URL_REVISAO, elemento=MagicMock()))


def test_revisao_falsa_quando_o_botao_nao_esta_na_pagina():
    # mesmo path, mas pagina de erro: nao pode ser tratada como revisao
    assert not nfse.esperar_revisao(_driver(URL_REVISAO, falha_find=True))


def test_revisao_falsa_em_outra_etapa():
    url = 'https://www.nfse.gov.br/EmissorNacional/DPS/Tributacao?idr=X'
    assert not nfse.esperar_revisao(_driver(url, elemento=MagicMock()))


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
    from selenium.common.exceptions import WebDriverException
    driver = MagicMock()
    type(driver).current_url = property(
        lambda self: (_ for _ in ()).throw(WebDriverException('sessao morta')))
    assert not nfse.esperar_revisao(driver)
    assert not nfse.detectar_emitida(driver)


def test_paths_de_revisao_e_confirmacao_sao_distintos():
    assert nfse.PATH_REVISAO != nfse.PATH_CONFIRMACAO
    assert nfse.PATH_CONFIRMACAO not in nfse.PATH_REVISAO
