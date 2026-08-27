"""Assinatura XMLDSig no perfil da NF-e (MANIF-12).

Tres camadas de prova, e as tres sao necessarias:

1. **Pino de regressao** — `assinar` tem de reproduzir, byte a byte, o
   `DigestValue` e o `SignatureValue` congelados abaixo. Esses valores foram
   gerados em 2026-08-17 por esta mesma implementacao, num momento em que ela
   estava **comprovadamente correta**: ela acabava de validar a assinatura de 3
   NF-e reais emitidas e assinadas por terceiros (`recon.md` §4-5, com os
   digests oficiais registrados). Sem o pino, o teste de ida e volta sozinho
   passaria com uma implementacao errada porem autoconsistente — assinar e
   conferir do mesmo jeito errado fecha.
2. **Ida e volta** — `assinar` seguido de `verificar` fecha, inclusive depois de
   serializar e reparsear (que e o caminho que a SEFAZ enxerga).
3. **Discriminacao** — alterar um byte tem de fazer `verificar` falhar. Um
   verificador que sempre devolve True passaria em (2) e so morre aqui.

**Por que nao ha NF-e real neste diretorio:** ela traria CNPJ, endereco,
telefone, inscricao estadual e os itens vendidos de um cliente do escritorio, e
documento de cliente nao precisa morar no repositorio. A prova contra dado real
foi feita e esta registrada em `recon.md`; o pino carrega o resultado dela para
ca sem carregar os dados.

Perfil medido em NF-e reais, nao lembrado: DigestMethod SHA-1, SignatureMethod
RSA-SHA1, C14N 1.0, transforms enveloped-signature + C14N.
"""
import xml.etree.ElementTree as ET
from pathlib import Path

from cryptography.hazmat.primitives.serialization import (
    load_pem_private_key,
    pkcs12,
)
from cryptography.x509 import load_pem_x509_certificate

from app.services import nfe_assinatura as assin
from tests.test_manifestador_cofre import _fazer_pfx

NS_NFE = 'http://www.portalfiscal.inf.br/nfe'
NS_DSIG = 'http://www.w3.org/2000/09/xmldsig#'

# Chave FIXA de teste (nao e de ninguem; gerada para este arquivo). Precisa ser
# fixa porque RSA-SHA1 PKCS#1 v1.5 e deterministico: mesma chave + mesmos bytes
# canonicos = mesma assinatura, e e isso que torna o pino possivel.
CHAVE_PINO = Path(__file__).parent / 'fixtures' / 'chave_de_teste.pem'

ID_PINO = 'ID21020043170122333444000181650010000045391000045390' + '01'
# Valores congelados sobre a fixture de teste. Recalculados em 26/08/2026,
# quando o CNPJ real da chave saiu do repositorio: o ALGORITMO nao mudou
# (SHA-1 / RSA-SHA1 / C14N 1.0, AD-027), so o conteudo assinado.
DIGEST_ESPERADO = 'NpsXPIwP0Not72fVXO8ApHNn3ak='
ASSINATURA_ESPERADA = (
    'mObcxp+iUOQ3FyBojk0pGdKQWtbYU3FCSFJirZpGZtITlf4rf0ozUQ8TaIzNxpRN'
    '28zk5T4plqL7UigAvz/xAWAVOE8yfT5x1MOX4VbS13nYf81w4r3s0KSMgb99I4a/'
    '9e/SAEjlIC89PToz3y7I4+Rs+Kpg4TpVeIj/XlNv5lw5tuJIlfj1+klaSX8Sylpy'
    'MFX6m3D0MOqQE60QhuDududD4bPvNpQ6TPjqrO08L8b/enKShkg5uixgRhCv8CAd'
    'FWzNa661SE0ARGQuLrBM9F/ujAfEn+5u0Wi1XAWXOJ02oMDSEF1OM6N3LFrl3unP'
    'xSWY9NBAQ57gGV20eIOhsA=='
)


def _par_do_pino():
    """(chave, certificado) fixos — os que geraram os valores congelados."""
    pem = CHAVE_PINO.read_bytes()
    return load_pem_private_key(pem, password=None), load_pem_x509_certificate(pem)


def _par_de_teste():
    """(chave privada, certificado) recem-gerados."""
    chave, cert, _cadeia = pkcs12.load_key_and_certificates(
        _fazer_pfx(cn='EMPRESA X LTDA:11222333000181'), b'123456')
    return chave, cert


