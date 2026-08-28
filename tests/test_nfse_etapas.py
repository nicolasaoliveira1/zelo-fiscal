"""Preenchimento das tres etapas do assistente DPS (NFSE-13).

Cada teste asserta o PAR (seletor, valor) que foi de fato usado — nao apenas
que a funcao rodou sem levantar. Os valores conferidos vieram da recon contra o
portal real; se algum divergir, a nota sai errada e o erro so apareceria na
tela de revisao (ou, no P3, nem isso).

O teste mais importante do arquivo e o ultimo: prova que a automacao NAO toca
os campos que o portal ja traz corretos nem os calculados/bloqueados. Mexer
neles reabre secoes condicionais e muda a nota.
"""
from dataclasses import replace
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.automation import nfse
from app.services import nfse_contrato

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
    documento='44.556.677/0001-86',
    valor_final=Decimal('826.09'),
    competencia='06/2026',
)


class DriverEspiao:
    """Driver falso que registra tudo que foi tocado, por id/seletor.

    Simula tres comportamentos reais do portal, sem os quais os testes nao
    provariam nada:

    - `autopreenchidos`: campos que o PORTAL preenche sozinho (emitente apos a
      data, tomador apos o documento);
    - `ocultos` / `desabilitados`: partes do formulario ainda nao liberadas;
    - `catalogo`: os dois Select2 que buscam as opcoes NO SERVIDOR — nascem sem
      nenhuma `<option>`, e so passam a ter a escolhida depois de digitar,
      esperar e clicar.
    """

    def __init__(self):
        self.autopreenchidos = {
            'Prestador_Inscricao': '11.222.333/0001-81',
            'Tomador_Nome': 'PAPELARIA CENTRAL',
        }
        # rotulo -> valor, por campo de busca (como o portal devolve)
        self.catalogo = {
            'LocalPrestacao_CodigoMunicipioPrestacao': [('Imbé/RS', '4310330')],
            'ServicoPrestado_CodigoTributacaoNacional': [
                ('17.19.01 - Contabilidade, inclusive serviços', '17.19.01')],
        }
        self.ocultos = set()
        self.desabilitados = set()
        self.preenchidos = {}
        self.chosen = {}
        self.selects = {}
        self.radios = {}
        self.clicados = []
        self.valores = {}          # valor atual de cada <select>
        self.busca_aberta = None   # id do Select2 com a lista aberta
        self.termos = []           # o que foi digitado em cada busca
        self.current_url = ''

    # --- elementos simples ---
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
        if valor.startswith('span.select2-selection'):
            return [self._caixa_select2(valor)]
        if valor == nfse.SEL_SELECT2_BUSCA:
            return [self._campo_busca()] if self.busca_aberta else []
        if valor == nfse.SEL_SELECT2_OPCAO:
            return self._opcoes_visiveis()
        return [self.find_element(by, valor)]

    # --- simulacao do Select2 com busca ---
    def _caixa_select2(self, seletor):
        # o seletor traz "select2-" duas vezes (span.select2-selection[...
        # aria-labelledby="select2-<ID>-container"]); extrai o ID pelo rotulo
        import re
        achado = re.search(r'select2-([^"]+)-container', seletor)
        alvo = achado.group(1) if achado else seletor
        caixa = MagicMock()
        caixa.is_displayed.return_value = True
        caixa.is_enabled.return_value = True
        caixa.click.side_effect = lambda: setattr(self, 'busca_aberta', alvo)
        return caixa

    def _campo_busca(self):
        campo = MagicMock()
        campo.is_displayed.return_value = True
        campo.is_enabled.return_value = True
        campo.send_keys.side_effect = lambda t: self.termos.append((self.busca_aberta, t))
        return campo

    def _opcoes_visiveis(self):
        """So aparece o que casa com o termo digitado — como a busca do portal."""
        if not self.busca_aberta or not self.termos:
            return []
        campo, termo = self.termos[-1]
        chave = nfse._chave(termo)
        achadas = []
        for rotulo, valor in self.catalogo.get(campo, []):
            if not nfse._chave(rotulo).startswith(chave):
                continue
            opcao = MagicMock()
            opcao.text = rotulo
            opcao.is_displayed.return_value = True
            opcao.is_enabled.return_value = True
            opcao.click.side_effect = (
                lambda c=campo, v=valor: (self.valores.__setitem__(c, v),
                                          setattr(self, 'busca_aberta', None)))
            achadas.append(opcao)
        return achadas

    def execute_script(self, script, *args):
        if 'return el ? el.value : null' in script:
            return self.valores.get(args[0])
        if 'scrollIntoView' in script:
            return None
        if 'chosen:updated' in script:
            self.chosen[args[0]] = args[1]
            self.valores[args[0]] = args[1]
            return args[1]
        if args:
            self.clicados.append(getattr(args[0], '_valor', '?'))
        return None

    def tocados(self):
        return (set(self.preenchidos) | set(self.chosen) | set(self.selects)
                | set(self.radios) | set(self.clicados) | set(self.valores))


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
    assert driver.preenchidos['Tomador_Inscricao'] == '44.556.677/0001-86'
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

