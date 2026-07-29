"""Preenchimento das tres etapas do assistente DPS (NFSE-13).

Cada teste asserta o PAR (seletor, valor) que foi de fato usado — nao apenas
que a funcao rodou sem levantar. Os valores conferidos vieram da recon contra o
portal real; se algum divergir, a nota sai errada e o erro so apareceria na
tela de revisao (ou, no P3, nem isso).

O teste mais importante do arquivo e o ultimo: prova que a automacao NAO toca
os campos que o portal ja traz corretos nem os calculados/bloqueados. Mexer
neles reabre secoes condicionais e muda a nota.
"""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.automation import nfse

CONFIG = SimpleNamespace(
    regime_apuracao_sn='1',
    municipio_servico_codigo='4310330',
    municipio_servico_nome='Imbé/RS',
    codigo_tributacao='17.19.01',
    item_nbs='113022100',
    piscofins_situacao='0',
    piscofins_tipo_retencao='0',
)

NOTA = SimpleNamespace(
    cnpj='33.684.001/0001-51',
    valor_final=Decimal('826.09'),
    competencia='06/2026',
)


class DriverEspiao:
    """Driver falso que registra tudo que foi tocado, por id/seletor."""

    def __init__(self):
        self.preenchidos = {}
        self.chosen = {}
        self.selects = {}
        self.radios = {}
        self.clicados = []
        self.current_url = ''

    # --- API que o modulo usa ---
    def find_element(self, by, valor):
        elemento = MagicMock()
        elemento._by = by
        elemento._valor = valor
        elemento.send_keys.side_effect = lambda texto: self.preenchidos.__setitem__(valor, texto)
        elemento.click.side_effect = lambda: self.clicados.append(valor)
        if valor.startswith('input[name='):
            nome = valor.split('"')[1]
            marcado = valor.split('"')[3]
            self.radios[nome] = marcado
        return elemento

    def execute_script(self, script, *args):
        if 'chosen:updated' in script:
            self.chosen[args[0]] = args[1]
            return args[1]
        if args:
            self.clicados.append(getattr(args[0], '_valor', '?'))
        return None

    # --- tudo que foi tocado, em qualquer via ---
    def tocados(self):
        return (set(self.preenchidos) | set(self.chosen) | set(self.selects)
                | set(self.radios) | set(self.clicados))


def _select_falso(espiao):
    class Select:
        def __init__(self, elemento):
            self._id = elemento._valor

        def select_by_value(self, valor):
            espiao.selects[self._id] = valor
    return Select


@pytest.fixture()
def driver(monkeypatch):
    espiao = DriverEspiao()
    import selenium.webdriver.support.ui as ui
    monkeypatch.setattr(ui, 'Select', _select_falso(espiao))
    return espiao


# --- formatadores ----------------------------------------------------------

@pytest.mark.parametrize('valor,esperado', [
    (Decimal('826.09'), '826,09'),
    (Decimal('1784.00'), '1784,00'),
    (Decimal('5000'), '5000,00'),
    (Decimal('0.55'), '0,55'),
])
def test_valor_sai_no_padrao_brasileiro(valor, esperado):
    assert nfse.formatar_valor(valor) == esperado


def test_data_sai_no_formato_do_portal():
    assert nfse.formatar_data(date(2026, 7, 28)) == '28/07/2026'


# --- etapa 1: pessoas ------------------------------------------------------

def test_etapa_pessoas_preenche_data_regime_tomador_e_avanca(driver):
    nfse.preencher_etapa_pessoas(driver, NOTA, CONFIG, date(2026, 7, 28))

    assert driver.preenchidos['DataCompetencia'] == '28/07/2026'
    assert driver.chosen['SimplesNacional_RegimeApuracaoTributosSN'] == '1'
    assert driver.radios['Tomador.LocalDomicilio'] == '1'   # Brasil
    assert driver.preenchidos['Tomador_Inscricao'] == '33.684.001/0001-51'
    assert 'btnAvancar' in driver.clicados


def test_etapa_pessoas_nao_toca_os_dados_do_emitente(driver):
    """Emitente, nome do tomador e endereco vem do proprio portal."""
    nfse.preencher_etapa_pessoas(driver, NOTA, CONFIG, date(2026, 7, 28))
    for campo in ('Prestador_Inscricao', 'Prestador_Nome', 'Tomador_Nome',
                  'Tomador_EnderecoNacional_CEP', 'Tomador_EnderecoNacional_Bairro'):
        assert campo not in driver.tocados(), f'{campo} e preenchido pelo portal'


