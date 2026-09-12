"""Credencial do próprio escritório para a API nacional de NFS-e.

O cofre continua sendo o dono da regra de casamento, prontidão e senha. Este
módulo só localiza a empresa configurada e adapta a credencial para o tipo
compartilhado pelo transporte mTLS.
"""

from dataclasses import dataclass
from datetime import datetime

from app import db
from app.models import ConfiguracaoNfse, Empresa, EstadoCertificado
from app.services import manifestador_cofre
from app.services.pem_temporario import Credencial


CAUSAS = ('sem_empresa', 'sem_certificado', 'nao_pronto', 'vencido', 'ok')


@dataclass(frozen=True)
class Diagnostico:
    """Estado da credencial sem expor senha ou material de chave."""

    disponivel: bool
    causa: str
    cnpj: str | None = None
    not_after: datetime | None = None

    def __post_init__(self):
        if self.causa not in CAUSAS:
            raise ValueError(f'Causa de credencial desconhecida: {self.causa}')


def credencial_do_escritorio():
    """Devolve ``Credencial`` para a empresa configurada, ou ``None``.

    A senha só passa pela memória desta fronteira para ser entregue ao
    context manager de PEM; não é gravada, registrada nem incluída no retorno
    do diagnóstico.
    """
    empresa = _empresa_configurada()
    if empresa is None:
        return None

    try:
        resultado = manifestador_cofre.credencial(empresa)
    except manifestador_cofre.CofreError:
        return None
    if not resultado:
        return None

    caminho, senha = resultado
    return Credencial(caminho=caminho, senha=senha)


def diagnostico():
    """Identifica a causa local antes de qualquer conexão de rede."""
    empresa = _empresa_configurada()
    if empresa is None:
        return Diagnostico(False, 'sem_empresa')

    certificado = getattr(empresa, 'certificado', None)
    cnpj = empresa.cnpj
    if certificado is None or not certificado.caminho:
        return Diagnostico(False, 'sem_certificado', cnpj=cnpj)
    if certificado.estado == EstadoCertificado.VENCIDO:
        return Diagnostico(
            False, 'vencido', cnpj=cnpj, not_after=certificado.not_after)
    if certificado.estado != EstadoCertificado.PRONTO:
        return Diagnostico(False, 'nao_pronto', cnpj=cnpj,
                           not_after=certificado.not_after)
    return Diagnostico(True, 'ok', cnpj=cnpj,
                       not_after=certificado.not_after)


def _empresa_configurada():
    config = db.session.get(ConfiguracaoNfse, 1)
    if config is None or not config.empresa_escritorio_id:
        return None
    return db.session.get(Empresa, config.empresa_escritorio_id)
