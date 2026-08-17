r"""Transporte com os webservices da SEFAZ (MANIF-13, AD-027).

Camada de REDE pura: nao sabe o que e `Empresa` nem `ChaveManifestacao`. Recebe
uma credencial (caminho do `.pfx` + senha) e devolve uma sessao HTTP autenticada
por certificado de cliente.

**O arquivo temporario nao e desleixo, e limitacao do Python.**
`ssl.SSLContext.load_cert_chain` — usado por baixo do `requests` — so aceita
CAMINHO de arquivo, nunca bytes em memoria. Entao a chave privada decifrada
precisa tocar o disco durante o handshake. As mitigacoes:

- o arquivo nasce em `tempfile` LOCAL, nunca no drive de rede (que exporia a
  chave a todo o escritorio) nem no diretorio do projeto (que arriscaria o
  commit);
- e criado por `mkstemp`, que no POSIX ja nasce 0600 e no Windows fica no
  diretorio temporario do proprio usuario;
- e removido no `finally`, inclusive quando o corpo levanta.

O `.pfx` ja mora no `Z:` com senha `123456`, entao o temporario nao e o elo
fraco — mas quem mexer aqui precisa saber que ele existe.

Enderecos medidos, nao lembrados (`recon.md` §6): o `NFeRecepcaoEvento4`
responde em `www` e `www1`; o `NFeDistribuicaoDFe` responde **so em `www1`** —
`www` devolve 404.
"""
import os
import tempfile
from contextlib import contextmanager

import requests
from cryptography.hazmat.primitives import serialization

from app.services.execution_logger import log_event
from app.services.manifestador_cofre import carregar_pfx
from app.utils import get_config_value

# Portal do governo recusa cliente anonimo (mesma licao da BrasilAPI na spec 08
# e do portal_health na spec 09): sem User-Agent explicito volta 403 mesmo com
# certificado valido.
USER_AGENT = 'Zelo/1.0 (Rotinas Fiscais; contato via escritorio contabil)'

URLS = {
    'producao': {
        'evento': 'https://www1.nfe.fazenda.gov.br/NFeRecepcaoEvento4/'
                  'NFeRecepcaoEvento4.asmx',
        # www NAO tem este caminho (404 medido). Nao "corrija" para www.
        'distribuicao': 'https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/'
                        'NFeDistribuicaoDFe.asmx',
    },
    'homologacao': {
        # `hom1`, nao `hom`: medido com certificado real, `hom` devolve 404 nos
        # dois servicos e `hom1` devolve 200. A sondagem SEM certificado dava
        # 403 nos dois e nao distinguia — foi o teste autenticado que separou.
        'evento': 'https://hom1.nfe.fazenda.gov.br/NFeRecepcaoEvento4/'
                  'NFeRecepcaoEvento4.asmx',
        'distribuicao': 'https://hom1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/'
                        'NFeDistribuicaoDFe.asmx',
    },
}

AMBIENTE_PADRAO = 'producao'
TIMEOUT_PADRAO = 30


class SefazError(Exception):
    """Falha acionavel do transporte — o texto diz ao operador o que fazer."""


class Credencial:
    """Caminho do `.pfx` e senha em claro, para uso imediato.

    `__repr__` omite a senha: e o `repr` que acaba num log de excecao."""

    def __init__(self, caminho, senha):
        self.caminho = caminho
        self.senha = senha

    def __repr__(self):
        return f'<Credencial {self.caminho!r}>'


def ambiente_atual():
    return str(get_config_value('MANIF_AMBIENTE_SEFAZ', AMBIENTE_PADRAO)).strip()


def url_de(servico, ambiente=None):
    """URL do servico no ambiente, ou `SefazError` se um dos dois nao existe."""
    ambiente = ambiente or ambiente_atual()
    try:
        return URLS[ambiente][servico]
    except KeyError as exc:
        raise SefazError(
            f'Nao conheco o servico {servico!r} no ambiente {ambiente!r}. '
            f'Ambientes: {", ".join(sorted(URLS))}.') from exc


@contextmanager
def _pem_temporario(credencial):
    """PEM (chave + certificado + cadeia) num arquivo temporario local.

    Removido no `finally` inclusive em excecao — ver docstring do modulo."""
    info = carregar_pfx(credencial.caminho, (credencial.senha or '').encode() or None)
    if info is None or info.chave_privada is None:
        raise SefazError(
            f'Nao consegui abrir o certificado em {credencial.caminho}. '
            f'Confira a senha no cofre e se o arquivo continua na pasta.')

    partes = [
        info.chave_privada.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()),
        info.certificado.public_bytes(serialization.Encoding.PEM),
    ]
    partes.extend(c.public_bytes(serialization.Encoding.PEM)
                  for c in (info.cadeia or []))

    descritor, caminho = tempfile.mkstemp(suffix='.pem', prefix='zelo-mtls-')
    try:
        with os.fdopen(descritor, 'wb') as arquivo:
            arquivo.write(b''.join(partes))
        yield caminho
    finally:
        try:
            os.remove(caminho)
        except OSError as exc:
            # nao mascarar: chave privada esquecida no disco e digna de log
            log_event('nfe_sefaz_temp_nao_removido', level='WARNING',
                      caminho=caminho, error=str(exc))


@contextmanager
def sessao_mtls(credencial, timeout=TIMEOUT_PADRAO):
    """Sessao HTTP autenticada pelo certificado da empresa.

    Use sempre como context manager: e a saida do bloco que apaga o material de
    chave do disco."""
    with _pem_temporario(credencial) as pem:
        sessao = requests.Session()
        sessao.cert = pem
        sessao.headers.update({'User-Agent': USER_AGENT})
        sessao.request_timeout = timeout
        try:
            yield sessao
        finally:
            sessao.close()


def testar_conexao(credencial, url=None, ambiente=None):
    """Diagnostico do transporte: o certificado e aceito? **Nao envia evento.**

    Faz um GET no `?wsdl`. O sinal medido no recon e o 403 x 200: 403 significa
    "o caminho existe e exige certificado", entao um 403 AQUI, ja apresentando o
    certificado, significa que a SEFAZ nao o aceitou. Nunca levanta — o
    resultado e um relatorio."""
    alvo = url or url_de('evento', ambiente)
    try:
        with sessao_mtls(credencial) as sessao:
            resposta = sessao.get(f'{alvo}?wsdl', timeout=TIMEOUT_PADRAO)
    except SefazError as exc:
        return {'url': alvo, 'status': None, 'autenticado': False,
                'erro': str(exc)}
    except requests.exceptions.RequestException as exc:
        return {'url': alvo, 'status': None, 'autenticado': False,
                'erro': str(exc)}

    autenticado = 200 <= resposta.status_code < 300
    log_event('nfe_sefaz_teste_conexao', url=alvo, status=resposta.status_code,
              autenticado=autenticado)
    return {
        'url': alvo,
        'status': resposta.status_code,
        'autenticado': autenticado,
        'erro': None if autenticado else
                'A SEFAZ respondeu 403: o certificado nao foi aceito para este '
                'servico. Confira se e o e-CNPJ do destinatario e se esta '
                'valido.' if resposta.status_code == 403 else
                f'Resposta inesperada: HTTP {resposta.status_code}.',
    }
