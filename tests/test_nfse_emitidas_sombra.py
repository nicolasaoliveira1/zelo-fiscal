"""Comparação somente leitura das observações do portal e do ADN."""
from datetime import date
from decimal import Decimal

from app import db
from app.models import EventoEmitidaNfse, ObservacaoEmitidaNfse
from app.services import nfse_emitidas


INICIO = date(2026, 7, 1)
FIM = date(2026, 7, 31)
DOCUMENTO = '44.556.677/0001-86'
DOCUMENTO_ALTERNATIVO = '55.666.777/0001-55'
CHAVE_IDENTICA = '0' * 50
CHAVE_PORTAL = '1' * 50
CHAVE_ADN = '2' * 50
CHAVE_VALOR = '3' * 50
CHAVE_SITUACAO = '4' * 50
CHAVE_FORA = '5' * 50


def _observacao(
        fonte, chave, *, valor='1280.40', situacao='100', data=FIM,
        documento=DOCUMENTO):
    return ObservacaoEmitidaNfse(
        fonte=fonte,
        chave=chave,
        data_geracao=data,
        documento=documento,
        valor=Decimal(valor),
        situacao_fonte=situacao,
    )


def _persistir(*registros):
    db.session.add_all(registros)
    db.session.commit()


def test_fontes_idênticas_não_criam_divergência(app, ids):
    with app.app_context():
        _persistir(
            _observacao('portal', CHAVE_IDENTICA, situacao='P100_GERADA'),
            _observacao('adn', CHAVE_IDENTICA),
        )

        resultado = nfse_emitidas.comparar_fontes(INICIO, FIM)

        assert isinstance(resultado, nfse_emitidas.Comparacao)
        assert resultado.so_no_portal == ()
        assert resultado.so_no_adn == ()
        assert resultado.divergentes == ()


def test_nota_somente_no_portal_fica_separada(app, ids):
    with app.app_context():
        _persistir(_observacao('portal', CHAVE_PORTAL))

        resultado = nfse_emitidas.comparar_fontes(INICIO, FIM)

        assert [item.chave for item in resultado.so_no_portal] == [CHAVE_PORTAL]
        assert resultado.so_no_adn == ()
        assert resultado.divergentes == ()


def test_nota_somente_no_adn_fica_separada(app, ids):
    with app.app_context():
        _persistir(_observacao('adn', CHAVE_ADN))

        resultado = nfse_emitidas.comparar_fontes(INICIO, FIM)

        assert resultado.so_no_portal == ()
        assert [item.chave for item in resultado.so_no_adn] == [CHAVE_ADN]
        assert resultado.divergentes == ()


def test_valor_divergente_nomeia_o_campo_sem_misturar_situacao(app, ids):
    with app.app_context():
        _persistir(
            _observacao(
                'portal', CHAVE_VALOR, valor='1280.40', situacao='P100_GERADA'),
            _observacao('adn', CHAVE_VALOR, valor='1280.41'),
        )

        resultado = nfse_emitidas.comparar_fontes(INICIO, FIM)

        assert len(resultado.divergentes) == 1
        divergencia = resultado.divergentes[0]
        assert divergencia.chave == CHAVE_VALOR
        assert divergencia.campos == ('valor',)
        assert divergencia.situacao_divergente is False
        assert divergencia.situacao_portal == 'gerada'
        assert divergencia.situacao_adn == 'gerada'


def test_data_e_documento_divergentes_são_comparados_pela_mesma_chave(
        app, ids):
    with app.app_context():
        _persistir(
            _observacao('portal', CHAVE_VALOR, data=FIM),
            _observacao(
                'adn', CHAVE_VALOR, data=date(2026, 8, 1),
                documento=DOCUMENTO_ALTERNATIVO),
        )

        resultado = nfse_emitidas.comparar_fontes(INICIO, FIM)

        assert len(resultado.divergentes) == 1
        assert resultado.divergentes[0].campos == (
            'data_geracao', 'documento')


def test_cancelamento_do_adn_e_situacao_divergente_normalizada(app, ids):
    with app.app_context():
        _persistir(
            _observacao(
                'portal', CHAVE_SITUACAO, situacao='P100_GERADA'),
            _observacao('adn', CHAVE_SITUACAO),
            EventoEmitidaNfse(
                chave=CHAVE_SITUACAO, tipo='e101101', num_seq=1),
        )

        resultado = nfse_emitidas.comparar_fontes(INICIO, FIM)

        assert len(resultado.divergentes) == 1
        divergencia = resultado.divergentes[0]
        assert divergencia.campos == ()
        assert divergencia.situacao_divergente is True
        assert divergencia.situacao_portal == 'gerada'
        assert divergencia.situacao_adn == 'cancelada'


def test_observacao_fora_do_periodo_não_aparece(app, ids):
    with app.app_context():
        _persistir(_observacao(
            'portal', CHAVE_FORA, data=date(2026, 8, 1)))

        resultado = nfse_emitidas.comparar_fontes(INICIO, FIM)

        assert resultado == nfse_emitidas.Comparacao()


def test_comparar_fontes_não_escreve_no_banco(app, ids, monkeypatch):
    with app.app_context():
        _persistir(
            _observacao('portal', CHAVE_IDENTICA, situacao='P100_GERADA'),
            _observacao('adn', CHAVE_IDENTICA),
        )
        contagens_antes = (
            len(db.session.new), len(db.session.dirty), len(db.session.deleted))

        def _recusar_escrita(*_args, **_kwargs):
            raise AssertionError('comparar_fontes não pode escrever')

        monkeypatch.setattr(db.session, 'add', _recusar_escrita)
        monkeypatch.setattr(db.session, 'add_all', _recusar_escrita)
        monkeypatch.setattr(db.session, 'commit', _recusar_escrita)
        monkeypatch.setattr(db.session, 'delete', _recusar_escrita)

        resultado = nfse_emitidas.comparar_fontes(INICIO, FIM)

        assert resultado.divergentes == ()
        assert contagens_antes == (
            len(db.session.new), len(db.session.dirty), len(db.session.deleted))
