"""Rota de sondagem dos acessos SEFIN e ADN (APINF-01/02)."""
from app.services import nfse_api_adn
from app.services.nfse_api_transporte import Desfecho


def _acessos(situacao_sefin, situacao_adn, http_sefin=None, http_adn=None):
    return nfse_api_adn.Acessos(
        Desfecho(situacao_sefin, http_sefin, 'resposta-interna-sintetica',
                 f'sefin-{situacao_sefin}'),
        Desfecho(situacao_adn, http_adn, 'resposta-interna-sintetica',
                 f'adn-{situacao_adn}'),
    )


def test_acesso_devolve_sefin_e_adn_separadamente(client, monkeypatch):
    monkeypatch.setattr(
        nfse_api_adn, 'verificar_acesso',
        lambda: _acessos('ok', 'negado', 204, 403),
    )

    resposta = client.post('/nfse/api/acesso', json={})

    assert resposta.status_code == 200
    dados = resposta.get_json()
    assert dados['status'] == 'ok'
    assert dados['acessos']['sefin'] == {
        'situacao': 'ok', 'http': 204, 'mensagem': 'sefin-ok',
    }
    assert dados['acessos']['adn'] == {
        'situacao': 'negado', 'http': 403, 'mensagem': 'adn-negado',
    }
    assert 'resposta-interna-sintetica' not in resposta.get_data(as_text=True)


def test_acesso_sem_credencial_exibe_causa_nos_dois_servicos(
        client, monkeypatch):
    monkeypatch.setattr(
        nfse_api_adn, 'verificar_acesso',
        lambda: _acessos('credencial', 'credencial'),
    )

    resposta = client.post('/nfse/api/acesso', json={})

    assert resposta.status_code == 200
    dados = resposta.get_json()
    assert dados['acessos']['sefin']['situacao'] == 'credencial'
    assert dados['acessos']['adn']['situacao'] == 'credencial'


def test_acesso_indisponivel_nao_apaga_o_desfecho_do_outro_servico(
        client, monkeypatch):
    monkeypatch.setattr(
        nfse_api_adn, 'verificar_acesso',
        lambda: _acessos('indisponivel', 'ok', 503, 200),
    )

    resposta = client.post('/nfse/api/acesso', json={})

    assert resposta.status_code == 200
    dados = resposta.get_json()['acessos']
    assert dados['sefin']['situacao'] == 'indisponivel'
    assert dados['sefin']['http'] == 503
    assert dados['adn']['situacao'] == 'ok'
    assert dados['adn']['http'] == 200


def test_falha_inesperada_da_sondagem_devolve_envelope_de_erro(
        client, monkeypatch):
    def falhar():
        raise RuntimeError('falha interna sintetica')

    monkeypatch.setattr(nfse_api_adn, 'verificar_acesso', falhar)

    resposta = client.post('/nfse/api/acesso', json={})

    assert resposta.status_code == 500
    dados = resposta.get_json()
    assert dados['status'] == 'error'
    assert dados['message']
    assert dados['request_id']
    assert 'falha interna sintetica' not in dados['message']


def test_acesso_exige_papel_de_operador_com_envelope_json(login_as):
    resposta = login_as('leitura').post('/nfse/api/acesso')

    assert resposta.status_code == 403
    dados = resposta.get_json()
    assert dados['status'] == 'error'
    assert dados['message']
    assert dados['request_id']


def test_painel_exibe_verificacao_separada_e_carrega_js(login_as):
    resposta = login_as('operador').get('/nfse')

    assert resposta.status_code == 200
    corpo = resposta.get_data(as_text=True)
    assert 'id="nfseApiAcesso"' in corpo
    assert 'id="btnVerificarAcesso"' in corpo
    assert 'id="nfseApiAcessoResultados"' in corpo
    assert 'Testa SEFIN e ADN separadamente' in corpo
    assert 'js/nfse.js' in corpo
