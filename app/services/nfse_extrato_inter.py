"""Leitura do extrato do Banco Inter em PDF -> lancamentos + descricao (NFSE-25).

Zero Selenium, como o `nfse_import`: e camada conferivel, roda antes de abrir
qualquer navegador. Duas responsabilidades, ambas puras:

1. `ler_pdf()` — extrai os lancamentos do PDF;
2. `interpretar_descricao()` — le a descricao do Pix e separa tomador,
   competencia e servico.

**Por que ler por coordenada e nao pelo texto corrido.** O `extract_text()`
devolve `... 1.806,00 3.862,63` sem dizer qual numero e Entrada e qual e Saldo,
e o Inter so imprime o Saldo na ULTIMA linha de cada dia — ou seja, a mesma
linha aparece com um ou dois numeros no fim, sem nada que os distinga. Pior: a
Data tambem so aparece na PRIMEIRA linha do dia. Pelas coordenadas nao ha
ambiguidade: as tres colunas de valor sao alinhadas a direita em x1 fixo
(633.3 Entrada / 683.7 Saida / 728.9 Saldo, identicos ao x1 do cabecalho), entao
a coluna de cada valor e um fato lido do PDF, nao um chute.

**A competencia aqui e LITERAL.** No CSV de cobrancas ela e derivada (mes
anterior ao vencimento do titulo, porque la a data e de vencimento); no Inter
ela vem escrita na descricao do Pix ("- 06/2026") e e essa que vale. Sao duas
regras diferentes de proposito — ver `nfse_import.competencia_da_descricao`.
"""
from __future__ import annotations

import io
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from app.file_manager import remover_acentos

# Rotulos do cabecalho da tabela do extrato. Servem para (a) achar a linha de
# cabecalho e (b) tirar dela as coordenadas das colunas — nada e hardcoded em
# pixel, o proprio PDF diz onde cada coluna esta.
COLUNAS_TEXTO = ('Data', 'Nome', 'Descrição', 'Ref.', 'Identif.')
COLUNAS_VALOR = ('Entrada', 'Saída', 'Saldo')
CABECALHO = COLUNAS_TEXTO + COLUNAS_VALOR

# Tolerancia entre a borda direita do valor e a do rotulo da coluna. Na amostra
# real a diferenca e ZERO (633.3 == 633.3): as colunas sao alinhadas a direita
# no mesmo x. 4pt cobre variacao de renderizacao sem chegar perto da coluna
# vizinha, que fica a ~50pt de distancia.
TOLERANCIA_COLUNA = 4.0

# Valor monetario brasileiro, com os parenteses que o Inter usa para saida.
RE_VALOR = re.compile(r'^\(?\d{1,3}(\.\d{3})*,\d{2}\)?$')

MSG_VAZIO = 'Arquivo vazio: envie o extrato em PDF exportado do Banco Inter.'
MSG_ILEGIVEL = (
    'Nao foi possivel ler o PDF: o arquivo pode estar corrompido ou protegido '
    'por senha.')
MSG_SEM_TABELA = (
    'Formato inesperado: o PDF nao tem a tabela do extrato do Banco Inter '
    '(colunas Data / Nome / Descrição / Entrada / Saída / Saldo). Confira se e '
    'o extrato da conta, e nao um comprovante ou relatorio.')


class ExtratoInterInvalidoError(ValueError):
    """PDF vazio, ilegivel ou sem a tabela do extrato.

    Erro proprio, e nao o `ArquivoInvalidoError` do `nfse_import`, para a
    dependencia ficar de mao unica: o import conhece o leitor, o leitor nao
    conhece o import."""


@dataclass
class LancamentoInter:
    """Uma linha da tabela do extrato, como o banco a imprimiu.

    Entrada e saida sao mutuamente exclusivas na pratica, mas as duas ficam
    aqui: o import precisa das SAIDAS para propor o abatimento de estorno, nao
    so das entradas que viram nota."""
    numero: int
    data: date | None = None
    nome: str = ''
    descricao: str = ''
    entrada: Decimal | None = None
    saida: Decimal | None = None
    saldo: Decimal | None = None


