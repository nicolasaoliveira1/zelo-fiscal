r"""Assinatura XMLDSig no perfil da NF-e (MANIF-12, AD-027).

Generico de proposito: assina um ELEMENTO identificado por `Id`, sem saber o que
e evento, NF-e ou qualquer outra coisa. O `manifestador_service` monta o evento;
este modulo so o assina.

**Sem biblioteca de XMLDSig.** `xml.etree.ElementTree.canonicalize` implementa
C14N 2.0 e a NF-e exige C14N 1.0 — mas para este formato de documento (namespace
default unico, sem prefixos, sem comentarios, sem `xml:`) os dois produzem os
mesmos bytes. Isso nao foi deduzido: foi medido reproduzindo o `DigestValue` e
validando o `SignatureValue` RSA de 3 NF-e reais assinadas (`recon.md` §4-5).
`lxml`, `signxml` e `zeep` resolveriam um problema que nao existe.

Perfil, tambem medido nas mesmas notas:

    DigestMethod           SHA-1
    SignatureMethod        RSA-SHA1
    CanonicalizationMethod C14N 1.0 (REC-xml-c14n-20010315)
    Transforms             enveloped-signature, C14N 1.0
    Signature              IRMA do bloco assinado, dentro do pai

SHA-1 nao e escolha: e o que o schema da NF-e exige. Trocar por SHA-256 faz a
SEFAZ rejeitar.
"""
import base64
import hashlib
import xml.etree.ElementTree as ET
from threading import Lock

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509 import load_der_x509_certificate

NS_DSIG = 'http://www.w3.org/2000/09/xmldsig#'

ALG_C14N = 'http://www.w3.org/TR/2001/REC-xml-c14n-20010315'
ALG_ENVELOPED = 'http://www.w3.org/2000/09/xmldsig#enveloped-signature'
ALG_DIGEST = 'http://www.w3.org/2000/09/xmldsig#sha1'
ALG_ASSINATURA = 'http://www.w3.org/2000/09/xmldsig#rsa-sha1'

# Protege o registro global de namespaces do ElementTree durante a
# serializacao — ver `serializar`.
_LOCK_NAMESPACES = Lock()


class AssinaturaError(Exception):
    """Nao da para assinar o que foi pedido."""


def _ns_de(elemento):
    """URI do namespace do proprio elemento (tags vem como `{uri}local`)."""
    tag = elemento.tag
    return tag[1:tag.index('}')] if tag.startswith('{') else None


def serializar(elemento):
    """XML do elemento com o namespace DELE como default (sem prefixo).

    E a convencao da NF-e real, conferida byte a byte numa nota do drive:
    `<NFe xmlns="...portalfiscal.inf.br/nfe">` e, dentro dela,
    `<Signature xmlns="...xmldsig#">` **redeclarando** o default. Nao e estetica:
    o C14N preserva prefixos, entao o digest so bate se o documento enviado tiver
    exatamente a forma que foi canonicalizada.

    Duas armadilhas do `ElementTree` moram aqui:

    - `tostring(default_namespace=...)` **nao serve**: ele recusa qualquer
      atributo sem namespace, e `Id`, `versao` e `Algorithm` sao exatamente
      isso. O unico mecanismo do stdlib que controla prefixo e o registro
      global de namespaces.
    - esse registro e **global ao processo**, e o lote roda numa thread enquanto
      as requisicoes rodam em outras. Por isso o registro e escopado por um lock
      e o mapa inteiro e restaurado no `finally` — sem isso, uma serializacao
      concorrente sairia com o prefixo do vizinho e o digest nao bateria.
    """
    ns = _ns_de(elemento)
    if not ns:
        return ET.tostring(elemento, encoding='unicode')

    with _LOCK_NAMESPACES:
        anterior = dict(ET._namespace_map)
        try:
            ET.register_namespace('', ns)
            return ET.tostring(elemento, encoding='unicode')
        finally:
            ET._namespace_map.clear()
            ET._namespace_map.update(anterior)


def _canonicalizar(elemento):
    """Bytes canonicos do elemento, com o namespace herdado ja materializado.

    Serializar o subtree faz o `xmlns` aparecer no proprio elemento, que e
    exatamente o que o C14N produz para um no que herda namespace do pai."""
    return ET.canonicalize(xml_data=serializar(elemento)).encode('utf-8')


def serializar_documento(container):
    """Bytes a enviar: cada bloco no seu proprio namespace default.

    Nao da para pedir isso ao `ElementTree` de uma vez — ele so tem UM default
    por serializacao, entao a `<Signature>` sairia prefixada (`ns0:`). E o
    prefixo nao e cosmetico: a SEFAZ recalcula o C14N a partir dos bytes que
    recebe, e um `SignedInfo` prefixado canonicaliza diferente do que foi
    assinado — o evento voltaria rejeitado sem nenhum sinal do nosso lado.

    Por isso os dois blocos sao serializados em separado, cada um com seu
    namespace como default, e a assinatura e costurada no lugar. E a mesma forma
    da NF-e real do drive, conferida byte a byte."""
    assinaturas = [f for f in container if f.tag == f'{{{NS_DSIG}}}Signature']
    if not assinaturas:
        return serializar(container)

    for nodo in assinaturas:
        container.remove(nodo)
    try:
        corpo = serializar(container)
    finally:
        for nodo in assinaturas:
            container.append(nodo)

    costura = ''.join(serializar(nodo) for nodo in assinaturas)
    fechamento = f'</{container.tag.split("}")[-1]}>'
    if not corpo.rstrip().endswith(fechamento):
        raise AssinaturaError(
            'Nao consegui costurar a assinatura: o elemento raiz veio numa '
            'forma inesperada.')
    corte = corpo.rstrip()[:-len(fechamento)]
    return f'{corte}{costura}{fechamento}'


