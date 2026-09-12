"""Persistência da numeração durável do ensaio restrito da NFS-e."""

import re
from datetime import datetime

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError, OperationalError

from app import db
from app.models import ContadorDpsNfse


AMBIENTE_RESTRITA = 'restrita'
NUMERO_INICIAL = 1
TENTATIVAS_RESERVA = 10


class NfseApiEnsaioError(RuntimeError):
    """Erro controlado do fluxo de ensaio."""


class AmbienteDpsInvalidoError(NfseApiEnsaioError):
    """O contador solicitado não pertence ao ambiente permitido no P2."""


class PrestadorDpsInvalidoError(NfseApiEnsaioError):
    """O identificador do prestador não é representável no P2."""


class SerieDpsInvalidaError(NfseApiEnsaioError):
    """A série não atende ao formato ou à faixa reservada do P2."""


class ReservaDpsConflitoError(NfseApiEnsaioError):
    """O contador não pôde ser reservado após as tentativas seguras."""


def validar_serie(serie):
    """Valida e devolve a série textual sem normalização silenciosa."""
    if not isinstance(serie, str) or re.fullmatch(r'[0-9]{1,5}', serie) is None:
        raise SerieDpsInvalidaError(
            'A série restrita deve conter de 1 a 5 algarismos.')
    if 80000 <= int(serie) <= 89999:
        raise SerieDpsInvalidaError(
            'A faixa de séries restrita de 80000 a 89999 é reservada.')
    return serie


def _validar_prestador(prestador):
    if not isinstance(prestador, str) or re.fullmatch(r'[0-9]{14}', prestador) is None:
        raise PrestadorDpsInvalidoError(
            'O prestador do ensaio deve ter 14 algarismos.')


def _validar_chave(ambiente, prestador, serie):
    if ambiente != AMBIENTE_RESTRITA:
        raise AmbienteDpsInvalidoError(
            'O contador do P2 aceita somente o ambiente restrito.')
    _validar_prestador(prestador)
    validar_serie(serie)


def reservar_numero(ambiente, prestador, serie, *, agora=None):
    """Reserva atomicamente o próximo número da série restrita.

    A atualização condicional protege o contador mesmo quando o dialeto não
    implementa `SELECT ... FOR UPDATE` (como SQLite). Se a primeira execução
    precisar criar a linha, a unicidade do banco arbitra a corrida de criação.
    """
    _validar_chave(ambiente, prestador, serie)
    instante = agora or datetime.now()

    for _ in range(TENTATIVAS_RESERVA):
        contador = ContadorDpsNfse.query.filter_by(
            ambiente=ambiente,
            prestador=prestador,
            serie=serie,
        ).with_for_update().one_or_none()

        if contador is None:
            db.session.add(ContadorDpsNfse(
                ambiente=ambiente,
                prestador=prestador,
                serie=serie,
                proximo_numero=NUMERO_INICIAL + 1,
                atualizado_em=instante,
            ))
            try:
                db.session.commit()
            except (IntegrityError, OperationalError):
                db.session.rollback()
                continue
            return NUMERO_INICIAL

        numero = int(contador.proximo_numero)
        resultado = db.session.execute(
            update(ContadorDpsNfse)
            .where(
                ContadorDpsNfse.id == contador.id,
                ContadorDpsNfse.proximo_numero == numero,
            )
            .values(
                proximo_numero=numero + 1,
                atualizado_em=instante,
            )
        )
        if resultado.rowcount != 1:
            db.session.rollback()
            continue
        try:
            db.session.commit()
        except OperationalError:
            db.session.rollback()
            continue
        return numero

    db.session.rollback()
    raise ReservaDpsConflitoError(
        'Não foi possível reservar o número do ensaio com segurança.')
