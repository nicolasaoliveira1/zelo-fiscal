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


# --- envio do evento (MANIF-13/16/17) ---------------------------------------

NS_NFE = 'http://www.portalfiscal.inf.br/nfe'
NS_DSIG = 'http://www.w3.org/2000/09/xmldsig#'
NS_SOAP = 'http://www.w3.org/2003/05/soap-envelope'
NS_WSDL_EVENTO = 'http://www.portalfiscal.inf.br/nfe/wsdl/NFeRecepcaoEvento4'

VERSAO_LOTE = '1.00'

# Codigos de desfecho. Sao os DOIS que mudam o rumo do fluxo; qualquer outro e
# rejeicao e vai para a tela com o texto oficial, sem parafrase.
CSTAT_REGISTRADO = ('135', '136')
CSTAT_DUPLICIDADE = ('573',)
# Consumo indevido (NT 2018.002): a SEFAZ bloqueou ESTE CNPJ por 1 hora. Nao e
# problema da nota — e do nosso acesso ao servico. Continuar enviando durante o
# bloqueio REINICIA o cronometro, e 50 bloqueios consecutivos viram bloqueio
# PERMANENTE, que so a SEFAZ destrava. Por isso este codigo para o lote em vez
# de virar mais uma linha vermelha na lista.
CSTAT_CONSUMO_INDEVIDO = ('656',)

# Excecoes que acontecem DEPOIS de o pedido sair. Nelas nao da para saber se a
# SEFAZ processou o evento — e os dois chutes erram em direcoes opostas (perder
# um evento que existe x reenviar um ja protocolado).
_ERROS_APOS_ENVIO = (
    requests.exceptions.ReadTimeout,
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.ContentDecodingError,
)


class RespostaSefaz:
    """Desfecho de um envio. `cStat` e `xMotivo` vem CRUS da SEFAZ."""

    def __init__(self, cstat=None, xmotivo=None, protocolo=None,
                 dh_registro=None, bruto='', erro=None, indefinido=False):
        self.cstat = cstat
        self.xmotivo = xmotivo
        self.protocolo = protocolo or None
        self.dh_registro = dh_registro
        self.bruto = bruto
        self.erro = erro
        self.indefinido = indefinido

    @property
    def registrado(self):
        return self.cstat in CSTAT_REGISTRADO

    @property
    def duplicidade(self):
        return self.cstat in CSTAT_DUPLICIDADE

    @property
    def consumo_indevido(self):
        """A SEFAZ bloqueou o CNPJ. Quem le isto tem de PARAR, nao retentar."""
        return self.cstat in CSTAT_CONSUMO_INDEVIDO

    def __repr__(self):
        return (f'<RespostaSefaz cstat={self.cstat} prot={self.protocolo} '
                f'indefinido={self.indefinido}>')


def _envelopar(evento, id_lote='1'):
    """SOAP com um `envEvento` de UM evento so.

    O evento entra como TEXTO ja serializado, nunca reserializado pelo envelope:
    o C14N preserva prefixos, e reescrever o XML aqui mudaria os bytes que a
    SEFAZ canonicaliza para conferir a assinatura."""
    from app.services.nfe_assinatura import serializar_documento

    corpo_evento = serializar_documento(evento)
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<soap:Envelope xmlns:soap="{NS_SOAP}">'
        '<soap:Body>'
        f'<nfeDadosMsg xmlns="{NS_WSDL_EVENTO}">'
        f'<envEvento xmlns="{NS_NFE}" versao="{VERSAO_LOTE}">'
        f'<idLote>{id_lote}</idLote>'
        f'{corpo_evento}'
        '</envEvento>'
        '</nfeDadosMsg>'
        '</soap:Body>'
        '</soap:Envelope>'
    ).encode('utf-8')


