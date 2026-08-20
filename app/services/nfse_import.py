"""Import do extrato do banco -> notas de NFSe (NFSE-01..07, NFSE-25..27).

Zero Selenium: e a camada conferivel, que roda antes de abrir qualquer
navegador.

DOIS formatos entram pela mesma porta (`importar()`), escolhidos pelo conteudo
do arquivo e nao pela extensao:

- **CSV de cobrancas (Banrisul)**, descrito abaixo — o formato original;
- **PDF do extrato (Banco Inter)**, lido pelo `nfse_extrato_inter`, onde os
  honorarios chegam por Pix.

Os dois viram `LinhaExtrato` e dali para baixo o codigo e o mesmo. A unica
diferenca que atravessa a fronteira e a COMPETENCIA: no CSV ela e derivada (mes
anterior ao vencimento do titulo), no Inter ela vem escrita na descricao do Pix.

O CSV do banco vem sem cabecalho, com delimitador ';', todos os
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
from app.services import nfse_extrato_inter as inter
from app.utils import TIPO_CPF, detectar_tipo_documento
from app.models import OrigemVinculoNfse

# De qual arquivo a linha veio. Fica gravado na nota: quando uma nota sai
# errada, a primeira pergunta e "de que extrato isso veio".
ORIGEM_CSV = 'csv'
ORIGEM_INTER = 'inter'

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
class LinhaExtrato:
    """Uma linha crua do extrato, ja convertida para os tipos do dominio.

    Forma NORMALIZADA: os dois formatos de arquivo (CSV de cobrancas do
    Banrisul e PDF do Banco Inter) desembocam aqui, e do `_montar_nota()` para
    baixo nada mais sabe de qual banco a linha veio. Por isso ha campos que so
    um dos dois preenche — `vencimento` e as parcelas F/G/H sao do CSV;
    `competencia`, `descricao_*` e `saida` sao do Inter.

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

    # --- so do extrato do Inter ------------------------------------------
    origem: str = ORIGEM_CSV
    # competencia LITERAL, lida da descricao do Pix. Quando None, o
    # `_montar_nota` cai na regra do CSV (mes anterior ao vencimento).
    competencia: str | None = None
    descricao_extrato: str | None = None
    descricao_servico: str | None = None
    descricao_pendente: bool = False
    # valor da coluna Saida. Nao vira nota: alimenta a proposta de agrupamento,
    # que e como o estorno abate as entradas do mesmo tomador.
    saida: Decimal | None = None


# Nome antigo: o CSV foi o unico formato ate o extrato do Inter entrar, e a
# suite referencia `LinhaCsv`. Alias em vez de rename cego para o diff da
# feature nao virar churn de teste.
LinhaCsv = LinhaExtrato


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


def parse_inter(conteudo, categoria, servicos=None):
    """Le o extrato em PDF do Inter e devolve as linhas que viram nota.

    Duas condicoes para a linha entrar, e as duas importam:

    - **a categoria** (`HONORÁRIOS - CLIENTES`, configuravel) — e o unico sinal
      que separa recebimento de cliente de qualquer outro credito na conta;
    - **ter valor na coluna Entrada** — saida nao vira nota. As saidas nao sao
      jogadas fora: voltam junto, marcadas, porque o estorno de um cliente
      precisa ser proposto como abatimento das entradas dele.

    Devolve `(linhas, saidas)`.
    """
    lancamentos = inter.ler_pdf(conteudo)
    alvo = inter.normalizar_termo(categoria)

    linhas = []
    saidas = []
    for lancamento in lancamentos:
        if lancamento.saida is not None:
            saidas.append(lancamento)
            continue
        if lancamento.entrada is None:
            continue
        if inter.normalizar_termo(lancamento.nome) != alvo:
            continue
        linhas.append(_linha_do_inter(lancamento, servicos))

    return linhas, saidas


