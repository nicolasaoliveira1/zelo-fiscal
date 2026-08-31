"""Resumo mensal imprimível da NFS-e, sempre lido do espelho local."""
from datetime import date, datetime
from decimal import Decimal

from app import db
from app.models import NotaEmitidaNfse, SituacaoNotaEmitida
from app.services import nfse_emitidas


def _gravar_emitidas(app):
    with app.app_context():
        db.session.add_all([
            NotaEmitidaNfse(
                chave='A' * 50,
                data_geracao=date(2026, 8, 5),
                valor=Decimal('100.50'),
                situacao=SituacaoNotaEmitida.GERADA,
                consultado_em=datetime(2026, 8, 30, 9, 15),
            ),
            NotaEmitidaNfse(
                chave='B' * 50,
                data_geracao=date(2026, 8, 20),
                valor=Decimal('200.00'),
                situacao=SituacaoNotaEmitida.GERADA,
                consultado_em=datetime(2026, 8, 31, 11, 30),
            ),
            NotaEmitidaNfse(
                chave='C' * 50,
                data_geracao=date(2026, 8, 21),
                valor=None,
                situacao=SituacaoNotaEmitida.GERADA,
                consultado_em=datetime(2026, 8, 31, 11, 30),
            ),
            NotaEmitidaNfse(
                chave='D' * 50,
                data_geracao=date(2026, 8, 22),
                valor=Decimal('999.00'),
                situacao='P200_SITUACAO_SINTETICA',
                consultado_em=datetime(2026, 8, 31, 11, 30),
            ),
        ])
        db.session.commit()


def test_resumo_mensal_mostra_periodo_totais_e_rastreabilidade(client, app, monkeypatch):
    _gravar_emitidas(app)

    def _nao_consultar(*args, **kwargs):
        raise AssertionError('abrir o resumo não pode consultar o portal')

    monkeypatch.setattr(nfse_emitidas, 'consultar', _nao_consultar)
    resposta = client.get('/nfse/emitidas/resumo?mes=08/2026')

    assert resposta.status_code == 200
    pagina = resposta.get_data(as_text=True)
    assert '01/08/2026' in pagina
    assert '31/08/2026' in pagina
    assert 'R$ 300,50' in pagina
    assert '<strong>3</strong>' in pagina
    assert '31/08/2026 11:30' in pagina
    assert '1 nota emitida está' in pagina
    assert 'P200_SITUACAO_SINTETICA' in pagina
    assert 'Imprimir / salvar PDF' in pagina


def test_resumo_sem_consulta_explica_como_obter_os_dados(client):
    resposta = client.get('/nfse/emitidas/resumo?mes=08/2026')

    assert resposta.status_code == 200
    assert 'Ainda não há dados deste mês' in resposta.get_data(as_text=True)


def test_resumo_recusa_mes_invalido(client):
    resposta = client.get('/nfse/emitidas/resumo?mes=13/2026')

    assert resposta.status_code == 400
    assert resposta.content_type.startswith('text/html')
    assert resposta.get_json(silent=True) is None
    assert 'MM/AAAA' in resposta.get_data(as_text=True)


def test_resumo_exige_papel_de_operador(login_as):
    resposta = login_as('leitura').get('/nfse/emitidas/resumo?mes=08/2026')

    assert resposta.status_code == 403
