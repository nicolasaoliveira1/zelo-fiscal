"""Testes de rota do fluxo Federal assistido (COV-01b).

Monitor de download (F2): delega ao nucleo `certidao_service.finalizar_federal`
e mapeia a resposta. Aqui a deteccao de arquivo e o nucleo sao mockados para
evitar o loop de 180s / sleeps reais; o foco e o WIRING + o mapeamento do JSON.
"""
from contextlib import contextmanager
from datetime import date
from io import BytesIO
from unittest.mock import patch

from app.models import Certidao, TipoCertidao
from app.routes import certidoes


def _federal_id(app):
    with app.app_context():
        return Certidao.query.filter_by(tipo=TipoCertidao.FEDERAL).first().id


@contextmanager
def _monitor_mocks(finalizar_ret):
    """Mocka a deteccao (arquivo achado na 1a iteracao) + o nucleo + I/O de chave."""
    with patch.object(certidoes.time, 'sleep'), \
            patch.object(certidoes.file_manager, 'criar_chave_interrupcao', return_value='k'), \
            patch.object(certidoes.file_manager, 'chave_interrupcao_mais_recente_que', return_value=False), \
            patch.object(certidoes.file_manager, 'remover_chave_interrupcao'), \
            patch.object(certidoes.file_manager, 'obter_caminho_chave_interrupcao',
                         return_value='/tmp/chave-inexistente'), \
            patch.object(certidoes, '_snapshot_downloads_pdf', return_value=set()), \
            patch.object(certidoes, '_pick_changed_download_pdf', return_value='/tmp/fed.pdf'), \
            patch.object(certidoes.file_manager, 'mover_e_renomear', return_value=(True, '/rede/fed.pdf')), \
            patch.object(certidoes, '_gerar_visualizar_token', return_value='tok'), \
            patch.object(certidoes.certidao_service, 'finalizar_federal', return_value=finalizar_ret):
        yield


def test_monitor_negativa_retorna_validade_e_token(app, client):
    # FED-01/02: arquivo detectado + negativa -> success com data_validade (do nucleo) e token.
    fid = _federal_id(app)
    with _monitor_mocks({'ok': True, 'pendente': False,
                         'data_validade': date(2026, 12, 31), 'message': None}):
        resp = client.get(f'/certidao/monitorar_download_federal/{fid}')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['status'] == 'success'
    assert data['data_validade'] == '2026-12-31'
    assert data['data_validade_formatada'] == '31/12/2026'
    assert data['visualizar_token'] == 'tok'


def test_monitor_positiva_retorna_status_pendente(app, client):
    # FED-04: positiva -> status 'pendente' (sem modal de data); nucleo ja marcou PENDENTE.
    fid = _federal_id(app)
    with _monitor_mocks({'ok': True, 'pendente': True, 'data_validade': None,
                         'message': 'Certidão Federal POSITIVA: pendente.'}):
        resp = client.get(f'/certidao/monitorar_download_federal/{fid}')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['status'] == 'pendente'
    assert 'pendente' in data['mensagem'].lower()
    assert 'data_validade' not in data


def test_monitor_falha_do_nucleo_retorna_json_error(app, client):
    # FED-05: nucleo devolve ok=False -> rota responde json_error (status error).
    fid = _federal_id(app)
    with _monitor_mocks({'ok': False, 'grave': True,
                         'message': 'Erro ao marcar PENDENTE no banco.'}):
        resp = client.get(f'/certidao/monitorar_download_federal/{fid}')
    assert resp.status_code == 500
    data = resp.get_json()
    assert data['status'] == 'error'
    assert 'PENDENTE' in data['message']


def test_monitor_exige_login(app, client_anon):
    # AD-005: rota protegida — sem sessao, nao executa (redirect/401), sem tocar no nucleo.
    fid = _federal_id(app)
    resp = client_anon.get(f'/certidao/monitorar_download_federal/{fid}')
    assert resp.status_code in (302, 401)


# ---- F3: upload manual (POST /certidao/federal/registrar/<id>) ----

def _pdf_upload(nome='cert.pdf', conteudo=b'%PDF-1.4 fake federal'):
    return {'arquivo': (BytesIO(conteudo), nome)}


