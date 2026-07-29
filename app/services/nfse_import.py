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
from app.utils import TIPO_CPF, detectar_tipo_documento
from app.models import OrigemVinculoNfse

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


# --- resolucao nome do banco -> Empresa (NFSE-03) --------------------------

# Limiar DUPLO, deliberado (ND-003): score alto sozinho nao distingue "match
# bom" de "match bom mas ambiguo". Errar aqui emite nota fiscal com o CNPJ de
# outro cliente, o que exige cancelamento — entao um segundo colocado proximo
# manda a linha para conferencia humana em vez de chutar.
LIMIAR_SCORE = 90
LIMIAR_GAP = 10


@dataclass
class Vinculo:
    """Resultado da resolucao do tomador.

    Tres desfechos possiveis:
    - `empresa` preenchida: tomador cadastrado (caso comum);
    - so `documento`: CPF, ou CNPJ de empresa ainda nao cadastrada, lembrado de
      um mes anterior — emite normalmente, sem cadastro;
    - nada: vai para conferencia humana.
    """
    empresa: object | None = None
    origem: str | None = None
    score: int | None = None
    documento: str | None = None
    tipo_documento: str | None = None

    @property
    def resolvido(self):
        return self.empresa is not None or bool(self.documento)


def _indice_por_nome(empresas):
    """nome normalizado -> empresa. Em caso de nomes repetidos no cadastro,
    guarda a lista para poder recusar o match exato ambiguo."""
    indice = {}
    for empresa in empresas:
        indice.setdefault(normalizar_nome(empresa.nome), []).append(empresa)
    return indice


def resolver_empresa(nome, empresas, apelidos=None):
    """Resolve o nome cru do banco para uma `Empresa` cadastrada.

    Cascata: exato normalizado -> apelido salvo -> fuzzy com limiar duplo.
    `empresas` e `apelidos` sao pre-carregados pelo chamador (uma consulta por
    importacao, nao uma por linha).

    O scorer e `token_set_ratio` porque o cadastro guarda apelido curto
    ('ALUMAP') enquanto o banco manda a razao social truncada em 35 caracteres
    ('ALUMAP COMERCIO DE ALUMINIOS LTDA'): token_set_ratio pontua 100 quando um
    conjunto de tokens e subconjunto do outro. Mesmo scorer de file_manager.
    """
    from thefuzz import fuzz, process

    chave = normalizar_nome(nome)
    if not chave:
        return Vinculo()

    indice = _indice_por_nome(empresas)

    # 1) exato normalizado — so vale se for inequivoco
    candidatas = indice.get(chave) or []
    if len(candidatas) == 1:
        return Vinculo(candidatas[0], OrigemVinculoNfse.EXATO, 100)
    if len(candidatas) > 1:
        return Vinculo()

    # 2) apelido salvo (decisao humana anterior) tem precedencia sobre o fuzzy
    for apelido in (apelidos or []):
        if apelido.nome_norm != chave:
            continue
        if apelido.empresa_id:
            for empresa in empresas:
                if empresa.id == apelido.empresa_id:
                    return Vinculo(empresa, OrigemVinculoNfse.APELIDO, 100)
        if apelido.documento:
            # documento avulso: CPF (nunca vira cadastro) ou CNPJ de empresa
            # ainda nao cadastrada. Evita redigitar o numero todo mes.
            return Vinculo(origem=OrigemVinculoNfse.APELIDO, score=100,
                           documento=apelido.documento,
                           tipo_documento=apelido.tipo_documento)

    # 3) fuzzy com limiar duplo
    chaves = list(indice.keys())
    if not chaves:
        return Vinculo()

    ranking = process.extract(chave, chaves, scorer=fuzz.token_set_ratio, limit=2)
    if not ranking:
        return Vinculo()

    melhor_chave, melhor = ranking[0]
    segundo = ranking[1][1] if len(ranking) > 1 else 0

    if melhor < LIMIAR_SCORE or (melhor - segundo) < LIMIAR_GAP:
        # bom demais para ignorar, ambiguo demais para arriscar: vai para o humano
        return Vinculo()

    empatadas = indice.get(melhor_chave) or []
    if len(empatadas) != 1:
        return Vinculo()
    return Vinculo(empatadas[0], OrigemVinculoNfse.FUZZY, int(melhor))


# --- importacao transacional (NFSE-04..07) ---------------------------------

# Tolerancia da conferencia F + G - H == I. Um centavo cobre arredondamento do
# extrato sem deixar passar erro real.
TOLERANCIA_VALOR = Decimal('0.01')


