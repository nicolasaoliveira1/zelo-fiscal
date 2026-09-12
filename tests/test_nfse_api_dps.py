"""Montagem offline de DPS com documentos e nomes sintéticos."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services import nfse_api_dps as dps_api


NS = dps_api.NAMESPACE_NFSE
PRESTADOR_SINTETICO = '11222333000181'
TOMADOR_SINTETICO = '44556677000186'
CHAVE_SINTETICA = 'NFS' + '0' * 50


def _nota(**valores):
    padrao = {
        'status': 'emitida',
        'documento': '44.556.677/0001-86',
        'valor_final': Decimal('1280.40'),
        'competencia': '09/2026',
        'descricao_servico': 'SERVIÇO SINTÉTICO DE ENSAIO',
    }
    padrao.update(valores)
    return SimpleNamespace(**padrao)


def _config(**valores):
    padrao = {
        'municipio_servico_codigo': '4310330',
        'codigo_tributacao': '17.19.01',
        'item_nbs': '113022100',
        'descricao_template': 'HONORÁRIOS SINTÉTICOS {competencia}',
        'regime_apuracao_sn': '1',
        'piscofins_situacao': '0',
        'piscofins_tipo_retencao': '0',
    }
    padrao.update(valores)
    return SimpleNamespace(**padrao)


def _xml_referencia(sem_dcompet=False, com_ibscbs=False, prestador=PRESTADOR_SINTETICO):
    dcompet = '' if sem_dcompet else '<dCompet>2026-06-17</dCompet>'
    ibscbs = '<IBSCBS><finNFSe>0</finNFSe></IBSCBS>' if com_ibscbs else ''
    return f'''<NFSe xmlns="{NS}" versao="1.01">
  <infNFSe Id="{CHAVE_SINTETICA}">
    <DPS versao="1.01"><infDPS>
      <tpAmb>1</tpAmb><dhEmi>2026-06-18T10:00:00-03:00</dhEmi>
      <verAplic>fonte-sintética</verAplic><serie>4</serie><nDPS>8</nDPS>
      {dcompet}<tpEmit>1</tpEmit><cLocEmi>4310330</cLocEmi>
      <prest><CNPJ>{prestador}</CNPJ><xNome>Prestador Sintético</xNome>
        <regTrib><opSimpNac>3</opSimpNac><regApTribSN>1</regApTribSN>
          <regEspTrib>0</regEspTrib></regTrib>
      </prest>
      <toma><CNPJ>{TOMADOR_SINTETICO}</CNPJ><xNome>Tomador Sintético</xNome></toma>
      <serv><locPrest><cLocPrestacao>4310330</cLocPrestacao></locPrest>
        <cServ><cTribNac>171901</cTribNac><xDescServ>SERVIÇO SINTÉTICO DE ENSAIO</xDescServ>
          <cNBS>113022100</cNBS></cServ></serv>
      <valores><vServPrest><vServ>1280.40</vServ></vServPrest>
        <trib><tribMun><tribISSQN>1</tribISSQN><tpRetISSQN>1</tpRetISSQN></tribMun>
          <tribFed><piscofins><CST>00</CST><tpRetPisCofins>0</tpRetPisCofins></piscofins></tribFed>
          <totTrib><indTotTrib>0</indTotTrib></totTrib></trib>
      </valores>{ibscbs}
    </infDPS></DPS>
  </infNFSe>
</NFSe>'''.encode('utf-8')


def _texto(elemento, nome):
    filho = elemento.find(f'{{{NS}}}{nome}')
    return filho.text if filho is not None else None


def test_ler_referencia_preserva_data_completa_do_xml_e_nao_a_competencia_mensal():
    referencia = dps_api.ler_referencia(_nota(), _xml_referencia())

    assert referencia.d_compet == '2026-06-17'
    assert referencia.d_compet != '2026-09-01'
    assert referencia.prestador == PRESTADOR_SINTETICO
    assert referencia.documento_tomador == TOMADOR_SINTETICO
    assert referencia.valor_servico == Decimal('1280.40')


def test_montar_fixa_homologacao_injeta_dh_emi_e_aplica_configuracao():
    referencia = dps_api.ler_referencia(_nota(), _xml_referencia())
    instante = datetime(2026, 9, 12, 14, 35, 20, tzinfo=timezone(timedelta(hours=-3)))

    montada = dps_api.montar(
        referencia, _config(), serie='7', numero=12, agora=instante)
    inf_dps = montada.find(f'{{{NS}}}infDPS')
    serv = inf_dps.find(f'{{{NS}}}serv')
    c_serv = serv.find(f'{{{NS}}}cServ')

    assert montada.get('versao') == '1.01'
    assert _texto(inf_dps, 'tpAmb') == '2'
    assert _texto(inf_dps, 'dhEmi') == '2026-09-12T14:35:20-03:00'
    assert _texto(inf_dps, 'dhEmi') != _texto(inf_dps, 'dCompet')
    assert _texto(inf_dps, 'dCompet') == '2026-06-17'
    assert _texto(inf_dps, 'serie') == '7'
    assert _texto(inf_dps, 'nDPS') == '12'
    assert _texto(serv.find(f'{{{NS}}}locPrest'), 'cLocPrestacao') == '4310330'
    assert _texto(c_serv, 'cTribNac') == '171901'
    assert _texto(c_serv, 'cNBS') == '113022100'
    assert _texto(c_serv, 'xDescServ') == 'SERVIÇO SINTÉTICO DE ENSAIO'
    assert _texto(
        inf_dps.find(f'{{{NS}}}valores/{{{NS}}}trib/{{{NS}}}tribFed/{{{NS}}}piscofins'),
        'CST') == '00'


def test_identificador_segue_o_formato_oficial_de_45_posicoes():
    referencia = dps_api.ler_referencia(_nota(), _xml_referencia())
    montada = dps_api.montar(
        referencia, _config(), serie='7', numero=12,
        agora=datetime(2026, 9, 12, 14, 35, 20, tzinfo=timezone.utc))

    esperado = (
        'DPS4310330' + '1' + PRESTADOR_SINTETICO + '00007'
        + '12'.zfill(15))
    assert len(esperado) == 45
    assert dps_api.identificador(montada) == esperado
    assert montada.find(f'{{{NS}}}infDPS').get('Id') == esperado


def test_nota_pendente_e_xml_ausente_sao_recusados_antes_da_montagem():
    with pytest.raises(dps_api.NotaHistoricaNaoEmitidaError):
        dps_api.ler_referencia(_nota(status='pronta'), _xml_referencia())
    with pytest.raises(dps_api.XmlReferenciaAusenteError):
        dps_api.ler_referencia(_nota(), None)


def test_dcompet_ausente_e_documento_nao_representavel_sao_recusados():
    with pytest.raises(dps_api.CampoObrigatorioAusenteError):
        dps_api.ler_referencia(_nota(), _xml_referencia(sem_dcompet=True))
    with pytest.raises(dps_api.DocumentoNaoRepresentavelError):
        dps_api.ler_referencia(_nota(), _xml_referencia(prestador='1234567890123A'))


def test_ibscbs_fica_fora_do_primeiro_ensaio():
    with pytest.raises(dps_api.IbscbsForaEscopoError):
        dps_api.ler_referencia(_nota(), _xml_referencia(com_ibscbs=True))


def test_montar_nao_recebe_ambiente_publico():
    referencia = dps_api.ler_referencia(_nota(), _xml_referencia())
    with pytest.raises(TypeError):
        dps_api.montar(
            referencia, _config(), serie='7', numero=12,
            agora=datetime(2026, 9, 12, tzinfo=timezone.utc),
            ambiente='restrita')