def _digest(elemento):
    return base64.b64encode(hashlib.sha1(_canonicalizar(elemento)).digest()).decode()


def _sem_assinatura(elemento):
    """Copia do elemento sem `<Signature>` — o efeito do enveloped-transform.

    Necessario porque a assinatura da NF-e fica ao LADO do bloco assinado; se
    algum fluxo a colocar dentro, o transform tem de remove-la antes do digest.
    """
    copia = ET.fromstring(ET.tostring(elemento, encoding='unicode'))
    for pai in copia.iter():
        for filho in list(pai):
            if filho.tag == f'{{{NS_DSIG}}}Signature':
                pai.remove(filho)
    return copia


def assinar(pai, elemento, chave_privada, certificado):
    """Assina `elemento` e pendura a `<Signature>` como irma dele em `pai`.

    Devolve o no `<Signature>` criado."""
    id_valor = elemento.get('Id')
    if not id_valor:
        raise AssinaturaError(
            'O elemento a assinar precisa de um atributo Id — e ele que a '
            'Reference da assinatura aponta.')

    digest = _digest(_sem_assinatura(elemento))

    signed_info = ET.Element(f'{{{NS_DSIG}}}SignedInfo')
    ET.SubElement(signed_info, f'{{{NS_DSIG}}}CanonicalizationMethod',
                  {'Algorithm': ALG_C14N})
    ET.SubElement(signed_info, f'{{{NS_DSIG}}}SignatureMethod',
                  {'Algorithm': ALG_ASSINATURA})
    referencia = ET.SubElement(signed_info, f'{{{NS_DSIG}}}Reference',
                               {'URI': f'#{id_valor}'})
    transforms = ET.SubElement(referencia, f'{{{NS_DSIG}}}Transforms')
    ET.SubElement(transforms, f'{{{NS_DSIG}}}Transform',
                  {'Algorithm': ALG_ENVELOPED})
    ET.SubElement(transforms, f'{{{NS_DSIG}}}Transform', {'Algorithm': ALG_C14N})
    ET.SubElement(referencia, f'{{{NS_DSIG}}}DigestMethod',
                  {'Algorithm': ALG_DIGEST})
    ET.SubElement(referencia, f'{{{NS_DSIG}}}DigestValue').text = digest

    assinatura_bytes = chave_privada.sign(
        _canonicalizar(signed_info), padding.PKCS1v15(), hashes.SHA1())

    nodo = ET.Element(f'{{{NS_DSIG}}}Signature')
    nodo.append(signed_info)
    ET.SubElement(nodo, f'{{{NS_DSIG}}}SignatureValue').text = base64.b64encode(
        assinatura_bytes).decode()
    key_info = ET.SubElement(nodo, f'{{{NS_DSIG}}}KeyInfo')
    x509_data = ET.SubElement(key_info, f'{{{NS_DSIG}}}X509Data')
    # base64 do DER, sem cabecalho PEM — e o que o schema da NF-e espera
    ET.SubElement(x509_data, f'{{{NS_DSIG}}}X509Certificate').text = \
        base64.b64encode(certificado.public_bytes(Encoding.DER)).decode()

    pai.append(nodo)
    return nodo


def _por_id(container, id_valor):
    """O elemento de `Id` dado, procurando o proprio container tambem."""
    if container.get('Id') == id_valor:
        return container
    for no in container.iter():
        if no.get('Id') == id_valor:
            return no
    return None


def verificar(container):
    """A assinatura dentro de `container` confere? Nunca levanta — devolve False.

    Recebe o CONTAINER (o `<evento>`, o `<NFe>`, o `<nfeProc>`), nao o bloco
    assinado: no perfil da NF-e a `<Signature>` e IRMA do bloco, e no
    `ElementTree` um elemento nao conhece o proprio pai — passar so o bloco
    tornaria a assinatura inalcancavel. Alem disso e assim que um verificador de
    verdade trabalha: acha a assinatura, le a `Reference` e resolve o `Id` que
    ela aponta.

    Confere as DUAS coisas, porque cada uma pega um ataque diferente: o digest
    prova que o conteudo nao mudou; o `SignatureValue` prova que quem montou o
    `SignedInfo` tinha a chave privada. Validar so o digest deixaria passar um
    `SignedInfo` reescrito por quem nao tem a chave.

    Este verificador foi validado contra NF-e reais assinadas pelo emitente
    (`recon.md` §5) — por isso ele serve de PROVA para a assinatura que geramos,
    e nao apenas de espelho dela."""
    try:
        sig = container.find(f'.//{{{NS_DSIG}}}Signature')
        if sig is None:
            return False

        signed_info = sig.find(f'{{{NS_DSIG}}}SignedInfo')
        referencia = sig.find(f'.//{{{NS_DSIG}}}Reference')
        digest_oficial = sig.find(f'.//{{{NS_DSIG}}}DigestValue').text.strip()
        assinatura = base64.b64decode(
            ''.join(sig.find(f'{{{NS_DSIG}}}SignatureValue').text.split()))
        cert_b64 = ''.join(
            sig.find(f'.//{{{NS_DSIG}}}X509Certificate').text.split())
        certificado = load_der_x509_certificate(base64.b64decode(cert_b64))

        assinado = _por_id(container, (referencia.get('URI') or '').lstrip('#'))
        if assinado is None:
            return False

        if _digest(_sem_assinatura(assinado)) != digest_oficial:
            return False

        certificado.public_key().verify(
            assinatura, _canonicalizar(signed_info), padding.PKCS1v15(),
            hashes.SHA1())
        return True
    except (InvalidSignature, AttributeError, ValueError, TypeError,
            ET.ParseError):
        return False
