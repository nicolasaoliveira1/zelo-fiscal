"""Envelope XML do ADN: só bytes sintéticos, sem rede."""
import base64
import gzip
from datetime import date
from decimal import Decimal

import pytest

from app.services import nfse_api_xml as xml_api


NS = xml_api.NAMESPACE_NFSE


def _xml(raiz):
    return f'<{raiz} xmlns="{NS}" versao="1.01"/>'.encode('utf-8')


def _compactar(conteudo):
    return base64.b64encode(gzip.compress(conteudo)).decode('ascii')


CHAVE = '0' * 50
ID_NFSE = 'NFS' + CHAVE


def _xml_nfse(documento_tag='CNPJ', documento='44556677000186', valor='1280.40'):
    return f'''<NFSe xmlns="{NS}" versao="1.01">
  <infNFSe Id="{ID_NFSE}">
    <xLocEmi>Município Sintético/RS</xLocEmi>
    <cStat>100</cStat>
    <dhProc>2026-07-31T18:45:00-03:00</dhProc>
    <valores><vLiq>{valor}</vLiq></valores>
    <DPS>
      <infDPS>
        <dCompet>2026-06-01</dCompet>
        <cLocEmi>0000000</cLocEmi>
        <prest><CNPJ>11222333000181</CNPJ><xNome>Prestador Sintético</xNome></prest>
        <toma><{documento_tag}>{documento}</{documento_tag}><xNome>Tomador Sintético</xNome></toma>
      </infDPS>
    </DPS>
  </infNFSe>
</NFSe>'''.encode('utf-8')


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


def test_ler_nfse_mapeia_chave_datas_tomador_municipio_valor_e_situacao():
    linha = xml_api.ler_nfse(_xml_nfse())

    assert linha.chave == CHAVE
    assert linha.data_geracao == date(2026, 7, 31)
    assert linha.competencia == '06/2026'
    assert linha.documento == '44.556.677/0001-86'
    assert linha.nome_tomador == 'Tomador Sintético'
    assert linha.municipio == 'Município Sintético/RS'
    assert linha.valor == Decimal('1280.40')
    assert linha.situacao == '100'


def test_ler_nfse_le_cpf_do_tomador_e_nao_o_cnpj_do_prestador():
    linha = xml_api.ler_nfse(_xml_nfse(
        documento_tag='CPF', documento='39053344705'))

    assert linha.documento == '390.533.447-05'


def test_ler_nfse_reconhece_escritorio_no_tomador():
    linha = xml_api.ler_nfse(_xml_nfse(
        documento_tag='CNPJ', documento='00000000000000'))

    assert linha.documento == '00.000.000/0000-00'
    assert linha.documento != '11.222.333/0001-81'


def test_ler_nfse_recusa_documento_do_tomador_com_caractere_invalido():
    with pytest.raises(xml_api.DocumentoTomadorInvalidoError):
        xml_api.ler_nfse(_xml_nfse(documento='4455667700018A'))


def test_ler_nfse_recusa_chave_fora_do_formato_oficial():
    xml = _xml_nfse().replace(ID_NFSE.encode('utf-8'), b'NFS-invalida')

    with pytest.raises(xml_api.ChaveNfseInvalidaError):
        xml_api.ler_nfse(xml)


def test_ler_nfse_recusa_valor_com_mais_casas_que_a_coluna():
    with pytest.raises(xml_api.ValorNfseInvalidoError):
        xml_api.ler_nfse(_xml_nfse(valor='1280.401'))
