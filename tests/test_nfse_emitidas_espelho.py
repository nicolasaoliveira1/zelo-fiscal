"""Observações independentes e projeção canônica do espelho de NFS-e."""
from datetime import date, datetime
from decimal import Decimal

from app import db
from app.models import EventoEmitidaNfse, NotaEmitidaNfse, ObservacaoEmitidaNfse
from app.services import nfse_emitidas


CHAVE = '0' * 50
DOCUMENTO_CNPJ = '44.556.677/0001-86'
DOCUMENTO_CPF = '390.533.447-05'


def _linha(*, documento=DOCUMENTO_CNPJ, valor='1280.40', situacao='P100_GERADA'):
    return nfse_emitidas.LinhaEmitida(
        chave=CHAVE,
        data_geracao=date(2026, 7, 31),
        documento=documento,
        nome_tomador='Tomador Sintético',
        competencia='06/2026',
        municipio='Município Sintético/RS',
        valor=Decimal(valor),
        situacao=situacao,
    )


def test_gravar_observacoes_insere_e_depois_atualiza_a_mesma_fonte(app):
    with app.app_context():
        assert nfse_emitidas._gravar_observacoes([_linha()], 'portal') == (1, 0)
        assert nfse_emitidas._gravar_observacoes([
            _linha(documento=DOCUMENTO_CPF, valor='1400.00')
        ], 'portal') == (0, 1)

        observacoes = ObservacaoEmitidaNfse.query.all()
        assert len(observacoes) == 1
        assert observacoes[0].documento == DOCUMENTO_CPF
        assert observacoes[0].valor == Decimal('1400.00')


def test_fontes_distintas_preservam_dois_retratos_e_adn_prevalece(app):
    with app.app_context():
        nfse_emitidas._gravar_observacoes([_linha()], 'portal')
        nfse_emitidas._gravar_observacoes([_linha(
            documento=DOCUMENTO_CPF, valor='1300.00', situacao='100')], 'adn')
        db.session.flush()

        assert ObservacaoEmitidaNfse.query.count() == 2
        assert nfse_emitidas.projetar_espelho([CHAVE]) == (1, 0)
        nota = NotaEmitidaNfse.query.one()
        assert nota.documento == DOCUMENTO_CPF
        assert nota.valor == Decimal('1300.00')
        assert nota.situacao == '100'
        assert nota.origem_autoritativa == 'adn'
        assert nota.situacao_fiscal == 'gerada'


def test_evento_adn_normaliza_cancelamento_na_projecao(app):
    with app.app_context():
        nfse_emitidas._gravar_observacoes([_linha()], 'portal')
        db.session.add(EventoEmitidaNfse(
            chave=CHAVE, tipo='e101101', data=datetime(2026, 8, 1, 10, 30)))
        assert nfse_emitidas.projetar_espelho([CHAVE]) == (1, 0)

        nota = NotaEmitidaNfse.query.one()
        assert nota.situacao_fiscal == 'cancelada'
        assert nota.origem_autoritativa == 'adn'


def test_gravar_e_projetar_nao_fazem_commit_interno(app, monkeypatch):
    def _falhar_commit():
        raise AssertionError('a unidade de trabalho pertence ao chamador')

    with app.app_context():
        monkeypatch.setattr(db.session, 'commit', _falhar_commit)

        assert nfse_emitidas._gravar_observacoes([_linha()], 'portal') == (1, 0)
        assert nfse_emitidas.projetar_espelho([CHAVE]) == (1, 0)
