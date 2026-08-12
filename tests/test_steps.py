"""Testes do executor de steps municipais (steps.executar_municipio).

Demonstra que dá para exercitar a lógica de fluxo Selenium com um driver/wait
FALSOS (unittest.mock), sem navegador real — exatamente o tipo de teste viável
para os fluxos de automação.
"""
from unittest.mock import MagicMock

from app.automation import steps


def test_sem_steps_retorna_none():
    assert steps.executar_municipio(MagicMock(), MagicMock(), [], '', '') is None


def test_fill_usa_cnpj_e_click():
    driver = MagicMock()
    wait = MagicMock()
    elemento = MagicMock()
    wait.until.return_value = elemento

    steps_def = [
        {'tipo': 'fill', 'by': 'id', 'locator': 'campoCnpj', 'value': 'cnpj', 'sleep': 0},
        {'tipo': 'click', 'by': 'id', 'locator': 'btnEmitir', 'sleep': 0},
    ]
    resultado = steps.executar_municipio(
        driver, wait, steps_def, '12345678000199', '', 'after_cnpj'
    )
    assert resultado is None                       # nao encerrou sem arquivo
    elemento.send_keys.assert_any_call('12345678000199')  # preencheu o CNPJ
    assert elemento.click.called                   # clicou no botao


def test_fill_usa_inscricao():
    driver = MagicMock()
    wait = MagicMock()
    elemento = MagicMock()
    wait.until.return_value = elemento

    steps_def = [{'tipo': 'fill', 'by': 'id', 'locator': 'insc', 'value': 'inscricao', 'sleep': 0}]
    steps.executar_municipio(driver, wait, steps_def, '12345678000199', '000123', 'after_cnpj')
    elemento.send_keys.assert_any_call('000123')


def test_by_invalido_ignora_step():
    driver = MagicMock()
    wait = MagicMock()
    steps_def = [{'tipo': 'click', 'by': 'xpto_invalido', 'locator': 'x', 'sleep': 0}]
    # by desconhecido -> step ignorado, sem chamar wait.until
    steps.executar_municipio(driver, wait, steps_def, '', '')
    assert not wait.until.called


# --- clicar_pre_fill: nucleo compartilhado do passo pre-CNPJ ---------------

def test_pre_fill_clica_e_usa_o_by_configurado():
    from unittest.mock import patch

    from selenium.webdriver.common.by import By
    wait = MagicMock()
    elemento = MagicMock()
    wait.until.return_value = elemento
    info = {'pre_fill_click_id': "input[value='J']", 'pre_fill_click_by': 'css_selector'}

    with patch.object(steps.EC, 'element_to_be_clickable') as cond:
        assert steps.clicar_pre_fill(info, wait, by_padrao='id') is True
    assert elemento.click.called
    # o by da config vence o padrao do chamador
    assert cond.call_args.args[0] == (By.CSS_SELECTOR, "input[value='J']")


def test_pre_fill_sem_configuracao_nao_toca_no_driver():
    wait = MagicMock()
    assert steps.clicar_pre_fill({}, wait) is None
    assert steps.clicar_pre_fill(None, wait) is None
    assert not wait.until.called


def test_pre_fill_by_invalido_e_falha_declarada():
    # Nao clica (como antes), mas devolve False: o dry-run precisa distinguir
    # "nao ha passo" de "ha passo e nao deu para executar".
    wait = MagicMock()
    info = {'pre_fill_click_id': 'x', 'pre_fill_click_by': 'xpto_invalido'}
    assert steps.clicar_pre_fill(info, wait) is False
    assert not wait.until.called


def test_pre_fill_elemento_ausente_nao_levanta():
    wait = MagicMock()
    wait.until.side_effect = RuntimeError('sumiu')
    info = {'pre_fill_click_id': 'x', 'pre_fill_click_by': 'id'}
    assert steps.clicar_pre_fill(info, wait) is False   # best-effort, como antes


def test_pre_fill_usa_by_padrao_quando_a_config_nao_diz():
    wait = MagicMock()
    wait.until.return_value = MagicMock()
    info = {'pre_fill_click_id': 'x', 'pre_fill_click_by': None}
    assert steps.clicar_pre_fill(info, wait, by_padrao='id') is True
    assert steps.clicar_pre_fill(info, wait) is False   # sem padrao, nada a fazer