def _evento_de_teste(id_valor='ID21020043170122333444000181650010000045391000045390' + '01'):
    """Um `infEvento` cru, no formato que o T9 vai montar."""
    ET.register_namespace('', NS_NFE)
    raiz = ET.Element(f'{{{NS_NFE}}}evento', {'versao': '1.00'})
    inf = ET.SubElement(raiz, f'{{{NS_NFE}}}infEvento', {'Id': id_valor})
    ET.SubElement(inf, f'{{{NS_NFE}}}cOrgao').text = '91'
    ET.SubElement(inf, f'{{{NS_NFE}}}tpAmb').text = '1'
    ET.SubElement(inf, f'{{{NS_NFE}}}CNPJ').text = '11222333000181'
    ET.SubElement(inf, f'{{{NS_NFE}}}tpEvento').text = '210200'
    return raiz, inf


# --- 1. pino de regressao ---------------------------------------------------

def _evento_do_pino():
    ET.register_namespace('', NS_NFE)
    raiz = ET.Element(f'{{{NS_NFE}}}evento', {'versao': '1.00'})
    inf = ET.SubElement(raiz, f'{{{NS_NFE}}}infEvento', {'Id': ID_PINO})
    for tag, valor in (('cOrgao', '91'), ('tpAmb', '1'),
                       ('CNPJ', '11222333000181'), ('tpEvento', '210200')):
        ET.SubElement(inf, f'{{{NS_NFE}}}{tag}').text = valor
    return raiz, inf


def test_digest_reproduz_o_valor_congelado():
    """Se a canonicalizacao mudar, este digest muda — e a SEFAZ passaria a
    rejeitar sem que nenhum outro teste percebesse."""
    chave, cert = _par_do_pino()
    raiz, inf = _evento_do_pino()

    assin.assinar(raiz, inf, chave, cert)

    assert raiz.find(f'.//{{{NS_DSIG}}}DigestValue').text == DIGEST_ESPERADO


def test_assinatura_reproduz_o_valor_congelado():
    """Cobre o SEGUNDO C14N — o do SignedInfo. Um erro so ali deixaria o digest
    certo e a assinatura errada, e o evento voltaria rejeitado."""
    chave, cert = _par_do_pino()
    raiz, inf = _evento_do_pino()

    assin.assinar(raiz, inf, chave, cert)

    assert raiz.find(f'.//{{{NS_DSIG}}}SignatureValue').text == \
        ASSINATURA_ESPERADA


def test_perfil_de_algoritmos_e_o_da_nfe():
    """Trava o perfil medido nas NF-e reais (`recon.md` §5). Trocar SHA-1 por
    SHA-256 faz a SEFAZ rejeitar, e so este teste diria isso."""
    chave, cert = _par_do_pino()
    raiz, inf = _evento_do_pino()
    assin.assinar(raiz, inf, chave, cert)
    sig = raiz.find(f'.//{{{NS_DSIG}}}Signature')

    assert sig.find(f'.//{{{NS_DSIG}}}SignatureMethod').get('Algorithm') == \
        'http://www.w3.org/2000/09/xmldsig#rsa-sha1'
    assert sig.find(f'.//{{{NS_DSIG}}}DigestMethod').get('Algorithm') == \
        'http://www.w3.org/2000/09/xmldsig#sha1'
    assert sig.find(f'{{{NS_DSIG}}}SignedInfo/'
                    f'{{{NS_DSIG}}}CanonicalizationMethod').get('Algorithm') == \
        'http://www.w3.org/TR/2001/REC-xml-c14n-20010315'
    assert [t.get('Algorithm') for t in sig.iter(f'{{{NS_DSIG}}}Transform')] == [
        'http://www.w3.org/2000/09/xmldsig#enveloped-signature',
        'http://www.w3.org/TR/2001/REC-xml-c14n-20010315',
    ]


# --- 2. ida e volta ---------------------------------------------------------

def test_assinar_e_verificar_fecham():
    chave, cert = _par_de_teste()
    raiz, inf = _evento_de_teste()

    assin.assinar(raiz, inf, chave, cert)

    assert assin.verificar(raiz) is True


def test_assinatura_entra_como_irma_do_bloco_assinado():
    """Na NF-e a `<Signature>` fica ao lado do bloco assinado, dentro do pai —
    nao dentro dele. Errar a posicao muda o que o enveloped-transform remove."""
    chave, cert = _par_de_teste()
    raiz, inf = _evento_de_teste()

    assin.assinar(raiz, inf, chave, cert)

    filhos = [f.tag for f in raiz]
    assert filhos == [f'{{{NS_NFE}}}infEvento', f'{{{NS_DSIG}}}Signature']
    assert inf.find(f'{{{NS_DSIG}}}Signature') is None


