"""Montagem offline de DPS com documentos e nomes sintéticos."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

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


def _montada():
    referencia = dps_api.ler_referencia(_nota(), _xml_referencia())
    return dps_api.montar(
        referencia, _config(), serie='7', numero=12,
        agora=datetime(2026, 9, 12, 14, 35, 20, tzinfo=timezone.utc))


def _credencial_sintetica():
    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, 'BR'),
        x509.NameAttribute(NameOID.COMMON_NAME, 'Certificado Sintético'),
    ])
    certificado = (
        x509.CertificateBuilder()
        .subject_name(nome)
        .issuer_name(nome)
        .public_key(chave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime(2026, 1, 1, tzinfo=timezone.utc))
        .not_valid_after(datetime(2027, 1, 1, tzinfo=timezone.utc))
        .sign(chave, hashes.SHA256()))
    return chave, certificado


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


def test_validar_carrega_o_xsd_restrito_e_aceita_dps_sintetica():
    referencia = dps_api.ler_referencia(_nota(), _xml_referencia())
    montada = dps_api.montar(
        referencia, _config(), serie='7', numero=12,
        agora=datetime(2026, 9, 12, 14, 35, 20, tzinfo=timezone.utc))

    assert dps_api.validar(montada) == []


def test_validar_traduz_caminho_de_campo_reprovado_sem_expor_valor():
    referencia = dps_api.ler_referencia(_nota(), _xml_referencia())
    montada = dps_api.montar(
        referencia, _config(), serie='7', numero=12,
        agora=datetime(2026, 9, 12, 14, 35, 20, tzinfo=timezone.utc))
    c_trib_nac = montada.find(
        f'.//{{{NS}}}cTribNac')
    c_trib_nac.text = 'X'

    problemas = dps_api.validar(montada)

    assert problemas
    assert any('/DPS/infDPS/serv/cServ/cTribNac' == problema.caminho
               for problema in problemas)
    assert all('X' not in problema.mensagem for problema in problemas)


def test_validar_aponta_grupo_quando_campo_obrigatorio_e_removido():
    referencia = dps_api.ler_referencia(_nota(), _xml_referencia())
    montada = dps_api.montar(
        referencia, _config(), serie='7', numero=12,
        agora=datetime(2026, 9, 12, 14, 35, 20, tzinfo=timezone.utc))
    c_serv = montada.find(f'.//{{{NS}}}cServ')
    c_serv.remove(c_serv.find(f'{{{NS}}}cTribNac'))

    problemas = dps_api.validar(montada)

    assert any('/DPS/infDPS/serv/cServ' == problema.caminho
               for problema in problemas)


def test_validar_aponta_campo_com_tamanho_invalido():
    referencia = dps_api.ler_referencia(_nota(), _xml_referencia())
    montada = dps_api.montar(
        referencia, _config(), serie='7', numero=12,
        agora=datetime(2026, 9, 12, 14, 35, 20, tzinfo=timezone.utc))
    montada.find(f'.//{{{NS}}}serie').text = '123456'

    problemas = dps_api.validar(montada)

    assert any('/DPS/infDPS/serie' == problema.caminho
               for problema in problemas)


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


def test_assinar_reutiliza_o_perfil_xml_dsig_e_verifica_o_id_oficial():
    montada = _montada()
    chave, certificado = _credencial_sintetica()

    assinada = dps_api.assinar(montada, chave, certificado)
    assinatura = assinada.find(
        f'{{{dps_api.nfe_assinatura.NS_DSIG}}}Signature')
    signed_info = assinatura.find(
        f'{{{dps_api.nfe_assinatura.NS_DSIG}}}SignedInfo')
    referencia = assinatura.find(
        f'.//{{{dps_api.nfe_assinatura.NS_DSIG}}}Reference')

    assert assinada is montada
    assert dps_api.verificar(assinada) is True
    assert referencia.get('URI') == '#' + dps_api.identificador(montada)
    assert signed_info.find(
        f'{{{dps_api.nfe_assinatura.NS_DSIG}}}CanonicalizationMethod').get(
            'Algorithm') == dps_api.nfe_assinatura.ALG_C14N
    assert signed_info.find(
        f'{{{dps_api.nfe_assinatura.NS_DSIG}}}SignatureMethod').get(
            'Algorithm') == dps_api.nfe_assinatura.ALG_ASSINATURA
    assert signed_info.find(
        f'.//{{{dps_api.nfe_assinatura.NS_DSIG}}}DigestMethod').get(
            'Algorithm') == dps_api.nfe_assinatura.ALG_DIGEST
    transformacoes = signed_info.findall(
        f'.//{{{dps_api.nfe_assinatura.NS_DSIG}}}Transform')
    assert [transformacao.get('Algorithm') for transformacao in transformacoes] == [
        dps_api.nfe_assinatura.ALG_ENVELOPED,
        dps_api.nfe_assinatura.ALG_C14N,
    ]
    assert dps_api.validar(assinada) == []


def test_assinatura_falha_apos_adulterar_campo_da_dps():
    montada = _montada()
    chave, certificado = _credencial_sintetica()
    dps_api.assinar(montada, chave, certificado)

    montada.find(f'.//{{{NS}}}xDescServ').text += ' ADULTERADO'

    assert dps_api.verificar(montada) is False


def test_assinar_recusa_id_que_nao_seja_o_calculado():
    montada = _montada()
    montada.find(f'.//{{{NS}}}infDPS').set('Id', 'DPS' + '1' * 42)
    chave, certificado = _credencial_sintetica()

    with pytest.raises(dps_api.AssinaturaDpsInvalidaError):
        dps_api.assinar(montada, chave, certificado)
