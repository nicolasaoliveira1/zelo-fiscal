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
MANIF_BATCH_LOCK = Lock()

FGTS_BATCH_STATE = batch_engine.batch_state_defaults()
RS_BATCH_STATE = batch_engine.batch_state_defaults()
MUNICIPAL_BATCH_STATE = batch_engine.batch_state_defaults()
TRABALHISTA_BATCH_STATE = batch_engine.batch_state_defaults()
NFSE_BATCH_STATE = batch_engine.batch_state_defaults()
MANIF_BATCH_STATE = batch_engine.batch_state_defaults()

EMISSAO_INDIVIDUAL_LOCK = Lock()
_EMISSAO_INDIVIDUAL_STATE = {'ativa': False}


def emissao_individual_ativa():
    with EMISSAO_INDIVIDUAL_LOCK:
        return _EMISSAO_INDIVIDUAL_STATE['ativa']


def marcar_emissao_individual(ativa):
    with EMISSAO_INDIVIDUAL_LOCK:
        _EMISSAO_INDIVIDUAL_STATE['ativa'] = bool(ativa)


# === quem esta segurando o navegador agora =================================
# Ate aqui, os guardas eram por TIPO: um lote FGTS so barrava outro FGTS. Nada
# no servidor impedia dois lotes de tipos diferentes, nem emissao individual
# durante lote de outro tipo, nem duas individuais ao mesmo tempo. Isso nunca
# acontecia porque o overlay de tela cheia cobria Certidões e o operador nao
# conseguia clicar — ou seja, a trava era a UI, por acidente.
#
# Ao permitir minimizar o lote, essa trava acidental deixa de existir. Este
# agregador e a trava explicita que toma o lugar dela.
#
# Ele DERIVA do estado que ja existe em vez de manter um registro paralelo: um
# segundo registro divergiria do estado do lote na primeira falha de sincronia,
# e o sintoma seria o pior possivel — o sistema recusando trabalho que nao esta
# acontecendo, ou liberando o que esta.
_LOTES_REGISTRADOS = (
    ('FGTS', FGTS_BATCH_LOCK, FGTS_BATCH_STATE),
    ('Estadual RS', RS_BATCH_LOCK, RS_BATCH_STATE),
    ('Municipal', MUNICIPAL_BATCH_LOCK, MUNICIPAL_BATCH_STATE),
    ('Trabalhista', TRABALHISTA_BATCH_LOCK, TRABALHISTA_BATCH_STATE),
    ('NFSe', NFSE_BATCH_LOCK, NFSE_BATCH_STATE),
)


def automacao_em_curso():
    """Quem esta usando o navegador agora, ou None se esta livre.

    Devolve dict com `tipo` ('lote'|'individual'), `rotulo` (nome legivel, para
    a mensagem dizer QUEM ocupa) e `status`. Um lote pausado continua ocupando:
    ele tem driver aberto e "Retomar" disponivel. Pausa de breaker ja vencida
    NAO ocupa — quem fecha e a janela do breaker (spec 09), e a regra disso vive
    em `batch_engine.lote_ocupa_o_tipo`, consultada aqui em vez de recopiada."""
    for rotulo, lock, state in _LOTES_REGISTRADOS:
        with lock:
            if batch_engine.lote_ocupa_o_tipo(state):
                return {'tipo': 'lote', 'rotulo': rotulo, 'status': state.get('status')}

    if emissao_individual_ativa():
        return {'tipo': 'individual', 'rotulo': 'uma emissao individual',
                'status': 'running'}

    return None


def mensagem_automacao_em_curso(em_curso):
    """Mensagem para o operador, nomeando quem ocupa e o que fazer.

    Nomear importa: com o lote minimizado o operador nao ve mais qual esta
    rodando, e "automacao em andamento" sozinho manda ele procurar no escuro."""
    if not em_curso:
        return ''
    if em_curso['tipo'] == 'lote':
        if em_curso.get('status') == 'paused':
            return (f"O lote {em_curso['rotulo']} esta pausado e ainda segura o navegador. "
                    'Retome para concluir, ou pare o lote para liberar.')
        return (f"O lote {em_curso['rotulo']} esta em andamento. "
                'Aguarde concluir, ou pare o lote para liberar.')
    return ('Ha uma emissao individual em andamento. Aguarde concluir.')


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


# Opcoes do lote de manifestacao. Ficam FORA de MANIF_BATCH_STATE pela mesma
# razao das opcoes da NFSe: `init_batch_run` chama `reset_batch_state` e dispara
# o worker dentro do mesmo lock, entao qualquer chave escrita no estado antes de
# iniciar seria apagada, e escrever depois correria com o worker ja lendo.
#
# `tipo_evento` nao tem valor "esperto" de default: ele e escolhido a cada lote
# na tela, porque Confirmacao da Operacao e irreversivel e nao deve sair por
# omissao.
MANIF_OPCOES_LOCK = Lock()
_MANIF_BATCH_OPCOES = {'modo': 'empresa', 'tipo_evento': '210200',
                       'empresa_id': None, 'competencia': None,
                       'chave_id': None}


def manif_batch_opcoes():
    with MANIF_OPCOES_LOCK:
        return dict(_MANIF_BATCH_OPCOES)


def definir_manif_opcoes(**valores):
    """Grava as opcoes do proximo lote. Chaves desconhecidas sao ignoradas."""
    with MANIF_OPCOES_LOCK:
        for chave, valor in valores.items():
            if chave in _MANIF_BATCH_OPCOES:
                _MANIF_BATCH_OPCOES[chave] = valor
        return dict(_MANIF_BATCH_OPCOES)