def test_etapa_servico_seleciona_municipio_tributacao_e_nbs(driver):
    """Duas vias distintas, porque o portal tem dois tipos de select.

    Municipio e codigo de tributacao nascem SEM opcoes e as buscam no servidor
    conforme se digita — precisam ser dirigidos como o operador faz. O item da
    NBS ja vem com as 919 opcoes carregadas (Chosen) e aceita a via direta."""
    nfse.preencher_etapa_servico(driver, NOTA, CONFIG, 'HONORARIOS DE 06/2026')

    assert driver.valores['LocalPrestacao_CodigoMunicipioPrestacao'] == '4310330'
    assert driver.valores['ServicoPrestado_CodigoTributacaoNacional'] == '17.19.01'
    assert driver.chosen['ServicoPrestado_CodigoNBS'] == '113022100'


def test_municipio_e_escolhido_digitando_e_clicando_na_sugestao(driver):
    """Definir o valor por jQuery nunca funcionaria: o <select> nasce vazio e a
    <option> so passa a existir depois da busca."""
    nfse.preencher_etapa_servico(driver, NOTA, CONFIG, 'x')

    digitados = [t for campo, t in driver.termos
                 if campo == 'LocalPrestacao_CodigoMunicipioPrestacao']
    assert digitados, 'nada foi digitado na busca do municipio'
    assert digitados[0].lower().startswith('imb')
    assert driver.valores['LocalPrestacao_CodigoMunicipioPrestacao'] ==         CONFIG.municipio_servico_codigo


def test_busca_encurta_o_termo_ate_achar(driver):
    """O portal pode nao casar acento. Comeca por 'Imbe' e, se nao achar,
    encurta para 'Imb' — que e o que o operador digita na mao."""
    driver.catalogo['LocalPrestacao_CodigoMunicipioPrestacao'] = [('Imbé/RS', '4310330')]
    # so casa com 3 letras: simula servidor que nao normaliza acento
    original = driver._opcoes_visiveis

    def so_prefixo_curto():
        if driver.termos and len(driver.termos[-1][1]) > 3:
            return []
        return original()
    driver._opcoes_visiveis = so_prefixo_curto

    nfse.preencher_etapa_servico(driver, NOTA, CONFIG, 'x')
    digitados = [t for campo, t in driver.termos
                 if campo == 'LocalPrestacao_CodigoMunicipioPrestacao']
    assert len(digitados) > 1, 'nao tentou encurtar o termo'
    assert driver.valores['LocalPrestacao_CodigoMunicipioPrestacao'] == '4310330'


def test_municipio_ausente_na_busca_da_erro_acionavel(driver):
    """Sem o municipio, a nota sairia sem local de prestacao."""
    driver.catalogo['LocalPrestacao_CodigoMunicipioPrestacao'] = []
    with pytest.raises(nfse.InteracaoPortalError) as exc:
        nfse.preencher_etapa_servico(driver, NOTA, CONFIG, 'x')
    assert CONFIG.municipio_servico_nome in str(exc.value)
    assert 'btnAvancar' not in driver.clicados


def test_nenhum_select_usa_o_Select_do_selenium(driver):
    """Regressao: Select() falha em todos eles, e a falha aparece so no portal
    real — o dublê aceitaria em silencio."""
    nfse.preencher_etapa_servico(driver, NOTA, CONFIG, 'x')
    nfse.preencher_etapa_tributacao(driver, NOTA, CONFIG)
    assert driver.selects == {}, 'nenhum select do assistente aceita Select()' 


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