# --- leitura do PDF --------------------------------------------------------

def _palavras_por_linha(palavras):
    """Agrupa as palavras da pagina por linha visual (mesma coordenada `top`).

    Arredondar o `top` basta porque o Inter renderiza cada linha da tabela num
    unico baseline — nao ha sobrescrito nem celula de duas linhas."""
    grupos = defaultdict(list)
    for palavra in palavras:
        grupos[round(palavra['top'])].append(palavra)
    return [sorted(grupos[topo], key=lambda p: p['x0']) for topo in sorted(grupos)]


def _colunas(linhas):
    """Coordenadas das colunas, tiradas da linha de cabecalho da tabela.

    Devolve `{rotulo: (x0, x1)}` ou None se o cabecalho nao aparecer. Guarda as
    duas bordas porque as colunas de texto sao alinhadas a esquerda (vale o x0)
    e as de valor a direita (vale o x1)."""
    for palavras in linhas:
        textos = [p['text'] for p in palavras]
        if not all(rotulo in textos for rotulo in CABECALHO):
            continue
        return {rotulo: (palavras[textos.index(rotulo)]['x0'],
                         palavras[textos.index(rotulo)]['x1'])
                for rotulo in CABECALHO}
    return None


def _coluna_da_palavra(palavra, colunas):
    """Em que coluna a palavra esta, pela sua posicao horizontal.

    A checagem de valor vem primeiro, mas exige as DUAS condicoes: o texto tem
    de parecer dinheiro E terminar na borda da coluna. So a posicao nao basta —
    uma descricao longa o bastante para alcancar a faixa da Entrada viraria um
    valor. E so o formato tambem nao basta: um valor citado dentro da descricao
    ("parcela de 1.500,00") nao esta na coluna Entrada e nao pode virar o valor
    da nota."""
    if RE_VALOR.match(palavra['text']):
        for rotulo in COLUNAS_VALOR:
            if abs(palavra['x1'] - colunas[rotulo][1]) <= TOLERANCIA_COLUNA:
                return rotulo

    escolhida = COLUNAS_TEXTO[0]
    for rotulo in COLUNAS_TEXTO:
        if palavra['x0'] >= colunas[rotulo][0] - TOLERANCIA_COLUNA:
            escolhida = rotulo
    return escolhida


def _para_data(bruto):
    """'06/07/26' -> date(2026, 7, 6). O Inter imprime o ano com dois digitos."""
    partes = (bruto or '').strip().split('/')
    if len(partes) != 3:
        return None
    try:
        dia, mes, ano = (int(parte) for parte in partes)
    except (TypeError, ValueError):
        return None
    if ano < 100:
        ano += 2000
    try:
        return date(ano, mes, dia)
    except ValueError:
        return None


def _para_decimal(bruto):
    """'(1.784,00)' -> Decimal('1784.00').

    O parentese ja foi lido como coluna Saida, entao aqui ele so atrapalha e
    sai; o sinal NAO e invertido — quem sabe que saida subtrai e quem monta a
    proposta de agrupamento."""
    texto = (bruto or '').strip().strip('()')
    if not texto:
        return None
    texto = texto.replace('.', '').replace(',', '.')
    try:
        return Decimal(texto)
    except (InvalidOperation, ValueError):
        return None


def _montar_lancamento(numero, campos, data_corrente):
    return LancamentoInter(
        numero=numero,
        # Data em branco = mesma data da linha de cima: o Inter so imprime a
        # data na primeira linha de cada dia. Sem herdar, seis das dez linhas de
        # honorarios da amostra real ficariam sem data.
        data=_para_data(campos.get('Data')) or data_corrente,
        nome=(campos.get('Nome') or '').strip(),
        descricao=(campos.get('Descrição') or '').strip(),
        entrada=_para_decimal(campos.get('Entrada')),
        saida=_para_decimal(campos.get('Saída')),
        saldo=_para_decimal(campos.get('Saldo')),
    )


