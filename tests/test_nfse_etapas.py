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
    documento='33.684.001/0001-51',
    valor_final=Decimal('826.09'),
    competencia='06/2026',
)


class DriverEspiao:
    """Driver falso que registra tudo que foi tocado, por id/seletor.

    `autopreenchidos` simula os campos que o PORTAL preenche sozinho (emitente
    apos a data, tomador apos o documento) — sem eles a automacao espera para
    sempre, que e exatamente o que deve acontecer quando o portal nao responde.
    """

    def __init__(self):
        self.autopreenchidos = {
            'Prestador_Inscricao': '94.645.405/0001-20',
            'Tomador_Nome': 'L. LUIS PETRY',
        }
        # campos que o portal ainda nao revelou / mantem travados
        self.ocultos = set()
        self.desabilitados = set()
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
        elemento.parent = self
        elemento.send_keys.side_effect = lambda texto: (
            self.preenchidos.__setitem__(valor, texto))
        elemento.get_attribute.side_effect = lambda attr: (
            self.preenchidos.get(valor, self.autopreenchidos.get(valor, ''))
            if attr == 'value' else None)
        elemento.click.side_effect = lambda: self.clicados.append(valor)
        elemento.is_displayed.side_effect = lambda: valor not in self.ocultos
        elemento.is_enabled.side_effect = lambda: valor not in self.desabilitados
        if valor.startswith('input[name='):
            nome = valor.split('"')[1]
            marcado = valor.split('"')[3]
            self.radios[nome] = marcado
        return elemento

    def find_elements(self, by, valor):
        """O modulo procura todos os que casam e prefere o visivel — mas aceita
        o oculto para radio, que no portal e sempre CSS-hidden."""
        return [self.find_element(by, valor)]

    def execute_script(self, script, *args):
        if 'scrollIntoView' in script:
            return None
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


@pytest.fixture(autouse=True)
def _espera_curta(monkeypatch):
    """Encurta a espera pelo autopreenchimento do portal.

    Sem isso, cada teste do caminho de falha segura a suite pelo timeout real
    de 15s — e teste lento e teste que ninguem roda."""
    monkeypatch.setattr(nfse, 'TIMEOUT_AUTOPREENCHIMENTO', 0.05)


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


# --- saida do campo e espera pelo portal (bug real) ------------------------

def test_sai_do_campo_depois_de_digitar(driver):
    """Sem sair do campo da data, o portal nao processa o valor e os campos
    seguintes nunca sao liberados. O teclado nao serve: TAB cai no botao
    "Abrir calendario" ao lado, e ESC ABRE o datepicker. A saida e simular o
    clique fora, cujo mousedown no documento fecha o calendario."""
    from selenium.webdriver.common.keys import Keys
    enviados = []
    scripts = []

    class Espiao(DriverEspiao):
        def find_element(self, by, valor):
            elemento = super().find_element(by, valor)
            original = elemento.send_keys.side_effect
            elemento.send_keys.side_effect = lambda t: (enviados.append((valor, t)),
                                                        original(t))[1]
            return elemento

        def execute_script(self, script, *args):
            scripts.append(script)
            return super().execute_script(script, *args)

    nfse.preencher_etapa_pessoas(Espiao(), NOTA, CONFIG, date(2026, 7, 28))

    teclas = [t for _, t in enviados]
    assert Keys.TAB not in teclas and Keys.ESCAPE not in teclas
    assert any('mousedown' in s for s in scripts), 'o calendario fecha no mousedown'
    assert any('blur' in s for s in scripts), 'o portal so processa no blur'


def test_radio_e_localizado_mesmo_sendo_invisivel(driver):
    """No portal NENHUM radio e visivel para o Selenium — sao inputs escondidos
    por CSS atras de labels estilizados, inclusive os ja marcados. Exigir
    visibilidade neles nao acha nada e quebra a etapa inteira."""
    driver.ocultos.add('input[name="Tomador.LocalDomicilio"][value="1"]')
    nfse.preencher_etapa_pessoas(driver, NOTA, CONFIG, date(2026, 7, 28))
    assert driver.radios['Tomador.LocalDomicilio'] == '1'
    assert 'btnAvancar' in driver.clicados


def test_espera_o_campo_ficar_interagivel_antes_de_digitar(driver):
    """Os campos do tomador so aparecem depois de marcar "Brasil". Digitar
    antes disso e exatamente o "element not interactable" relatado."""
    driver.ocultos.add('Tomador_Inscricao')
    with pytest.raises(nfse.InteracaoPortalError) as exc:
        nfse.preencher_etapa_pessoas(driver, NOTA, CONFIG, date(2026, 7, 28))
    assert 'Tomador_Inscricao' in str(exc.value)
    assert 'btnAvancar' not in driver.clicados


def test_campo_desabilitado_tambem_e_esperado(driver):
    driver.desabilitados.add('Tomador_Inscricao')
    with pytest.raises(nfse.InteracaoPortalError):
        nfse.preencher_etapa_pessoas(driver, NOTA, CONFIG, date(2026, 7, 28))


def test_espera_o_portal_carregar_o_emitente_antes_de_seguir(driver):
    """O emitente so aparece depois que a data perde o foco; seguir antes disso
    encontra os campos ainda travados."""
    driver.autopreenchidos.pop('Prestador_Inscricao')
    with pytest.raises(nfse.InteracaoPortalError) as exc:
        nfse.preencher_etapa_pessoas(driver, NOTA, CONFIG, date(2026, 7, 28),
                                     )
    assert 'emitente' in str(exc.value).lower()
    # nao seguiu para os campos do tomador
    assert 'Tomador_Inscricao' not in driver.preenchidos


def test_documento_nao_reconhecido_pelo_portal_da_erro_acionavel(driver):
    """Sem o nome do tomador, o portal nao reconheceu o documento — emitir
    assim geraria nota sem tomador."""
    driver.autopreenchidos.pop('Tomador_Nome')
    with pytest.raises(nfse.InteracaoPortalError) as exc:
        nfse.preencher_etapa_pessoas(driver, NOTA, CONFIG, date(2026, 7, 28))
    assert NOTA.documento in str(exc.value)
    assert 'btnAvancar' not in driver.clicados, 'avancou com o tomador vazio'
