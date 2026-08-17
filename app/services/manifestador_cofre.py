r"""Cofre de certificados A1 da carteira (MANIF-01..06, AD-027).

Responde uma pergunta so: "qual certificado usar para manifestar por esta
empresa, e ele esta utilizavel?".

Tres invariantes vieram da varredura real das 93 empresas ativas
(`.specs/features/manifestador-nfe/recon.md`) e nao devem ser afrouxadas:

1. **A empresa e identificada pelo CNPJ de dentro do certificado**, extraido do
   CN (`RAZAO SOCIAL:14 digitos`). Nao pelo nome do arquivo, nem pelo da pasta,
   nem pela razao social: na carteira existe certificado cujo CN e
   `CONSULTA RFB A REALIZAR NA HORA DA VALIDACAO`, existem `SOARES & LEAL` e
   `SOARES E LEAL` para o mesmo CNPJ, e `E E C PEREIRA` aparece em 5 CNPJs.
2. **Nada e copiado.** O que se guarda e o caminho e a senha cifrada; o `.pfx`
   e lido do drive no momento do uso. Assim a renovacao anual na pasta e
   herdada sem recadastro, e nao se duplica chave privada.
3. **A senha nunca vaza** — nem em log, nem em `repr`, nem em mensagem de erro.

A cifra usa Fernet com a chave em `MANIF_VAULT_KEY` (env, fora do banco e fora
do git). Sem ela o cofre recusa ler e gravar senha com mensagem acionavel — o
inventario continua funcionando, porque metadado de certificado nao e segredo.
"""
import re
from datetime import datetime

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.serialization import pkcs12

from app.services.execution_logger import log_event
from app.utils import get_config_value

VAULT_KEY_ENV = 'MANIF_VAULT_KEY'

# Ordem importa: `123456` abre 69 das 93 empresas da carteira, entao e a
# primeira tentativa. A senha vazia cobre o .pfx exportado sem protecao.
SENHAS_PADRAO = (b'123456', b'')

# O CN termina em `:CNPJ`. Exatamente 14 digitos: um e-CPF (11) NAO pode casar
# como se fosse a empresa — manifestar por e-CPF exigiria procuracao eletronica,
# que e outro fluxo.
_CNPJ_NO_CN = re.compile(r':(\d{14})\s*$')

_OID_COMMON_NAME = '2.5.4.3'


class CofreError(Exception):
    """Falha acionavel do cofre — o texto diz ao operador o que fazer."""


class InfoCertificado:
    """O que um `.pfx` aberto revela. `__repr__` omite material sensivel de
    proposito: e o `repr` que acaba dentro de um log de excecao."""

    def __init__(self, caminho, subject_cn, issuer_cn, cnpj, not_after,
                 chave_privada=None, certificado=None, cadeia=None):
        self.caminho = caminho
        self.subject_cn = subject_cn
        self.issuer_cn = issuer_cn
        self.cnpj = cnpj
        self.not_after = not_after
        self.chave_privada = chave_privada
        self.certificado = certificado
        self.cadeia = cadeia or []

    @property
    def vencido(self):
        return self.not_after is not None and self.not_after <= datetime.now()

    def __repr__(self):
        return (f'<InfoCertificado {self.subject_cn!r} cnpj={self.cnpj} '
                f'vence={self.not_after:%d/%m/%Y}>' if self.not_after
                else f'<InfoCertificado {self.subject_cn!r} cnpj={self.cnpj}>')


# --- identificacao ----------------------------------------------------------

def cnpj_do_cn(cn):
    """CNPJ (14 digitos) no fim do CN, ou None.

    E a UNICA chave de casamento certificado->empresa. Ver invariante 1 no
    cabecalho do modulo."""
    if not cn:
        return None
    achado = _CNPJ_NO_CN.search(str(cn))
    return achado.group(1) if achado else None


def _cn_de(nome_x509):
    for atributo in nome_x509:
        if atributo.oid.dotted_string == _OID_COMMON_NAME:
            return str(atributo.value).strip()
    return ''


def _not_after(certificado):
    """Vencimento como datetime naive local, para comparar com `datetime.now()`
    (AD-004). `not_valid_after_utc` e o atributo novo; o antigo segue como
    fallback para nao prender a feature a uma versao do `cryptography`."""
    bruto = getattr(certificado, 'not_valid_after_utc', None)
    if bruto is None:
        bruto = certificado.not_valid_after
    if bruto.tzinfo is not None:
        bruto = bruto.astimezone().replace(tzinfo=None)
    return bruto


