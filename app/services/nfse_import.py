"""Import do CSV de cobrancas do banco -> notas de NFSe (NFSE-01..07).

Zero Selenium: e a camada conferivel, que roda antes de abrir qualquer
navegador. O CSV do banco vem sem cabecalho, com delimitador ';', todos os
campos entre aspas duplas, datas dd/mm/aaaa e valores no padrao brasileiro
('1.784,00'). Dez colunas, nesta ordem:

    A data de pagamento | B nome do tomador | C e D numeros do banco (descartadas)
    E vencimento | F valor do titulo | G acrescimos | H deducoes
    I valor final | J tipo de cobranca (descartada)

`I` e o valor a emitir; a conta `F + G - H` e apenas rede de seguranca contra
CSV corrompido (NFSE-04, conferida no `importar()`).
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from app.file_manager import remover_acentos

DELIMITADOR = ';'
COLUNAS_ESPERADAS = 10
# A amostra nao tem acento, entao o encoding nao e inferivel: extratos de banco
# brasileiros sao tipicamente cp1252, e o utf-8-sig cobre exportacoes modernas.
ENCODINGS = ('utf-8-sig', 'cp1252')

# Indices das colunas aproveitadas (C, D e J sao descartadas de proposito).
COL_DATA_PAGAMENTO = 0
COL_NOME = 1
COL_VENCIMENTO = 4
COL_VALOR_TITULO = 5
COL_ACRESCIMOS = 6
COL_DEDUCOES = 7
COL_VALOR_FINAL = 8

MSG_VAZIO = (
    'Arquivo vazio: envie o CSV de cobrancas exportado do banco.')
MSG_ILEGIVEL = (
    'Nao foi possivel ler o arquivo: envie o CSV de cobrancas exportado do '
    'banco (arquivo de texto, nao PDF/XLS).')
MSG_FORMATO = (
    f'Formato inesperado: nenhuma linha tem as {COLUNAS_ESPERADAS} colunas do '
    f'extrato do banco separadas por "{DELIMITADOR}". Confira o arquivo exportado.')


class ArquivoInvalidoError(ValueError):
    """CSV vazio, ilegivel ou fora do formato do banco (NFSE-07).

    Recusa o arquivo inteiro: o `importar()` nao persiste lote parcial."""


@dataclass
class LinhaCsv:
    """Uma linha crua do extrato, ja convertida para os tipos do dominio.

    `invalida` marca a linha que nao da para emitir (valor final ou vencimento
    ilegivel) sem abortar o resto do arquivo — edge case da spec."""
    numero: int
    nome: str = ''
    nome_norm: str = ''
    data_pagamento: date | None = None
    vencimento: date | None = None
    valor_titulo: Decimal | None = None
    acrescimos: Decimal | None = None
    deducoes: Decimal | None = None
    valor_final: Decimal | None = None
    invalida: bool = False
    motivo: str | None = None


def normalizar_nome(valor):
    """Chave canonica do nome do tomador: sem acento, caixa alta, espacos
    colapsados. E a chave do apelido salvo (NFSE-03) e a base do matching.

    Diferente de `utils.normalizar_cidade`, que descarta os espacos: aqui os
    tokens precisam sobreviver, porque o scorer e `token_set_ratio`."""
    texto = remover_acentos(str(valor or '')).upper()
    return re.sub(r'\s+', ' ', texto).strip()


def competencia_da_descricao(vencimento):
    """Competencia que vai na descricao da nota: mes ANTERIOR ao vencimento,
    'MM/AAAA'. Vencimento em janeiro vira dezembro do ano anterior.

    Nao confundir com a data de competencia da NFSe (data de hoje, no portal).
    """
    if vencimento is None:
        raise ValueError('vencimento obrigatorio para calcular a competencia')
    mes = vencimento.month - 1
    ano = vencimento.year
    if mes == 0:
        mes, ano = 12, ano - 1
    return f'{mes:02d}/{ano}'


def _decodificar(conteudo):
    if isinstance(conteudo, str):
        return conteudo
    for encoding in ENCODINGS:
        try:
            return conteudo.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ArquivoInvalidoError(MSG_ILEGIVEL)


def _para_data(bruto):
    texto = (bruto or '').strip()
    if not texto:
        return None
    partes = texto.split('/')
    if len(partes) != 3:
        return None
    try:
        dia, mes, ano = (int(p) for p in partes)
        return date(ano, mes, dia)
    except (TypeError, ValueError):
        return None


def _para_decimal(bruto):
    """'1.784,00' -> Decimal('1784.00'). Devolve None se nao for numerico."""
    texto = (bruto or '').strip()
    if not texto:
        return None
    texto = texto.replace('.', '').replace(',', '.')
    try:
        return Decimal(texto)
    except (InvalidOperation, ValueError):
        return None


def parse_csv(conteudo):
    """Le o extrato do banco e devolve uma `LinhaCsv` por linha de dados.

    Levanta `ArquivoInvalidoError` quando o arquivo e vazio, ilegivel ou
    nenhuma linha tem as 10 colunas esperadas (NFSE-07). Linhas isoladas
    malformadas viram `invalida` e nao abortam o arquivo."""
    texto = _decodificar(conteudo)
    if not texto.strip():
        raise ArquivoInvalidoError(MSG_VAZIO)

    linhas = []
    com_formato = 0
    leitor = csv.reader(io.StringIO(texto), delimiter=DELIMITADOR)
    for numero, campos in enumerate(leitor, start=1):
        if not any((campo or '').strip() for campo in campos):
            continue
        if len(campos) < COLUNAS_ESPERADAS:
            linhas.append(LinhaCsv(
                numero=numero, invalida=True,
                motivo=(f'Linha {numero}: esperadas {COLUNAS_ESPERADAS} colunas, '
                        f'encontradas {len(campos)}.')))
            continue
        com_formato += 1
        linhas.append(_montar_linha(numero, campos))

    if com_formato == 0:
        raise ArquivoInvalidoError(MSG_FORMATO)
    return linhas


def _montar_linha(numero, campos):
    nome = (campos[COL_NOME] or '').strip()
    linha = LinhaCsv(
        numero=numero,
        nome=nome,
        nome_norm=normalizar_nome(nome),
        data_pagamento=_para_data(campos[COL_DATA_PAGAMENTO]),
        vencimento=_para_data(campos[COL_VENCIMENTO]),
        valor_titulo=_para_decimal(campos[COL_VALOR_TITULO]),
        acrescimos=_para_decimal(campos[COL_ACRESCIMOS]),
        deducoes=_para_decimal(campos[COL_DEDUCOES]),
        valor_final=_para_decimal(campos[COL_VALOR_FINAL]),
    )

    # Nome vazio NAO invalida a linha: ela fica pendente de empresa (edge case).
    motivos = []
    if linha.valor_final is None:
        motivos.append(f'valor final invalido ("{(campos[COL_VALOR_FINAL] or "").strip()}")')
    if linha.vencimento is None:
        motivos.append(f'vencimento invalido ("{(campos[COL_VENCIMENTO] or "").strip()}")')
    if motivos:
        linha.invalida = True
        linha.motivo = f'Linha {numero}: ' + ', '.join(motivos) + '.'
    return linha
