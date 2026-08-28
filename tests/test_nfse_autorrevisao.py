"""Auto-revisao antes de emitir sozinho (NFSE-24, P3).

Este e o unico ponto do sistema em que a automacao produz um documento fiscal
sem olho humano na frente. Os testes aqui existem para provar o lado NEGATIVO:
que ela se recusa a emitir quando a tela nao bate com a nota — inclusive quando
nao consegue ler a tela.

A tela de revisao e dublada como um mapa de (secao, rotulo) -> texto, no formato
que a pagina real usa (`R$ 826,09`, CNPJ formatado). O XPath de verdade e
exercitado em test_nfse_automation.py.
"""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.automation import nfse

DOCUMENTO = '44.556.677/0001-86'
VALOR = Decimal('826.09')
DESCRICAO = 'HONORÁRIOS PROFISSIONAIS REFERENTES AO MÊS DE 06/2026'


def _driver_revisao(documento=DOCUMENTO, valor='R$ 826,09', descricoes=None):
    """Driver falso da tela de revisao.

    `descricoes` sao os <dd> da secao "Serviço Prestado" — plural porque a
    secao real tem varios, e a conferencia procura o texto esperado entre eles.
    """
    if descricoes is None:
        descricoes = ['17.19.01 - Contabilidade', 'IMBÉ/RS', DESCRICAO]

    def _find_element(_by, xpath):
        if nfse.SECAO_TOMADOR in xpath:
            if documento is None:
                raise nfse.WebDriverException('sem tomador')
            return _elemento(documento)
        if nfse.SECAO_VALORES in xpath:
            if valor is None:
                raise nfse.WebDriverException('sem valor')
            return _elemento(valor)
        raise nfse.WebDriverException('nao achou')

    def _find_elements(_by, xpath):
        if nfse.SECAO_SERVICO in xpath and xpath.endswith('//dd'):
            return [_elemento(t) for t in descricoes]
        try:
            return [_find_element(_by, xpath)]
        except nfse.WebDriverException:
            return []

    driver = MagicMock()
    driver.find_element.side_effect = _find_element
    driver.find_elements.side_effect = _find_elements
    return driver


def _elemento(texto):
    el = MagicMock()
    el.text = texto
    el.is_displayed.return_value = True
    return el


def _conferir(driver, documento=DOCUMENTO, valor=VALOR, descricao=DESCRICAO):
    return nfse.conferir_revisao(driver, documento, valor, descricao)


# --- o caminho que autoriza emitir ------------------------------------------

def test_tudo_conferindo_nao_acusa_divergencia():
    assert _conferir(_driver_revisao()) == []


def test_documento_e_comparado_por_digitos():
    """A tela mostra formatado e a nota guarda formatado, mas um dos dois pode
    mudar de formato sem que nada esteja errado."""
    assert _conferir(_driver_revisao(documento='44556677000186')) == []


def test_valor_com_milhar_e_lido_corretamente():
    divergencias = _conferir(_driver_revisao(valor='R$ 3.238,87'),
                             valor=Decimal('3238.87'))
    assert divergencias == []


def test_descricao_com_acento_ou_caixa_diferente_ainda_confere():
    driver = _driver_revisao(
        descricoes=['honorarios profissionais referentes ao mes de 06/2026'])
    assert _conferir(driver) == []


def test_descricao_continua_legivel_quando_a_secao_muda_de_forma():
    """O valor completo e único permite tolerar mudança estrutural da seção."""
    driver = _driver_revisao(descricoes=[])
    original = driver.find_elements.side_effect

    def encontrar(by, xpath):
        if xpath == '//dd':
            return [_elemento(DESCRICAO)]
        return original(by, xpath)

    driver.find_elements.side_effect = encontrar

    assert _conferir(driver) == []


# --- o que precisa BARRAR a emissao -----------------------------------------

def test_valor_adulterado_impede_a_emissao():
    """O teste independente que a spec exige (NFSE-24): adulterar o valor
    esperado e confirmar que a automacao se recusa."""
    divergencias = _conferir(_driver_revisao(), valor=Decimal('999.00'))
    assert divergencias
    assert '826,09' in divergencias[0]
    assert '999,00' in divergencias[0]