def _linha_do_inter(lancamento, servicos):
    lido = inter.interpretar_descricao(lancamento.descricao, servicos)
    return LinhaExtrato(
        numero=lancamento.numero,
        origem=ORIGEM_INTER,
        assinatura=_assinatura_inter(lancamento),
        nome=lido.nome,
        nome_norm=normalizar_nome(lido.nome),
        data_pagamento=lancamento.data,
        # Sem vencimento no extrato do Inter: la a data e do PAGAMENTO, e e por
        # isso que a competencia nao pode ser derivada dela. Ficar None aqui NAO
        # invalida a linha (ao contrario do CSV, onde o vencimento e a origem da
        # competencia e sem ele nao ha o que emitir).
        vencimento=None,
        valor_final=lancamento.entrada,
        competencia=lido.competencia,
        descricao_extrato=lancamento.descricao or None,
        descricao_servico=lido.servico,
        descricao_pendente=lido.pendente,
    )


def _assinatura_inter(lancamento):
    """Identidade da linha do extrato do Inter, para deduplicar entre arquivos.

    Mesma ideia do `_assinatura` do CSV — a linha INTEIRA, nunca um campo
    isolado —, mas montada sobre os campos crus do lancamento em vez da
    descricao ja interpretada: a interpretacao muda quando o operador ensina um
    servico novo, e a identidade da linha do banco nao pode mudar junto."""
    return '|'.join((
        lancamento.data.isoformat() if lancamento.data else '',
        (lancamento.nome or '').strip(),
        (lancamento.descricao or '').strip(),
        str(lancamento.entrada if lancamento.entrada is not None else ''),
    ))


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
    ('VIDROMAX') enquanto o banco manda a razao social truncada em 35 caracteres
    ('VIDROMAX COMERCIO DE VIDROS LTDA'): token_set_ratio pontua 100 quando um
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


# Status que ja "ocupam" uma competencia e por isso bloqueiam uma segunda nota
# igual. `aguardando_confirmacao` entra junto com `emitida`: ela e uma DPS que
# ja existe no portal esperando o operador clicar. Sem ela na lista, reimportar
# o extrato devolvia a linha como Pronta e o operador preencheria de novo,
# abrindo uma SEGUNDA DPS para o mesmo tomador.
#
# `cancelada` entra pelo motivo oposto, e nao por ser nota: e uma DECISAO do
# operador de que aquilo nao vira nota. Fora da lista, reimportar o extrato
# ressuscitaria a linha como Pronta e ela seria emitida — a decisao dele se
# perderia calada. Dentro, a linha volta como duplicata, que e liberavel: ele ve
# que ja decidiu e escolhe de novo.
STATUS_QUE_OCUPAM_COMPETENCIA = ('emitida', 'aguardando_confirmacao', 'cancelada')


def chave_duplicidade(documento, competencia, descricao_servico=None):
    """Identidade de uma nota para efeito de duplicidade (ND-004).

    O DOCUMENTO, e nao o `empresa_id`: parte dos tomadores e pessoa fisica ou
    empresa nao cadastrada, e nesses casos `empresa_id` e nulo — com a chave por
    empresa a duplicata nunca seria detectada justamente para eles.

    O SERVICO entra na chave porque a competencia sozinha deixou de identificar
    a nota quando o extrato do Inter passou a trazer servicos avulsos: uma
    alteracao contratual e uma baixa da mesma empresa caem no mesmo mes e sao
    duas notas legitimas. Sem o servico na chave, a segunda viraria duplicata da
    primeira e o operador teria de liberar uma duplicata que nao existe.
    Honorarios tem `descricao_servico` nulo e a chave volta a ser a de sempre.
    """
    return (documento, competencia, descricao_servico or None)


def _competencias_ja_emitidas():
    """chave de duplicidade -> id da nota que ja a ocupa."""
    from app.models import NotaNfse
    consulta = (NotaNfse.query
                .filter(NotaNfse.status.in_(STATUS_QUE_OCUPAM_COMPETENCIA))
                .with_entities(NotaNfse.id, NotaNfse.documento,
                               NotaNfse.competencia, NotaNfse.descricao_servico))
    # Devolve o id junto para a duplicata poder apontar para a nota que ja foi
    # emitida — sem ele o operador ve "duplicata" sem saber de qual.
    return {chave_duplicidade(documento, competencia, servico): nota_id
            for nota_id, documento, competencia, servico in consulta if documento}