def test_etapa_usa_seletores_do_snapshot_recebido(driver):
    base = nfse_contrato.contrato_inicial_execucao()
    trocas = {
        nfse.CHAVE_DATA_COMPETENCIA: 'campo-sintetico-data',
        nfse.CHAVE_INSCRICAO_PRESTADOR: 'campo-sintetico-prestador',
        nfse.CHAVE_REGIME_APURACAO: 'campo-sintetico-regime',
        nfse.CHAVE_DOMICILIO_TOMADOR: 'grupo-sintetico',
        nfse.CHAVE_INSCRICAO_TOMADOR: 'campo-sintetico-tomador',
        nfse.CHAVE_NOME_TOMADOR: 'campo-sintetico-nome',
    }
    contrato = replace(
        base,
        campos=tuple(
            replace(
                campo,
                seletor=trocas.get(campo.chave_semantica, campo.seletor),
            )
            for campo in base.campos
        ),
    )
    driver.autopreenchidos.update({
        'campo-sintetico-prestador': 'prestador-sintetico',
        'campo-sintetico-nome': 'tomador-sintetico',
    })

    nfse.preencher_etapa_pessoas(
        driver, NOTA, CONFIG, date(2026, 7, 28), contrato=contrato
    )

    assert 'campo-sintetico-data' in driver.preenchidos
    assert 'campo-sintetico-regime' in driver.chosen
    assert 'grupo-sintetico' in driver.radios
    assert 'campo-sintetico-tomador' in driver.preenchidos
    assert 'DataCompetencia' not in driver.tocados()


def test_etapa_aplica_os_valores_resolvidos_do_snapshot(driver):
    valores = {
        nfse.CHAVE_DATA_COMPETENCIA: date(2026, 8, 25),
        nfse.CHAVE_REGIME_APURACAO: 'REGIME-SINTETICO',
        nfse.CHAVE_DOMICILIO_TOMADOR: '2',
        nfse.CHAVE_INSCRICAO_TOMADOR: 'DOCUMENTO-CONTRATADO-SINTETICO',
    }

    nfse.preencher_etapa_pessoas(
        driver,
        NOTA,
        CONFIG,
        date(2026, 7, 28),
        valores_contrato=valores,
    )

    assert driver.preenchidos['DataCompetencia'] == '25/08/2026'
    assert driver.chosen['SimplesNacional_RegimeApuracaoTributosSN'] == (
        'REGIME-SINTETICO'
    )
    assert driver.radios['Tomador.LocalDomicilio'] == '2'
    assert driver.preenchidos['Tomador_Inscricao'] == (
        'DOCUMENTO-CONTRATADO-SINTETICO'
    )


def _regra_sintetica(**valores):
    padrao = {
        'chave_semantica': 'campo.adicional',
        'etapa': 'servico',
        'seletor_tipo': 'id',
        'seletor': 'campo-adicional',
        'rotulo': 'Campo adicional',
        'tipo': 'text',
        'interacao': 'texto',
        'obrigatorio': False,
        'ordem': 0,
        'condicao_chave': None,
        'condicao_valor': None,
        'origem': 'nota',
        'fonte': 'descricao',
        'valor_fixo': None,
    }
    padrao.update(valores)
    return SimpleNamespace(**padrao)


def test_campos_adicionais_respeitam_ordem_condicao_e_nao_tocam_intocavel(driver):
    regras = [
        _regra_sintetica(
            chave_semantica='campo.radio', seletor_tipo='name', seletor='GrupoSintetico',
            interacao='radio', ordem=1, origem='fixo', fonte=None, valor_fixo='1',
        ),
        _regra_sintetica(
            chave_semantica='campo.texto', seletor='campo-texto', ordem=2,
            origem='nota', fonte='descricao',
        ),
        _regra_sintetica(
            chave_semantica='campo.padrao', ordem=0, origem='padrao_portal',
            interacao='texto', fonte=None,
        ),
        _regra_sintetica(
            chave_semantica='campo.condicional', seletor='campo-condicional', ordem=3,
            condicao_chave='campo.radio',
            condicao_valor='2', origem='nota', fonte='descricao',
        ),
        _regra_sintetica(
            chave_semantica='campo.intocavel', ordem=4, origem='intocavel',
            interacao='texto', fonte=None,
        ),
    ]
    valores = {
        'campo.radio': '1',
        'campo.texto': 'texto-sintético',
        'campo.condicional': 'condicional-sintético',
    }

    nfse.aplicar_campos_adicionais(driver, regras, valores)

    assert driver.radios['GrupoSintetico'] == '1'
    assert driver.preenchidos['campo-texto'] == 'texto-sintético'
    assert 'campo-condicional' not in driver.preenchidos
    assert 'campo.intocavel' not in driver.tocados()