def test_tomador_diferente_impede_a_emissao():
    """O erro mais caro possivel: nota no CNPJ de outro cliente."""
    divergencias = _conferir(_driver_revisao(documento='11.111.111/0001-11'))
    assert divergencias
    assert '11.111.111/0001-11' in divergencias[0]


def test_competencia_errada_na_descricao_impede_a_emissao():
    """Descricao do mes passado sai como nota do mes errado."""
    driver = _driver_revisao(
        descricoes=['HONORÁRIOS PROFISSIONAIS REFERENTES AO MÊS DE 05/2026'])
    divergencias = _conferir(driver)
    assert divergencias
    assert 'competencia' in divergencias[0].lower()


def test_tomador_ilegivel_conta_como_divergencia():
    """Nao conseguir conferir NAO e o mesmo que conferir e estar certo: se o
    portal mudar o layout, a alternativa seria emitir as cegas."""
    divergencias = _conferir(_driver_revisao(documento=None))
    assert divergencias
    assert 'ler' in divergencias[0].lower()


def test_valor_ilegivel_conta_como_divergencia():
    divergencias = _conferir(_driver_revisao(valor=None))
    assert any('ler' in d.lower() for d in divergencias)


def test_valor_nao_numerico_conta_como_divergencia():
    divergencias = _conferir(_driver_revisao(valor='R$ ---'))
    assert any('ler' in d.lower() for d in divergencias)


def test_secao_de_servico_vazia_impede_a_emissao():
    assert _conferir(_driver_revisao(descricoes=[]))


def test_varias_divergencias_sao_todas_relatadas():
    """O operador precisa ver tudo que nao bate, nao so a primeira coisa."""
    driver = _driver_revisao(documento='11.111.111/0001-11', valor='R$ 1,00',
                             descricoes=['outra coisa'])
    assert len(_conferir(driver)) == 3


# --- o CNPJ do emitente nao pode ser confundido com o do tomador ------------

def test_documento_e_lido_da_secao_do_tomador():
    """A revisao mostra DOIS "CNPJ:": o do escritorio (emitente) vem primeiro.
    Ler sem ancorar na secao compararia o CNPJ do escritorio com o do cliente."""
    driver = _driver_revisao()
    _conferir(driver)

    # `_localizar` busca pelo plural, para poder escolher entre varios
    xpaths = [c[0][1] for c in driver.find_elements.call_args_list
              if nfse.SECAO_TOMADOR in c[0][1]]
    assert xpaths, 'o documento precisa ser buscado dentro da secao do tomador'
    assert 'emissao-titulo' in xpaths[0]


def test_secoes_sao_ancoradas_por_titulo_exato():
    """"Serviço Prestado" e substring de "Valores do Serviço Prestado": um
    `contains` casaria as duas e leria o campo da secao errada."""
    assert "normalize-space()='{secao}'" in nfse._XP_SECAO


def _regra_revisao(**valores):
    padrao = {
        'chave_semantica': 'campo.sintetico',
        'tipo': 'text',
        'obrigatorio': True,
        'conferivel_automatico': True,
        'origem': 'fixo',
        'valor_fixo': 'Valor sintético',
        'revisao_secao': 'Seção sintética',
        'revisao_rotulo': 'Rótulo sintético',
        'prova_avanco': True,
    }
    padrao.update(valores)
    return SimpleNamespace(**padrao)


def _driver_com_campo_adicional(valores):
    driver = _driver_revisao()
    original = driver.find_elements.side_effect

    def _find_elements(by, xpath):
        if 'Seção sintética' in xpath and 'Rótulo sintético' in xpath:
            return [_elemento(valor) for valor in valores]
        return original(by, xpath)

    driver.find_elements.side_effect = _find_elements
    return driver