def _ler_arquivos(arquivos, categoria=None, servicos=None):
    """Le e concatena varios extratos numa lista unica de linhas.

    `arquivos` e uma sequencia de (nome, conteudo), e cada um pode ser o CSV de
    cobrancas OU o PDF do Inter — o formato sai do CONTEUDO (`inter.e_pdf`), nao
    da extensao, e uma selecao pode misturar os dois.

    Linhas identicas entre arquivos sao descartadas uma unica vez — o operador
    costuma baixar periodos que se sobrepoem, e a mesma cobranca aparece nos
    dois.

    Se QUALQUER arquivo nao for um extrato reconhecido, a importacao inteira e
    recusada citando o nome dele: aceitar os demais deixaria o operador achando
    que importou tudo.

    Devolve `(linhas, ignoradas, saidas)`. As `saidas` sao os debitos do extrato
    do Inter; nao viram nota, mas alimentam a proposta de agrupamento.
    """
    linhas = []
    saidas = []
    vistas = set()
    ignoradas = 0

    for nome, conteudo in arquivos:
        try:
            if inter.e_pdf(conteudo):
                do_arquivo, do_arquivo_saidas = parse_inter(
                    conteudo, categoria, servicos)
                saidas.extend(do_arquivo_saidas)
            else:
                do_arquivo = parse_csv(conteudo)
        except (ArquivoInvalidoError, inter.ExtratoInterInvalidoError) as exc:
            rotulo = f' "{nome}"' if nome else ''
            raise ArquivoInvalidoError(f'Arquivo{rotulo}: {exc}') from exc

        for linha in do_arquivo:
            if linha.assinatura and linha.assinatura in vistas:
                ignoradas += 1
                continue
            if linha.assinatura:
                vistas.add(linha.assinatura)
            linhas.append(linha)

    return linhas, ignoradas, saidas


def importar(conteudo, nome_arquivo=None, execution_id=None):
    """Le um ou varios extratos, resolve os documentos e persiste numa transacao.

    `conteudo` aceita um arquivo so (bytes/str) ou uma lista de (nome, conteudo).
    Arquivo invalido levanta ANTES de qualquer escrita: nunca sobra lote parcial
    (NFSE-07). Devolve o `LoteNfse` criado, com `ignoradas_duplicadas` anotado.
    """
    from app import db
    from app.models import (
        ApelidoNfse, Empresa, LoteNfse, NotaNfse, ServicoNfse, StatusNotaNfse)
    # Import tardio: o `nfse_grupos` chama de volta o `recalcular_status` daqui,
    # e no topo do modulo os dois se importariam em circulo.
    from app.services import nfse_config, nfse_grupos

    if isinstance(conteudo, (bytes, bytearray, str)):
        arquivos = [(nome_arquivo, conteudo)]
    else:
        arquivos = list(conteudo)

    # Uma consulta por importacao, nao uma por linha — mesmo contrato de
    # `empresas`/`apelidos` no `resolver_empresa`.
    servicos = ServicoNfse.query.all()
    categoria = nfse_config.get_config_nfse().categoria_extrato

    # parse primeiro: se algum arquivo nao presta, nada e persistido
    linhas, ignoradas, saidas = _ler_arquivos(arquivos, categoria, servicos)

    empresas = Empresa.query.all()
    apelidos = ApelidoNfse.query.all()
    emitidas = _competencias_ja_emitidas()
    vistas_no_lote = {}

    nomes = [nome for nome, _ in arquivos if nome]
    lote = LoteNfse(nome_arquivo=', '.join(nomes)[:200] or nome_arquivo,
                    total=len(linhas), execution_id=execution_id)
    db.session.add(lote)

    try:
        notas = []
        for linha in linhas:
            nota = _montar_nota(linha, empresas, apelidos, NotaNfse, StatusNotaNfse)
            nota.lote = lote
            notas.append(nota)

            chave = chave_duplicidade(nota.documento, nota.competencia,
                                      nota.descricao_servico)
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

        # Depois de montadas todas: a proposta olha as notas do lote inteiro
        # contra as saidas do periodo, e nao uma linha isolada.
        nfse_grupos.propor(notas, saidas)

        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    lote.ignoradas_duplicadas = ignoradas
    return lote


