"""Cliente sem estado da SEFIN restrita para o ensaio de NFS-e.

O módulo só conhece o contrato HTTP congelado no OpenAPI restrito. Não recebe
ambiente nem URL pública: o destino é resolvido internamente e conferido antes
de abrir a sessão mTLS. A decisão de consultar antes de enviar pertence ao
orquestrador do ensaio da T26.
"""
import base64
from dataclasses import dataclass, field
import gzip
import json
import re
from urllib.parse import quote, urlsplit
import xml.etree.ElementTree as ET

from app.services import nfse_api_credencial, nfse_api_dps, nfse_api_transporte
from app.services import nfse_api_xml, nfe_assinatura


HOST_RESTRITO = 'sefin.producaorestrita.nfse.gov.br'
AMBIENTE_RESTRITA = 'restrita'
CAMINHO_NFSE = 'nfse'
CAMINHO_DPS = 'dps'
SITUACOES = (
    'gerado_teste', 'rejeitado', 'credencial', 'indisponivel',
    'nao_encontrado', 'identificador_invalido', 'resposta_invalida',
)
_PADRAO_ID_DPS = re.compile(
    r'DPS[0-9]{7}(1[0-9]{14}|2[0-9A-Z]{14})[0-9]{20}')


class NfseApiSefinError(ValueError):
    """Falha de contrato ou configuração antes do transporte HTTP."""


@dataclass(frozen=True)
class ResultadoRestrito:
    """Desfecho seguro de uma operação na produção restrita."""

    situacao: str
    http: int | None = None
    identificador: str | None = None
    chave_acesso: str | None = None
    codigo: str | None = None
    motivo: str | None = None
    data_hora_processamento: str | None = None
    versao_aplicativo: str | None = None
    xml_nfse: bytes | None = field(default=None, repr=False)

    def __post_init__(self):
        if self.situacao not in SITUACOES:
            raise ValueError(
                f'Situação restrita desconhecida: {self.situacao}')


def _url_restrita(servico, *partes):
    """Resolve e valida o destino restrito antes de qualquer sessão."""
    url = nfse_api_transporte.url_de(servico, AMBIENTE_RESTRITA)
    partes_url = urlsplit(url)
    try:
        porta = partes_url.port
    except ValueError as exc:
        raise NfseApiSefinError(
            'O destino da SEFIN não pertence ao host restrito permitido.') from exc
    if (partes_url.scheme != 'https'
            or partes_url.hostname != HOST_RESTRITO
            or porta not in (None, 443)
            or partes_url.username is not None
            or partes_url.password is not None):
        raise NfseApiSefinError(
            'O destino da SEFIN não pertence ao host restrito permitido.')
    sufixo = '/'.join(quote(str(parte), safe='') for parte in partes)
    return url.rstrip('/') + (f'/{sufixo}' if sufixo else '')


def _validar_identificador(identificador):
    if (not isinstance(identificador, str)
            or _PADRAO_ID_DPS.fullmatch(identificador) is None):
        raise NfseApiSefinError(
            'O identificador da DPS não atende ao formato oficial.')
    return identificador


def _inf_dps(dps):
    if (not isinstance(dps, ET.Element)
            or dps.tag != f'{{{nfse_api_dps.NAMESPACE_NFSE}}}DPS'):
        raise NfseApiSefinError(
            'O envio exige uma DPS no namespace oficial.')
    inf_dps = dps.find(
        f'{{{nfse_api_dps.NAMESPACE_NFSE}}}infDPS')
    if inf_dps is None:
        raise NfseApiSefinError('A DPS não informa o grupo infDPS.')
    return inf_dps


def _validar_dps_assinada(dps):
    inf_dps = _inf_dps(dps)
    if nfse_api_dps._texto(inf_dps, 'tpAmb') != nfse_api_dps.TP_AMBIENTE_RESTRITO:
        raise NfseApiSefinError(
            'O ensaio restrito exige tpAmb=2 antes do transporte.')
    problemas = nfse_api_dps.validar(dps)
    if problemas:
        raise NfseApiSefinError(
            'A DPS foi reprovada no XSD restrito antes do envio.')
    if not nfse_api_dps.verificar(dps):
        raise NfseApiSefinError(
            'A assinatura da DPS não pôde ser verificada localmente.')
    identificador = nfse_api_dps.identificador(dps)
    if inf_dps.get('Id') != identificador:
        raise NfseApiSefinError(
            'O Id da DPS não corresponde ao identificador oficial.')
    return identificador


def _compactar_dps(dps):
    try:
        xml = nfe_assinatura.serializar_documento(dps).encode('utf-8')
        compactado = gzip.compress(xml, mtime=0)
        return base64.b64encode(compactado).decode('ascii')
    except (nfe_assinatura.AssinaturaError, AttributeError, TypeError,
            ValueError) as exc:
        raise NfseApiSefinError(
            'A DPS não pôde ser serializada para o contrato da SEFIN.') from exc


def _json(corpo):
    try:
        valor = json.loads(corpo)
    except (TypeError, ValueError):
        return None
    return valor if isinstance(valor, dict) else None


def _texto_resposta(dados, campo):
    valor = dados.get(campo)
    return valor.strip() if isinstance(valor, str) and valor.strip() else None


def _mensagens_erro(dados):
    erros = dados.get('erros')
    if isinstance(erros, list) and erros:
        primeira = erros[0]
    else:
        primeira = dados.get('erro')
    if not isinstance(primeira, dict):
        return None, None
    codigo = _texto_resposta(primeira, 'codigo')
    partes = [
        _texto_resposta(primeira, campo)
        for campo in ('descricao', 'complemento')
    ]
    motivo = ' — '.join(parte for parte in partes if parte) or None
    return codigo, motivo


