r"""Cofre de certificados A1 da carteira (MANIF-01..06, AD-027).

Responde uma pergunta so: "qual certificado usar para manifestar por esta
empresa, e ele esta utilizavel?".

Tres invariantes vieram da varredura real da carteira
(`.specs/features/manifestador-nfe/recon.md`) e nao devem ser afrouxadas:

1. **A empresa e identificada pelo CNPJ de dentro do certificado**, extraido do
   CN (`RAZAO SOCIAL:14 digitos`). Nao pelo nome do arquivo, nem pelo da pasta,
   nem pela razao social: na carteira existe certificado cujo CN e
   `CERTIFICADO A VALIDAR NA EMISSAO`, existem `MARTINS & FILHOS` e
   `MARTINS E FILHOS` para o mesmo CNPJ, e `A B C COMERCIO` aparece em 5 CNPJs.
2. **Nada e copiado.** O que se guarda e o caminho e a senha cifrada; o `.pfx`
   e lido do drive no momento do uso. Assim a renovacao anual na pasta e
   herdada sem recadastro, e nao se duplica chave privada.
3. **A senha nunca vaza** — nem em log, nem em `repr`, nem em mensagem de erro.

A cifra usa Fernet com a chave em `MANIF_VAULT_KEY` (env, fora do banco e fora
do git). Sem ela o cofre recusa ler e gravar senha com mensagem acionavel — o
inventario continua funcionando, porque metadado de certificado nao e segredo.
"""
import os
import re
from datetime import datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.serialization import pkcs12

from app.file_manager import encontrar_pasta_empresa, get_caminho_rede
from app.services.execution_logger import log_event
from app.utils import get_config_value

VAULT_KEY_ENV = 'MANIF_VAULT_KEY'

# Ordem importa: `123456` abre a maior parte da carteira, entao e a
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


# --- inventario do drive ----------------------------------------------------

EXTENSOES_PFX = ('.pfx', '.p12')
# Fundo suficiente para `EMPRESA\DOC. EMPRESA\CERTIFICADO A-1 SENHA X\a.pfx`, e
# raso o bastante para a varredura nao custar minutos por empresa: a medida real
# foi ~135 s para a carteira inteira com este limite.
PROFUNDIDADE_MAX = 4

# A senha as vezes esta escrita no caminho ('CERTIFICADO A-1 SENHA 17022013').
# Isso vira SUGESTAO para o operador confirmar, nunca aplicacao automatica:
# deduzir credencial de nome de pasta e adivinhacao, e errar em silencio aqui
# deixaria o pre-voo dizendo PRONTO para uma empresa que falha no lote.
# `[^\s\\/]+` e nao `\S+`: a senha esta num SEGMENTO do caminho, e `\S+`
# atravessaria a barra capturando o proximo nivel junto.
_SENHA_NO_CAMINHO = re.compile(r'\bSENHA\s+([^\s\\/]+)', re.IGNORECASE)
# Senha de certificado nao tem 70 caracteres. Acima disso o que veio depois da
# palavra e nome de arquivo, nao credencial — melhor nao sugerir nada.
TAMANHO_MAX_SUGESTAO = 30


def rede_disponivel():
    """O drive base responde? Separado para o teste poder simular o `Z:` fora."""
    try:
        return os.path.isdir(get_caminho_rede())
    except OSError:
        return False


def sugerir_senha(caminho):
    """Trecho apos a palavra SENHA no caminho, ou None. Apenas sugestao."""
    achado = _SENHA_NO_CAMINHO.search(str(caminho or ''))
    if not achado:
        return None
    candidato = achado.group(1).strip()
    for extensao in EXTENSOES_PFX:
        if candidato.lower().endswith(extensao):
            candidato = candidato[:-len(extensao)]
    if not candidato or len(candidato) > TAMANHO_MAX_SUGESTAO:
        return None
    return candidato