def _divergiu(linha):
    """True quando a soma das parcelas nao bate com o valor final do extrato.

    Nao muda o valor a emitir (a coluna I manda): e rede de seguranca contra
    CSV corrompido, sinalizada para o operador conferir."""
    parcelas = (linha.valor_titulo, linha.acrescimos, linha.deducoes, linha.valor_final)
    if any(parcela is None for parcela in parcelas):
        return False
    esperado = linha.valor_titulo + linha.acrescimos - linha.deducoes
    return abs(esperado - linha.valor_final) > TOLERANCIA_VALOR


def _competencias_ja_emitidas():
    """(documento, competencia) das notas ja EMITIDAS — base da trava (ND-004).

    A chave e o DOCUMENTO, nao o `empresa_id`: parte dos tomadores e pessoa
    fisica ou empresa nao cadastrada, e nesses casos `empresa_id` e nulo — com
    a chave antiga a duplicata nunca seria detectada justamente para eles.
    """
    from app.models import NotaNfse, StatusNotaNfse
    consulta = (NotaNfse.query
                .filter(NotaNfse.status == StatusNotaNfse.EMITIDA)
                .with_entities(NotaNfse.documento, NotaNfse.competencia))
    return {(documento, competencia) for documento, competencia in consulta
            if documento}


def importar(conteudo, nome_arquivo=None, execution_id=None):
    """Le o extrato, resolve os CNPJs e persiste lote + notas numa transacao.

    Arquivo invalido levanta `ArquivoInvalidoError` ANTES de qualquer escrita:
    nunca sobra lote parcial (NFSE-07). Devolve o `LoteNfse` criado.
    """
    from app import db
    from app.models import ApelidoNfse, Empresa, LoteNfse, NotaNfse, StatusNotaNfse

    # parse primeiro: se o arquivo nao presta, nada e persistido
    linhas = parse_csv(conteudo)

    empresas = Empresa.query.all()
    apelidos = ApelidoNfse.query.all()
    emitidas = _competencias_ja_emitidas()
    vistas_no_lote = {}

    lote = LoteNfse(nome_arquivo=nome_arquivo, total=len(linhas), execution_id=execution_id)
    db.session.add(lote)

    try:
        for linha in linhas:
            nota = _montar_nota(linha, empresas, apelidos, NotaNfse, StatusNotaNfse)
            nota.lote = lote

            chave = (nota.documento, nota.competencia)
            if nota.documento and nota.status in (StatusNotaNfse.PRONTA,
                                                  StatusNotaNfse.PESSOA_FISICA,
                                                  StatusNotaNfse.CADASTRO_PENDENTE):
                if chave in emitidas or chave in vistas_no_lote:
                    nota.status = StatusNotaNfse.DUPLICATA
                    nota.duplicata_de_id = vistas_no_lote.get(chave)
                else:
                    vistas_no_lote[chave] = None
            db.session.add(nota)

        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return lote


def _montar_nota(linha, empresas, apelidos, NotaNfse, StatusNotaNfse):
    nota = NotaNfse(
        nome_csv=linha.nome or None,
        nome_csv_norm=linha.nome_norm or None,
        data_pagamento=linha.data_pagamento,
        vencimento=linha.vencimento,
        valor_titulo=linha.valor_titulo,
        acrescimos=linha.acrescimos,
        deducoes=linha.deducoes,
        valor_final=linha.valor_final,
        divergencia_valor=_divergiu(linha),
    )

    if linha.invalida:
        nota.status = StatusNotaNfse.INVALIDA
        nota.erro = linha.motivo
        return nota

    nota.competencia = competencia_da_descricao(linha.vencimento)

    vinculo = resolver_empresa(linha.nome, empresas, apelidos)
    if not vinculo.resolvido:
        nota.status = StatusNotaNfse.EMPRESA_PENDENTE
        return nota

    nota.origem_vinculo = vinculo.origem
    nota.score_match = vinculo.score

    if vinculo.empresa is not None:
        nota.empresa_id = vinculo.empresa.id
        nota.documento = vinculo.empresa.cnpj
        nota.tipo_documento = detectar_tipo_documento(vinculo.empresa.cnpj)
        nota.status = StatusNotaNfse.PRONTA
        return nota

    # documento avulso lembrado de um mes anterior
    nota.documento = vinculo.documento
    nota.tipo_documento = vinculo.tipo_documento or detectar_tipo_documento(vinculo.documento)
    nota.status = (StatusNotaNfse.PESSOA_FISICA
                   if nota.tipo_documento == TIPO_CPF
                   else StatusNotaNfse.CADASTRO_PENDENTE)
    return nota
