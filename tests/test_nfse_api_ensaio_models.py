"""Persistência e reserva do ensaio restrito, sem rede nem XML de cliente."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import (
    ConfiguracaoNfse,
    ContadorDpsNfse,
    EnsaioDpsNfse,
    LoteNfse,
    NotaNfse,
    Usuario,
)
from app.services import nfse_api_ensaio as ensaio


PRESTADOR_SINTETICO = '11222333000181'


def _usuario_admin():
    return Usuario.query.filter_by(papel='admin').first()


def _nota_emitida():
    lote = LoteNfse(total=1)
    nota = NotaNfse(lote=lote, status='emitida')
    db.session.add(nota)
    db.session.flush()
    return nota


def _ensaio(nota, operador, **valores):
    padrao = {
        'nota_nfse_id': nota.id,
        'operador_id': operador.id,
        'ambiente': 'restrita',
        'prestador': PRESTADOR_SINTETICO,
        'serie': '7',
        'numero': 1,
        'identificador_dps': 'DPS' + '1' * 42,
        'estado': 'reservado',
        'xml_referencia': '<xml-sintetico>',
        'ultima_falha': 'falha-sintetica',
    }
    padrao.update(valores)
    return EnsaioDpsNfse(**padrao)


def test_configuracao_e_tentativa_tem_campos_p2_sem_mudar_status_da_nota(app, ids):
    with app.app_context():
        config = ConfiguracaoNfse(id=1)
        nota = _nota_emitida()
        operador = _usuario_admin()
        tentativa = _ensaio(nota, operador)
        db.session.add_all([config, tentativa])
        db.session.commit()

        recarregada = db.session.get(EnsaioDpsNfse, tentativa.id)
        assert recarregada.ambiente == 'restrita'
        assert recarregada.estado == 'reservado'
        assert recarregada.nota_nfse_id == nota.id
        assert db.session.get(NotaNfse, nota.id).status == 'emitida'
        assert recarregada.criado_em is not None
        assert recarregada.atualizado_em is not None
        assert '<xml-sintetico>' not in repr(recarregada)
        assert 'falha-sintetica' not in repr(recarregada)

    assert isinstance(ConfiguracaoNfse.__table__.c.serie_dps_restrita.type,
                      sa.String)
    assert ConfiguracaoNfse.__table__.c.serie_dps_restrita.type.length == 5
    assert isinstance(EnsaioDpsNfse.__table__.c.estado.type, sa.String)
    assert isinstance(EnsaioDpsNfse.__table__.c.numero.type, sa.BigInteger)


def test_identificador_e_tupla_de_numero_sao_unicos(app, ids):
    with app.app_context():
        nota = _nota_emitida()
        operador = _usuario_admin()
        db.session.add(_ensaio(nota, operador))
        db.session.commit()

        duplicado_identificador = _ensaio(
            nota, operador, numero=2, serie='8')
        db.session.add(duplicado_identificador)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

        duplicado_numero = _ensaio(
            nota, operador, identificador_dps='DPS' + '2' * 42)
        db.session.add(duplicado_numero)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


@pytest.mark.parametrize('serie', [None, '', '123456', 'abc', '80000', '89999'])
def test_reserva_recusa_serie_invalida(app, ids, serie):
    with app.app_context():
        with pytest.raises(ensaio.SerieDpsInvalidaError):
            ensaio.reservar_numero(
                'restrita', PRESTADOR_SINTETICO, serie)
        assert ContadorDpsNfse.query.count() == 0


def test_reserva_incrementa_sem_reutilizar_numero(app, ids):
    with app.app_context():
        primeiro = ensaio.reservar_numero(
            'restrita', PRESTADOR_SINTETICO, '7',
            agora=datetime(2026, 9, 12, 12, 0))
        segundo = ensaio.reservar_numero(
            'restrita', PRESTADOR_SINTETICO, '7',
            agora=datetime(2026, 9, 12, 12, 1))

        assert (primeiro, segundo) == (1, 2)
        contador = ContadorDpsNfse.query.one()
        assert contador.proximo_numero == 3
        assert contador.atualizado_em == datetime(2026, 9, 12, 12, 1)


def test_reservas_concorrentes_recebem_numeros_distintos(app, ids):
    with app.app_context():
        db.session.add(ContadorDpsNfse(
            ambiente='restrita', prestador=PRESTADOR_SINTETICO,
            serie='7', proximo_numero=1))
        db.session.commit()

    def reservar(_indice):
        with app.app_context():
            return ensaio.reservar_numero(
                'restrita', PRESTADOR_SINTETICO, '7')

    with ThreadPoolExecutor(max_workers=2) as executor:
        numeros = list(executor.map(reservar, range(2)))

    with app.app_context():
        contador = ContadorDpsNfse.query.one()
        assert sorted(numeros) == [1, 2]
        assert contador.proximo_numero == 3
