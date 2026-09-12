"""Desempacotamento e classificação dos XML recebidos pelo ADN.

Este módulo só conhece o envelope do documento. A leitura dos campos da NFS-e
e dos eventos fica nas funções especializadas que usam este contrato, sem rede
nem persistência.
"""
import base64
import binascii
import gzip
import xml.etree.ElementTree as ET
import zlib


NAMESPACE_NFSE = 'http://www.sped.fazenda.gov.br/nfse'
TIPOS_XML = ('nfse', 'evento', 'desconhecido')


class NfseApiXmlError(ValueError):
    """Erro nomeado ao transformar o conteúdo recebido em XML."""


class Base64InvalidoError(NfseApiXmlError):
    """O campo não contém Base64 válido."""


class GzipInvalidoError(NfseApiXmlError):
    """O Base64 não contém um GZip completo e válido."""


class XmlMalformadoError(NfseApiXmlError):
    """O conteúdo descompactado não é um XML bem formado."""


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
    try:
        raiz = ET.fromstring(xml_bytes)
    except (ET.ParseError, TypeError, ValueError) as exc:
        raise XmlMalformadoError(
            'O XML recebido está malformado ou incompleto.') from exc

    if not isinstance(raiz.tag, str):
        return 'desconhecido'
    namespace, _, nome = raiz.tag.rpartition('}')
    if namespace[1:] != NAMESPACE_NFSE:
        return 'desconhecido'
    if nome == 'NFSe':
        return 'nfse'
    if nome == 'evento':
        return 'evento'
    return 'desconhecido'