# --- leitura do arquivo -----------------------------------------------------

def carregar_pfx(caminho, senha):
    """`InfoCertificado` do `.pfx`, ou None quando a senha nao abre.

    Nunca levanta por senha errada nem por arquivo corrompido: quem chama
    precisa distinguir "nao abriu" de "nao existe", e as duas coisas sao
    esperadas no inventario."""
    try:
        with open(caminho, 'rb') as arquivo:
            dados = arquivo.read()
    except OSError:
        return None

    try:
        chave, certificado, cadeia = pkcs12.load_key_and_certificates(dados, senha)
    except Exception:
        # senha errada, arquivo que nao e pkcs12, DER truncado — tudo cai aqui e
        # tudo significa a mesma coisa para quem chama: "este par nao serve"
        return None

    if certificado is None:
        return None

    subject_cn = _cn_de(certificado.subject)
    return InfoCertificado(
        caminho=str(caminho),
        subject_cn=subject_cn,
        issuer_cn=_cn_de(certificado.issuer),
        cnpj=cnpj_do_cn(subject_cn),
        not_after=_not_after(certificado),
        chave_privada=chave,
        certificado=certificado,
        cadeia=cadeia,
    )


def abrir_com_senhas_conhecidas(caminho, senhas=SENHAS_PADRAO):
    """(senha em claro, InfoCertificado) ou (None, None) se nenhuma abriu."""
    for senha in senhas:
        info = carregar_pfx(caminho, senha or None)
        if info is not None:
            return (senha or b'').decode('utf-8', 'replace'), info
    return None, None


def escolher_melhor(candidatos):
    """Entre varios `.pfx` do mesmo CNPJ, o de vencimento mais distante.

    Mesma regra do `cert_store.encontrar_issuer` (ND-006): o recem-renovado
    vence o antigo. Vencido so entra se nao houver nenhum valido — devolver o
    vencido e melhor que devolver None, porque o estado `vencido` e uma
    informacao util no pre-voo, e "sem arquivo" seria mentira."""
    validos = [c for c in candidatos if c is not None]
    if not validos:
        return None

    dentro_da_validade = [c for c in validos if not c.vencido]
    pool = dentro_da_validade or validos
    escolhido = max(pool, key=lambda c: c.not_after or datetime.min)

    descartados = [c.caminho for c in validos if c is not escolhido]
    if descartados:
        log_event('manifestador_cofre_pfx_descartado',
                  escolhido=escolhido.caminho, descartados=descartados,
                  cnpj=escolhido.cnpj)
    return escolhido


# --- cifra da senha ---------------------------------------------------------

def gerar_chave_cofre():
    """Chave Fernet nova, em texto — para popular `MANIF_VAULT_KEY` no .env."""
    return Fernet.generate_key().decode()


def _fernet():
    bruto = get_config_value(VAULT_KEY_ENV, '') or ''
    if not str(bruto).strip():
        raise CofreError(
            f'O cofre de certificados esta sem chave: defina {VAULT_KEY_ENV} no '
            f'.env com uma chave Fernet. O inventario continua funcionando sem '
            f'ela, mas senha de certificado nao pode ser gravada nem lida.')
    try:
        return Fernet(str(bruto).strip().encode())
    except Exception as exc:
        raise CofreError(
            f'{VAULT_KEY_ENV} nao e uma chave Fernet valida. Gere uma nova com '
            f'`python -c "from cryptography.fernet import Fernet; '
            f'print(Fernet.generate_key().decode())"`.') from exc


def cifrar_senha(senha):
    """Token Fernet da senha. A senha em claro nunca sai daqui."""
    return _fernet().encrypt((senha or '').encode()).decode()


def decifrar_senha(token):
    """Senha em claro a partir do token.

    Um token que nao abre significa que a chave do cofre mudou — e a mensagem
    diz isso, sem citar o token nem qualquer senha (invariante 3)."""
    if not token:
        return ''
    try:
        return _fernet().decrypt(str(token).encode()).decode()
    except InvalidToken as exc:
        raise CofreError(
            f'Nao consegui abrir a senha guardada: ela foi cifrada com outra '
            f'chave. Se {VAULT_KEY_ENV} mudou, as senhas precisam ser '
            f'informadas de novo no pre-voo do cofre.') from exc
