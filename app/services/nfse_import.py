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
    # identidade da linha crua, usada para deduplicar quando varios arquivos
    # do banco se sobrepoem (o operador baixa periodos que se cruzam)
    assinatura: str | None = None


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


def _assinatura(campos):
    """Identidade da linha crua: todos os campos normalizados.

    Deliberadamente a linha INTEIRA, e nao so o 'nosso numero' do banco: usar um
    identificador isolado descartaria em silencio uma linha que fosse de fato
    diferente. Aqui, duas linhas so somem se forem identicas — nenhuma
    informacao se perde. Divergencias reais (mesmo tomador e competencia em
    linhas diferentes) continuam aparecendo como duplicata para o operador
    decidir."""
    return ''.join((campo or '').strip() for campo in campos[:COLUNAS_ESPERADAS])


def _montar_linha(numero, campos):
    nome = (campos[COL_NOME] or '').strip()
    linha = LinhaCsv(
        numero=numero,
        assinatura=_assinatura(campos),
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


# Status que ja "ocupam" um par (documento, competencia) e por isso bloqueiam
# uma segunda nota igual. `aguardando_confirmacao` entra junto com `emitida`:
# ela e uma DPS que ja existe no portal esperando o operador clicar. Sem ela na
# lista, reimportar o extrato devolvia a linha como Pronta e o operador
# preencheria de novo, abrindo uma SEGUNDA DPS para o mesmo tomador.
STATUS_QUE_OCUPAM_COMPETENCIA = ('emitida', 'aguardando_confirmacao')


def _competencias_ja_emitidas():
    """(documento, competencia) -> id das notas que ja ocupam a competencia.

    Base da trava (ND-004). A chave e o DOCUMENTO, nao o `empresa_id`: parte
    dos tomadores e pessoa fisica ou empresa nao cadastrada, e nesses casos
    `empresa_id` e nulo — com a chave antiga a duplicata nunca seria detectada
    justamente para eles.
    """
    from app.models import NotaNfse
    consulta = (NotaNfse.query
                .filter(NotaNfse.status.in_(STATUS_QUE_OCUPAM_COMPETENCIA))
                .with_entities(NotaNfse.id, NotaNfse.documento,
                               NotaNfse.competencia))
    # Devolve o id junto para a duplicata poder apontar para a nota que ja foi
    # emitida — sem ele o operador ve "duplicata" sem saber de qual.
    return {(documento, competencia): nota_id
            for nota_id, documento, competencia in consulta if documento}


def _ler_arquivos(arquivos):
    """Le e concatena varios extratos numa lista unica de linhas.

    `arquivos` e uma sequencia de (nome, conteudo). Linhas identicas entre
    arquivos sao descartadas uma unica vez — o operador costuma baixar periodos
    que se sobrepoem, e a mesma cobranca aparece nos dois.

    Se QUALQUER arquivo nao for o extrato do banco, a importacao inteira e
    recusada citando o nome dele: aceitar os demais deixaria o operador achando
    que importou tudo.
    """
    linhas = []
    vistas = set()
    ignoradas = 0

    for nome, conteudo in arquivos:
        try:
            do_arquivo = parse_csv(conteudo)
        except ArquivoInvalidoError as exc:
            rotulo = f' "{nome}"' if nome else ''
            raise ArquivoInvalidoError(f'Arquivo{rotulo}: {exc}') from exc

        for linha in do_arquivo:
            if linha.assinatura and linha.assinatura in vistas:
                ignoradas += 1
                continue
            if linha.assinatura:
                vistas.add(linha.assinatura)
            linhas.append(linha)

    return linhas, ignoradas


def importar(conteudo, nome_arquivo=None, execution_id=None):
    """Le um ou varios extratos, resolve os documentos e persiste numa transacao.

    `conteudo` aceita um arquivo so (bytes/str) ou uma lista de (nome, conteudo).
    Arquivo invalido levanta ANTES de qualquer escrita: nunca sobra lote parcial
    (NFSE-07). Devolve o `LoteNfse` criado, com `ignoradas_duplicadas` anotado.
    """
    from app import db
    from app.models import ApelidoNfse, Empresa, LoteNfse, NotaNfse, StatusNotaNfse

    if isinstance(conteudo, (bytes, bytearray, str)):
        arquivos = [(nome_arquivo, conteudo)]
    else:
        arquivos = list(conteudo)

    # parse primeiro: se algum arquivo nao presta, nada e persistido
    linhas, ignoradas = _ler_arquivos(arquivos)

    empresas = Empresa.query.all()
    apelidos = ApelidoNfse.query.all()
    emitidas = _competencias_ja_emitidas()
    vistas_no_lote = {}

    nomes = [nome for nome, _ in arquivos if nome]
    lote = LoteNfse(nome_arquivo=', '.join(nomes)[:200] or nome_arquivo,
                    total=len(linhas), execution_id=execution_id)
    db.session.add(lote)

    try:
        for linha in linhas:
            nota = _montar_nota(linha, empresas, apelidos, NotaNfse, StatusNotaNfse)
            nota.lote = lote

            chave = (nota.documento, nota.competencia)
            if nota.documento and nota.status in (StatusNotaNfse.PRONTA,
                                                  StatusNotaNfse.PESSOA_FISICA,
                                                  StatusNotaNfse.CADASTRO_PENDENTE):
                anterior = vistas_no_lote.get(chave)
                if chave in emitidas or anterior is not None:
                    nota.status = StatusNotaNfse.DUPLICATA
                    if anterior is not None:
                        # a original ainda nao tem id (nada foi para o banco);
                        # o relacionamento resolve a FK no flush
                        nota.duplicata_de = anterior
                    else:
                        nota.duplicata_de_id = emitidas.get(chave)
                else:
                    vistas_no_lote[chave] = nota
            db.session.add(nota)

        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    lote.ignoradas_duplicadas = ignoradas
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


def reconciliar_com_cadastro(notas=None):
    """Liga notas de documento avulso a Empresas que passaram a existir.

    Depois que o operador usa o atalho "Cadastrar", a Empresa existe mas a nota
    continua apontando so para o documento — na volta a linha ainda diria "sem
    cadastro", e o apelido memorizado repetiria isso no mes seguinte.

    Roda na abertura da pagina e no import. So mexe em `cadastro_pendente` (CNPJ
    aguardando cadastro); `pessoa_fisica` e estado final e nao e reconciliado.
    Devolve quantas linhas foram ligadas.
    """
    from app import db
    from app.models import ApelidoNfse, Empresa, NotaNfse, StatusNotaNfse

    if notas is None:
        notas = NotaNfse.query.filter_by(status=StatusNotaNfse.CADASTRO_PENDENTE).all()

    pendentes = [n for n in notas
                 if n.status == StatusNotaNfse.CADASTRO_PENDENTE and n.documento]
    if not pendentes:
        return 0

    documentos = {n.documento for n in pendentes}
    por_cnpj = {e.cnpj: e for e in Empresa.query.filter(Empresa.cnpj.in_(documentos)).all()}
    if not por_cnpj:
        return 0

    ligadas = 0
    for nota in pendentes:
        empresa = por_cnpj.get(nota.documento)
        if empresa is None:
            continue
        nota.empresa_id = empresa.id
        nota.status = StatusNotaNfse.PRONTA
        ligadas += 1

        # o apelido tambem precisa passar a apontar para a Empresa, senao o
        # proximo import voltaria a resolver so pelo documento avulso
        if nota.nome_csv_norm:
            apelido = ApelidoNfse.query.filter_by(nome_norm=nota.nome_csv_norm).first()
            if apelido is not None:
                apelido.empresa_id = empresa.id
                apelido.documento = None
                apelido.tipo_documento = None

    if ligadas:
        db.session.commit()
    return ligadas
