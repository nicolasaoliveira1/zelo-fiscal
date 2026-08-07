r"""Descoberta do certificado no repositorio do Windows (store "MY").

O filtro da policy `AutoSelectCertificateForUrls` casa por CN do issuer **e** do
subject juntos. O subject e estavel — nome do titular + CPF/CNPJ, que nao mudam
na renovacao —, mas o **issuer muda**: o e-CPF do escritorio saiu da
"AC DIGITALSIGN RFB G3" para a "AC SyngularID Multipla" na renovacao de
30/07/2026, e a policy com o issuer antigo fixado deixou de casar com qualquer
certificado. O sintoma nao e erro nenhum: o Chrome simplesmente volta a exibir o
dialogo "Selecione um certificado" e o lote fica parado esperando um clique.

Por isso o issuer e **descoberto aqui**, e nao configurado: dado o CN do subject,
varremos a store e devolvemos o issuer do certificado valido (dentro da
validade, com chave privada) de vencimento mais distante. Isso tambem resolve a
ambiguidade da ND-006 sem afrouxar o filtro — o certificado vencido de mesmo
subject fica fora da escolha, e a policy gravada continua com ISSUER + SUBJECT,
tao estreita quanto antes.

Duas decisoes de implementacao:

- ctypes/crypt32 direto, sem dependencia nova e sem subir um `powershell` a cada
  ativacao de lote (o processo custaria mais que a varredura inteira);
- as duas stores sao varridas (usuario e maquina), porque o dialogo do Chrome
  tambem oferece as duas: filtrar so a do usuario funcionaria nesta maquina e
  falharia em silencio numa instalacao machine-wide.

Nada aqui levanta: sem Windows, sem crypt32 ou sem certificado a funcao devolve
None e quem chama cai no issuer configurado.
"""
import ctypes
import os
from ctypes import wintypes

from app.services.execution_logger import log_event

CERT_STORE_PROV_SYSTEM_W = 10
CERT_STORE_READONLY_FLAG = 0x00008000
CERT_SYSTEM_STORE_CURRENT_USER = 1 << 16
CERT_SYSTEM_STORE_LOCAL_MACHINE = 2 << 16

CERT_NAME_ATTR_TYPE = 3
CERT_NAME_ISSUER_FLAG = 0x1
CERT_KEY_PROV_INFO_PROP_ID = 2

OID_COMMON_NAME = b'2.5.4.3'

_STORES = (
    ('usuario', CERT_SYSTEM_STORE_CURRENT_USER),
    ('maquina', CERT_SYSTEM_STORE_LOCAL_MACHINE),
)


class _CryptBlob(ctypes.Structure):
    _fields_ = [
        ('cbData', wintypes.DWORD),
        ('pbData', ctypes.POINTER(ctypes.c_byte)),
    ]


class _CryptAlgId(ctypes.Structure):
    _fields_ = [
        ('pszObjId', ctypes.c_char_p),
        ('Parameters', _CryptBlob),
    ]


class _CryptBitBlob(ctypes.Structure):
    _fields_ = [
        ('cbData', wintypes.DWORD),
        ('pbData', ctypes.POINTER(ctypes.c_byte)),
        ('cUnusedBits', wintypes.DWORD),
    ]


class _CertPublicKeyInfo(ctypes.Structure):
    _fields_ = [
        ('Algorithm', _CryptAlgId),
        ('PublicKey', _CryptBitBlob),
    ]


class _CertInfo(ctypes.Structure):
    _fields_ = [
        ('dwVersion', wintypes.DWORD),
        ('SerialNumber', _CryptBlob),
        ('SignatureAlgorithm', _CryptAlgId),
        ('Issuer', _CryptBlob),
        ('NotBefore', wintypes.FILETIME),
        ('NotAfter', wintypes.FILETIME),
        ('Subject', _CryptBlob),
        ('SubjectPublicKeyInfo', _CertPublicKeyInfo),
        ('IssuerUniqueId', _CryptBitBlob),
        ('SubjectUniqueId', _CryptBitBlob),
        ('cExtension', wintypes.DWORD),
        ('rgExtension', ctypes.c_void_p),
    ]


class _CertContext(ctypes.Structure):
    _fields_ = [
        ('dwCertEncodingType', wintypes.DWORD),
        ('pbCertEncoded', ctypes.POINTER(ctypes.c_byte)),
        ('cbCertEncoded', wintypes.DWORD),
        ('pCertInfo', ctypes.POINTER(_CertInfo)),
        ('hCertStore', ctypes.c_void_p),
    ]


