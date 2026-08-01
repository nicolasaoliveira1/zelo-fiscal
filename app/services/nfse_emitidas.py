"""Notas emitidas no portal: consulta, agregacao e conciliacao (NFSE-28+).

Contraparte do `nfse_import`. Enquanto ele responde "o que eu preciso emitir"
(a partir do extrato do banco), este responde "o que a Receita registra que eu
emiti" — e e o confronto entre os dois que revela cliente que pagou e nao teve
nota, e nota emitida sem pagamento correspondente.

O motivo imediato e o total emitido no mes, que hoje o operador soma a mao.

Camada de dominio: quebra o periodo, chama a raspagem
(`app/automation/nfse_emitidas.py`), grava o espelho e concilia. Os seletores
usados la vieram todos da recon assistida de 31/07/2026 — nenhum suposto, que e
a regra que evitou a falha silenciosa dos selects escondidos atras do Chosen
(ND-008).
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal

# Maior janela por consulta, em dias corridos e inclusiva nas duas pontas.
# Confirmado na recon: 01/07 a 31/07 (31 dias) foi aceito e devolveu 80
# registros, ou seja um mes civil cabe numa consulta so. O corte por mes do
# `dividir_periodo` existe pela LEITURA (o total e lido por mes), nao por
# limitacao do portal; o limite fica parametrizado porque o portal pode aperta-lo
# sem avisar, e nesse dia basta mudar este numero.
LIMITE_DIAS_PORTAL = 31


def _fim_do_mes(dia):
    return date(dia.year, dia.month, monthrange(dia.year, dia.month)[1])


def dividir_periodo(inicio, fim, limite_dias=LIMITE_DIAS_PORTAL):
    """Quebra um intervalo nas consultas que o portal aceita.

    Devolve uma lista de `(inicio, fim)`, em ordem, cobrindo o intervalo pedido
    inteiro, sem sobreposicao e sem buraco.

    Corta por **mes civil** e nao em blocos corridos de N dias porque o
    resultado e lido por mes: o total de julho tem de sair de uma consulta que
    comeca em 01/07 e termina em 31/07, senao a soma do mes viria repartida
    entre dois blocos e o operador teria de somar de novo — que e exatamente o
    trabalho manual que esta feature elimina.

    Se um mes civil ainda estourar o limite do portal (o caso de 31 dias contra
    um corte de 30), o mes e subdividido. Sao poucas linhas a mais e tornam o
    codigo correto qualquer que seja o limite real, que so a recon confirma.
    """
    if inicio > fim:
        raise ValueError('A data inicial nao pode ser depois da final.')
    if limite_dias < 1:
        raise ValueError('O limite de dias precisa ser positivo.')

    blocos = []
    atual = inicio
    while atual <= fim:
        # nunca passa do fim do mes civil nem do fim do periodo pedido
        limite_mes = min(_fim_do_mes(atual), fim)
        while atual <= limite_mes:
            # -1 porque o intervalo e inclusivo nas duas pontas: de 01 a 31 sao
            # 31 dias, nao 30
            fim_bloco = min(limite_mes, atual + timedelta(days=limite_dias - 1))
            blocos.append((atual, fim_bloco))
            atual = fim_bloco + timedelta(days=1)
    return blocos


def competencia_do_bloco(inicio):
    """'MM/AAAA' do mes a que o bloco pertence — a chave de agregacao.

    Mesmo formato usado pela `NotaNfse.competencia`, para os dois lados poderem
    ser confrontados sem conversao no meio."""
    return f'{inicio.month:02d}/{inicio.year}'


# --- consulta ao portal ----------------------------------------------------

def consultar(inicio, fim, execution_id=None):
    """Consulta o portal no periodo e grava o espelho das notas emitidas.

    Percorre bloco a bloco (`dividir_periodo`), pagina a pagina, e faz UPSERT
    pela chave de acesso: reconsultar o mesmo mes atualiza as linhas em vez de
    duplicar o total — a operacao e idempotente, e precisa ser, porque o
    operador vai reconsultar para conferir.

    Usa a sessao ja aberta (`nfse_session.SESSAO`), a mesma do preenchimento:
    o certificado e pedido uma vez por sessao, nao uma por consulta.

    Devolve `{'periodo', 'blocos', 'lidas', 'novas', 'atualizadas'}`.
    """
    from app import db
    from app.automation import nfse_emitidas as automacao
    from app.models import NotaEmitidaNfse
    from app.services.execution_logger import log_event
    from app.services.nfse_session import SESSAO

    blocos = dividir_periodo(inicio, fim)
    driver = SESSAO.garantir()

    lidas = []
    for comeco, termino in blocos:
        log_event('nfse_emitidas_bloco', inicio=str(comeco), fim=str(termino),
                  execution_id=execution_id)
        lidas.extend(automacao.listar_periodo(
            driver, comeco, termino,
            log=lambda evento, **campos: log_event(
                evento, execution_id=execution_id, **campos)))

    novas = atualizadas = 0
    existentes = {n.chave: n for n in NotaEmitidaNfse.query.filter(
        NotaEmitidaNfse.chave.in_([linha.chave for linha in lidas])).all()} if lidas else {}

    for linha in lidas:
        registro = existentes.get(linha.chave)
        if registro is None:
            registro = NotaEmitidaNfse(chave=linha.chave)
            db.session.add(registro)
            novas += 1
        else:
            atualizadas += 1
        registro.data_geracao = linha.data_geracao
        registro.competencia_dps = linha.competencia or None
        registro.documento = linha.documento or None
        registro.nome_tomador = linha.nome_tomador or None
        registro.municipio = linha.municipio or None
        registro.valor = linha.valor
        registro.situacao = linha.situacao or None
        registro.consultado_em = datetime.now()

    db.session.commit()
    conciliar()

    log_event('nfse_emitidas_consulta_ok', lidas=len(lidas), novas=novas,
              atualizadas=atualizadas, execution_id=execution_id)
    return {
        'periodo': (inicio, fim),
        'blocos': len(blocos),
        'lidas': len(lidas),
        'novas': novas,
        'atualizadas': atualizadas,
    }


# --- agregacao e conciliacao ------------------------------------------------

def mes_de(dia):
    """'MM/AAAA' de uma data. None quando nao ha data."""
    return f'{dia.month:02d}/{dia.year}' if dia else None


def _quando(nota):
    """Data que situa a linha do extrato no tempo, para desempate."""
    return nota.data_pagamento or nota.vencimento


def _distancia(nota, emitida):
    """Dias entre o pagamento e a geracao da nota. Grande quando falta data."""
    quando = _quando(nota)
    if quando is None or emitida.data_geracao is None:
        return 10 ** 6
    return abs((emitida.data_geracao - quando).days)


# Janela para aceitar um par que casa por documento mas NAO por valor. 75 dias
# cobre com folga o ciclo normal (paga-se em julho o honorario de junho, e a
# nota sai no fim de julho) sem alcancar o mesmo cliente dois meses adiante.
JANELA_DIAS = 75


def conciliar(competencia=None):
    """Liga cada nota emitida a linha do extrato que a originou.

    **A chave NAO e a competencia** (ND-027). `NotaNfse.competencia` e o mes de
    REFERENCIA do honorario; `NotaEmitidaNfse.competencia_dps` e o mes da
    EMISSAO. O cliente paga em julho o honorario de junho, entao os dois
    divergem no caso normal — casa-los acusava "sem nota" para quase tudo.

    O que de fato identifica a mesma nota nos dois lados e **documento +
    valor**. Duas passadas:

    1. documento + valor EXATO — o caso comum, e discrimina bem: um cliente com
       tres notas no mes tem tres valores distintos;
    2. o que sobrou, por documento dentro de uma janela de dias — e assim que
       "mesma nota, valor diferente" continua detectavel. Sem a segunda passada,
       um valor divergente apareceria como duas coisas (linha sem nota E nota
       sem linha) em vez de uma.

    Em ambas, so liga quando o par e INEQUIVOCO; havendo mais de um candidato,
    vence o mais proximo no tempo, e havendo empate de proximidade nao liga
    nenhum — inventar correspondencia que ninguem conferiu e pior que deixar
    aparecer como divergencia.

    `competencia` restringe o lado do EXTRATO (mes de referencia); as notas
    emitidas sao consideradas todas, sempre, porque a nota de junho pode ter
    saido em julho — que e justamente o caso que quebrava antes.
    """
    from app import db
    from app.models import NotaEmitidaNfse, NotaNfse, SituacaoNotaEmitida

    emitidas = [e for e in NotaEmitidaNfse.query.all()
                if e.situacao == SituacaoNotaEmitida.GERADA and e.documento]
    if not emitidas:
        return 0

    consulta = NotaNfse.query
    if competencia:
        consulta = consulta.filter_by(competencia=competencia)
    notas = [n for n in consulta.all() if n.documento]

    anterior = {e.id: e.nota_id for e in emitidas}
    for emitida in emitidas:
        emitida.nota_id = None

    tomadas = set()

    def _casar(candidatas_de):
        for emitida in emitidas:
            if emitida.nota_id is not None:
                continue
            candidatas = [n for n in candidatas_de(emitida) if n.id not in tomadas]
            if not candidatas:
                continue
            if len(candidatas) > 1:
                candidatas.sort(key=lambda n: _distancia(n, emitida))
                # empate de proximidade: nao ha como escolher com honestidade
                if _distancia(candidatas[0], emitida) == _distancia(candidatas[1], emitida):
                    continue
            emitida.nota_id = candidatas[0].id
            tomadas.add(candidatas[0].id)

    _casar(lambda e: [n for n in notas
                      if n.documento == e.documento
                      and n.valor_final is not None and e.valor is not None
                      and Decimal(n.valor_final) == Decimal(e.valor)])

    _casar(lambda e: [n for n in notas
                      if n.documento == e.documento
                      and _distancia(n, e) <= JANELA_DIAS])

    mudou = sum(1 for e in emitidas if anterior.get(e.id) != e.nota_id)
    db.session.commit()
    return mudou


def resumo(mes_geracao):
    """Total EMITIDO no mes — as notas GERADAS nele.

    O mes sai da `data_geracao`, que e o fato, e nao da competencia do DPS: sao
    quase sempre iguais (a automacao preenche o DPS com hoje), mas uma nota
    emitida a mao pode ter data de competencia diferente, e o que o operador
    quer somar e "o que saiu neste mes".

    `total` conta apenas o que e comprovadamente `GERADA`. Notas em qualquer
    outra situacao entram em `outras_situacoes`, separadas e visiveis: os
    codigos de cancelada/substituida nao apareceram na recon, e somar ou
    descartar por adivinhacao erraria um total fiscal nos dois sentidos."""
    from app.models import NotaEmitidaNfse, SituacaoNotaEmitida

    emitidas = [e for e in NotaEmitidaNfse.query.all()
                if mes_de(e.data_geracao) == mes_geracao]
    geradas = [n for n in emitidas if n.situacao == SituacaoNotaEmitida.GERADA]

    contagem = {}
    for nota in emitidas:
        if nota.situacao == SituacaoNotaEmitida.GERADA:
            continue
        chave = nota.situacao or '(sem situação)'
        contagem[chave] = contagem.get(chave, 0) + 1

    return {
        'mes': mes_geracao,
        'quantidade': len(geradas),
        'total': sum((n.valor for n in geradas if n.valor is not None), Decimal(0)),
        'outras_situacoes': contagem,
        'consultado_em': max((n.consultado_em for n in emitidas), default=None),
    }


def divergencias(competencia):
    """O que nao bate, nos dois sentidos, para um mes de REFERENCIA.

    `competencia` e o mes do honorario (o lado do extrato), nao o da emissao: a
    pergunta e "das linhas de junho, quais ficaram sem nota?", e a nota de junho
    pode ter saido em julho.

    - `sem_nota`: linha do extrato que devia virar nota e nao achou par em nota
      emitida NENHUMA — cliente pagou e ficou sem nota;
    - `sem_extrato`: nota emitida cuja linha nao foi identificada, entre as
      GERADAS no periodo coberto por essa competencia;
    - `valor_diferente`: par encontrado pela segunda passada, valores distintos.
    """
    from app.models import NotaEmitidaNfse, NotaNfse, SituacaoNotaEmitida, StatusNotaNfse

    notas = NotaNfse.query.filter_by(competencia=competencia).all()

    # Status que significam "esta linha nao deveria virar nota": cobrar por ela
    # seria falso alarme.
    ignorados = (StatusNotaNfse.CANCELADA, StatusNotaNfse.AGRUPADA,
                 StatusNotaNfse.INVALIDA, StatusNotaNfse.DUPLICATA)
    esperadas = [n for n in notas if n.status not in ignorados]
    por_id = {n.id: n for n in notas}

    emitidas = [e for e in NotaEmitidaNfse.query.all()
                if e.situacao == SituacaoNotaEmitida.GERADA]
    ligadas = {e.nota_id for e in emitidas if e.nota_id}

    valor_diferente = []
    for emitida in emitidas:
        nota = por_id.get(emitida.nota_id)
        if nota is None or nota.valor_final is None or emitida.valor is None:
            continue
        if Decimal(nota.valor_final) != Decimal(emitida.valor):
            valor_diferente.append((nota, emitida))

    # "Nota sem linha" so pode ser afirmada onde HA extrato importado.
    #
    # Sem este recorte a lista fica inutil: nos dados reais ela acusou 104
    # orfas, quase todas notas de periodos cujo extrato nunca foi importado — o
    # sistema chamava de "sem pagamento identificado" o que era, na verdade,
    # "nao tenho como saber". Uma lista assim ensina o operador a ignora-la, que
    # e o pior desfecho possivel para um painel de conferencia.
    janela = _janela_coberta()
    sem_extrato = [e for e in emitidas
                   if not e.nota_id and _coberta(janela, e.data_geracao)]

    return {
        'sem_nota': [n for n in esperadas if n.id not in ligadas],
        'sem_extrato': sem_extrato,
        'nao_conferiveis': sum(1 for e in emitidas
                               if not e.nota_id and not _coberta(janela, e.data_geracao)),
        'valor_diferente': valor_diferente,
    }


def _janela_coberta():
    """Periodo em que ha extrato importado — de TODOS os lotes, nao so os da
    competencia consultada: a pergunta "esta nota tem pagamento?" e global.

    A margem e assimetrica de proposito. No fim, `JANELA_DIAS`, porque a nota
    sai DEPOIS do pagamento. No comeco, nenhuma: uma nota gerada antes do
    pagamento mais antigo que conhecemos nao pode corresponder a pagamento
    nenhum que tenhamos — inclui-la acusaria como orfa toda nota de todo mes
    anterior a primeira importacao. Nos dados reais era essa margem que
    produzia 104 falsas orfas."""
    from app.models import NotaNfse

    datas = [d for d in (_quando(n) for n in NotaNfse.query.all()) if d]
    if not datas:
        return None
    return (min(datas), max(datas) + timedelta(days=JANELA_DIAS))


def _coberta(janela, dia):
    """A data cai onde ha extrato para comparar? Sem janela, nada e conferivel."""
    if janela is None or dia is None:
        return False
    return janela[0] <= dia <= janela[1]