def _driver_com_rotulo_descoberto(rotulo, valor):
    driver = _driver_revisao()
    original = driver.find_elements.side_effect
    termo = _elemento(rotulo)
    termo.find_element.return_value = _elemento(valor)

    def _find_elements(by, xpath):
        if xpath == '//dt[following-sibling::dd[1]]':
            return [termo]
        return original(by, xpath)

    driver.find_elements.side_effect = _find_elements
    return driver


def test_revisao_declarativa_confere_valor_e_mantem_elegibilidade():
    regra = _regra_revisao()
    resultado = nfse.conferir_revisao(
        _driver_com_campo_adicional(['Valor sintético']),
        DOCUMENTO,
        VALOR,
        DESCRICAO,
        regras_adicionais=[regra],
    )

    assert resultado == []
    assert resultado.elegivel_automatico is True


def test_revisao_declarativa_acusa_valor_divergente_e_ilegivel():
    regra = _regra_revisao()
    divergente = nfse.conferir_revisao(
        _driver_com_campo_adicional(['Outro valor']),
        DOCUMENTO,
        VALOR,
        DESCRICAO,
        regras_adicionais=[regra],
    )
    ilegivel = nfse.conferir_revisao(
        _driver_com_campo_adicional([]),
        DOCUMENTO,
        VALOR,
        DESCRICAO,
        regras_adicionais=[regra],
    )

    assert any('campo contratado' in item.lower() for item in divergente)
    assert not divergente.elegivel_automatico
    assert any('não consegui conferir' in item.lower() for item in ilegivel)
    assert not ilegivel.elegivel_automatico


def test_revisao_declarativa_acusa_secao_ambigua_e_rotulo_duplicado():
    regra = _regra_revisao()
    ambigua = nfse.conferir_revisao(
        _driver_com_campo_adicional(['A', 'B']),
        DOCUMENTO,
        VALOR,
        DESCRICAO,
        regras_adicionais=[regra],
    )

    assert len(ambigua) == 1
    assert 'ambíguo' in ambigua[0]
    assert not ambigua.elegivel_automatico


def test_revisao_declarativa_reusa_fallback_de_secao():
    regra = _regra_revisao()
    driver = _driver_revisao()
    original = driver.find_elements.side_effect

    def encontrar(by, xpath):
        if 'Rótulo sintético' in xpath and 'Seção sintética' not in xpath:
            return [_elemento('Valor sintético')]
        if 'Rótulo sintético' in xpath:
            return []
        return original(by, xpath)

    driver.find_elements.side_effect = encontrar
    resultado = nfse.conferir_revisao(
        driver, DOCUMENTO, VALOR, DESCRICAO, regras_adicionais=[regra]
    )

    assert resultado == []
    assert resultado.elegivel_automatico is True


def test_revisao_descobre_campo_por_rotulo_e_rotulo_da_opcao():
    regra = _regra_revisao(
        chave_semantica='campo.pergunta',
        revisao_secao=None,
        revisao_rotulo=None,
        revisao_rotulo_candidato='Preencher as informações IBS/CBS?',
        valores_esperados=('0', 'Não'),
        prova_aplicacao=True,
    )

    resultado = nfse.conferir_revisao(
        _driver_com_rotulo_descoberto(
            'Preencher as informações IBS/CBS:', 'Não'
        ),
        DOCUMENTO,
        VALOR,
        DESCRICAO,
        regras_adicionais=[regra],
    )

    assert resultado == []
    assert resultado.avisos_assistidos == ()
    assert resultado.elegivel_automatico is True


def test_revisao_descoberta_continua_bloqueando_valor_divergente():
    regra = _regra_revisao(
        chave_semantica='campo.pergunta',
        revisao_secao=None,
        revisao_rotulo=None,
        revisao_rotulo_candidato='Pergunta sintética?',
        valores_esperados=('0', 'Não'),
        prova_aplicacao=True,
    )

    resultado = nfse.conferir_revisao(
        _driver_com_rotulo_descoberto('Pergunta sintética:', 'Sim'),
        DOCUMENTO,
        VALOR,
        DESCRICAO,
        regras_adicionais=[regra],
    )

    assert any('campo.pergunta' in item for item in resultado)
    assert resultado.elegivel_automatico is False


