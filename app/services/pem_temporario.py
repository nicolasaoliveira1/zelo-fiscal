"""Credencial e arquivo PEM temporário para sessões mTLS."""

import os
import tempfile
from contextlib import contextmanager

from cryptography.hazmat.primitives import serialization

from app.services.execution_logger import log_event
from app.services.manifestador_cofre import carregar_pfx


class PemTemporarioError(Exception):
    """Falha ao carregar o certificado para o arquivo PEM temporário."""


class Credencial:
    """Caminho do `.pfx` e senha em claro, para uso imediato.

    `__repr__` omite a senha: é o `repr` que acaba num log de exceção.
    """

    def __init__(self, caminho, senha):
        self.caminho = caminho
        self.senha = senha

    def __repr__(self):
        return f'<Credencial {self.caminho!r}>'


@contextmanager
def pem_temporario(credencial):
    """PEM (chave + certificado + cadeia) em arquivo temporário local."""
    info = carregar_pfx(credencial.caminho, (credencial.senha or '').encode() or None)
    if info is None or info.chave_privada is None:
        raise PemTemporarioError(
            f'Não consegui abrir o certificado em {credencial.caminho}. '
            'Confira a senha no cofre e se o arquivo continua na pasta.')

    partes = [
        info.chave_privada.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()),
        info.certificado.public_bytes(serialization.Encoding.PEM),
    ]
    partes.extend(c.public_bytes(serialization.Encoding.PEM)
                  for c in (info.cadeia or []))

    descritor, caminho = tempfile.mkstemp(suffix='.pem', prefix='zelo-mtls-')
    try:
        with os.fdopen(descritor, 'wb') as arquivo:
            arquivo.write(b''.join(partes))
        yield caminho
    finally:
        try:
            os.remove(caminho)
        except OSError as exc:
            log_event('nfe_sefaz_temp_nao_removido', level='WARNING',
                      caminho=caminho, error=str(exc))
