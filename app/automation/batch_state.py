"""Estado compartilhado dos lotes (FGTS, Estadual RS, Municipal, NFSe).

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
NFSE_BATCH_LOCK = Lock()

FGTS_BATCH_STATE = batch_engine.batch_state_defaults()
RS_BATCH_STATE = batch_engine.batch_state_defaults()
MUNICIPAL_BATCH_STATE = batch_engine.batch_state_defaults()
TRABALHISTA_BATCH_STATE = batch_engine.batch_state_defaults()
NFSE_BATCH_STATE = batch_engine.batch_state_defaults()

EMISSAO_INDIVIDUAL_LOCK = Lock()
_EMISSAO_INDIVIDUAL_STATE = {'ativa': False}


def emissao_individual_ativa():
    with EMISSAO_INDIVIDUAL_LOCK:
        return _EMISSAO_INDIVIDUAL_STATE['ativa']


def marcar_emissao_individual(ativa):
    with EMISSAO_INDIVIDUAL_LOCK:
        _EMISSAO_INDIVIDUAL_STATE['ativa'] = bool(ativa)


# Opcoes do lote de NFSe. Ficam FORA de NFSE_BATCH_STATE de proposito:
# `init_batch_run` chama `reset_batch_state` e dispara o worker dentro do mesmo
# lock, entao qualquer chave escrita no estado antes de iniciar seria apagada, e
# escrever depois correria com o worker ja lendo.
NFSE_OPCOES_LOCK = Lock()
_NFSE_BATCH_OPCOES = {'modo': 'lote', 'ignorar_aliquota': False}


def nfse_batch_opcoes():
    with NFSE_OPCOES_LOCK:
        return dict(_NFSE_BATCH_OPCOES)


def definir_nfse_batch_opcoes(modo, ignorar_aliquota=False):
    with NFSE_OPCOES_LOCK:
        _NFSE_BATCH_OPCOES['modo'] = modo
        _NFSE_BATCH_OPCOES['ignorar_aliquota'] = bool(ignorar_aliquota)


def nfse_batch_stop_requested():
    return NFSE_BATCH_STATE.get('stop_requested')


def fgts_stop_requested():
    return FGTS_BATCH_STATE.get('stop_requested')


def rs_batch_stop_requested():
    return RS_BATCH_STATE.get('stop_requested')


def municipal_batch_stop_requested():
    return MUNICIPAL_BATCH_STATE.get('stop_requested')


def trabalhista_batch_stop_requested():
    return TRABALHISTA_BATCH_STATE.get('stop_requested')
