"""Desempacotamento, classificação e leitura dos XML recebidos pelo ADN.

Este módulo transforma os documentos no DTO que o domínio entende, sem rede,
persistência ou automação fiscal.
"""
import base64
import binascii
import gzip
import re
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.services.nfse_emitidas import LinhaEmitida
from app.utils import formatar_documento


NAMESPACE_NFSE = 'http://www.sped.fazenda.gov.br/nfse'
TIPOS_XML = ('nfse', 'evento', 'desconhecido')
TIPOS_EVENTO_CANCELAMENTO = ('e101101', 'e105102')


class NfseApiXmlError(ValueError):
    """Erro nomeado ao transformar o conteúdo recebido em XML."""


class Base64InvalidoError(NfseApiXmlError):
    """O campo não contém Base64 válido."""


class GzipInvalidoError(NfseApiXmlError):
    """O Base64 não contém um GZip completo e válido."""


class XmlMalformadoError(NfseApiXmlError):
    """O conteúdo descompactado não é um XML bem formado."""


class NfseEstruturaInvalidaError(NfseApiXmlError):
    """A raiz ou um campo obrigatório da NFS-e não está presente."""


class DocumentoTomadorInvalidoError(NfseApiXmlError):
    """O CPF/CNPJ informado no tomador não tem formato estrutural válido."""


class ChaveNfseInvalidaError(NfseApiXmlError):
    """A chave de acesso não segue o identificador oficial da NFS-e."""


class ValorNfseInvalidoError(NfseApiXmlError):
    """O valor da NFS-e não cabe sem perda na coluna monetária do domínio."""


class EventoEstruturaInvalidaError(NfseApiXmlError):
    """O evento não contém a identificação ou data exigida pelo XSD."""


@dataclass(frozen=True)
class EventoLido:
    """Evento do ADN reduzido aos campos usados pelo espelho."""

    chave: str
    tipo: str
    num_seq: int = 1
    data: datetime | None = None

    @property
    def tratado(self):
        """Indica se o P1 conhece o evento como cancelamento fiscal."""
        return self.tipo in TIPOS_EVENTO_CANCELAMENTO


def _parsear_xml(xml_bytes):
    try:
        return ET.fromstring(xml_bytes)
    except (ET.ParseError, TypeError, ValueError) as exc:
        raise XmlMalformadoError(
            'O XML recebido está malformado ou incompleto.') from exc


def _namespace_e_nome(elemento):
    if not isinstance(elemento.tag, str):
        return '', ''
    namespace, separador, nome = elemento.tag.rpartition('}')
    if not separador:
        return '', elemento.tag
    return namespace[1:], nome


def _filho(elemento, nome):
    if elemento is None:
        return None
    return elemento.find(f'{{{NAMESPACE_NFSE}}}{nome}')


def _texto(elemento, nome):
    filho = _filho(elemento, nome)
    return (filho.text or '').strip() if filho is not None else ''


def _exigir_texto(elemento, nome, caminho):
    valor = _texto(elemento, nome)
    if not valor:
        raise NfseEstruturaInvalidaError(
            f'A NFS-e não informa o campo obrigatório {caminho}.')
    return valor


def _validar_raiz_nfse(raiz):
    namespace, nome = _namespace_e_nome(raiz)
    if namespace != NAMESPACE_NFSE or nome != 'NFSe':
        raise NfseEstruturaInvalidaError(
            'O XML recebido não contém a raiz oficial NFSe.')


def _data_geracao(inf_nfse):
    bruto = _exigir_texto(inf_nfse, 'dhProc', 'infNFSe/dhProc')
    try:
        return datetime.fromisoformat(bruto.replace('Z', '+00:00')).date()
    except (TypeError, ValueError) as exc:
        raise NfseEstruturaInvalidaError(
            'A data de geração da NFS-e não está no formato ISO válido.') from exc


def _competencia_dps(inf_dps):
    bruto = _exigir_texto(inf_dps, 'dCompet', 'DPS/infDPS/dCompet')
    try:
        competencia = date.fromisoformat(bruto)
    except (TypeError, ValueError) as exc:
        raise NfseEstruturaInvalidaError(
            'A competência do DPS não está no formato AAAA-MM-DD.') from exc
    return f'{competencia.month:02d}/{competencia.year}'