def test_campo_adicional_desconhecido_ou_dependencia_ausente_bloqueia(driver):
    desconhecido = _regra_sintetica(interacao='clique-generico')
    dependente = _regra_sintetica(condicao_chave='campo.inexistente')

    with pytest.raises(nfse.ContratoNfseIncompativelError):
        nfse.aplicar_campos_adicionais(driver, [desconhecido], {'campo.adicional': 'x'})
    with pytest.raises(nfse.ContratoNfseIncompativelError):
        nfse.aplicar_campos_adicionais(driver, [dependente], {'campo.adicional': 'x'})
    assert driver.tocados() == set()


def test_radio_com_seletor_nao_aprovado_e_recusado(driver):
    """O grupo de radio seguia a mesma regra dos demais adaptadores só por
    coincidência: `seletor_tipo` fora do conjunto caía em CSS por omissão, em
    vez de recusar. Clique errado em documento fiscal não tem rollback, e a
    coluna é `String(20)` sem `CHECK` — nada além deste guarda impede um valor
    novo chegar aqui."""

    regra = _regra_sintetica(
        chave_semantica='campo.radio', seletor_tipo='xpath',
        seletor='//input[@name="GrupoSintetico"]', interacao='radio',
        origem='fixo', fonte=None, valor_fixo='1',
    )

    with pytest.raises(nfse.ContratoNfseIncompativelError):
        nfse.aplicar_campos_adicionais(driver, [regra], {'campo.radio': '1'})
    assert driver.tocados() == set()


def test_texto_que_nao_gruda_no_campo_e_recusado_na_hora(driver):
    """Confirmar o valor logo apos digitar e o que sustenta `prova_aplicacao`.

    A autorrevisao deixou de exigir leitor na revisao para campos que o resumo
    nao exibe, aceitando o preenchimento como prova. Essa troca so e honesta se
    o preenchimento ELE MESMO confirmar o que gravou: um campo mascarado que
    descarta o texto passaria a nota adiante com o campo em branco."""

    class Surdo(DriverEspiao):
        def find_element(self, by, valor):
            elemento = super().find_element(by, valor)
            if valor == 'campo-texto':
                elemento.send_keys.side_effect = lambda texto: None
            return elemento

    surdo = Surdo()
    regra = _regra_sintetica(chave_semantica='campo.texto', seletor='campo-texto')

    with pytest.raises(nfse.ContratoNfseIncompativelError) as exc:
        nfse.aplicar_campos_adicionais(
            surdo, [regra], {'campo.texto': 'texto-sintético'}
        )
    assert 'confirmou' in str(exc.value)


def test_radio_que_nao_fica_marcado_e_recusado_na_hora(driver):
    """Mesmo contrato do texto, do lado do radio: clique sem marcacao nao e
    prova. O portal desabilita opcao por regra de negocio e o clique via JS nao
    levanta — sem `is_selected` o grupo seguiria com a opcao do portal."""

    class Teimoso(DriverEspiao):
        def find_element(self, by, valor):
            elemento = super().find_element(by, valor)
            if str(valor).startswith('input[name="GrupoSintetico"'):
                elemento.is_selected.side_effect = lambda: False
            return elemento

    regra = _regra_sintetica(
        chave_semantica='campo.radio', seletor_tipo='name',
        seletor='GrupoSintetico', interacao='radio', origem='fixo',
        fonte=None, valor_fixo='1',
    )

    with pytest.raises(nfse.InteracaoPortalError) as exc:
        nfse.aplicar_campos_adicionais(Teimoso(), [regra], {'campo.radio': '1'})
    assert 'confirmou' in str(exc.value)


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