def _achar_pfx(raiz):
    """Todo .pfx/.p12 sob a pasta, ate PROFUNDIDADE_MAX niveis.

    Nome de pasta e de arquivo sao IGNORADOS de proposito: na carteira real as
    pastas variam (CERTIFICADO, CERTIFIC, DOCUMENTOS, solto) e o arquivo
    costuma ter o nome do dono da empresa, nao dela."""
    base = Path(raiz)
    achados = []
    try:
        for caminho in base.rglob('*'):
            try:
                if len(caminho.relative_to(base).parts) > PROFUNDIDADE_MAX:
                    continue
                if caminho.suffix.lower() in EXTENSOES_PFX and caminho.is_file():
                    achados.append(caminho)
            except OSError:
                continue
    except OSError as exc:
        log_event('manifestador_cofre_varredura_falhou', level='WARNING',
                  pasta=str(raiz), error=str(exc))
    return achados


def _senhas_a_tentar(certificado_atual):
    """A senha ja guardada primeiro, depois as padrao.

    Sem a guardada na frente, reinventariar jogaria de volta para
    SENHA_PENDENTE justamente as empresas cuja senha o operador ja informou."""
    senhas = []
    if certificado_atual is not None and certificado_atual.senha_cifrada:
        try:
            senhas.append(decifrar_senha(certificado_atual.senha_cifrada).encode())
        except CofreError:
            # chave do cofre trocou: segue com as padrao e o estado cai em
            # senha_pendente, que e a verdade
            pass
    senhas.extend(SENHAS_PADRAO)
    return tuple(senhas)


def _classificar(empresa, pasta, certificado_atual):
    """(estado, InfoCertificado|None, senha|None, detalhe) para uma empresa."""
    from app.models import EstadoCertificado

    if not pasta:
        return EstadoCertificado.SEM_PASTA, None, None, ''

    arquivos = _achar_pfx(pasta)
    if not arquivos:
        return EstadoCertificado.SEM_ARQUIVO, None, None, ''

    senhas = _senhas_a_tentar(certificado_atual)
    abertos = []
    for caminho in arquivos:
        senha, info = abrir_com_senhas_conhecidas(caminho, senhas)
        if info is not None:
            abertos.append((senha, info))

    if not abertos:
        detalhe = '; '.join(str(c) for c in arquivos)[:500]
        return EstadoCertificado.SENHA_PENDENTE, None, None, detalhe

    cnpj_cadastro = re.sub(r'\D', '', empresa.cnpj or '')
    casados = [(s, i) for s, i in abertos if i.cnpj == cnpj_cadastro]
    if not casados:
        detalhe = '; '.join(i.subject_cn for _s, i in abertos)[:500]
        return EstadoCertificado.CNPJ_DIVERGENTE, None, None, detalhe

    melhor = escolher_melhor([i for _s, i in casados])
    senha = next(s for s, i in casados if i is melhor)
    estado = (EstadoCertificado.VENCIDO if melhor.vencido
              else EstadoCertificado.PRONTO)
    return estado, melhor, senha, ''


def _aplicar(empresa, estado, info, senha, detalhe):
    """Grava o resultado da classificacao, criando a linha se preciso."""
    from app import db
    from app.models import CertificadoEmpresa

    cert = empresa.certificado
    if cert is None:
        cert = CertificadoEmpresa(empresa_id=empresa.id)
        db.session.add(cert)

    cert.estado = estado
    cert.detalhe = detalhe or None
    cert.verificado_em = datetime.now()

    if info is not None:
        cert.caminho = info.caminho
        cert.subject_cn = info.subject_cn
        cert.issuer_cn = info.issuer_cn
        cert.cnpj_certificado = info.cnpj
        cert.not_after = info.not_after
        # So regrava a senha quando ela NAO e uma das padrao: guardar `123456`
        # cifrado nao protege nada e cria dependencia da chave do cofre para 69
        # da carteira.
        if senha and senha.encode() not in SENHAS_PADRAO:
            try:
                cert.senha_cifrada = cifrar_senha(senha)
            except CofreError:
                pass
    else:
        cert.caminho = (detalhe.split('; ')[0] if estado ==
                        _ESTADO_SENHA_PENDENTE else None)
        cert.subject_cn = None
        cert.issuer_cn = None
        cert.cnpj_certificado = None
        cert.not_after = None
        cert.senha_cifrada = None

    return cert


_ESTADO_SENHA_PENDENTE = 'senha_pendente'


