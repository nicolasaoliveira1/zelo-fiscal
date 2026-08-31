"""Proposta de agrupamento de lancamentos numa nota so (NFSE-27).

O extrato do Inter as vezes quebra um servico em varios Pix, e as vezes o
cliente paga a mais e recebe estorno. O caso real que originou isto: 684,00
("ALT. CONTRATO - PARTE") + 2.000,00 do mesmo cliente, e no dia seguinte um
estorno de (1.784,00). A nota certa e UMA, de 900,00, de alteracao contratual —
mas nenhuma das tres linhas, sozinha, diz isso.

**O sistema propoe, o operador confirma.** Nunca o contrario (ND-005). O
casamento e por nome aproximado entre lancamentos do banco; errar aqui nao e
bug de tela, e nota fiscal com valor errado, que se conserta com cancelamento
junto a prefeitura. Entao a proposta so calcula e mostra a conta — 684,00 +
2.000,00 - 1.784,00 — e espera.

Enquanto a proposta existe e nao foi respondida, as notas do grupo ficam FORA da
fila de emissao: emitir a entrada bruta de 2.000,00 enquanto ha um estorno de
1.784,00 pendurado nela e justamente o erro que a proposta existe para evitar.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from app.services.nfse_extrato_inter import normalizar_termo

# Quao parecidos dois nomes precisam ser para o sistema PROPOR que sao o mesmo
# cliente. Mais frouxo que o limiar de vinculo empresa->CNPJ (90 + gap de 10, em
# `nfse_import`) de proposito: la o desfecho e automatico e um erro emite nota
# no CNPJ errado; aqui o desfecho e uma pergunta na tela, e o custo de propor
# demais e o operador clicar "descartar". Errar para menos e pior — um estorno
# nao proposto vira nota emitida a maior.
LIMIAR_NOME_GRUPO = 80


def _score(a, b):
    from thefuzz import fuzz
    return fuzz.token_set_ratio(a, b)


def _valor(numero):
    return Decimal(numero or 0)


def _formatar(valor):
    """1784 -> '1.784,00'. Com separador de milhar: o detalhe do grupo e uma
    conta que o operador confere contra o extrato, e '2000,00' ao lado de
    '1.784,00' obriga a reler para ter certeza da ordem de grandeza."""
    return f'{_valor(valor):,.2f}'.replace(',', '\x00').replace('.', ',').replace('\x00', '.')


def _elegivel(nota):
    """Nota que ainda pode entrar num grupo.

    Fora: o que ja e documento fiscal ou decisao tomada (emitida, aguardando
    confirmacao no portal, cancelada, agrupada) e o que nem linha valida e."""
    from app.models import StatusNotaNfse
    return nota.status not in (
        StatusNotaNfse.EMITIDA,
        StatusNotaNfse.AGUARDANDO_CONFIRMACAO,
        StatusNotaNfse.PREENCHENDO,
        StatusNotaNfse.CANCELADA,
        StatusNotaNfse.AGRUPADA,
        StatusNotaNfse.INVALIDA,
    )


def _mesma_competencia(notas):
    return len({nota.competencia for nota in notas}) == 1


def propor(notas, saidas):
    """Monta as propostas de agrupamento de um lote recem-importado.

    Marca as notas envolvidas com um `grupo_sugerido` comum e grava, na
    primeira delas, o valor liquido e a conta por extenso. Nao persiste nada por
    conta propria: roda dentro da transacao do `importar()`.

    Duas situacoes geram proposta, e as duas exigem mais de uma linha:

    1. **ha estorno** — uma saida do extrato cujo nome casa com o tomador. Aqui
       basta UMA entrada: a nota liquida difere da entrada bruta;
    2. **o mesmo tomador pagou em varias entradas** no mesmo mes E pelo menos
       uma delas nomeia um servico. Sem servico nomeado, varias entradas do
       mesmo cliente no mes sao apenas honorarios de clientes diferentes que o
       fuzzy juntou, ou pagamentos legitimamente separados — propor agrupamento
       ali so atrapalharia.

    Devolve a lista de tokens de grupo criados.
    """
    candidatas = [nota for nota in notas if _elegivel(nota) and nota.nome_csv_norm]
    if not candidatas:
        return []

    tokens = []
    usadas = set()

    for indice, nota in enumerate(candidatas):
        if id(nota) in usadas:
            continue

        irmas = [nota]
        for outra in candidatas[indice + 1:]:
            if id(outra) in usadas:
                continue
            if _score(nota.nome_csv_norm, outra.nome_csv_norm) >= LIMIAR_NOME_GRUPO:
                irmas.append(outra)

        estornos = [saida for saida in saidas
                    if _score(nota.nome_csv_norm,
                              normalizar_termo(saida.nome or saida.descricao))
                    >= LIMIAR_NOME_GRUPO]

        tem_servico = any(irma.descricao_servico for irma in irmas)
        if estornos:
            pass                       # 1) estorno sozinho ja justifica
        elif len(irmas) > 1 and tem_servico and _mesma_competencia(irmas):
            pass                       # 2) varias entradas + servico nomeado
        else:
            continue

        liquido = (sum((_valor(irma.valor_final) for irma in irmas), Decimal(0))
                   - sum((_valor(estorno.saida) for estorno in estornos), Decimal(0)))
        # Liquido nao positivo = o estorno anulou (ou passou) o recebimento. Nao
        # ha nota a emitir, e propor uma de valor zero ou negativo seria pior que
        # nao propor nada: o operador cancela as linhas.
        if liquido <= 0:
            continue

        token = uuid.uuid4().hex[:32]
        for irma in irmas:
            irma.grupo_sugerido = token
            usadas.add(id(irma))

        lider = irmas[0]
        lider.grupo_valor_liquido = liquido
        lider.grupo_detalhe = _detalhe(irmas, estornos, liquido)
        # Descricao sugerida para a nota juntada: o servico escrito em ALGUMA
        # das linhas, que nao e necessariamente a primeira — no caso real
        # "ALT. CONTRATO" veio na linha de 684,00 e nao na de 2.000,00. Fica
        # editavel na tela; e so uma sugestao.
        lider.grupo_descricao = next(
            (irma.descricao_servico for irma in irmas if irma.descricao_servico), None)
        tokens.append(token)

    return tokens


def _detalhe(irmas, estornos, liquido):
    """A conta por extenso, para o operador conferir antes de aceitar."""
    partes = ' + '.join(_formatar(irma.valor_final) for irma in irmas)
    for estorno in estornos:
        quando = estorno.data.strftime('%d/%m') if estorno.data else 'estorno'
        partes += f' - {_formatar(estorno.saida)} (estorno {quando})'
    return f'{partes} = {_formatar(liquido)}'[:300]


def _status_apos_grupo(nota):
    """Status da nota depois de responder a proposta.

    O `recalcular_status` e puro sobre as pendencias e ignora o status atual,
    entao aplica-lo cego aqui promoveria uma DUPLICATA a PRONTA — a nota
    escaparia da trava so por ter passado por um agrupamento. Estado ja decidido
    fica como esta; so o que estava pendente e recalculado."""
    from app.models import StatusNotaNfse
    from app.services import nfse_import

    if nota.status in (StatusNotaNfse.DUPLICATA, StatusNotaNfse.EMITIDA,
                       StatusNotaNfse.CANCELADA, StatusNotaNfse.AGRUPADA,
                       StatusNotaNfse.INVALIDA, StatusNotaNfse.FALHA,
                       StatusNotaNfse.PREENCHENDO,
                       StatusNotaNfse.AGUARDANDO_CONFIRMACAO):
        return nota.status
    return nfse_import.recalcular_status(nota)


def tem_proposta_pendente(nota):
    """True enquanto a proposta desta nota espera resposta do operador.

    E o que segura a nota fora da fila. Consultado pelo `nfse_service` e pelo
    `nfse_lote`, que sao os dois pontos que decidem o que entra na emissao.

    Confirmado e descartado saem os dois daqui, por caminhos opostos: no
    descartado nao ha mais grupo, no confirmado o grupo virou uma nota que
    precisa poder ser emitida."""
    return (bool(nota.grupo_sugerido)
            and not nota.grupo_descartado
            and not nota.grupo_confirmado)


def foi_agrupada(nota):
    """True quando o agrupamento ja foi aplicado e ainda da para desfazer."""
    return bool(nota.grupo_sugerido) and bool(nota.grupo_confirmado)


def _notas_do_grupo(token):
    from app.models import NotaNfse
    return (NotaNfse.query.filter_by(grupo_sugerido=token)
            .order_by(NotaNfse.id).all())


def _lider(notas):
    return next((nota for nota in notas if nota.grupo_valor_liquido is not None),
                notas[0])


def confirmar(token, valor=None, descricao=None):
    """Aplica o agrupamento: uma nota sobrevive com o liquido, as outras saem.

    A sobrevivente e a lider da proposta. A descricao que ela leva vem, nesta
    ordem: o que o operador digitou na faixa, a sugestao gravada na proposta
    (o servico escrito em alguma das linhas), ou o que a lider ja tinha.

    `valor` permite corrigir o liquido; e o UNICO ponto do sistema em que o
    valor da nota nao vem direto do extrato, e por isso fica marcado com
    `valor_ajustado`.

    NADA e destruido: `valor_extrato` guarda o valor do banco e as colunas
    `grupo_*_anterior` guardam o retrato da lider, para o `desfazer` devolver
    exatamente o que era. O token TAMBEM sobrevive — sem ele nao haveria como
    reencontrar as irmas.
    """
    from app import db
    from app.models import StatusNotaNfse

    notas = _notas_do_grupo(token)
    if not notas:
        return None
    if any(nota.grupo_confirmado for nota in notas):
        raise ValueError('Este agrupamento ja foi aplicado.')

    lider = _lider(notas)
    liquido = _valor(valor) if valor is not None else _valor(lider.grupo_valor_liquido)
    if liquido <= 0:
        raise ValueError('O valor da nota agrupada precisa ser maior que zero.')

    escolhida = (descricao or '').strip() or lider.grupo_descricao

    # Retrato antes de mexer: e o que o desfazer devolve.
    lider.grupo_descricao_anterior = lider.descricao_servico
    lider.grupo_pendente_anterior = lider.descricao_pendente

    for nota in notas:
        nota.grupo_confirmado = True
        if nota is lider:
            continue
        nota.status = StatusNotaNfse.AGRUPADA
        nota.agrupada_em_id = lider.id

    lider.valor_final = liquido
    # "ajustado a mao" = o numero DIFERE do que o sistema calculou, e nao "veio
    # um valor na requisicao": a tela manda o campo sempre preenchido com a
    # sugestao, e marcar por isso poria o selo de valor mexido em toda nota
    # agrupada — justamente onde ele precisa significar alguma coisa.
    lider.valor_ajustado = liquido != _valor(lider.grupo_valor_liquido)
    lider.grupo_descricao = escolhida
    if escolhida:
        lider.descricao_servico = escolhida
        # a descricao resolve a nota: a pendencia, se havia, acabou
        lider.descricao_pendente = False
    lider.status = _status_apos_grupo(lider)

    # O agrupamento e a conciliação precisam ser confirmados juntos. O avaliador
    # consulta o estado novo na transação corrente; se falhar, a rota desfaz
    # tudo com rollback e não publica um agrupamento sem conferência atualizada.
    from app.services import nfse_emitidas
    nfse_emitidas.conciliar(persistir=False)
    db.session.commit()
    return lider


def desfazer(token):
    """Volta atras num agrupamento aplicado, devolvendo a proposta em aberto.

    O oposto exato do `confirmar`: o valor da lider volta ao do extrato, a
    descricao e a pendencia voltam ao retrato, as irmas saem de `agrupada` e a
    proposta fica de novo esperando resposta — nao descartada, porque desfazer
    nao e recusar; o operador pode querer juntar de novo com outro valor.

    O valor volta de `valor_extrato` e nao de um retrato proprio porque
    `valor_extrato` nunca muda: e o numero que esta no PDF do banco, e nenhuma
    sequencia de juntar/desfazer pode faze-lo derivar.
    """
    from app import db

    notas = _notas_do_grupo(token)
    if not notas:
        return None
    if not any(nota.grupo_confirmado for nota in notas):
        raise ValueError('Este agrupamento nao foi aplicado; nao ha o que desfazer.')

    lider = _lider(notas)

    for nota in notas:
        nota.grupo_confirmado = False
        if nota is lider:
            continue
        nota.agrupada_em_id = None
        nota.status = _status_ao_sair_do_grupo(nota)

    if lider.valor_extrato is not None:
        lider.valor_final = lider.valor_extrato
    lider.valor_ajustado = False
    lider.descricao_servico = lider.grupo_descricao_anterior
    if lider.grupo_pendente_anterior is not None:
        lider.descricao_pendente = lider.grupo_pendente_anterior
    lider.grupo_descricao_anterior = None
    lider.grupo_pendente_anterior = None
    lider.status = _status_ao_sair_do_grupo(lider)

    from app.services import nfse_emitidas
    # Mantém a alteração do grupo e a conciliação na mesma transação lógica.
    nfse_emitidas.conciliar(persistir=False)
    db.session.commit()
    return lider


def _status_ao_sair_do_grupo(nota):
    """`recalcular_status` sem o guarda de estado ja decidido.

    Diferente do `_status_apos_grupo`: ali o objetivo e preservar o que ja foi
    decidido; aqui a nota esta SAINDO de um estado aplicado (`agrupada`, ou uma
    lider com valor de grupo) e precisa mesmo ser recalculada."""
    from app.models import StatusNotaNfse
    from app.services import nfse_import

    if nota.status in (StatusNotaNfse.EMITIDA, StatusNotaNfse.CANCELADA,
                       StatusNotaNfse.INVALIDA, StatusNotaNfse.PREENCHENDO,
                       StatusNotaNfse.AGUARDANDO_CONFIRMACAO):
        return nota.status
    return nfse_import.recalcular_status(nota)


def descartar(token):
    """Recusa a proposta: cada linha segue como nota propria.

    `grupo_descartado` fica marcado em vez de o token ser apagado para a tela
    poder dizer "havia uma proposta e voce recusou" — sem isso, o proximo import
    do mesmo periodo proporia tudo de novo e a recusa nao teria memoria."""
    from app import db
    from app.models import NotaNfse

    notas = NotaNfse.query.filter_by(grupo_sugerido=token).all()
    if not notas:
        return []

    for nota in notas:
        nota.grupo_descartado = True
        nota.grupo_valor_liquido = None
        nota.status = _status_apos_grupo(nota)

    db.session.commit()
    return notas
