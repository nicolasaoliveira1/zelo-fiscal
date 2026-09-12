"""Eventos de cancelamento do ADN e seus efeitos no espelho de NFS-e."""
from datetime import date, datetime
from decimal import Decimal

import pytest

from app import db
from app.models import (
    EventoEmitidaNfse,
    LoteNfse,
    NotaEmitidaNfse,
    NotaNfse,
    StatusNotaNfse,
)
from app.services import nfse_emitidas
from app.services.nfse_api_xml import EventoLido


CHAVE = '0' * 50
DOCUMENTO = '44.556.677/0001-86'
GERACAO = date(2026, 7, 15)


@pytest.fixture()
def banco(app):
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _evento(tipo='e101101', num_seq=1):
    return EventoLido(
        chave=CHAVE,
        tipo=tipo,
        num_seq=num_seq,
        data=datetime(2026, 8, 1, 10, 30),
    )


def _linha_adn():
    return nfse_emitidas.LinhaEmitida(
        chave=CHAVE,
        data_geracao=GERACAO,
        documento=DOCUMENTO,
        nome_tomador='Tomador Sintético',
        competencia='07/2026',
        municipio='Município Sintético/RS',
        valor=Decimal('400.00'),
        situacao='100',
    )


def _emitida(*, nota_id=None, situacao_fiscal='gerada'):
    return NotaEmitidaNfse(
        chave=CHAVE,
        data_geracao=GERACAO,
        documento=DOCUMENTO,
        valor=Decimal('400.00'),
        situacao='P100_GERADA',
        situacao_fiscal=situacao_fiscal,
        nota_id=nota_id,
        consultado_em=datetime(2026, 7, 15, 12, 0),
    )


def _nota_fila():
    lote = LoteNfse(nome_arquivo='extrato-sintetico.pdf', total=1)
    db.session.add(lote)
    db.session.flush()
    nota = NotaNfse(
        lote_id=lote.id,
        documento=DOCUMENTO,
        valor_final=Decimal('400.00'),
        status=StatusNotaNfse.PRONTA,
        data_pagamento=date(2026, 7, 10),
    )
    db.session.add(nota)
    db.session.flush()
    return nota


@pytest.mark.parametrize('tipo', ['e101101', 'e105102'])
def test_reprocessar_o_mesmo_evento_nao_duplica(banco, tipo):
    with banco.app_context():
        evento = _evento(tipo)

        assert nfse_emitidas.registrar_eventos([evento]) == 1
        assert nfse_emitidas.registrar_eventos([evento]) == 0
        assert EventoEmitidaNfse.query.count() == 1


def test_evento_sem_nota_fica_pendente_no_banco(banco):
    with banco.app_context():
        assert nfse_emitidas.registrar_eventos([_evento()]) == 1

        assert EventoEmitidaNfse.query.one().chave == CHAVE
        assert NotaEmitidaNfse.query.count() == 0


def test_evento_pendente_e_reaplicado_quando_a_nota_chega(banco):
    with banco.app_context():
        nfse_emitidas.registrar_eventos([_evento()])
        db.session.commit()

        nfse_emitidas._gravar_observacoes([_linha_adn()], 'adn')
        assert nfse_emitidas.projetar_espelho([CHAVE]) == (1, 0)

        nota = NotaEmitidaNfse.query.one()
        assert nota.situacao_fiscal == 'cancelada'
        assert nota.origem_autoritativa == 'adn'


def test_nota_cancelada_fica_fora_do_resumo(banco):
    with banco.app_context():
        db.session.add(_emitida())
        db.session.commit()

        nfse_emitidas.registrar_eventos([_evento()])
        resumo = nfse_emitidas.resumo('07/2026')

        assert resumo['quantidade'] == 0
        assert resumo['total'] == Decimal('0')
        assert resumo['outras_situacoes'] == {'cancelada': 1}


def test_nota_cancelada_nao_entra_nem_permanece_conciliada(banco):
    with banco.app_context():
        nota_fila = _nota_fila()
        emitida = _emitida(nota_id=nota_fila.id)
        db.session.add(emitida)
        db.session.commit()

        nfse_emitidas.registrar_eventos([_evento()])
        assert emitida.nota_id is None
        assert nfse_emitidas.conciliar() == 0
        assert emitida.nota_id is None
