r"""Nucleo generico de auto-selecao de certificado do Chrome (NFSE-10).

Manipula a policy `AutoSelectCertificateForUrls`
(`HKCU\Software\Policies\Google\Chrome\...`), que faz o Chrome escolher o
certificado cliente sem exibir o dialogo. Extraido de `driver.py` (ND-002):
antes era especifico do SEFAZ RS, agora e parametrizado por
(pattern, indice, issuer CN, subject CN) e o RS e apenas o primeiro consumidor.

Duas restricoes do desenho (ND-002c/ND-006):

- o refcount e **por indice**, nao global: com um contador unico, desativar a
  policy de um fluxo removeria a policy de outro rodando junto (o RS passaria a
  pedir certificado no meio do lote);
- issuer e subject sao **por politica**, nunca herdados: cada fluxo usa um
  certificado diferente (o RS um e-CPF, a NFSe um e-CNPJ) e existem certificados
  com o mesmo subject e issuers distintos — filtrar so por subject e ambiguo.
"""
import json
import os
from threading import Lock

try:
    import winreg
except ImportError:
    winreg = None

from app.services.execution_logger import log_event

CHAVE_REGISTRO = r"Software\Policies\Google\Chrome\AutoSelectCertificateForUrls"

_POLICY_LOCK = Lock()
# indice do registro -> quantas ativacoes em aberto
_POLICY_ACTIVE_COUNT = {}


class PoliticaCertificado:
    """Alvo de auto-selecao: onde gravar (indice) e o que casar (pattern/filtro)."""

    def __init__(self, pattern, indice='1', issuer_cn='', subject_cn='', prefixo_log='cert_autoselect'):
        self.pattern = (pattern or '').strip()
        self.indice = str(indice or '1').strip() or '1'
        self.issuer_cn = (issuer_cn or '').strip()
        self.subject_cn = (subject_cn or '').strip()
        self.prefixo_log = prefixo_log

    def montar(self):
        """Dict da policy, ou None quando falta pattern ou o filtro fica vazio."""
        if not self.pattern:
            return None

        filtro = {}
        if self.issuer_cn:
            filtro['ISSUER'] = {'CN': self.issuer_cn}
        if self.subject_cn:
            filtro['SUBJECT'] = {'CN': self.subject_cn}

        if not filtro:
            return None

        return {
            'pattern': self.pattern,
            'filter': filtro,
        }


def _sincronizar(politica, aplicar=True):
    if os.name != 'nt' or winreg is None:
        return

    indice = politica.indice
    conteudo = politica.montar() if aplicar else None

    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, CHAVE_REGISTRO) as chave:
            if conteudo is None:
                try:
                    winreg.DeleteValue(chave, indice)
                    log_event(f'{politica.prefixo_log}_removida', indice=indice)
                except FileNotFoundError:
                    pass
                return

            valor = json.dumps(conteudo, ensure_ascii=False, separators=(',', ':'))
            winreg.SetValueEx(chave, indice, 0, winreg.REG_SZ, valor)
            log_event(f'{politica.prefixo_log}_aplicada', indice=indice)
    except OSError as exc:
        log_event(f'{politica.prefixo_log}_sync_error', level='WARNING', error=str(exc))


def ativar(politica):
    """Grava a policy na 1a ativacao do indice. False se nao ha o que aplicar."""
    if politica is None or not politica.montar():
        return False

    with _POLICY_LOCK:
        _POLICY_ACTIVE_COUNT[politica.indice] = _POLICY_ACTIVE_COUNT.get(politica.indice, 0) + 1
        if _POLICY_ACTIVE_COUNT[politica.indice] == 1:
            _sincronizar(politica, aplicar=True)
    return True


def desativar(politica):
    """Remove a policy do indice quando a ultima ativacao dele e devolvida."""
    if politica is None:
        return

    with _POLICY_LOCK:
        restante = _POLICY_ACTIVE_COUNT.get(politica.indice, 0) - 1
        if restante > 0:
            _POLICY_ACTIVE_COUNT[politica.indice] = restante
            return

        _POLICY_ACTIVE_COUNT[politica.indice] = 0
        _sincronizar(politica, aplicar=False)