def test_pergunta_nova_no_topo_da_etapa_destrava_a_competencia(driver, monkeypatch):
    """Achado de UAT: a reforma pôs "Preencher as informações IBS/CBS?" no topo
    de Pessoas, e ela mantém os demais campos inertes até ser respondida. A
    competência é o primeiro campo que a automação toca desde sempre — sem
    responder a pergunta, o preenchimento morria ali."""

    from app.services import nfse_contrato

    execucao = nfse_contrato.contrato_inicial_execucao()
    porteira = SimpleNamespace(
        chave_semantica='PreencherInfoIBSCBS', etapa='pessoas',
        seletor_tipo='name', seletor='PreencherInfoIBSCBS',
        rotulo='Preencher as informações IBS/CBS?', tipo='radio',
        interacao='radio', obrigatorio=True, ordem=99,
        condicao_chave=None, condicao_valor=None, origem='fixo', fonte=None,
        valor_fixo='0', revisao_secao=None, revisao_rotulo=None,
        conferivel_automatico=False, opcoes=(),
    )
    contrato = SimpleNamespace(
        contrato_id=execucao.contrato_id,
        campos=tuple(execucao.campos) + (porteira,),
        campo=execucao.campo,
    )

    driver.desabilitados.add('DataCompetencia')
    original = nfse._marcar_radio
    aplicar_original = nfse._aplicar_campo_contrato
    tentativas_competencia = []

    def marcar(drv, name, valor):
        elemento = original(drv, name, valor)
        if name == 'PreencherInfoIBSCBS':
            drv.desabilitados.discard('DataCompetencia')
        return elemento

    monkeypatch.setattr(nfse, '_marcar_radio', marcar)

    def aplicar(drv, campo, valor):
        if campo.chave_semantica == 'DataCompetencia':
            tentativas_competencia.append(valor)
        return aplicar_original(drv, campo, valor)

    monkeypatch.setattr(nfse, '_aplicar_campo_contrato', aplicar)

    nfse.preencher_etapa_pessoas(
        driver, NOTA, CONFIG, date(2026, 7, 28), contrato=contrato,
        # No fluxo real quem resolve é `_resolver_valores_contrato`.
        valores_contrato={'PreencherInfoIBSCBS': '0'},
    )

    assert driver.radios['PreencherInfoIBSCBS'] == '0'
    assert driver.preenchidos['DataCompetencia'] == '28/07/2026'
    assert tentativas_competencia == ['28/07/2026'], (
        'a porteira deve ser respondida antes, sem esperar a competência falhar'
    )


def test_sem_porteira_no_contrato_o_erro_do_portal_sobe_inalterado(driver):
    """Sem resposta decidida no contrato, o desfecho continua sendo o do
    portal — não um chute nosso."""

    driver.desabilitados.add('DataCompetencia')

    with pytest.raises(nfse.InteracaoPortalError) as erro:
        nfse.preencher_etapa_pessoas(driver, NOTA, CONFIG, date(2026, 7, 28))

    assert 'DataCompetencia' in str(erro.value)


# --- campos novos do contrato: alvo e formato -------------------------------

def _elemento_falso(visivel):
    elemento = MagicMock()
    elemento.is_displayed.return_value = visivel
    elemento.size = {'width': 10 if visivel else 0, 'height': 10 if visivel else 0}
    return elemento


def test_alvo_repetido_resolve_pelo_visivel(monkeypatch):
    """O portal repete `name`/`id` em partes ocultas — é por isso que
    `_localizar` prefere o visível. Exigir um único elemento na página inteira
    recusava campo que tem só uma cópia utilizável: foi o
    `"Valores.ValorServico" não possui alvo inequívoco` do log."""

    oculto, visivel = _elemento_falso(False), _elemento_falso(True)
    driver = MagicMock()
    driver.find_elements.return_value = [oculto, visivel]
    monkeypatch.setattr(nfse, '_visivel', lambda e: e.is_displayed())
    campo = SimpleNamespace(
        chave_semantica='Valores.ValorServico', seletor_tipo='css',
        seletor='[name="Valores.ValorServico"], [id="Valores.ValorServico"]',
    )

    assert nfse._localizar_campo_contrato(driver, campo) is visivel


def test_duas_copias_visiveis_continuam_sendo_ambiguas(monkeypatch):
    """Ambiguidade de verdade continua recusando: escolher no chute
    preencheria a cópia errada de um documento fiscal."""

    driver = MagicMock()
    driver.find_elements.return_value = [_elemento_falso(True), _elemento_falso(True)]
    monkeypatch.setattr(nfse, '_visivel', lambda e: e.is_displayed())
    campo = SimpleNamespace(
        chave_semantica='Campo.Ambiguo', seletor_tipo='css', seletor='[name="x"]',
    )

    with pytest.raises(nfse.ContratoNfseIncompativelError):
        nfse._localizar_campo_contrato(driver, campo)


def test_valor_e_data_vao_para_o_portal_no_formato_brasileiro():
    """`str()` cru entrega `649.00` e `2026-08-27`; o portal usa vírgula
    decimal e dd/mm/aaaa. Os campos históricos já formatavam — os campos NOVOS
    do contrato caíam no `str()`, então "Valor final da nota" digitava ponto."""

    from datetime import date as _date
    from decimal import Decimal as _Decimal

    assert nfse._texto_para_o_portal(_Decimal('649.00')) == '649,00'
    assert nfse._texto_para_o_portal(_date(2026, 8, 27)) == '27/08/2026'
    assert nfse._texto_para_o_portal('texto-sintetico') == 'texto-sintetico'