def _xml_da_resposta(valor):
    if not isinstance(valor, str) or not valor.strip():
        raise ValueError
    xml = nfse_api_xml.descomprimir(valor)
    if nfse_api_xml.classificar(xml) != 'nfse':
        raise ValueError
    return xml


def _resultado_sucesso(dados, *, http, identificador_esperado,
                      exige_xml):
    if dados.get('tipoAmbiente') != 2:
        return ResultadoRestrito(
            'resposta_invalida', http, identificador=identificador_esperado)
    identificador = _texto_resposta(dados, 'idDps')
    chave = _texto_resposta(dados, 'chaveAcesso')
    data_hora = _texto_resposta(dados, 'dataHoraProcessamento')
    versao = _texto_resposta(dados, 'versaoAplicativo')
    if (not identificador or identificador != identificador_esperado
            or not chave or re.fullmatch(r'NFS[0-9]{50}', chave) is None
            or not data_hora or not versao):
        return ResultadoRestrito(
            'resposta_invalida', http, identificador=identificador)
    xml = None
    if exige_xml:
        try:
            xml = _xml_da_resposta(dados.get('nfseXmlGZipB64'))
        except (ValueError, TypeError, nfse_api_xml.NfseApiXmlError):
            return ResultadoRestrito(
                'resposta_invalida', http, identificador=identificador,
                chave_acesso=chave)
    return ResultadoRestrito(
        'gerado_teste', http,
        identificador=identificador,
        chave_acesso=chave,
        data_hora_processamento=data_hora,
        versao_aplicativo=versao,
        xml_nfse=xml,
    )


def _resultado_erro(desfecho, *, identificador=None):
    dados = _json(desfecho.corpo)
    if desfecho.situacao == 'credencial' or desfecho.situacao == 'negado':
        return ResultadoRestrito(
            'credencial', desfecho.http, identificador=identificador,
            motivo='A credencial não foi aceita pela SEFIN restrita.')
    if desfecho.situacao == 'indisponivel':
        return ResultadoRestrito(
            'indisponivel', desfecho.http, identificador=identificador,
            motivo='A SEFIN restrita está indisponível ou não concluiu a operação.')
    if desfecho.http == 404:
        return ResultadoRestrito(
            'nao_encontrado', desfecho.http, identificador=identificador)
    if dados is None:
        return ResultadoRestrito(
            'resposta_invalida', desfecho.http, identificador=identificador)
    codigo, motivo = _mensagens_erro(dados)
    if codigo is None and motivo is None:
        return ResultadoRestrito(
            'resposta_invalida', desfecho.http, identificador=identificador)
    return ResultadoRestrito(
        'rejeitado', desfecho.http, identificador=identificador,
        codigo=codigo, motivo=motivo,
        data_hora_processamento=_texto_resposta(
            dados, 'dataHoraProcessamento'),
        versao_aplicativo=_texto_resposta(dados, 'versaoAplicativo'),
    )


def _executar_com_credencial(funcao):
    credencial = nfse_api_credencial.credencial_do_escritorio()
    if credencial is None:
        return None
    try:
        with nfse_api_transporte.sessao(credencial) as conexao:
            return funcao(conexao)
    except nfse_api_transporte.NfseApiTransporteError:
        return None


def enviar_restrita(dps_assinada):
    """Envia uma única DPS válida ao endpoint de produção restrita."""
    identificador = _validar_dps_assinada(dps_assinada)
    url = _url_restrita(CAMINHO_NFSE)
    compactado = _compactar_dps(dps_assinada)
    desfecho = _executar_com_credencial(
        lambda conexao: nfse_api_transporte.chamar(
            'POST', url, sessao=conexao, tentativas=1,
            json={'dpsXmlGZipB64': compactado}))
    if desfecho is None:
        return ResultadoRestrito(
            'credencial', identificador=identificador,
            motivo='Não há credencial pronta para a SEFIN restrita.')
    if desfecho.situacao == 'ok':
        if desfecho.http != 201:
            return ResultadoRestrito(
                'resposta_invalida', desfecho.http,
                identificador=identificador)
        dados = _json(desfecho.corpo)
        if dados is None:
            return ResultadoRestrito(
                'resposta_invalida', desfecho.http,
                identificador=identificador)
        return _resultado_sucesso(
            dados, http=desfecho.http, identificador_esperado=identificador,
            exige_xml=True)
    return _resultado_erro(desfecho, identificador=identificador)


def consultar_dps_restrita(identificador):
    """Consulta o identificador; ``None`` significa desfecho inconclusivo."""
    identificador = _validar_identificador(identificador)
    url = _url_restrita(CAMINHO_DPS, identificador)
    desfecho = _executar_com_credencial(
        lambda conexao: nfse_api_transporte.chamar(
            'GET', url, sessao=conexao))
    if desfecho is None:
        return ResultadoRestrito(
            'credencial', identificador=identificador,
            motivo='Não há credencial pronta para a SEFIN restrita.')
    if desfecho.situacao == 'indisponivel':
        return None
    if desfecho.situacao == 'ok':
        if desfecho.http != 200:
            return ResultadoRestrito(
                'resposta_invalida', desfecho.http,
                identificador=identificador)
        dados = _json(desfecho.corpo)
        if dados is None:
            return ResultadoRestrito(
                'resposta_invalida', desfecho.http,
                identificador=identificador)
        return _resultado_sucesso(
            dados, http=desfecho.http, identificador_esperado=identificador,
            exige_xml=False)
    return _resultado_erro(desfecho, identificador=identificador)
