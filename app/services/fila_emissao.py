"""Fila durável de emissão proativa (spec 02, AD-010).

Camada de durabilidade sobre `TarefaEmissao`: enfileira alvos de forma
idempotente, reconcilia órfãs após restart e resolve retry por item. Espelha o
par mutação + commit/rollback de `certidao_service`. NÃO reimplementa estado de
lote (isso é `batch_engine`): aqui só vive a fila persistente.
"""
from datetime import datetime, timedelta

from app import db
from app.errors import ErrorType, _CATALOGO, map_exception_to_error_type
from app.models import Certidao, Empresa, TarefaEmissao
from app.services.execution_logger import log_event

# Estados em que uma tarefa ainda representa trabalho ativo (não terminal).
STATUS_ATIVOS = ('pendente', 'rodando', 'retry')
# Estados prontos para o worker pegar num ciclo do agendador.
STATUS_ELEGIVEIS = ('pendente', 'retry')
MAX_TENTATIVAS_PADRAO = 3
# Uma tarefa 'rodando' há mais que isto é considerada órfã (processo travado).
TIMEOUT_ORFA_HORAS = 6


def _tipo_valor(tipo):
    return tipo.value if hasattr(tipo, 'value') else str(tipo)


def _commit(tarefa):
    try:
        db.session.commit()
        return True
    except Exception as exc:
        db.session.rollback()
        log_event('tarefa_emissao_commit_falhou', level='WARNING',
                  certidao_id=getattr(tarefa, 'certidao_id', None), error=str(exc))
        return False


def tarefa_ativa(certidao_id):
    """Tarefa não-terminal (pendente/rodando/retry) da certidão, se houver.

    Tarefas terminais (ok/falha) NÃO contam — permitem uma nova tarefa em outro
    dia (histórico de renovações do mesmo alvo)."""
    return (TarefaEmissao.query
            .filter(TarefaEmissao.certidao_id == certidao_id)
            .filter(TarefaEmissao.status.in_(STATUS_ATIVOS))
            .order_by(TarefaEmissao.id.desc())
            .first())


def enfileirar(certidao_id, tipo, *, execution_id=None):
    """Cria uma `TarefaEmissao` pendente para a certidão (AC P1.1).

    Idempotente: se já há tarefa ativa (pendente/rodando/retry) para a mesma
    certidão, NÃO duplica e retorna None (AC P1.5 — superset do "no dia", cobre
    também um retry pendente). Retorna a tarefa criada em sucesso; None em
    dedup, certidão inexistente ou falha de commit."""
    if tarefa_ativa(certidao_id) is not None:
        return None
    certidao = db.session.get(Certidao, certidao_id)
    if certidao is None:
        return None
    tarefa = TarefaEmissao(
        tipo=_tipo_valor(tipo),
        empresa_id=certidao.empresa_id,
        certidao_id=certidao_id,
        status='pendente',
        execution_id=execution_id,
    )
    db.session.add(tarefa)
    return tarefa if _commit(tarefa) else None


def enfileirar_a_vencer(alvos_por_tipo, *, execution_id=None):
    """Enfileira (idempotente) os alvos "a vencer" por tipo.

    `alvos_por_tipo`: dict {tipo_value: [certidao_id, ...]} calculado pelo
    chamador (o job usa os `calc_targets` por tipo, com os filtros de RS/
    municipal e a janela `get_a_vencer_dias(tipo)`). Manter o cálculo fora daqui
    evita acoplar a fila às rotas (ver Risks do design). Retorna
    {tipo_value: [ids efetivamente enfileirados]}."""
    enfileiradas = {}
    for tipo, ids in alvos_por_tipo.items():
        tv = _tipo_valor(tipo)
        criadas = [cid for cid in ids
                   if enfileirar(cid, tv, execution_id=execution_id) is not None]
        enfileiradas[tv] = criadas
    return enfileiradas


def tarefas_elegiveis(tipo):
    """Tarefas prontas para rodar de um tipo (pendente ou retry), mais antigas
    primeiro — o worker do agendador as consome em ordem."""
    return (TarefaEmissao.query
            .filter(TarefaEmissao.tipo == _tipo_valor(tipo))
            .filter(TarefaEmissao.status.in_(STATUS_ELEGIVEIS))
            .order_by(TarefaEmissao.agendada_em)
            .all())


def marcar_rodando(tarefa, *, execution_id=None):
    """Transição pendente/retry → rodando (AC P1.2)."""
    tarefa.status = 'rodando'
    tarefa.iniciada_em = datetime.now()
    if execution_id:
        tarefa.execution_id = execution_id
    return _commit(tarefa)


def marcar_ok(tarefa):
    """Transição rodando → ok (AC P1.2)."""
    tarefa.status = 'ok'
    tarefa.concluida_em = datetime.now()
    tarefa.erro = None
    return _commit(tarefa)