def montar_lancamentos(paginas):
    """Converte paginas de palavras em lancamentos.

    `paginas` e uma lista de listas de dicionarios de palavra (o formato do
    `page.extract_words()`) e nao objetos do pdfplumber: assim esta parte — que
    e onde mora a logica — e testavel sem gerar PDF.

    O cabecalho e procurado em toda pagina e o ultimo encontrado vale para as
    seguintes: no extrato de duas paginas ele se repete, mas depender disso
    quebraria num extrato longo cuja pagina do meio nao o repetisse.
    """
    colunas = None
    lancamentos = []
    numero = 0
    data_corrente = None

    for palavras in paginas:
        linhas = _palavras_por_linha(palavras)
        colunas = _colunas(linhas) or colunas
        if colunas is None:
            continue

        for palavras_da_linha in linhas:
            campos = defaultdict(list)
            for palavra in palavras_da_linha:
                campos[_coluna_da_palavra(palavra, colunas)].append(palavra['text'])
            texto = {rotulo: ' '.join(partes) for rotulo, partes in campos.items()}

            # O cabecalho cai aqui como uma linha qualquer; descarta-se pelo
            # proprio conteudo, e nao pela posicao, que muda de pagina a pagina.
            if texto.get('Descrição') == 'Descrição':
                continue
            # Sem descricao nao ha lancamento: e assim que caem os rotulos do
            # grafico de saldo, que o pdfplumber le junto com a tabela.
            if not texto.get('Descrição'):
                continue

            numero += 1
            lancamento = _montar_lancamento(numero, texto, data_corrente)
            data_corrente = lancamento.data or data_corrente
            lancamentos.append(lancamento)

    if colunas is None:
        raise ExtratoInterInvalidoError(MSG_SEM_TABELA)
    return lancamentos


def e_pdf(conteudo):
    """True se o conteudo e um PDF, pelo magic number e nao pela extensao.

    O nome do arquivo mente (o operador renomeia, o navegador acrescenta
    ' (1)'), e confundir os dois formatos daria um erro de parse
    incompreensivel em vez de 'este arquivo nao e o extrato'."""
    if isinstance(conteudo, str):
        return False
    return bytes(conteudo[:5]) == b'%PDF-'


def ler_pdf(conteudo):
    """Le o extrato em PDF e devolve os lancamentos — entradas E saidas."""
    import pdfplumber

    if not conteudo:
        raise ExtratoInterInvalidoError(MSG_VAZIO)

    try:
        with pdfplumber.open(io.BytesIO(bytes(conteudo))) as pdf:
            paginas = [pagina.extract_words() for pagina in pdf.pages]
    except Exception as exc:
        raise ExtratoInterInvalidoError(MSG_ILEGIVEL) from exc

    if not paginas:
        raise ExtratoInterInvalidoError(MSG_VAZIO)
    return montar_lancamentos(paginas)


# --- interpretacao da descricao do Pix (NFSE-26) ---------------------------

# Servicos que o escritorio cobra alem dos honorarios mensais, na forma como
# aparecem no Pix -> como devem sair na nota. Semente apenas: a memoria de
# verdade e a tabela `ServicoNfse`, que aprende com o operador. Chaves ja
# normalizadas (sem acento, caixa alta).
SERVICOS_PADRAO = {
    'ALT. CONTRATO': 'ALTERAÇÃO CONTRATUAL',
    'ALT CONTRATO': 'ALTERAÇÃO CONTRATUAL',
    'ALTERACAO DE CONTRATO': 'ALTERAÇÃO CONTRATUAL',
    'ALTERACAO CONTRATUAL': 'ALTERAÇÃO CONTRATUAL',
    'BAIXA': 'BAIXA DE EMPRESA',
    'ABERTURA': 'ABERTURA DE EMPRESA',
}

