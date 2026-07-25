"""Estado compartilhado dos lotes (FGTS, Estadual RS, Municipal).

Centraliza os locks e dicionários de estado antes definidos em routes.py, para
que rotas/workers e os módulos de emissão por tipo (automation/*) compartilhem
o mesmo objeto sem dependência circular.
"""
from threading import Lock

from app.services import batch_engine

FGTS_BATCH_LOCK = Lock()
RS_BATCH_LOCK = Lock()
MUNICIPAL_BATCH_LOCK = Lock()
TRABALHISTA_BATCH_LOCK = Lock()

FGTS_BATCH_STATE = batch_engine.batch_state_defaults()
RS_BATCH_STATE = batch_engine.batch_state_defaults()
MUNICIPAL_BATCH_STATE = batch_engine.batch_state_defaults()
TRABALHISTA_BATCH_STATE = batch_engine.batch_state_defaults()

EMISSAO_INDIVIDUAL_LOCK = Lock()
_EMISSAO_INDIVIDUAL_STATE = {'ativa': False}


def emissao_individual_ativa():
    with EMISSAO_INDIVIDUAL_LOCK:
        return _EMISSAO_INDIVIDUAL_STATE['ativa']


def marcar_emissao_individual(ativa):
    with EMISSAO_INDIVIDUAL_LOCK:
        _EMISSAO_INDIVIDUAL_STATE['ativa'] = bool(ativa)


def fgts_stop_requested():
    return FGTS_BATCH_STATE.get('stop_requested')


def rs_batch_stop_requested():
    return RS_BATCH_STATE.get('stop_requested')


def municipal_batch_stop_requested():
    return MUNICIPAL_BATCH_STATE.get('stop_requested')


def trabalhista_batch_stop_requested():
    return TRABALHISTA_BATCH_STATE.get('stop_requested')
