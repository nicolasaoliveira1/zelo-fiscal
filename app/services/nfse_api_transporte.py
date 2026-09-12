"""Transporte mTLS para os serviços da API nacional de NFS-e.

Este módulo conhece somente o transporte e o contrato de falhas da API. A
interpretação de XML, paginação e persistência ficam nos serviços que o
utilizam. Nenhuma função deste módulo inicia uma chamada automaticamente.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import threading
import time

import requests

from app import db
from app.models import ConfiguracaoNfse
from app.services.pem_temporario import PemTemporarioError, pem_temporario


AMBIENTES = ('producao', 'restrita')
SITUACOES = ('ok', 'negado', 'credencial', 'indisponivel', 'rejeitado')
TIMEOUT_PADRAO = 30
TENTATIVAS_PADRAO = 3
BACKOFF_BASE_PADRAO = 0.5
USER_AGENT = 'Zelo/1.0 (Rotinas Fiscais; contato via escritorio contabil)'


class NfseApiTransporteError(ValueError):
    """Erro de configuração do transporte, antes de uma chamada HTTP."""


@dataclass(frozen=True)
class Desfecho:
    """Resultado fechado de uma chamada, sem exceção de rede para o chamador."""

    situacao: str
    http: int | None = None
    corpo: str = ''
    mensagem: str = ''

    def __post_init__(self):
        if self.situacao not in SITUACOES:
            raise ValueError(f'Situação de transporte desconhecida: {self.situacao}')


_BASES = {
    'producao': {
        'sefin': 'https://sefin.nfse.gov.br/SefinNacional',
        'adn': 'https://adn.nfse.gov.br/contribuintes',
    },
    'restrita': {
        'sefin': 'https://sefin.producaorestrita.nfse.gov.br/API/SefinNacional',
        'adn': 'https://adn.producaorestrita.nfse.gov.br/contribuintes',
    },
}

# Os serviços que têm identificador no caminho devolvem o prefixo oficial;
# o serviço consumidor acrescenta a chave, o NSU ou o código do município.
_CAMINHOS = {
    'parametros_convenio': ('sefin', 'parametros_municipais'),
    'adn_dfe': ('adn', 'DFe'),
    'nfse': ('sefin', 'nfse'),
    'nfse_chave': ('sefin', 'nfse'),
    'dps': ('sefin', 'dps'),
    'eventos': ('sefin', 'nfse'),
}

URLS = {
    ambiente: {
        servico: f'{_BASES[ambiente][base]}/{caminho}'
        for servico, (base, caminho) in _CAMINHOS.items()
    }
    for ambiente in AMBIENTES
}

_CHAMADA_LOCK = threading.Lock()


def ambiente_atual():
    """Lê o ambiente configurado, usando restrita como default seguro."""
    config = db.session.get(ConfiguracaoNfse, 1)
    ambiente = getattr(config, 'api_ambiente', None) if config else None
    return str(ambiente or 'restrita').strip().lower()


def url_de(servico, ambiente=None):
    """Devolve o prefixo do endpoint oficial para o serviço e ambiente."""
    ambiente = ambiente_atual() if ambiente is None else ambiente
    if ambiente not in URLS:
        ambientes = ', '.join(AMBIENTES)
        raise NfseApiTransporteError(
            f'Ambiente da API nacional desconhecido: {ambiente!r}. '
            f'Use um destes: {ambientes}.')
    if servico not in URLS[ambiente]:
        servicos = ', '.join(_CAMINHOS)
        raise NfseApiTransporteError(
            f'Serviço da API nacional desconhecido: {servico!r}. '
            f'Use um destes: {servicos}.')
    return URLS[ambiente][servico]


@contextmanager
def sessao(credencial, timeout=TIMEOUT_PADRAO):
    """Abre uma sessão mTLS e remove o PEM temporário ao sair."""
    try:
        with pem_temporario(credencial) as caminho_pem:
            conexao = requests.Session()
            conexao.cert = caminho_pem
            conexao.headers.update({'User-Agent': USER_AGENT})
            conexao.request_timeout = timeout
            try:
                yield conexao
            finally:
                conexao.close()
    except PemTemporarioError as exc:
        raise NfseApiTransporteError(
            'Não foi possível preparar o certificado da API nacional.') from exc


def chamar(metodo, url, *, sessao, tentativas=TENTATIVAS_PADRAO,
           backoff_base=BACKOFF_BASE_PADRAO, **kwargs):
    """Executa uma chamada serializada e traduz seus resultados.

    Respostas 429 e 503 repetem a mesma requisição até o teto. Falhas de
    rede não atravessam esta fronteira: tornam-se ``indisponivel``.
    """
    metodo = str(metodo).upper()
    total_tentativas = max(1, int(tentativas))
    parametros = dict(kwargs)
    parametros.setdefault(
        'timeout', getattr(sessao, 'request_timeout', TIMEOUT_PADRAO))

    with _CHAMADA_LOCK:
        for tentativa in range(total_tentativas):
            try:
                resposta = sessao.request(metodo, url, **parametros)
            except requests.exceptions.SSLError:
                return Desfecho(
                    'credencial', None, '',
                    'O certificado não foi aceito no canal mTLS.')
            except (requests.exceptions.Timeout,
                    requests.exceptions.ConnectionError,
                    requests.exceptions.RequestException):
                return Desfecho(
                    'indisponivel', None, '',
                    'A API nacional está indisponível no momento.')

            desfecho = _desfecho_http(resposta)
            if (getattr(resposta, 'status_code', None) not in (429, 503)
                    or tentativa + 1 >= total_tentativas):
                return desfecho
            intervalo = max(0, float(backoff_base)) * (2 ** tentativa)
            time.sleep(intervalo)

    # O laço sempre retorna; esta linha mantém a garantia explícita para
    # analisadores estáticos e futuras alterações no corpo da função.
    return Desfecho(
        'indisponivel', None, '', 'A API nacional está indisponível no momento.')


def _desfecho_http(resposta):
    status = getattr(resposta, 'status_code', None)
    corpo = _corpo_da_resposta(resposta)
    if status is not None and 200 <= status < 300:
        return Desfecho('ok', status, corpo, 'Chamada concluída.')
    if status in (401, 403):
        return Desfecho(
            'negado', status, corpo,
            f'A API nacional negou a chamada (HTTP {status}).')
    if status in (429, 503) or (status is not None and status >= 500):
        return Desfecho(
            'indisponivel', status, corpo,
            f'A API nacional está indisponível (HTTP {status}).')
    return Desfecho(
        'rejeitado', status, corpo,
        f'A API nacional rejeitou a chamada (HTTP {status}).')


def _corpo_da_resposta(resposta):
    try:
        return resposta.text or ''
    except Exception:
        return ''