def test_etapa_pessoas_marca_brasil_e_nao_a_primeira_opcao(driver):
    """Os tres radios do grupo tem o mesmo id; value=0 e "Tomador nao
    informado", que passaria batido se a localizacao fosse por id."""
    nfse.preencher_etapa_pessoas(driver, NOTA, CONFIG, date(2026, 7, 28))
    assert driver.radios['Tomador.LocalDomicilio'] != '0'


# --- etapa 2: servico ------------------------------------------------------

def test_etapa_servico_usa_select_comum_nos_campos_visiveis(driver):
    nfse.preencher_etapa_servico(driver, NOTA, CONFIG, 'HONORARIOS DE 06/2026')

    # visiveis: Select() do Selenium
    assert driver.selects['LocalPrestacao_CodigoMunicipioPrestacao'] == '4310330'
    assert driver.selects['ServicoPrestado_CodigoTributacaoNacional'] == '17.19.01'
    # ocultos atras do Chosen: via jQuery
    assert driver.chosen['ServicoPrestado_CodigoNBS'] == '113022100'


def test_etapa_servico_nao_usa_chosen_nos_campos_visiveis(driver):
    """Municipio e codigo de tributacao NAO tem Chosen; usar a via do plugin
    neles seria mexer num elemento que nao existe."""
    nfse.preencher_etapa_servico(driver, NOTA, CONFIG, 'HONORARIOS DE 06/2026')
    assert 'LocalPrestacao_CodigoMunicipioPrestacao' not in driver.chosen
    assert 'ServicoPrestado_CodigoTributacaoNacional' not in driver.chosen


def test_etapa_servico_escreve_a_descricao_com_a_competencia(driver):
    nfse.preencher_etapa_servico(driver, NOTA, CONFIG,
                                 'HONORÁRIOS PROFISSIONAIS REFERENTES AO MÊS DE 06/2026')
    assert driver.preenchidos['ServicoPrestado_Descricao'].endswith('06/2026')


def test_etapa_servico_marca_nao_para_imunidade(driver):
    nfse.preencher_etapa_servico(driver, NOTA, CONFIG, 'x')
    assert driver.radios['ServicoPrestado.HaExportacaoImunidadeNaoIncidencia'] == '0'


def test_etapa_servico_avanca(driver):
    nfse.preencher_etapa_servico(driver, NOTA, CONFIG, 'x')
    assert 'btnAvancar' in driver.clicados


# --- etapa 3: tributacao ---------------------------------------------------

def test_etapa_tributacao_preenche_valor_retencao_e_piscofins(driver):
    nfse.preencher_etapa_tributacao(driver, NOTA, CONFIG)

    assert driver.preenchidos['Valores_ValorServico'] == '826,09'
    assert driver.radios['ISSQN.HaRetencao'] == '0'         # Nao
    assert driver.chosen['TributacaoFederal_PISCofins_SituacaoTributaria'] == '0'
    assert driver.chosen['TributacaoFederal_PISCofins_TipoRetencao'] == '0'
    assert 'btnAvancar' in driver.clicados


def test_retencao_do_issqn_e_sempre_marcada(driver):
    """Esse radio nao vem marcado do portal (nem Sim nem Nao) e e obrigatorio:
    esquecer dele trava o avanco da etapa."""
    nfse.preencher_etapa_tributacao(driver, NOTA, CONFIG)
    assert 'ISSQN.HaRetencao' in driver.radios


# --- o que a automacao NAO pode tocar --------------------------------------

def test_nenhuma_etapa_toca_os_campos_intocaveis(driver):
    """Campos que o portal ja traz corretos ou calcula sozinho.

    Os tres do ISSQN (base de calculo, valor, aliquota) sao BLOQUEADOS pelo
    portal — escrever neles falharia. Os demais ja vem com o valor certo, e
    mexer reabre secoes condicionais do formulario.
    """
    nfse.preencher_etapa_pessoas(driver, NOTA, CONFIG, date(2026, 7, 28))
    nfse.preencher_etapa_servico(driver, NOTA, CONFIG, 'x')
    nfse.preencher_etapa_tributacao(driver, NOTA, CONFIG)

    tocados = driver.tocados()
    for campo in nfse.CAMPOS_INTOCAVEIS:
        assert campo not in tocados, f'{campo} nao pode ser tocado pela automacao'
