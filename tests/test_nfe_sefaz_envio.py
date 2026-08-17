"""Envio SOAP do evento e leitura da resposta da SEFAZ (MANIF-13/16/17).

Nenhum teste toca a rede: o transporte e injetado. O que se prova aqui e a
interpretacao — e, sobretudo, a distincao entre "nao enviei" (retentavel) e
"enviei e nao sei o desfecho" (so acao humana resolve).
"""
import pytest
import requests

from app.services import nfe_sefaz
from app.services import manifestador_service as svc
from app.services import nfe_assinatura as assin
from tests.test_nfe_assinatura import _par_de_teste

CHAVE = '43170107461248000107650010000045391000045390'
NS_NFE = 'http://www.portalfiscal.inf.br/nfe'


def _evento_assinado():
    chave_rsa, cert = _par_de_teste()
    raiz = svc.montar_evento(chave=CHAVE, cnpj_destinatario='11222333000181')
    assin.assinar(raiz, raiz.find(f'{{{NS_NFE}}}infEvento'), chave_rsa, cert)
    return raiz


def _resposta_soap(cstat='135', xmotivo='Evento registrado e vinculado a NF-e',
                   nprot='143210000123456', cstat_lote='128'):
    return f'''<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope">
  <soap:Body>
    <nfeResultMsg xmlns="http://www.portalfiscal.inf.br/nfe/wsdl/NFeRecepcaoEvento4">
      <retEnvEvento xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.00">
        <idLote>1</idLote>
        <tpAmb>1</tpAmb>
        <verAplic>AN_1.0</verAplic>
        <cOrgao>91</cOrgao>
        <cStat>{cstat_lote}</cStat>
        <xMotivo>Lote de Evento Processado</xMotivo>
        <retEvento versao="1.00">
          <infEvento>
            <tpAmb>1</tpAmb>
            <verAplic>AN_1.0</verAplic>
            <cOrgao>91</cOrgao>
            <cStat>{cstat}</cStat>
            <xMotivo>{xmotivo}</xMotivo>
            <chNFe>{CHAVE}</chNFe>
            <tpEvento>210200</tpEvento>
            <nSeqEvento>1</nSeqEvento>
            <dhRegEvento>2026-08-17T15:00:00-03:00</dhRegEvento>
            <nProt>{nprot}</nProt>
          </infEvento>
        </retEvento>
      </retEnvEvento>
    </nfeResultMsg>
  </soap:Body>
</soap:Envelope>'''


class _Transporte:
    """Sessao falsa: registra o que foi enviado e devolve o que for combinado."""

    def __init__(self, resposta=None, status=200, erro=None, erro_no_envio=None):
        self.enviados = []
        self._resposta = resposta if resposta is not None else _resposta_soap()
        self._status = status
        self._erro = erro
        self._erro_no_envio = erro_no_envio
        self.headers = {}
        self.cert = None

    def post(self, url, data=None, headers=None, timeout=None, **kwargs):
        self.enviados.append({'url': url, 'data': data, 'headers': headers or {}})
        if self._erro:
            raise self._erro
        if self._erro_no_envio:
            raise self._erro_no_envio

        class _R:
            status_code = self._status
            text = self._resposta
        return _R()

    def close(self):
        pass


def _enviar(evento=None, transporte=None):
    transporte = transporte or _Transporte()
    return nfe_sefaz.enviar_evento(
        evento or _evento_assinado(), sessao=transporte), transporte


# --- envelope ---------------------------------------------------------------

def test_envia_um_unico_evento_por_lote():
    """1 por `envEvento` de proposito: a resposta mapeia 1:1 na chave e uma
    rejeicao nao arrasta as vizinhas."""
    _resultado, transporte = _enviar()
    corpo = transporte.enviados[0]['data'].decode('utf-8')

    assert corpo.count('<evento ') == 1
    assert '<envEvento' in corpo
    assert '<idLote>' in corpo


def test_envelope_preserva_a_assinatura_do_evento():
    """O envelope nao pode reserializar o evento com outro prefixo: o C14N
    preserva prefixos, e a SEFAZ recalcularia um digest diferente."""
    import xml.etree.ElementTree as ET

    _resultado, transporte = _enviar()
    corpo = transporte.enviados[0]['data'].decode('utf-8')

    inicio = corpo.index('<evento ')
    fim = corpo.index('</evento>') + len('</evento>')
    assert assin.verificar(ET.fromstring(corpo[inicio:fim])) is True


def test_cabecalho_soap_declara_a_acao():
    _resultado, transporte = _enviar()
    cabecalhos = transporte.enviados[0]['headers']

    assert 'application/soap+xml' in cabecalhos.get('Content-Type', '')


def test_envia_para_a_url_do_ambiente():
    _resultado, transporte = _enviar()
    assert transporte.enviados[0]['url'] == \
        nfe_sefaz.URLS['producao']['evento']


# --- leitura do desfecho (MANIF-16) -----------------------------------------