# Pedacos que marcam "isto e a mensalidade", sem nomear servico nenhum. Achar um
# deles nao resolve a nota sozinho — a competencia e que resolve —, mas tira a
# palavra do nome do tomador ("Valeria Cabreira Brust - honor." e a Valeria).
MARCADORES_HONORARIOS = (
    'HONORARIOS', 'HONORARIO', 'HONOR.', 'HONOR', 'HON.', 'MENSALIDADE',
    'REFERENTE', 'REF.',
)

# Qualificam o PAGAMENTO, nao o servico: "ALT. CONTRATO - PARTE" e uma alteracao
# contratual paga em parte, e a nota nao diz "PARTE". Como o valor da nota sai
# do agrupamento que o operador confirma, o qualificador so precisa sair do
# texto e do nome do tomador.
QUALIFICADORES_PAGAMENTO = ('PARTE', 'PARCIAL', 'SINAL', 'ENTRADA', 'RESTANTE', 'SALDO')

# 'Pix', 'Pix recebido', 'Pix enviado' — ruido fixo do inicio da descricao.
RE_PREFIXO_PIX = re.compile(r'^\s*PIX\b\s*(RECEBIDO|ENVIADO)?\s*-?\s*')
# Competencia escrita: '06/2026', '-06/2026', '06 / 2026'.
RE_COMPETENCIA = re.compile(r'\b(0?[1-9]|1[0-2])\s*/\s*(20\d{2})\b')
# Separador de segmentos: ' - ' e tambem ' -06/2026' (sem espaco depois).
RE_SEPARADOR = re.compile(r'\s+-\s*')


def normalizar_termo(valor):
    """Chave canonica de um termo do extrato: sem acento, caixa alta, espacos
    colapsados. Mesma ideia do `nfse_import.normalizar_nome`, para o outro eixo."""
    texto = remover_acentos(str(valor or '')).upper()
    return re.sub(r'\s+', ' ', texto).strip()


@dataclass
class DescricaoInterpretada:
    """O que a descricao do Pix disse — e o que ela nao disse.

    `pendente` e o ponto do desenho: sem competencia e sem servico reconhecido
    nao ha texto para a nota, e chutar escreveria a coisa errada num documento
    fiscal. A linha para e espera o operador (status `descricao_pendente`)."""
    nome: str = ''
    competencia: str | None = None
    servico: str | None = None
    termo_servico: str | None = None
    honorarios: bool = False
    pendente: bool = True
    partes_ignoradas: list = field(default_factory=list)


def chave_descricao(descricao):
    """A descricao normalizada e sem o prefixo 'Pix' — a chave da memoria.

    Existe para a gravacao e a busca usarem exatamente a mesma forma. Gravar a
    descricao crua ('PIX RECEBIDO - GAMA SAUDE LTDA') e buscar depois de tirar o
    prefixo ('GAMA SAUDE LTDA') faria a memoria nunca casar — o operador
    ensinaria o servico todo mes e o sistema esqueceria todo mes."""
    return RE_PREFIXO_PIX.sub('', normalizar_termo(descricao)).strip(' -')


def _tabela_servicos(servicos=None):
    """Semente + memoria aprendida, com a memoria por cima.

    `servicos` sao registros `ServicoNfse` pre-carregados pelo chamador (uma
    consulta por importacao, nao uma por linha) — mesmo contrato de
    `resolver_empresa(empresas, apelidos)`."""
    tabela = dict(SERVICOS_PADRAO)
    for servico in (servicos or []):
        tabela[normalizar_termo(servico.termo_norm)] = servico.descricao
    return tabela