def _carregar_crypt32():
    """crypt32 com argtypes declarados, ou None fora do Windows."""
    if os.name != 'nt':
        return None

    try:
        crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    except (OSError, AttributeError):
        return None

    crypt32.CertOpenStore.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
    ]
    crypt32.CertOpenStore.restype = ctypes.c_void_p

    crypt32.CertCloseStore.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    crypt32.CertCloseStore.restype = wintypes.BOOL

    crypt32.CertEnumCertificatesInStore.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    crypt32.CertEnumCertificatesInStore.restype = ctypes.POINTER(_CertContext)

    crypt32.CertGetNameStringW.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
    ]
    crypt32.CertGetNameStringW.restype = wintypes.DWORD

    crypt32.CertVerifyTimeValidity.argtypes = [ctypes.c_void_p, ctypes.POINTER(_CertInfo)]
    crypt32.CertVerifyTimeValidity.restype = wintypes.LONG

    crypt32.CertGetCertificateContextProperty.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
    ]
    crypt32.CertGetCertificateContextProperty.restype = wintypes.BOOL

    return crypt32


def _cn(crypt32, contexto, do_issuer=False):
    """CN do subject (ou do issuer) do contexto, ja sem espacos nas pontas."""
    flags = CERT_NAME_ISSUER_FLAG if do_issuer else 0
    oid = ctypes.cast(ctypes.create_string_buffer(OID_COMMON_NAME), ctypes.c_void_p)

    tamanho = crypt32.CertGetNameStringW(contexto, CERT_NAME_ATTR_TYPE, flags, oid, None, 0)
    if tamanho <= 1:  # 1 = so o terminador, ou seja, atributo ausente
        return ''

    buffer_ = ctypes.create_unicode_buffer(tamanho)
    crypt32.CertGetNameStringW(
        contexto, CERT_NAME_ATTR_TYPE, flags, oid,
        ctypes.cast(buffer_, ctypes.c_void_p), tamanho,
    )
    return (buffer_.value or '').strip()


def _tem_chave_privada(crypt32, contexto):
    tamanho = wintypes.DWORD(0)
    return bool(crypt32.CertGetCertificateContextProperty(
        contexto, CERT_KEY_PROV_INFO_PROP_ID, None, ctypes.byref(tamanho),
    ))


def _vencimento(info):
    """NotAfter como inteiro comparavel (FILETIME de 64 bits)."""
    ft = info.NotAfter
    return (ft.dwHighDateTime << 32) | ft.dwLowDateTime


def _varrer(crypt32, flag_store, subject_cn_alvo):
    """Candidatos validos de uma store: [(vencimento, issuer_cn)]."""
    handle = crypt32.CertOpenStore(
        ctypes.c_void_p(CERT_STORE_PROV_SYSTEM_W),
        0,
        None,
        flag_store | CERT_STORE_READONLY_FLAG,
        ctypes.c_wchar_p('MY'),
    )
    if not handle:
        return []

    encontrados = []
    try:
        contexto = crypt32.CertEnumCertificatesInStore(handle, None)
        while contexto:
            ponteiro = ctypes.cast(contexto, ctypes.c_void_p)

            if _cn(crypt32, ponteiro).casefold() == subject_cn_alvo:
                # dentro da validade (0) e utilizavel para autenticar (chave privada):
                # o certificado vencido de mesmo subject e justamente o que torna o
                # filtro so-por-subject ambiguo (ND-006)
                dentro_da_validade = crypt32.CertVerifyTimeValidity(
                    None, contexto.contents.pCertInfo,
                ) == 0
                if dentro_da_validade and _tem_chave_privada(crypt32, ponteiro):
                    issuer = _cn(crypt32, ponteiro, do_issuer=True)
                    if issuer:
                        encontrados.append((_vencimento(contexto.contents.pCertInfo.contents), issuer))

            contexto = crypt32.CertEnumCertificatesInStore(handle, contexto)
    finally:
        crypt32.CertCloseStore(handle, 0)

    return encontrados


def encontrar_issuer(subject_cn):
    """CN do issuer do certificado valido de `subject_cn`, ou None.

    Havendo mais de um valido para o mesmo subject, vence o de validade mais
    longa — que e o recem-renovado. Devolver None significa "nao sei", nunca
    "nao existe": quem chama mantem o issuer configurado.
    """
    alvo = (subject_cn or '').strip().casefold()
    if not alvo:
        return None

    crypt32 = _carregar_crypt32()
    if crypt32 is None:
        return None

    try:
        candidatos = []
        for _nome_store, flag in _STORES:
            candidatos.extend(_varrer(crypt32, flag, alvo))
    except OSError as exc:
        log_event('cert_store_erro', level='WARNING', subject_cn=subject_cn, error=str(exc))
        return None

    if not candidatos:
        # o caso do certificado vencido e ainda nao reinstalado: sem isto o
        # operador so descobre pelo dialogo do Chrome abrindo no meio do lote
        log_event('cert_store_sem_certificado_valido', level='WARNING', subject_cn=subject_cn)
        return None

    candidatos.sort(key=lambda item: item[0], reverse=True)
    issuer = candidatos[0][1]

    outros = {emissor for _venc, emissor in candidatos if emissor != issuer}
    if outros:
        log_event('cert_store_issuer_ambiguo', level='WARNING',
                  subject_cn=subject_cn, escolhido=issuer, descartados=sorted(outros))

    log_event('cert_store_issuer_resolvido', subject_cn=subject_cn, issuer_cn=issuer,
              candidatos=len(candidatos))
    return issuer