# --- leitura da revisão -----------------------------------------------------

def test_revisao_lida_mesmo_quando_a_secao_muda_de_forma(monkeypatch):
    """A seção exige `h4` com classe e título exatos. O portal trocar a tag ou
    acrescentar uma palavra no título não é erro fiscal — mas fazia a
    autorrevisão parar de conferir, e "não consegui ler" virava divergência
    num documento correto."""

    achado = MagicMock()
    achado.text = '  12,34  '
    driver = MagicMock()
    # A busca com seção não acha; a busca ampla acha um só.
    monkeypatch.setattr(nfse, '_localizar', lambda *a, **k: None)
    driver.find_elements.return_value = [achado]

    lido = nfse._dd_da_secao(driver, 'Valores do Serviço Prestado',
                             nfse.ROTULO_VALOR)

    assert lido == '12,34'


def test_rotulo_repetido_na_pagina_continua_sendo_recusa(monkeypatch):
    """Prestador e tomador têm ambos um CNPJ: dois casamentos não podem virar
    leitura, senão a conferência aprovaria o documento da parte errada."""

    driver = MagicMock()
    monkeypatch.setattr(nfse, '_localizar', lambda *a, **k: None)
    driver.find_elements.return_value = [MagicMock(), MagicMock()]

    assert nfse._dd_da_secao(driver, 'Tomador do Serviço',
                             nfse.ROTULO_DOCUMENTO) is None


def test_copia_oculta_nao_torna_o_valor_visivel_ambiguo(monkeypatch):
    """O portal mantém cópias ocultas; uma visível continua sendo inequívoca."""
    oculto = _elemento_falso(False)
    visivel = _elemento_falso(True)
    visivel.text = '12,34'
    driver = MagicMock()
    monkeypatch.setattr(nfse, '_localizar', lambda *a, **k: None)
    driver.find_elements.return_value = [oculto, visivel]

    assert nfse._dd_da_secao(
        driver, 'Valores do Serviço Prestado', nfse.ROTULO_VALOR
    ) == '12,34'


def test_secao_exata_continua_tendo_prioridade(monkeypatch):
    """O caminho preciso não pode ser trocado pelo tolerante: com as duas
    leituras disponíveis, vale a que conhece a seção."""

    da_secao = MagicMock()
    da_secao.text = 'valor-da-secao'
    outro = MagicMock()
    outro.text = 'valor-da-pagina'
    driver = MagicMock()
    driver.find_elements.side_effect = lambda _by, xpath: (
        [da_secao] if 'emissao-titulo' in xpath else [outro]
    )

    assert nfse._dd_da_secao(driver, 'Tomador do Serviço',
                             nfse.ROTULO_DOCUMENTO) == 'valor-da-secao'


def test_secao_e_rotulo_da_revisao_batem_com_o_portal_de_hoje():
    """Ancorado no esqueleto capturado da revisão real, não em suposição.

    O portal renomeou duas coisas sob os nossos pés, e cada uma derrubou uma
    conferência: a seção do tomador virou "Tomador/Adquirente do Serviço", e o
    rótulo do valor virou "Valor da operação/serviço prestado:".
    """
    import re

    from app.automation import nfse as automacao

    titulos_reais = [
        'Informações Gerais', 'Informações do Emitente',
        'Tomador/Adquirente do Serviço', 'Serviço Prestado',
        'Valores do Serviço Prestado', 'Tributação Municipal',
    ]
    rotulos_reais = [
        'CNPJ:', 'Nome/Razão Social:', 'Descrição do serviço:',
        'Valor da operação/serviço prestado:',
        'Tributação do ISSQN sobre o serviço prestado:',
    ]

    # O XPath casa o título EXATO, então a constante tem de ser o texto inteiro.
    for secao in (automacao.SECAO_TOMADOR, automacao.SECAO_VALORES,
                  automacao.SECAO_SERVICO):
        assert secao in titulos_reais, secao

    # E o rótulo do valor precisa casar exatamente um dos rótulos reais.
    alternativas = re.findall(r"contains\(\.,'([^']+)'\)",
                              automacao.ROTULO_VALOR)
    casam = [
        rotulo for rotulo in rotulos_reais
        if any(alvo in rotulo for alvo in alternativas)
    ]
    assert casam == ['Valor da operação/serviço prestado:']
