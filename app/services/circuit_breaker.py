"""Circuit breaker por portal (spec 09, RESOP-02).

Motivo: quando um portal esta fora, o lote segue tentando item apos item e
queima credito de 2captcha a toa. Depois de N falhas SEGUIDAS do mesmo portal o
breaker abre; o `batch_engine` consulta `aberto(alvo)` ANTES de criar o driver,
entao um item recusado nao abre navegador nem chama o solver.

Duas escolhas que nao devem ser "simplificadas" depois:

- **O alvo vem explicito de quem chama** (o fluxo do lote), nao do payload de
  log. O `diagnostics._alvo` so sabe o nome do municipio quando o evento carrega
  `municipio=`, e praticamente nenhum evento municipal carrega — um breaker
  baseado naquele alvo nunca abriria por municipio, em silencio.
- **A contagem e a mesma do `diagnostics`**, via `recorrencia.ContadorRecorrencia`
  (uma implementacao de "N seguidas + reset no sucesso", dois alimentadores).

Estado em memoria de processo, como `diagnostics` e o `batch_state` (AD-010): o
breaker responde "agora", nao guarda historico. Fecha SOZINHO quando a janela
expira — se o portal voltar de madrugada, ninguem precisa religar nada.
"""
import threading
from datetime import datetime, timedelta

from app.services.execution_logger import log_event
from app.services.recorrencia import ContadorRecorrencia
from app.utils import get_config_value

LIMIAR_PADRAO = 3
JANELA_MINUTOS_PADRAO = 60

# Rotulos dos portais de tipo unico. Vivem AQUI (e nao em routes) porque sao a
# chave do breaker para dois lados: quem alimenta (os fluxos de lote) e quem le
# (o semaforo do painel). Com duas listas, o painel marcaria como aberto um
# portal que o lote chama por outro nome — em silencio.
ALVO_FGTS = 'FGTS'
ALVO_ESTADUAL_RS = 'Estadual RS'
ALVO_TRABALHISTA = 'Trabalhista'
ALVO_FEDERAL = 'Federal'
# Municipio nao tem rotulo fixo: o alvo e a chave canonica da cidade
# (`utils.normalizar_cidade`), a mesma dos dois lados.
ALVO_MUNICIPAL_GENERICO = 'Municipal'

_lock = threading.Lock()
_contador = ContadorRecorrencia(LIMIAR_PADRAO)
_abertos = {}  # alvo -> {'desde': datetime, 'ocorrencias': int, 'motivo': str|None}


def _agora():
    """Isolado para os testes controlarem a janela sem dormir."""
    return datetime.now()


def _config_int(nome, padrao):
    try:
        valor = int(get_config_value(nome, padrao))
    except (TypeError, ValueError):
        return padrao
    return valor if valor > 0 else padrao


def _contador_atual():
    _contador.limiar = _config_int('BREAKER_LIMIAR', LIMIAR_PADRAO)
    return _contador


def registrar_falha(alvo, mensagem=None):
    """Conta uma falha do portal. Devolve True apenas quando o breaker ABRIU
    agora — assim o alerta sai uma vez, e nao a cada item do lote."""
    if not alvo:
        return False
    contador = _contador_atual()
    ocorrencias = contador.falha(alvo)
    if not contador.estourou(alvo):
        return False
    with _lock:
        if alvo in _abertos:
            return False
        _abertos[alvo] = {
            'desde': _agora(),
            'ocorrencias': ocorrencias,
            'motivo': mensagem,
        }
    log_event('breaker_aberto', level='WARNING', alvo=alvo,
              ocorrencias=ocorrencias, message=mensagem)
    return True


def registrar_sucesso(alvo):
    """Desfecho nao-erro no portal: zera a contagem e fecha o breaker."""
    if not alvo:
        return
    _contador_atual().sucesso(alvo)
    with _lock:
        fechou = _abertos.pop(alvo, None) is not None
    if fechou:
        log_event('breaker_fechado', alvo=alvo, motivo='sucesso')


def aberto(alvo):
    """True enquanto o portal estiver marcado como fora. Ao passar da janela,
    fecha e zera a contagem — o proximo lote tenta do zero."""
    if not alvo:
        return False
    janela = timedelta(minutes=_config_int('BREAKER_JANELA_MINUTOS',
                                           JANELA_MINUTOS_PADRAO))
    with _lock:
        registro = _abertos.get(alvo)
        if registro is None:
            return False
        if _agora() - registro['desde'] < janela:
            return True
        _abertos.pop(alvo, None)
    _contador_atual().sucesso(alvo)
    log_event('breaker_fechado', alvo=alvo, motivo='janela expirada')
    return False


def abertos():
    """Portais atualmente marcados como fora, para o painel."""
    with _lock:
        return [
            {
                'alvo': alvo,
                'desde': registro['desde'].isoformat(),
                'ocorrencias': registro['ocorrencias'],
                'motivo': registro['motivo'],
            }
            for alvo, registro in _abertos.items()
        ]


def limpar():
    """Zera contagem e estado. Usado pelos testes; NAO e chamado no inicio de
    cada ciclo do agendador de proposito — a janela (60 min) e menor que o
    intervalo entre ciclos (diario), entao o breaker ja chega fechado."""
    _contador.limpar()
    with _lock:
        _abertos.clear()