def _documento_tomador(toma):
    if toma is None:
        return ''

    for nome, tamanho in (('CNPJ', 14), ('CPF', 11)):
        bruto = _texto(toma, nome)
        if not bruto:
            continue
        documento = bruto
        if (len(documento) != tamanho or not documento.isascii()
                or not documento.isdigit()):
            raise DocumentoTomadorInvalidoError(
                f'O {nome} do tomador não tem {tamanho} dígitos.')
        return formatar_documento(documento)
    return ''


def _valor_liquido(inf_nfse):
    valores = _filho(inf_nfse, 'valores')
    bruto = _exigir_texto(valores, 'vLiq', 'infNFSe/valores/vLiq')
    try:
        valor = Decimal(bruto)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValorNfseInvalidoError(
            'O valor líquido da NFS-e não é um decimal válido.') from exc

    if not valor.is_finite() or valor < 0:
        raise ValorNfseInvalidoError(
            'O valor líquido da NFS-e precisa ser finito e não negativo.')
    if valor.as_tuple().exponent < -2:
        raise ValorNfseInvalidoError(
            'O valor líquido da NFS-e tem mais de duas casas decimais.')
    if valor >= Decimal('10000000000'):
        raise ValorNfseInvalidoError(
            'O valor líquido da NFS-e excede a coluna Numeric(12,2).')
    try:
        return valor.quantize(Decimal('0.01'))
    except InvalidOperation as exc:
        raise ValorNfseInvalidoError(
            'O valor líquido da NFS-e não cabe na coluna Numeric(12,2).') from exc


def descomprimir(conteudo_b64):
    """Converte Base64 de GZip em bytes XML, com erros de fronteira nomeados."""
    try:
        if isinstance(conteudo_b64, str):
            conteudo_b64 = conteudo_b64.encode('ascii')
        if not isinstance(conteudo_b64, bytes) or not conteudo_b64:
            raise ValueError
        compactado = base64.b64decode(conteudo_b64, validate=True)
    except (binascii.Error, UnicodeError, TypeError, ValueError) as exc:
        raise Base64InvalidoError(
            'O documento recebido não contém Base64 válido.') from exc

    try:
        return gzip.decompress(compactado)
    except (EOFError, OSError, zlib.error) as exc:
        raise GzipInvalidoError(
            'O documento recebido não contém um GZip completo.') from exc


def classificar(xml_bytes):
    """Classifica a raiz no namespace oficial como NFS-e, evento ou desconhecido."""
    raiz = _parsear_xml(xml_bytes)

    namespace, nome = _namespace_e_nome(raiz)
    if namespace != NAMESPACE_NFSE:
        return 'desconhecido'
    if nome == 'NFSe':
        return 'nfse'
    if nome == 'evento':
        return 'evento'
    return 'desconhecido'


def ler_nfse(xml_bytes):
    """Lê uma NFS-e oficial na linha comum do espelho.

    A competência é a data do início da prestação (`dCompet`) do DPS. O valor
    é o líquido publicado na NFS-e (`vLiq`), que é o valor comparável ao
    espelho canônico e não o valor bruto do serviço.
    """
    raiz = _parsear_xml(xml_bytes)
    _validar_raiz_nfse(raiz)

    inf_nfse = _filho(raiz, 'infNFSe')
    if inf_nfse is None:
        raise NfseEstruturaInvalidaError(
            'A NFS-e não informa o grupo obrigatório infNFSe.')

    id_nfse = (inf_nfse.get('Id') or '').strip()
    if not re.fullmatch(r'NFS[0-9]{50}', id_nfse):
        raise ChaveNfseInvalidaError(
            'A chave de acesso da NFS-e deve ter o formato NFS + 50 dígitos.')
    # O XML oficial carrega o literal NFS no Id; o DTO compartilhado preserva
    # a chave canônica de 50 dígitos já usada pela raspagem do portal.
    chave = id_nfse[3:]

    dps = _filho(inf_nfse, 'DPS')
    inf_dps = _filho(dps, 'infDPS')
    if inf_dps is None:
        raise NfseEstruturaInvalidaError(
            'A NFS-e não informa o grupo obrigatório DPS/infDPS.')

    municipio = (
        _texto(inf_nfse, 'xLocEmi')
        or _texto(inf_nfse, 'xLocIncid')
        or _texto(inf_dps, 'cLocEmi')
    )
    if not municipio:
        raise NfseEstruturaInvalidaError(
            'A NFS-e não informa o município de emissão.')

    toma = _filho(inf_dps, 'toma')
    return LinhaEmitida(
        chave=chave,
        data_geracao=_data_geracao(inf_nfse),
        documento=_documento_tomador(toma),
        nome_tomador=_texto(toma, 'xNome')[:140],
        competencia=_competencia_dps(inf_dps),
        municipio=municipio[:60],
        valor=_valor_liquido(inf_nfse),
        situacao=_texto(inf_nfse, 'cStat')[:30],
    )