def test_campo_ausente_da_revisao_usa_prova_do_preenchimento():
    regra = _regra_revisao(
        chave_semantica='campo.ausente.do.resumo',
        revisao_secao=None,
        revisao_rotulo=None,
        revisao_rotulo_candidato='Campo ausente do resumo',
        prova_aplicacao=True,
    )

    resultado = nfse.conferir_revisao(
        _driver_revisao(),
        DOCUMENTO,
        VALOR,
        DESCRICAO,
        regras_adicionais=[regra],
    )

    assert resultado == []
    assert resultado.avisos_assistidos == ()
    assert resultado.elegivel_automatico is True


def test_campo_fiscal_sem_leitor_e_padrao_obrigatorio_sem_prova_bloqueiam_auto():
    """Campo sem leitor fecha o gate do AUTOMÁTICO, e é isso que importa.

    Ele não vira divergência da nota: sem `revisao_secao`/`revisao_rotulo` não
    há o que ler na revisão, então "não consegui conferir" é uma lacuna do
    CONTRATO, igual em toda nota, e não um achado sobre esta. Como divergência
    ela reprovava documento correto; some da lista da nota e aparece onde se
    conserta — no `erro_validacao` da candidata.
    """
    sem_leitor = _regra_revisao(
        chave_semantica='campo.sem_leitor',
        revisao_secao=None,
        revisao_rotulo=None,
    )
    padrao_sem_prova = _regra_revisao(
        chave_semantica='campo.padrao',
        origem='padrao_portal',
        prova_avanco=False,
    )

    resultado = nfse.conferir_revisao(
        _driver_com_campo_adicional(['Valor sintético']),
        DOCUMENTO,
        VALOR,
        DESCRICAO,
        regras_adicionais=[sem_leitor, padrao_sem_prova],
    )

    # O gate do automático continua fechado — a garantia que este teste guarda.
    assert not resultado.elegivel_automatico
    # E a lacuna continua dita, como aviso do modo assistido.
    assert any('sem_leitor' in aviso for aviso in resultado.avisos_assistidos)
    assert not any('sem_leitor' in item for item in resultado)


def test_campo_nao_conferivel_aprova_somente_fluxo_assistido():
    regra = _regra_revisao(
        chave_semantica='campo.somente_assistido',
        revisao_secao=None,
        revisao_rotulo=None,
        conferivel_automatico=False,
    )

    resultado = nfse.conferir_revisao(
        _driver_revisao(),
        DOCUMENTO,
        VALOR,
        DESCRICAO,
        regras_adicionais=[regra],
    )

    assert resultado == []
    assert resultado.avisos_assistidos
    assert resultado.elegivel_automatico is False


def test_controle_opcional_sem_efeito_fiscal_nao_bloqueia_por_ser_select():
    regra = _regra_revisao(
        chave_semantica='campo.opcional.intocavel',
        tipo='select',
        obrigatorio=False,
        origem='intocavel',
        revisao_secao=None,
        revisao_rotulo=None,
        conferivel_automatico=False,
    )

    resultado = nfse.conferir_revisao(
        _driver_revisao(),
        DOCUMENTO,
        VALOR,
        DESCRICAO,
        regras_adicionais=[regra],
    )

    assert resultado == []
    assert resultado.avisos_assistidos == ()
    assert resultado.elegivel_automatico is True


def test_controle_opcional_preenchido_continua_exigindo_leitor():
    regra = _regra_revisao(
        chave_semantica='campo.opcional.preenchido',
        tipo='select',
        obrigatorio=False,
        origem='configuracao',
        revisao_secao=None,
        revisao_rotulo=None,
        conferivel_automatico=False,
    )

    resultado = nfse.conferir_revisao(
        _driver_revisao(),
        DOCUMENTO,
        VALOR,
        DESCRICAO,
        regras_adicionais=[regra],
    )

    assert resultado == []
    assert any('opcional.preenchido' in item for item in resultado.avisos_assistidos)
    assert resultado.elegivel_automatico is False