def _ler_resposta(texto):
    """`RespostaSefaz` a partir do XML devolvido, ou None se ilegivel.

    Le o `retEvento` quando existe; sem ele, cai no `cStat` do LOTE — que e o
    que aparece quando a SEFAZ recusa o envelope inteiro (falha de schema, por
    exemplo) e nao chega a avaliar o evento."""
    import xml.etree.ElementTree as ET

    try:
        raiz = ET.fromstring(texto)
    except ET.ParseError:
        return None

    def _t(no, tag):
        achado = no.find(f'.//{{{NS_NFE}}}{tag}') if no is not None else None
        return achado.text if achado is not None else None

    ret_lote = raiz.find(f'.//{{{NS_NFE}}}retEnvEvento')
    if ret_lote is None:
        return None

    ret_evento = ret_lote.find(f'.//{{{NS_NFE}}}retEvento')
    alvo = ret_evento if ret_evento is not None else ret_lote

    cstat = _t(alvo, 'cStat')
    if cstat is None:
        return None

    return RespostaSefaz(
        cstat=cstat,
        xmotivo=_t(alvo, 'xMotivo'),
        protocolo=_t(alvo, 'nProt'),
        dh_registro=_t(alvo, 'dhRegEvento'),
        bruto=texto,
    )


def enviar_evento(evento, credencial=None, ambiente=None, sessao=None,
                  timeout=TIMEOUT_PADRAO):
    """Manda UM evento assinado e devolve o desfecho. Nunca levanta por rede.

    `sessao` existe para o teste injetar o transporte; em producao vem `None` e
    a sessao mTLS e aberta a partir da `credencial`.

    A distincao que manda no fluxo (MANIF-17): falha **antes** do envio e
    retentavel; falha **depois** devolve `indefinido=True`, e a chave nao vira
    nem `manifestada` nem `pendente` — os dois chutes erram feio."""
    from app.services.nfe_assinatura import NS_DSIG as _NS

    if evento.find(f'{{{_NS}}}Signature') is None:
        raise SefazError(
            'O evento nao esta assinado. Enviar assim queima uma requisicao e '
            'volta rejeicao de schema.')

    alvo = url_de('evento', ambiente)
    corpo = _envelopar(evento)
    cabecalhos = {'Content-Type': f'application/soap+xml; charset=utf-8; '
                                  f'action="{NS_WSDL_EVENTO}/nfeRecepcaoEvento"'}

    def _postar(cliente):
        return cliente.post(alvo, data=corpo, headers=cabecalhos, timeout=timeout)

    try:
        if sessao is not None:
            resposta = _postar(sessao)
        else:
            with sessao_mtls(credencial, timeout=timeout) as cliente:
                resposta = _postar(cliente)
    except _ERROS_APOS_ENVIO as exc:
        log_event('nfe_sefaz_envio_indefinido', level='ERROR', url=alvo,
                  error=str(exc))
        return RespostaSefaz(bruto='', indefinido=True, erro=str(exc))
    except SefazError as exc:
        return RespostaSefaz(bruto='', erro=str(exc))
    except requests.exceptions.RequestException as exc:
        # conexao recusada, DNS, handshake: o pedido nao chegou a sair
        return RespostaSefaz(bruto='', erro=str(exc))

    if resposta.status_code >= 500:
        # a SEFAZ pode ter processado o evento e falhado ao responder
        log_event('nfe_sefaz_envio_indefinido', level='ERROR', url=alvo,
                  status=resposta.status_code)
        return RespostaSefaz(bruto=resposta.text, indefinido=True,
                             erro=f'HTTP {resposta.status_code} da SEFAZ.')

    if resposta.status_code >= 400:
        # 4xx morre na autorizacao/roteamento: nada foi processado
        return RespostaSefaz(
            bruto=resposta.text,
            erro=f'HTTP {resposta.status_code}: a SEFAZ recusou a requisicao '
                 f'antes de processar o evento.')

    lida = _ler_resposta(resposta.text)
    if lida is None:
        # 200 com corpo inesperado: pode ter processado. Adivinhar erraria.
        log_event('nfe_sefaz_resposta_ilegivel', level='ERROR', url=alvo)
        return RespostaSefaz(
            bruto=resposta.text, indefinido=True,
            erro='A SEFAZ respondeu 200 com um corpo que nao e o retorno do '
                 'evento. Confira no portal se a manifestacao saiu.')

    log_event('nfe_sefaz_evento_enviado', cstat=lida.cstat,
              protocolo=lida.protocolo)
    return lida