def _validar_raiz_evento(raiz):
    namespace, nome = _namespace_e_nome(raiz)
    if namespace != NAMESPACE_NFSE or nome != 'evento':
        raise EventoEstruturaInvalidaError(
            'O XML recebido não contém a raiz oficial evento.')


def _chave_evento(inf_ped_reg):
    bruto = _exigir_texto(inf_ped_reg, 'chNFSe', 'infPedReg/chNFSe')
    if bruto.startswith('NFS') and re.fullmatch(r'NFS[0-9]{50}', bruto):
        return bruto[3:]
    if re.fullmatch(r'[0-9]{50}', bruto):
        return bruto
    raise EventoEstruturaInvalidaError(
        'A chave do evento deve conter os 50 dígitos da NFS-e.')


def _tipo_evento(inf_ped_reg):
    for filho in list(inf_ped_reg):
        _, nome = _namespace_e_nome(filho)
        if re.fullmatch(r'e[0-9]{6}', nome):
            return nome
    raise EventoEstruturaInvalidaError(
        'O pedido de registro não informa o tipo do evento.')


def _numero_evento(inf_evento):
    bruto = _texto(inf_evento, 'nSeqEvento') or '1'
    try:
        numero = int(bruto)
    except (TypeError, ValueError) as exc:
        raise EventoEstruturaInvalidaError(
            'A sequência do evento não é um número válido.') from exc
    if not 1 <= numero <= 999:
        raise EventoEstruturaInvalidaError(
            'A sequência do evento precisa estar entre 1 e 999.')
    return numero


def _data_evento(inf_evento, inf_ped_reg):
    # dhProc é a data em que o evento foi registrado no ambiente nacional;
    # dhEvento fica como fallback para envelopes antigos/minimais.
    bruto = _texto(inf_evento, 'dhProc') or _texto(inf_ped_reg, 'dhEvento')
    if not bruto:
        raise EventoEstruturaInvalidaError(
            'O evento não informa dhProc nem dhEvento.')
    try:
        return datetime.fromisoformat(bruto.replace('Z', '+00:00'))
    except (TypeError, ValueError) as exc:
        raise EventoEstruturaInvalidaError(
            'A data do evento não está no formato ISO válido.') from exc


def ler_evento(xml_bytes):
    """Lê evento do ADN e preserva código desconhecido para decisão do domínio."""
    raiz = _parsear_xml(xml_bytes)
    _validar_raiz_evento(raiz)

    inf_evento = _filho(raiz, 'infEvento')
    ped_registro = _filho(inf_evento, 'pedRegEvento')
    inf_ped_reg = _filho(ped_registro, 'infPedReg')
    if inf_evento is None or inf_ped_reg is None:
        raise EventoEstruturaInvalidaError(
            'O evento não informa infEvento/pedRegEvento/infPedReg.')

    return EventoLido(
        chave=_chave_evento(inf_ped_reg),
        tipo=_tipo_evento(inf_ped_reg),
        num_seq=_numero_evento(inf_evento),
        data=_data_evento(inf_evento, inf_ped_reg),
    )
