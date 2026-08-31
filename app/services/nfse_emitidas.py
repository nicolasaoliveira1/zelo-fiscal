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
    from app.models import ConsultaEmitidaNfse, NotaEmitidaNfse
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
    consulta = ConsultaEmitidaNfse(inicio=inicio, fim=fim)
    db.session.add(consulta)
    db.session.commit()

    log_event('nfse_emitidas_consulta_ok', lidas=len(lidas), novas=novas,
              atualizadas=atualizadas, consulta_id=consulta.id,
              execution_id=execution_id)
    return {
        'periodo': (inicio, fim),
        'blocos': len(blocos),
        'lidas': len(lidas),
        'novas': novas,
        'atualizadas': atualizadas,
        'consulta_id': consulta.id,
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


def _nota_elegivel(nota):
    """Diz se a linha ainda representa uma obrigação ativa de emissão.

    A líder de um agrupamento confirmado continua elegível e já carrega o
    `valor_final` líquido. A irmã absorvida é excluída pelo status e pelo
    ponteiro, o que também protege contra dados antigos que tenham apenas um
    dos dois campos preenchido.
    """
    from app.models import StatusNotaNfse

    ignorados = (StatusNotaNfse.CANCELADA, StatusNotaNfse.AGRUPADA,
                 StatusNotaNfse.INVALIDA, StatusNotaNfse.DUPLICATA)
    return (bool(nota.documento)
            and nota.status not in ignorados
            and nota.agrupada_em_id is None)


def _candidatas(emitida, notas, tomadas, por_valor):
    """Retorna candidatas livres para uma das duas passadas do avaliador."""
    candidatas = [n for n in notas if n.id not in tomadas
                  and n.documento == emitida.documento]
    if por_valor:
        candidatas = [n for n in candidatas
                      if n.valor_final is not None and emitida.valor is not None
                      and Decimal(n.valor_final) == Decimal(emitida.valor)]
    else:
        candidatas = [n for n in candidatas
                      if _distancia(n, emitida) <= JANELA_DIAS]
    return candidatas


def _avaliar_conciliacao():
    """Avalia todos os pares e devolve vínculos, empates e alterações.

    O resultado é único para persistência e para o painel. A ordem por id torna
    a escolha reproduzível quando há vários tomadores iguais; um empate de
    distância continua sem vínculo e vira uma ocorrência de `ambigua`.
    """
    from app.models import NotaEmitidaNfse, NotaNfse, SituacaoNotaEmitida

    todas_emitidas = NotaEmitidaNfse.query.order_by(NotaEmitidaNfse.id).all()
    emitidas = [e for e in todas_emitidas
                if e.situacao == SituacaoNotaEmitida.GERADA and e.documento]
    notas = [n for n in NotaNfse.query.order_by(NotaNfse.id).all()
             if _nota_elegivel(n)]

    anterior = {e.id: e.nota_id for e in todas_emitidas}
    # Limpa também vínculos de situações que deixaram de ser elegíveis.
    for emitida in todas_emitidas:
        emitida.nota_id = None

    tomadas = set()
    ambiguas = {}

    for por_valor in (True, False):
        for emitida in emitidas:
            if emitida.nota_id is not None or emitida.id in ambiguas:
                continue
            candidatas = _candidatas(emitida, notas, tomadas, por_valor)
            if not candidatas:
                continue

            candidatas.sort(key=lambda n: (_distancia(n, emitida), n.id))
            menor_distancia = _distancia(candidatas[0], emitida)
            empatadas = [n for n in candidatas
                         if _distancia(n, emitida) == menor_distancia]
            if len(empatadas) > 1:
                ambiguas[emitida.id] = {
                    'emitida': emitida,
                    'candidatas': empatadas,
                }
                continue

            escolhida = candidatas[0]
            emitida.nota_id = escolhida.id
            tomadas.add(escolhida.id)

    mudou = sum(1 for e in todas_emitidas if anterior.get(e.id) != e.nota_id)
    return {
        'emitidas': emitidas,
        'notas': notas,
        'ambiguas': list(ambiguas.values()),
        'mudou': mudou,
    }


def conciliar():
    """Liga notas emitidas e extrato sem consultar competência de referência.

    O casamento usa documento, valor, data e a janela de 75 dias. Estados que
    não representam uma obrigação ativa ficam fora, e empates não recebem
    `nota_id`.
    """
    from app import db
    from app.services.execution_logger import log_event

    resultado = _avaliar_conciliacao()
    db.session.commit()
    log_event(
        'nfse_emitidas_conciliacao',
        emitidas=len(resultado['emitidas']),
        vinculadas=sum(1 for e in resultado['emitidas'] if e.nota_id),
        ambiguas=len(resultado['ambiguas']),
        alteradas=resultado['mudou'],
    )
    return resultado['mudou']


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


def resumo_periodo(inicio, fim):
    """Total das notas GERADAS dentro do intervalo consultado, inclusive."""
    from app.models import NotaEmitidaNfse, SituacaoNotaEmitida

    emitidas = [e for e in NotaEmitidaNfse.query.all()
                if e.data_geracao is not None
                and inicio <= e.data_geracao <= fim]
    geradas = [e for e in emitidas if e.situacao == SituacaoNotaEmitida.GERADA]
    contagem = {}
    for nota in emitidas:
        if nota.situacao == SituacaoNotaEmitida.GERADA:
            continue
        chave = nota.situacao or '(sem situação)'
        contagem[chave] = contagem.get(chave, 0) + 1

    return {
        'inicio': inicio,
        'fim': fim,
        'quantidade': len(geradas),
        'total': sum((n.valor for n in geradas if n.valor is not None), Decimal(0)),
        'outras_situacoes': contagem,
        'consultado_em': max((n.consultado_em for n in emitidas), default=None),
    }


def ultima_consulta(mes=None, consulta_id=None):
    """Busca uma leitura completa persistida, sem inferir competência."""
    from app import db
    from app.models import ConsultaEmitidaNfse

    consulta = ConsultaEmitidaNfse.query
    if consulta_id is not None:
        return consulta.filter_by(id=consulta_id).first()
    if mes:
        mes_numero, ano = mes.split('/')
        consulta = consulta.filter(
            db.extract('month', ConsultaEmitidaNfse.inicio) == int(mes_numero),
            db.extract('year', ConsultaEmitidaNfse.inicio) == int(ano),
        )
    return consulta.order_by(
        ConsultaEmitidaNfse.consultado_em.desc(),
        ConsultaEmitidaNfse.id.desc()).first()


def divergencias(inicio, fim=None):
    """Classifica a conferência pelo intervalo exato da consulta.

    `sem_nota` usa pagamentos dentro do intervalo; `sem_extrato` usa emissões
    dentro dele. O casamento continua global para permitir emissão tardia: uma
    nota de julho pode encontrar pagamento de junho dentro da janela vigente.

    A forma antiga `divergencias('MM/AAAA')` permanece somente para não quebrar
    integrações legadas enquanto os consumidores migram para
    `divergencias(inicio, fim)`. O painel não a utiliza mais.
    """
    if fim is None:
        return _divergencias_competencia_legada(inicio)
    return _divergencias_intervalo(inicio, fim)


def _resultado_divergencias(resultado, notas, emitidas, janela):
    """Monta as categorias a partir da avaliação já persistida."""
    ambiguas = resultado['ambiguas']
    ids_ambigua = {item['emitida'].id for item in ambiguas}
    ids_candidatas_ambiguas = {
        nota.id for item in ambiguas for nota in item['candidatas']}
    ligadas = {e.nota_id for e in emitidas if e.nota_id}

    valor_diferente = []
    for emitida in emitidas:
        if emitida.id in ids_ambigua or not emitida.nota_id:
            continue
        nota = next((n for n in notas if n.id == emitida.nota_id), None)
        if (nota is not None and nota.valor_final is not None
                and emitida.valor is not None
                and Decimal(nota.valor_final) != Decimal(emitida.valor)):
            valor_diferente.append((nota, emitida))

    return {
        'sem_nota': [n for n in notas
                     if n.id not in ligadas
                     and n.id not in ids_candidatas_ambiguas],
        'sem_extrato': [e for e in emitidas
                        if not e.nota_id
                        and e.id not in ids_ambigua
                        and _coberta(janela, e.data_geracao)],
        'nao_conferiveis': sum(1 for e in emitidas
                               if not e.nota_id
                               and e.id not in ids_ambigua
                               and not _coberta(janela, e.data_geracao)),
        'valor_diferente': valor_diferente,
        'ambigua': ambiguas,
    }


def _divergencias_intervalo(inicio, fim):
    from app import db

    resultado = _avaliar_conciliacao()
    db.session.commit()
    notas = [n for n in resultado['notas']
             if n.data_pagamento is not None
             and inicio <= n.data_pagamento <= fim]
    emitidas = [e for e in resultado['emitidas']
                if e.data_geracao is not None
                and inicio <= e.data_geracao <= fim]
    # A cobertura é calculada sobre todas as linhas importadas, mas as
    # classificações continuam limitadas às datas desta consulta.
    janela = _janela_coberta()
    return _resultado_divergencias(resultado, notas, emitidas, janela)


def _divergencias_competencia_legada(competencia):
    """Compatibilidade temporária para chamadas antigas da aplicação."""
    from app import db

    resultado = _avaliar_conciliacao()
    db.session.commit()
    notas = [n for n in resultado['notas'] if n.competencia == competencia]
    emitidas = resultado['emitidas']
    return _resultado_divergencias(resultado, notas, emitidas, _janela_coberta())


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
