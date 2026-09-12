"""Envelope XML do ADN: só bytes sintéticos, sem rede."""
import base64
import gzip

import pytest

from app.services import nfse_api_xml as xml_api


NS = xml_api.NAMESPACE_NFSE


def _xml(raiz):
    return f'<{raiz} xmlns="{NS}" versao="1.01"/>'.encode('utf-8')


def _compactar(conteudo):
    return base64.b64encode(gzip.compress(conteudo)).decode('ascii')


def test_descomprimir_retorna_o_xml_original():
    esperado = _xml('NFSe')

    assert xml_api.descomprimir(_compactar(esperado)) == esperado


@pytest.mark.parametrize(
    ('raiz', 'esperado'),
    [
        ('NFSe', 'nfse'),
        ('evento', 'evento'),
        ('DPS', 'desconhecido'),
    ],
)
def test_classificar_reconhece_as_raizes_do_namespace_oficial(raiz, esperado):
    assert xml_api.classificar(_xml(raiz)) == esperado


def test_namespace_diferente_e_desconhecido():
    assert xml_api.classificar(
        b'<NFSe xmlns="urn:documento-sintetico"/>') == 'desconhecido'


def test_base64_invalido_vira_erro_nomeado():
    with pytest.raises(xml_api.Base64InvalidoError):
        xml_api.descomprimir('conteudo que não é base64')


def test_gzip_truncado_vira_erro_nomeado():
    compactado = gzip.compress(_xml('NFSe'))[:-4]

    with pytest.raises(xml_api.GzipInvalidoError):
        xml_api.descomprimir(base64.b64encode(compactado))


def test_xml_malformado_vira_erro_nomeado():
    with pytest.raises(xml_api.XmlMalformadoError):
        xml_api.classificar(f'<NFSe xmlns="{NS}"'.encode('utf-8'))