def _extrair_servico(segmento, tabela):
    """Acha um termo de servico dentro do segmento e o remove.

    Devolve `(sobra, termo, descricao)`. Os termos sao testados do mais longo
    para o mais curto para 'ALT. CONTRATO' ganhar de um eventual 'ALT.', e a
    busca respeita fronteira de palavra para 'BAIXA' nao casar dentro de
    'BAIXADA'."""
    for termo in sorted(tabela, key=len, reverse=True):
        padrao = re.compile(r'(?<![0-9A-Z])' + re.escape(termo) + r'(?![0-9A-Z])')
        if padrao.search(segmento):
            return padrao.sub(' ', segmento, count=1).strip(), termo, tabela[termo]
    return segmento, None, None


def _limpar(segmento, palavras):
    """Remove palavras inteiras do segmento, devolvendo (sobra, achou)."""
    achou = False
    for palavra in palavras:
        padrao = re.compile(r'(?<![0-9A-Z])' + re.escape(palavra) + r'(?![0-9A-Z])')
        if padrao.search(segmento):
            achou = True
            segmento = padrao.sub(' ', segmento).strip()
    return re.sub(r'\s+', ' ', segmento).strip(), achou


def interpretar_descricao(descricao, servicos=None):
    """Le a descricao do Pix e separa tomador, competencia e servico.

    O formato nao e fixo — e texto que uma pessoa digitou no app do banco —,
    entao o metodo e por eliminacao: tira o prefixo 'Pix', quebra em segmentos
    por ' - ' e, de cada segmento, retira o que o sistema reconhece
    (competencia, servico, marcador de honorarios, qualificador de pagamento).
    O que sobra em todos os segmentos, junto, e o nome do tomador — que vai para
    o `resolver_empresa()` das cobrancas, sem nenhuma regra propria.

    Juntar a sobra de TODOS os segmentos, e nao so a do primeiro, e o que faz
    'baixa Texas Cidreira e Texas Tramandaí' devolver as duas empresas em vez de
    uma, e o que protege um nome que por acaso contenha ' - '.
    """
    tabela = _tabela_servicos(servicos)
    texto = chave_descricao(descricao)

    resultado = DescricaoInterpretada()
    nomes = []

    # Descricao inteira aprendida: o operador ja disse o que ESTE Pix significa.
    # Casa antes da busca por fragmento e, ao contrario dela, NAO consome o
    # texto — quando a descricao e so o nome do cliente ('Vida E Saude Produtos
    # Farmaceuticos'), consumi-la deixaria a nota sem tomador.
    if texto in tabela:
        resultado.servico = tabela[texto]
        resultado.termo_servico = texto

    for segmento in RE_SEPARADOR.split(texto):
        segmento = segmento.strip(' -')
        if not segmento:
            continue

        achado = RE_COMPETENCIA.search(segmento)
        if achado is not None:
            # Sempre 'MM/AAAA': o Pix as vezes traz '6/2026'.
            resultado.competencia = f'{int(achado.group(1)):02d}/{achado.group(2)}'
            segmento = RE_COMPETENCIA.sub(' ', segmento, count=1).strip()

        if resultado.servico is None:
            segmento, termo, descricao_servico = _extrair_servico(segmento, tabela)
            if termo is not None:
                resultado.termo_servico = termo
                resultado.servico = descricao_servico

        segmento, marcou = _limpar(segmento, MARCADORES_HONORARIOS)
        resultado.honorarios = resultado.honorarios or marcou

        segmento, _ = _limpar(segmento, QUALIFICADORES_PAGAMENTO)

        segmento = segmento.strip(' -.')
        if segmento:
            nomes.append(segmento)

    resultado.nome = ' - '.join(nomes)
    # Resolvida quando ha servico nomeado (a nota fala do servico) ou quando ha
    # competencia (a nota fala do mes). Sem nenhum dos dois nao ha o que
    # escrever, e a linha espera o operador.
    resultado.pendente = resultado.servico is None and resultado.competencia is None
    return resultado