def test_referencia_aponta_para_o_id_com_cerquilha():
    chave, cert = _par_de_teste()
    raiz, inf = _evento_de_teste(id_valor='ID2102001234500001')

    assin.assinar(raiz, inf, chave, cert)

    ref = raiz.find(f'.//{{{NS_DSIG}}}Reference')
    assert ref.get('URI') == '#ID2102001234500001'


def test_certificado_vai_embutido_no_keyinfo():
    """A SEFAZ precisa do certificado para conferir a assinatura."""
    chave, cert = _par_de_teste()
    raiz, inf = _evento_de_teste()

    assin.assinar(raiz, inf, chave, cert)

    x509 = raiz.find(f'.//{{{NS_DSIG}}}X509Certificate')
    assert x509 is not None and x509.text
    assert 'BEGIN CERTIFICATE' not in x509.text  # base64 puro, sem cabecalho PEM


def test_assinar_sem_id_e_recusado():
    chave, cert = _par_de_teste()
    raiz, inf = _evento_de_teste()
    del inf.attrib['Id']

    try:
        assin.assinar(raiz, inf, chave, cert)
        levantou = False
    except assin.AssinaturaError:
        levantou = True
    assert levantou is True


# --- 2b. o que sai pela rede -------------------------------------------------

def test_documento_serializado_continua_valido_ao_ser_reparseado():
    """O teste que decide se a SEFAZ aceita: ela nao recebe nossos objetos, ela
    recebe BYTES, e reconstroi o C14N a partir deles. Se a serializacao usar um
    prefixo diferente do que foi canonicalizado na hora de assinar, o digest nao
    fecha do lado de la e o evento volta rejeitado — sem nenhum sinal aqui."""
    chave, cert = _par_de_teste()
    raiz, inf = _evento_de_teste()
    assin.assinar(raiz, inf, chave, cert)

    bytes_enviados = assin.serializar_documento(raiz).encode('utf-8')
    reparseado = ET.fromstring(bytes_enviados)

    assert assin.verificar(reparseado) is True


def test_serializacao_usa_a_forma_da_nfe_real():
    """`<evento xmlns="...nfe">` com `<Signature xmlns="...xmldsig#">` dentro,
    redeclarando o default — exatamente como a nota real do drive."""
    chave, cert = _par_de_teste()
    raiz, inf = _evento_de_teste()
    assin.assinar(raiz, inf, chave, cert)

    texto = assin.serializar_documento(raiz)

    assert f'<evento xmlns="{NS_NFE}"' in texto
    assert f'<Signature xmlns="{NS_DSIG}">' in texto
    assert 'ns0:' not in texto


# --- 3. discriminacao -------------------------------------------------------

def test_conteudo_alterado_apos_assinar_e_recusado():
    chave, cert = _par_de_teste()
    raiz, inf = _evento_de_teste()
    assin.assinar(raiz, inf, chave, cert)

    inf.find(f'{{{NS_NFE}}}CNPJ').text = '99999999999999'

    assert assin.verificar(raiz) is False


def test_digest_adulterado_e_recusado():
    chave, cert = _par_de_teste()
    raiz, inf = _evento_de_teste()
    assin.assinar(raiz, inf, chave, cert)

    digest = raiz.find(f'.//{{{NS_DSIG}}}DigestValue')
    digest.text = 'AAAA' + (digest.text or '')[4:]

    assert assin.verificar(raiz) is False


def test_signature_value_adulterado_e_recusado():
    """Pega o caso em que o digest confere mas a assinatura RSA nao — ou seja,
    alguem reescreveu o SignedInfo inteiro sem ter a chave privada."""
    chave, cert = _par_de_teste()
    raiz, inf = _evento_de_teste()
    assin.assinar(raiz, inf, chave, cert)

    valor = raiz.find(f'.//{{{NS_DSIG}}}SignatureValue')
    valor.text = 'AAAA' + (valor.text or '')[4:]

    assert assin.verificar(raiz) is False


def test_assinatura_de_outra_chave_e_recusada():
    """Assinado por A, certificado embutido de B: tem de reprovar."""
    chave_a, _cert_a = _par_de_teste()
    _chave_b, cert_b = _par_de_teste()
    raiz, inf = _evento_de_teste()

    assin.assinar(raiz, inf, chave_a, cert_b)

    assert assin.verificar(raiz) is False


def test_verificar_sem_assinatura_devolve_false():
    raiz, _inf = _evento_de_teste()
    assert assin.verificar(raiz) is False