def test_upload_negativa_salva_e_reusa_nucleo(app, client):
    # FED-06: upload de PDF -> salva via file_manager + finalizar_federal (mesmo nucleo).
    fid = _federal_id(app)
    with patch.object(certidoes.file_manager, 'mover_e_renomear',
                      return_value=(True, '/rede/fed.pdf')) as mv, \
            patch.object(certidoes, '_gerar_visualizar_token', return_value='tok'), \
            patch.object(certidoes.certidao_service, 'finalizar_federal',
                         return_value={'ok': True, 'pendente': False,
                                       'data_validade': date(2026, 12, 31), 'message': None}) as fin:
        resp = client.post(f'/certidao/federal/registrar/{fid}',
                           data=_pdf_upload(), content_type='multipart/form-data')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['status'] == 'success'
    assert data['data_validade'] == '2026-12-31'
    assert data['visualizar_token'] == 'tok'
    mv.assert_called_once()
    fin.assert_called_once()


def test_upload_positiva_retorna_pendente(app, client):
    # FED-06/04: positiva no upload -> status 'pendente' (nucleo marcou PENDENTE).
    fid = _federal_id(app)
    with patch.object(certidoes.file_manager, 'mover_e_renomear',
                      return_value=(True, '/rede/fed.pdf')), \
            patch.object(certidoes.certidao_service, 'finalizar_federal',
                         return_value={'ok': True, 'pendente': True, 'data_validade': None,
                                       'message': 'Federal POSITIVA: pendente.'}):
        resp = client.post(f'/certidao/federal/registrar/{fid}',
                           data=_pdf_upload(), content_type='multipart/form-data')
    assert resp.status_code == 200
    assert resp.get_json()['status'] == 'pendente'


def test_upload_rejeita_nao_pdf(app, client):
    # FED-07: arquivo que nao e PDF -> json_error, sem tocar em file_manager/nucleo.
    fid = _federal_id(app)
    with patch.object(certidoes.file_manager, 'mover_e_renomear') as mv, \
            patch.object(certidoes.certidao_service, 'finalizar_federal') as fin:
        resp = client.post(f'/certidao/federal/registrar/{fid}',
                           data=_pdf_upload(nome='cert.txt'), content_type='multipart/form-data')
    assert resp.status_code == 400
    assert resp.get_json()['status'] == 'error'
    mv.assert_not_called()
    fin.assert_not_called()


def test_upload_rejeita_vazio(app, client):
    # FED-07: PDF vazio (0 bytes) -> json_error antes de mover.
    fid = _federal_id(app)
    with patch.object(certidoes.file_manager, 'mover_e_renomear') as mv:
        resp = client.post(f'/certidao/federal/registrar/{fid}',
                           data=_pdf_upload(conteudo=b''), content_type='multipart/form-data')
    assert resp.status_code == 400
    assert resp.get_json()['status'] == 'error'
    mv.assert_not_called()


def test_upload_rejeita_excede_limite(app, client):
    # FED-07: arquivo acima do limite -> 413 json_error, antes de mover/nucleo.
    fid = _federal_id(app)
    grande = b'%PDF-1.4 ' + b'x' * 64
    with patch.object(certidoes, '_MAX_UPLOAD_FEDERAL_BYTES', 8), \
            patch.object(certidoes.file_manager, 'mover_e_renomear') as mv, \
            patch.object(certidoes.certidao_service, 'finalizar_federal') as fin:
        resp = client.post(f'/certidao/federal/registrar/{fid}',
                           data=_pdf_upload(conteudo=grande), content_type='multipart/form-data')
    assert resp.status_code == 413
    assert resp.get_json()['status'] == 'error'
    mv.assert_not_called()
    fin.assert_not_called()


def test_upload_rejeita_tipo_nao_federal(app, ids):
    # FED-08: alvo de tipo != FEDERAL -> json_error, sem processar arquivo.
    from tests.conftest import USUARIOS_TESTE
    c = app.test_client()
    c.post('/login', data=dict(zip(('username', 'senha'), USUARIOS_TESTE['operador'])))
    resp = c.post(f"/certidao/federal/registrar/{ids['fgts']}",
                  data=_pdf_upload(), content_type='multipart/form-data')
    assert resp.status_code == 400
    assert resp.get_json()['status'] == 'error'


def test_upload_alvo_inexistente_404(app, client):
    # FED-08: certidao inexistente -> 404 json_error.
    resp = client.post('/certidao/federal/registrar/999999',
                       data=_pdf_upload(), content_type='multipart/form-data')
    assert resp.status_code == 404
    assert resp.get_json()['status'] == 'error'


def test_upload_exige_login(app, client_anon):
    # AD-005: sem sessao -> nao executa.
    fid = _federal_id(app)
    resp = client_anon.post(f'/certidao/federal/registrar/{fid}',
                           data=_pdf_upload(), content_type='multipart/form-data')
    assert resp.status_code in (302, 401)