def test_evento_registrado_vira_sucesso_com_protocolo():
    resultado, _t = _enviar()

    assert resultado.cstat == '135'
    assert resultado.xmotivo == 'Evento registrado e vinculado a NF-e'
    assert resultado.protocolo == '143210000123456'
    assert resultado.registrado is True
    assert resultado.duplicidade is False
    assert resultado.indefinido is False


def test_duplicidade_e_reconhecida_como_desfecho_proprio():
    """573 significa que a nota JA estava manifestada — e o resultado desejado,
    nao uma falha."""
    transporte = _Transporte(_resposta_soap(
        cstat='573', xmotivo='Rejeicao: Duplicidade de evento', nprot=''))
    resultado, _t = _enviar(transporte=transporte)

    assert resultado.cstat == '573'
    assert resultado.duplicidade is True
    assert resultado.registrado is False


def test_rejeicao_traz_o_texto_oficial_sem_parafrase():
    """O operador procura o motivo pelo codigo e pelo texto da SEFAZ; reescrever
    esconderia justamente o que ele precisa pesquisar."""
    transporte = _Transporte(_resposta_soap(
        cstat='596',
        xmotivo='Rejeicao: NF-e nao consta na base de dados da SEFAZ'))
    resultado, _t = _enviar(transporte=transporte)

    assert resultado.cstat == '596'
    assert resultado.xmotivo == \
        'Rejeicao: NF-e nao consta na base de dados da SEFAZ'
    assert resultado.registrado is False
    assert resultado.duplicidade is False
    assert resultado.indefinido is False


def test_erro_no_lote_sem_retevento_vira_rejeicao_do_lote():
    """A SEFAZ pode recusar o LOTE inteiro (schema invalido, por exemplo) e nao
    devolver `retEvento` nenhum."""
    transporte = _Transporte('''<?xml version="1.0"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body>
<retEnvEvento xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.00">
<cStat>215</cStat><xMotivo>Rejeicao: Falha no schema XML</xMotivo>
</retEnvEvento></soap:Body></soap:Envelope>''')
    resultado, _t = _enviar(transporte=transporte)

    assert resultado.cstat == '215'
    assert 'schema' in resultado.xmotivo.lower()
    assert resultado.registrado is False
    assert resultado.indefinido is False


# --- "nao enviei" x "enviei e nao sei" (MANIF-17) ---------------------------

def test_falha_de_conexao_antes_do_envio_e_retentavel():
    """Sem conexao o evento nao saiu: repetir e seguro."""
    transporte = _Transporte(
        erro=requests.exceptions.ConnectionError('sem rota para o host'))
    resultado, _t = _enviar(transporte=transporte)

    assert resultado.indefinido is False
    assert resultado.registrado is False
    assert 'rota' in resultado.erro


def test_timeout_de_leitura_e_INDEFINIDO():
    """O caso caro: o pedido saiu e a resposta nao voltou. Tratar como falha
    reenviaria um evento ja protocolado; tratar como sucesso perderia um evento
    que talvez nao exista. Quem decide e o operador."""
    transporte = _Transporte(
        erro=requests.exceptions.ReadTimeout('tempo esgotado lendo a resposta'))
    resultado, _t = _enviar(transporte=transporte)

    assert resultado.indefinido is True
    assert resultado.registrado is False


def test_conexao_cortada_no_meio_da_resposta_e_INDEFINIDO():
    transporte = _Transporte(
        erro=requests.exceptions.ChunkedEncodingError('conexao caiu'))
    resultado, _t = _enviar(transporte=transporte)

    assert resultado.indefinido is True


def test_http_500_e_INDEFINIDO():
    """A SEFAZ pode ter processado o evento e falhado ao responder."""
    transporte = _Transporte(status=500, resposta='<html>erro interno</html>')
    resultado, _t = _enviar(transporte=transporte)

    assert resultado.indefinido is True
    assert resultado.registrado is False


def test_http_403_nao_e_indefinido_porque_nada_foi_processado():
    """Certificado recusado significa que a requisicao morreu no handshake de
    autorizacao: nao ha evento pendurado do outro lado."""
    transporte = _Transporte(status=403, resposta='<html>Forbidden</html>')
    resultado, _t = _enviar(transporte=transporte)

    assert resultado.indefinido is False
    assert resultado.registrado is False


def test_resposta_ilegivel_com_http_200_e_INDEFINIDO():
    """200 com corpo que nao e o retorno esperado: o evento pode ter sido
    processado, e adivinhar erraria em uma das duas direcoes."""
    transporte = _Transporte(resposta='<html>manutencao programada</html>')
    resultado, _t = _enviar(transporte=transporte)

    assert resultado.indefinido is True


def test_bruto_da_resposta_e_preservado_para_diagnostico():
    resultado, _t = _enviar()
    assert '<retEnvEvento' in resultado.bruto


def test_evento_sem_assinatura_e_recusado_antes_de_sair():
    """Rede de seguranca: mandar evento sem assinatura queima uma requisicao e
    volta rejeicao de schema."""
    raiz = svc.montar_evento(chave=CHAVE, cnpj_destinatario='11222333000181')

    with pytest.raises(nfe_sefaz.SefazError):
        nfe_sefaz.enviar_evento(raiz, sessao=_Transporte())
