"""Modelos da conferência de NFS-e pela API nacional (APINF-01..05/10)."""
from datetime import date, datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import (
    ConfiguracaoNfse,
    EventoEmitidaNfse,
    NotaEmitidaNfse,
    ObservacaoEmitidaNfse,
    SincronizacaoAdnNfse,
)


CHAVE_SINTETICA = 'chave-nfse-sintetica'
DOCUMENTO_SINTETICO = '11222333000181'


def test_configuracao_api_tem_defaults_fk_e_tipos(app, ids):
    with app.app_context():
        config = ConfiguracaoNfse(
            id=1,
            empresa_escritorio_id=ids['empresa'],
        )
        db.session.add(config)
        db.session.commit()

        recarregada = db.session.get(ConfiguracaoNfse, 1)
        assert recarregada.empresa_escritorio_id == ids['empresa']
        assert recarregada.api_ambiente == 'restrita'
        assert recarregada.api_habilitada is False

    coluna = ConfiguracaoNfse.__table__.c.api_ambiente
    assert isinstance(coluna.type, sa.String)
    assert coluna.type.length == 10
    assert isinstance(ConfiguracaoNfse.__table__.c.api_habilitada.type,
                      sa.Boolean)
    alvo_fk = next(iter(
        ConfiguracaoNfse.__table__.c.empresa_escritorio_id.foreign_keys))
    assert alvo_fk.target_fullname == 'empresa.id'


def test_nota_emitida_e_modelos_adn_persistem_defaults_e_valor_decimal(app, ids):
    with app.app_context():
        nota = NotaEmitidaNfse(
            chave=CHAVE_SINTETICA,
            data_geracao=date(2026, 9, 1),
            competencia_dps='202609',
            documento=DOCUMENTO_SINTETICO,
            valor=Decimal('1234.56'),
            situacao='P100_GERADA',
            origem_autoritativa='adn',
            situacao_fiscal='gerada',
        )
        cursor = SincronizacaoAdnNfse(
            ambiente='restrita',
            documento_consulta=DOCUMENTO_SINTETICO,
        )
        observacao = ObservacaoEmitidaNfse(
            fonte='adn',
            chave=CHAVE_SINTETICA,
            data_geracao=date(2026, 9, 1),
            competencia_dps='202609',
            documento=DOCUMENTO_SINTETICO,
            valor=Decimal('1234.56'),
        )
        evento = EventoEmitidaNfse(
            chave=CHAVE_SINTETICA,
            tipo='e101101',
            data=datetime(2026, 9, 2, 10, 30),
            nsu=12,
        )
        db.session.add_all([nota, cursor, observacao, evento])
        db.session.commit()

        assert db.session.get(NotaEmitidaNfse, nota.id).valor == Decimal('1234.56')
        assert cursor.ultimo_nsu is None
        assert cursor.atualizado_em is not None
        assert observacao.observado_em is not None
        assert evento.num_seq == 1
        assert evento.recebido_em is not None

    assert isinstance(NotaEmitidaNfse.__table__.c.situacao_fiscal.type,
                      sa.String)
    assert NotaEmitidaNfse.__table__.c.situacao_fiscal.type.length == 12
    assert isinstance(NotaEmitidaNfse.__table__.c.valor.type, sa.Numeric)
    assert NotaEmitidaNfse.__table__.c.valor.type.precision == 12
    assert NotaEmitidaNfse.__table__.c.valor.type.scale == 2


def _assert_commit_rejeitado(obj):
    db.session.add(obj)
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_modelos_adn_aplicam_unicidade_por_identidade(app, ids):
    with app.app_context():
        db.session.add_all([
            SincronizacaoAdnNfse(
                ambiente='restrita', documento_consulta=DOCUMENTO_SINTETICO),
            SincronizacaoAdnNfse(
                ambiente='producao', documento_consulta=DOCUMENTO_SINTETICO),
            ObservacaoEmitidaNfse(fonte='portal', chave=CHAVE_SINTETICA),
            ObservacaoEmitidaNfse(fonte='adn', chave=CHAVE_SINTETICA),
            EventoEmitidaNfse(
                chave=CHAVE_SINTETICA, tipo='e101101', num_seq=1),
            EventoEmitidaNfse(
                chave=CHAVE_SINTETICA, tipo='e101101', num_seq=2),
        ])
        db.session.commit()

        _assert_commit_rejeitado(SincronizacaoAdnNfse(
            ambiente='restrita', documento_consulta=DOCUMENTO_SINTETICO))
        _assert_commit_rejeitado(ObservacaoEmitidaNfse(
            fonte='portal', chave=CHAVE_SINTETICA))
        _assert_commit_rejeitado(EventoEmitidaNfse(
            chave=CHAVE_SINTETICA, tipo='e101101', num_seq=1))

        assert SincronizacaoAdnNfse.query.count() == 2
        assert ObservacaoEmitidaNfse.query.count() == 2
        assert EventoEmitidaNfse.query.count() == 2