def _competencia_da_linha(linha):
    """Competencia da nota — as duas regras, lado a lado.

    No CSV a data disponivel e o VENCIMENTO do titulo, e a competencia e o mes
    anterior a ele. No Inter a competencia vem escrita na descricao do Pix e e
    ela que vale; quando o Pix nao a traz (servico avulso, ou descricao que o
    sistema nao entendeu), grava-se o mes do PAGAMENTO — que serve para agrupar
    e filtrar a lista, mas NAO entra no texto da nota.

    Confundir as duas erra o mes em toda nota: o vencimento e do mes seguinte ao
    servico, a data do Pix e do mesmo mes em que ele foi feito.
    """
    if linha.origem == ORIGEM_INTER:
        if linha.competencia:
            return linha.competencia
        if linha.data_pagamento:
            return f'{linha.data_pagamento.month:02d}/{linha.data_pagamento.year}'
        return None
    return competencia_da_descricao(linha.vencimento)


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
        # o mesmo numero, em duas colunas com papeis diferentes: `valor_final` e
        # o valor A EMITIR (um agrupamento pode reescreve-lo), `valor_extrato` e
        # o que o banco imprimiu e nao muda nunca
        valor_extrato=linha.valor_final,
        divergencia_valor=_divergiu(linha),
        origem_extrato=linha.origem,
        descricao_extrato=linha.descricao_extrato,
        descricao_servico=linha.descricao_servico,
        descricao_pendente=linha.descricao_pendente,
    )

    if linha.invalida:
        nota.status = StatusNotaNfse.INVALIDA
        nota.erro = linha.motivo
        return nota

    nota.competencia = _competencia_da_linha(linha)

    vinculo = resolver_empresa(linha.nome, empresas, apelidos)
    if vinculo.resolvido:
        nota.origem_vinculo = vinculo.origem
        nota.score_match = vinculo.score
        if vinculo.empresa is not None:
            nota.empresa_id = vinculo.empresa.id
            nota.documento = vinculo.empresa.cnpj
            nota.tipo_documento = detectar_tipo_documento(vinculo.empresa.cnpj)
        else:
            # documento avulso lembrado de um mes anterior
            nota.documento = vinculo.documento
            nota.tipo_documento = (vinculo.tipo_documento
                                   or detectar_tipo_documento(vinculo.documento))

    nota.status = recalcular_status(nota)
    return nota


def recalcular_status(nota):
    """Status que a nota tem, dadas as pendencias que restam.

    Nucleo compartilhado: usado no import, ao resolver a empresa, ao resolver a
    descricao, ao desmarcar "emitida na mao" e ao descancelar. Antes cada um
    desses pontos remontava a cadeia por conta propria, e bastava um esquecer a
    pendencia nova para a nota entrar na fila sem descricao.

    A ordem e a das consequencias, nao a do fluxo: **documento primeiro**.
    Emitir com o CNPJ de outro cliente exige cancelamento da nota junto a
    prefeitura; descricao errada tambem, mas o documento e o que identifica o
    tomador e e o erro mais caro de desfazer.

    E uma funcao PURA sobre as pendencias: ela responde "que status esta nota
    teria, olhando so o que falta nela" e ignora o status atual — de proposito,
    porque quem desmarca uma nota emitida precisa justamente sair do estado
    atual. Cabe a quem chama nao a aplicar sobre estado que deve ser preservado
    (duplicata, emitida, cancelada, agrupada).
    """
    from app.models import StatusNotaNfse

    if not nota.documento:
        return StatusNotaNfse.EMPRESA_PENDENTE
    if nota.descricao_pendente:
        return StatusNotaNfse.DESCRICAO_PENDENTE
    # `empresa_id` antes do CPF, como no `_status_apos_desmarcar` original: ha
    # Empresa cadastrada cujo "cnpj" e um CPF (firma individual), e para essa a
    # nota e PRONTA — ela tem cadastro. PESSOA_FISICA e o tomador SEM cadastro.
    if nota.empresa_id:
        return StatusNotaNfse.PRONTA
    if nota.tipo_documento == TIPO_CPF:
        return StatusNotaNfse.PESSOA_FISICA
    return StatusNotaNfse.CADASTRO_PENDENTE


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
