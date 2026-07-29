"""Sessao de navegador persistente do Emissor Nacional (NFSE-15).

Guarda UM navegador autenticado enquanto o operador trabalha: o login por
certificado e a conferencia da aliquota acontecem uma vez por sessao, nao por
nota. P1 (uma nota por vez) e P2 (lote assistido) consomem a mesma sessao, o
que evita dois caminhos de codigo.

O lock e nao-bloqueante de proposito, espelhando
`driver._municipal_profile_acquire/release`: uma segunda tentativa de emitir
recebe 409 na hora, em vez de ficar pendurada esperando a primeira terminar.
"""
from threading import Lock

from app.automation import nfse as automacao
from app.automation.cert_policy import PoliticaCertificado, ativar, desativar
from app.automation.driver import _criar_driver_chrome
from app.services.execution_logger import log_event
from app.utils import get_config_value, to_bool

PATTERN_PADRAO = 'https://certificado.nfse.gov.br'


def politica_nfse():
    """Politica de auto-selecao do certificado da NFSe.

    Indice PROPRIO no registro (o RS usa o 1): a policy e por indice, entao as
    duas convivem. Issuer e subject vem so das envs NFSE_*, nunca herdados do
    RS — sao certificados diferentes (o do RS e um e-CPF, o da NFSe e o e-CNPJ
    do escritorio), e existem dois e-CNPJ com o mesmo subject e issuers
    distintos, um deles vencido (ND-006).
    """
    if not to_bool(get_config_value('NFSE_CERT_AUTOSELECT_ENABLED', False), False):
        return None
    return PoliticaCertificado(
        pattern=get_config_value('NFSE_CERT_AUTOSELECT_PATTERN', PATTERN_PADRAO),
        indice=get_config_value('NFSE_CERT_AUTOSELECT_POLICY_INDEX', '2'),
        issuer_cn=get_config_value('NFSE_CERT_AUTOSELECT_ISSUER_CN', ''),
        subject_cn=get_config_value('NFSE_CERT_AUTOSELECT_SUBJECT_CN', ''),
        prefixo_log='nfse_autoselect',
    )


class NfseSession:
    """Dono do navegador autenticado. Instancia unica no modulo (`SESSAO`)."""

    def __init__(self):
        self._lock = Lock()
        self._driver = None
        self._politica = None
        self.aliquota = None
        self.aliquota_confirmada = False

    # --- exclusao mutua ---------------------------------------------------

    def adquirir(self, blocking=False):
        """True se tomou a sessao; False se ja estava em uso (base do 409)."""
        return self._lock.acquire(blocking=blocking)

    def liberar(self):
        """Idempotente: liberar sem ter adquirido nao levanta (seguro no
        finally, mesmo quando a aquisicao falhou antes)."""
        try:
            self._lock.release()
        except RuntimeError:
            pass

    @property
    def ocupada(self):
        return self._lock.locked()

    # --- ciclo de vida do navegador ---------------------------------------

    def driver_vivo(self):
        """False quando nao ha driver ou a janela foi fechada na mao."""
        if self._driver is None:
            return False
        try:
            self._driver.current_url
            return True
        except Exception:
            return False

    def garantir(self):
        """Devolve um navegador autenticado, reaproveitando o que ja existe.

        So faz login quando nao ha driver vivo — e o que evita repetir
        certificado e aliquota a cada nota."""
        if self.driver_vivo() and automacao.logado(self._driver):
            return self._driver

        if self._driver is not None:
            self._descartar_driver()

        self._politica = politica_nfse()
        if self._politica is not None:
            ativar(self._politica)

        try:
            self._driver = _criar_driver_chrome(anonimo=True)
            automacao.login_certificado(self._driver)
        except Exception:
            # nao deixa policy nem driver meio-abertos para tras
            self.encerrar()
            raise

        log_event('nfse_sessao_aberta')
        return self._driver

    def confirmar_aliquota(self, valor=None):
        """Marca a aliquota como conferida pelo operador — uma vez por sessao."""
        if valor is not None:
            self.aliquota = valor
        self.aliquota_confirmada = True

    def ler_aliquota(self):
        self.aliquota = automacao.ler_aliquota_simples(self.garantir())
        return self.aliquota

    def encerrar(self):
        """Fecha o navegador e libera a policy. Idempotente e a prova de erro:
        um `quit()` que levanta nao pode deixar a policy presa no registro."""
        self._descartar_driver()
        if self._politica is not None:
            try:
                desativar(self._politica)
            except Exception as exc:
                log_event('nfse_policy_release_falhou', level='WARNING', error=str(exc))
            self._politica = None
        self.aliquota = None
        self.aliquota_confirmada = False
        log_event('nfse_sessao_encerrada')

    def _descartar_driver(self):
        if self._driver is None:
            return
        try:
            self._driver.quit()
        except Exception as exc:
            log_event('nfse_driver_quit_falhou', level='WARNING', error=str(exc))
        finally:
            self._driver = None


SESSAO = NfseSession()
