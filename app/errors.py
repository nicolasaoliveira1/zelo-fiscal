from collections import namedtuple
from enum import Enum


class ErrorType(str, Enum):
    TIMEOUT = 'TIMEOUT'
    CAPTCHA = 'CAPTCHA'
    PORTAL = 'PORTAL'
    SELECTOR = 'SELECTOR'
    NETWORK_PATH = 'NETWORK_PATH'
    PERMISSION = 'PERMISSION'
    DB = 'DB'
    UNKNOWN = 'UNKNOWN'


# Marcadores de falha ao CONECTAR num host remoto. Existem separados do
# `map_exception_to_error_type` de proposito: o catalogo joga tanto o drive de
# rede local (Z: caiu) quanto a conexao recusada pelo portal em NETWORK_PATH, e
# para o circuit breaker (spec 09) a diferenca e tudo — ERR_CONNECTION_REFUSED e
# a assinatura MAIS COMUM de "portal fora", enquanto Z: fora nao diz nada sobre
# o portal. Lista estreita de proposito: exige a marca especifica do Chrome/
# socket, nunca a palavra solta "connection" (que aparece em erro de drive).
_MARCADORES_CONEXAO_HOST = (
    'ERR_CONNECTION', 'ERR_NAME_NOT_RESOLVED', 'ERR_INTERNET_DISCONNECTED',
    'ERR_ADDRESS_UNREACHABLE', 'ERR_TIMED_OUT', 'ERR_EMPTY_RESPONSE',
    'ECONNREFUSED', 'ECONNRESET', 'CONNECTIONERROR', 'CONNECTIONREFUSED',
    'CONNECTIONRESET', 'CONNECTION REFUSED', 'CONNECTION RESET',
    'CONNECTION ABORTED', 'MAX RETRIES EXCEEDED', 'NAME RESOLUTION',
)


def falha_de_conexao_com_host(exc):
    """True quando o texto indica que nao foi possivel CONECTAR no host remoto.

    Complementa (nao substitui) `map_exception_to_error_type`: aquele responde
    "de que familia e o erro"; este responde "isso e o portal nao respondendo?".
    """
    texto = str(exc or '').upper()
    return any(marcador in texto for marcador in _MARCADORES_CONEXAO_HOST)


def map_exception_to_error_type(exc):
    text = str(exc or '').upper()
    name = exc.__class__.__name__.upper() if exc else ''

    if 'TIMEOUT' in name or 'TIMEOUT' in text:
        return ErrorType.TIMEOUT

    if 'CAPTCHA' in text or 'ALTCHA' in text or '2CAPTCHA' in text:
        return ErrorType.CAPTCHA

    if 'PERMISSION' in name or 'ACCESS IS DENIED' in text or 'PERMISSAO' in text:
        return ErrorType.PERMISSION

    if 'NOSUCHELEMENT' in name or 'STALEELEMENT' in name or 'SELECTOR' in text:
        return ErrorType.SELECTOR

    if 'NETWORK' in text or 'Z:' in text or 'PATH' in text:
        return ErrorType.NETWORK_PATH

    if 'ECONN' in text or 'CONNECTION' in text or 'CONNREFUSED' in text or 'CONNRESET' in text:
        return ErrorType.NETWORK_PATH

    if 'DNS' in text or 'NAME RESOLUTION' in text:
        return ErrorType.NETWORK_PATH

    if 'SQL' in text or 'DATABASE' in text or 'DB' in text:
        return ErrorType.DB

    if 'WEBDRIVER' in name or 'SELENIUM' in text or 'PORTAL' in text:
        return ErrorType.PORTAL

    return ErrorType.UNKNOWN


# Mensagem amigavel por tipo de erro: o que aconteceu + o que fazer.
ErrorInfo = namedtuple('ErrorInfo', ['tipo', 'titulo', 'causa', 'acao', 'recuperavel', 'detalhe'])

_CATALOGO = {
    ErrorType.TIMEOUT: (
        'Tempo esgotado',
        'O portal demorou demais para responder.',
        'Tente novamente em instantes; se persistir, o site pode estar lento ou fora do ar.',
        True,
    ),
    ErrorType.CAPTCHA: (
        'Falha no captcha',
        'O captcha nao foi resolvido corretamente.',
        'Verifique a chave e o saldo do 2captcha e tente novamente.',
        True,
    ),
    ErrorType.PORTAL: (
        'Portal indisponivel',
        'O site do orgao retornou algo inesperado.',
        'Confira se o portal esta no ar e tente novamente mais tarde.',
        True,
    ),
    ErrorType.SELECTOR: (
        'Pagina do portal mudou',
        'Um elemento esperado nao foi encontrado — o site provavelmente mudou.',
        'Provavel quebra de seletor; e preciso revisar o mapeamento desse portal.',
        False,
    ),
    ErrorType.NETWORK_PATH: (
        'Pasta de rede inacessivel',
        'Nao foi possivel acessar a pasta de rede.',
        'Verifique se o drive de rede (Z:) esta mapeado e conectado e tente novamente.',
        True,
    ),
    ErrorType.PERMISSION: (
        'Permissao negada',
        'O sistema nao tem permissao para o arquivo ou pasta.',
        'Confira se o arquivo nao esta aberto/bloqueado e as permissoes da pasta.',
        True,
    ),
    ErrorType.DB: (
        'Erro no banco de dados',
        'Falha ao ler ou gravar no banco.',
        'Verifique a conexao com o banco e tente novamente.',
        True,
    ),
    ErrorType.UNKNOWN: (
        'Erro inesperado',
        'Ocorreu um erro nao classificado.',
        'Consulte os detalhes no log pelo req_id para diagnosticar.',
        False,
    ),
}


def descrever_erro(exc, contexto=None):
    """Traduz uma excecao em ErrorInfo (titulo, causa, acao, recuperavel)."""
    tipo = map_exception_to_error_type(exc)
    titulo, causa, acao, recuperavel = _CATALOGO[tipo]
    if contexto:
        causa = f'{causa} ({contexto})'
    return ErrorInfo(tipo, titulo, causa, acao, recuperavel, str(exc or '').strip())


def mensagem_usuario(exc, contexto=None):
    """Frase pronta para toast/UI: titulo + acao sugerida."""
    info = descrever_erro(exc, contexto)
    return f'{info.titulo}: {info.acao}'