def inventariar(empresas=None):
    """Varre o drive e grava o estado do certificado de cada empresa ativa.

    Custa rede: a medida real foi ~135 s para a carteira inteira. Por isso roda fora da
    requisicao e o resultado fica no banco — a tela le `estado_da_carteira()`.

    Levanta `CofreError` quando o drive base nao responde: marcar todo mundo
    como `sem_pasta` apagaria um inventario bom e mandaria o operador procurar
    defeito onde nao ha (o defeito e o `Z:`, nao o cadastro)."""
    from app import db
    from app.models import Empresa
    from app.services.receita_service import empresa_ativa

    if not rede_disponivel():
        raise CofreError(
            'Nao consegui ler o drive de rede das empresas. O inventario '
            'anterior do cofre continua valendo; refaca quando o drive voltar.')

    alvos = empresas if empresas is not None else Empresa.query.order_by(
        Empresa.nome).all()

    resumo = {}
    for empresa in alvos:
        if not empresa_ativa(empresa):
            continue
        try:
            pasta = encontrar_pasta_empresa(empresa.nome)
        except OSError as exc:
            # empresa isolada com problema de leitura: preserva o que ja havia
            log_event('manifestador_cofre_pasta_falhou', level='WARNING',
                      empresa=empresa.nome, error=str(exc))
            continue

        estado, info, senha, detalhe = _classificar(empresa, pasta,
                                                    empresa.certificado)
        _aplicar(empresa, estado, info, senha, detalhe)
        resumo[estado] = resumo.get(estado, 0) + 1

    db.session.commit()
    log_event('manifestador_cofre_inventario', **{k: v for k, v in resumo.items()})
    return resumo


def estado_da_carteira():
    """Contagem por estado, lida do BANCO — nunca da rede (o pre-voo abre a
    cada carregamento da pagina e nao pode custar a varredura)."""
    from app import db
    from app.models import CertificadoEmpresa

    linhas = db.session.query(
        CertificadoEmpresa.estado, db.func.count(CertificadoEmpresa.id)
    ).group_by(CertificadoEmpresa.estado).all()
    return {estado: total for estado, total in linhas}


def gravar_senha(empresa, senha):
    """Confere a senha contra o arquivo e so entao a guarda cifrada.

    Devolve False sem gravar quando a senha nao abre: gravar sem conferir
    deixaria o pre-voo dizendo PRONTO para uma empresa que falharia no meio do
    lote — que e exatamente o que o pre-voo existe para evitar."""
    from app import db
    from app.models import EstadoCertificado

    cert = empresa.certificado
    if cert is None or not cert.caminho:
        return False

    info = carregar_pfx(cert.caminho, (senha or '').encode() or None)
    if info is None:
        return False

    cnpj_cadastro = re.sub(r'\D', '', empresa.cnpj or '')
    if info.cnpj != cnpj_cadastro:
        cert.estado = EstadoCertificado.CNPJ_DIVERGENTE
        cert.detalhe = info.subject_cn
        db.session.commit()
        return False

    cert.senha_cifrada = cifrar_senha(senha)
    cert.subject_cn = info.subject_cn
    cert.issuer_cn = info.issuer_cn
    cert.cnpj_certificado = info.cnpj
    cert.not_after = info.not_after
    cert.detalhe = None
    cert.estado = (EstadoCertificado.VENCIDO if info.vencido
                   else EstadoCertificado.PRONTO)
    cert.verificado_em = datetime.now()
    db.session.commit()
    log_event('manifestador_cofre_senha_gravada', empresa=empresa.nome)
    return True


def credencial(empresa):
    """(caminho, senha em claro) para uso imediato, ou None.

    So devolve para empresa com certificado PRONTO — o pre-voo e a fronteira, e
    quem chama nao deve reimplementar a regra."""
    from app.models import EstadoCertificado

    cert = getattr(empresa, 'certificado', None)
    if cert is None or cert.estado != EstadoCertificado.PRONTO or not cert.caminho:
        return None

    senha = decifrar_senha(cert.senha_cifrada) if cert.senha_cifrada else '123456'
    return cert.caminho, senha