def marcar_falha(tarefa, erro):
    """Transição rodando → falha definitiva, persistindo o erro (AC P1.2)."""
    tarefa.status = 'falha'
    tarefa.concluida_em = datetime.now()
    tarefa.erro = (erro or '')[:500]
    return _commit(tarefa)


def resolver_falha(tarefa, erro, *, max_tentativas=MAX_TENTATIVAS_PADRAO):
    """Incrementa tentativas e decide retry vs falha definitiva (AC P1.4).

    `retry` enquanto `tentativas < max_tentativas`; ao esgotar, `falha`
    definitiva. Retorna o status final ('retry' | 'falha')."""
    tarefa.tentativas = (tarefa.tentativas or 0) + 1
    tarefa.erro = (erro or '')[:500]
    if tarefa.tentativas < max_tentativas:
        tarefa.status = 'retry'
        tarefa.iniciada_em = None
    else:
        tarefa.status = 'falha'
        tarefa.concluida_em = datetime.now()
    _commit(tarefa)
    return tarefa.status


# --- dead-letter: o que morreu na fila (spec 09, RESOP-01) ------------------

def tarefas_falhas(limite=200):
    """Tarefas que esgotaram as tentativas, mais recentes primeiro.

    So `falha`: `retry` e `pendente` sao trabalho vivo, e misturar os dois
    transformaria a tela de "o que morreu" na fila inteira."""
    return (TarefaEmissao.query
            .filter(TarefaEmissao.status == 'falha')
            .order_by(TarefaEmissao.concluida_em.desc(), TarefaEmissao.id.desc())
            .limit(limite)
            .all())


def motivo_da_tarefa(tarefa):
    """Motivo da falha como `ErrorType`, classificando a mensagem gravada.

    Reusa o catalogo de `app/errors.py` (a funcao normaliza com `str(exc)`, logo
    aceita a string do campo `erro`): nenhuma tabela de palavras-chave nova, e as
    falhas antigas — anteriores a esta feature — saem classificadas do mesmo
    jeito, sem backfill."""
    return map_exception_to_error_type(getattr(tarefa, 'erro', None) or '').value


def _nomes_das_empresas(tarefas):
    """{empresa_id: nome} numa unica query (sem N+1 na lista de falhas)."""
    ids = {t.empresa_id for t in tarefas if t.empresa_id}
    if not ids:
        return {}
    linhas = db.session.query(Empresa.id, Empresa.nome).filter(Empresa.id.in_(ids)).all()
    return dict(linhas)


def _item_falha(tarefa, nomes):
    return {
        'id': tarefa.id,
        'certidao_id': tarefa.certidao_id,
        'empresa_id': tarefa.empresa_id,
        'empresa': nomes.get(tarefa.empresa_id),
        'tipo': tarefa.tipo,
        'tentativas': tarefa.tentativas or 0,
        'erro': tarefa.erro,
        'concluida_em': tarefa.concluida_em.isoformat() if tarefa.concluida_em else None,
    }


def agrupar_falhas(limite=200):
    """Falhas agrupadas por motivo, com o titulo/acao do catalogo de erro —
    o operador le "Tempo esgotado", nao `TIMEOUT`. Grupos maiores primeiro."""
    tarefas = tarefas_falhas(limite=limite)
    nomes = _nomes_das_empresas(tarefas)
    grupos = {}
    for tarefa in tarefas:
        motivo = motivo_da_tarefa(tarefa)
        grupo = grupos.get(motivo)
        if grupo is None:
            titulo, _causa, acao, _rec = _CATALOGO[ErrorType(motivo)]
            grupo = grupos[motivo] = {
                'error_type': motivo, 'titulo': titulo, 'acao': acao,
                'total': 0, 'itens': [],
            }
        grupo['total'] += 1
        grupo['itens'].append(_item_falha(tarefa, nomes))
    return sorted(grupos.values(), key=lambda g: g['total'], reverse=True)


def reconciliar_orfas(*, apenas_timeout=False):
    """Recupera tarefas 'rodando' que ficaram órfãs (AC P1.3).

    No boot (`apenas_timeout=False`) toda 'rodando' vira 'pendente' — após um
    restart nada está de fato rodando. Como saneamento periódico
    (`apenas_timeout=True`) só recupera as presas além de `TIMEOUT_ORFA_HORAS`
    (processo travado), sem roubar uma tarefa genuinamente em curso. Retorna a
    contagem reconciliada."""
    q = TarefaEmissao.query.filter(TarefaEmissao.status == 'rodando')
    if apenas_timeout:
        limite = datetime.now() - timedelta(hours=TIMEOUT_ORFA_HORAS)
        q = q.filter((TarefaEmissao.iniciada_em.is_(None))
                     | (TarefaEmissao.iniciada_em < limite))
    orfas = q.all()
    for t in orfas:
        t.status = 'pendente'
        t.iniciada_em = None
    if orfas and not _commit(orfas[0]):
        return 0
    if orfas:
        log_event('tarefa_emissao_reconciliadas', quantidade=len(orfas))
    return len(orfas)
