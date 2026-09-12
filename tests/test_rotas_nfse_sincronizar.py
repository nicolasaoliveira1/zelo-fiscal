"""Rota de sincronização incremental das notas emitidas pelo ADN."""
from app.services import nfse_api_adn


def _resultado(**alteracoes):
    valores = {
        'nsu_inicial': 0,
        'nsu_final': 12,
        'lidos': 4,
        'gravados': 3,
        'ignorados': 1,
        'falha': None,
        'desfecho': 'concluida',
        'nsu_falha': None,
    }
    valores.update(alteracoes)
    return nfse_api_adn.Resultado(**valores)


def test_sincronizar_devolve_faixa_e_contagens(client, monkeypatch):
    chamada = {}

    def sincronizar(*, execution_id):
        chamada['execution_id'] = execution_id
        return _resultado()

    monkeypatch.setattr(nfse_api_adn, 'sincronizar', sincronizar)

    resposta = client.post('/nfse/emitidas/sincronizar')

    assert resposta.status_code == 200
    dados = resposta.get_json()
    resumo = dados['sincronizacao']
    assert dados['status'] == 'ok'
    assert dados['execution_id'] == chamada['execution_id']
    assert resumo['faixa_nsu'] == {'inicio': 0, 'fim': 12}
    assert resumo['nsu_inicial'] == 0
    assert resumo['nsu_final'] == 12
    assert resumo['lidos'] == 4
    assert resumo['gravados'] == 3
    assert resumo['ignorados'] == 1
    assert resumo['desfecho'] == 'concluida'


def test_sincronizar_recusa_lease_ocupado_com_mensagem_legivel(
        client, monkeypatch):
    def sincronizar(*, execution_id):
        raise nfse_api_adn.SincronizacaoEmCursoError(
            'detalhe interno que não deve ser exibido')

    monkeypatch.setattr(nfse_api_adn, 'sincronizar', sincronizar)

    resposta = client.post('/nfse/emitidas/sincronizar')

    assert resposta.status_code == 409
    dados = resposta.get_json()
    assert dados['status'] == 'error'
    assert dados['desfecho'] == 'em_curso'
    assert 'já existe uma sincronização' in dados['message'].lower()
    assert 'detalhe interno' not in resposta.get_data(as_text=True)
    assert dados['request_id']


def test_sincronizar_indisponibilidade_devolve_503_e_resumo(
        client, monkeypatch):
    monkeypatch.setattr(
        nfse_api_adn,
        'sincronizar',
        lambda *, execution_id: _resultado(
            nsu_final=None,
            nsu_falha=13,
            lidos=0,
            gravados=0,
            ignorados=0,
            falha='resposta interna do transporte',
            desfecho='indisponivel'),
    )

    resposta = client.post('/nfse/emitidas/sincronizar')

    assert resposta.status_code == 503
    dados = resposta.get_json()
    assert dados['status'] == 'error'
    assert dados['desfecho'] == 'indisponivel'
    assert 'serviço do adn está indisponível' in dados['message'].lower()
    assert 'resposta interna do transporte' not in resposta.get_data(as_text=True)
    assert dados['sincronizacao']['faixa_nsu'] == {'inicio': 0, 'fim': None}
    assert dados['sincronizacao']['nsu_falha'] == 13


def test_sincronizar_recusa_adn_com_codigo_sem_expor_corpo(
        client, monkeypatch):
    monkeypatch.setattr(
        nfse_api_adn,
        'sincronizar',
        lambda *, execution_id: _resultado(
            falha='A API nacional negou a chamada (HTTP 403).',
            desfecho='negado'),
    )

    resposta = client.post('/nfse/emitidas/sincronizar')

    assert resposta.status_code == 502
    dados = resposta.get_json()
    assert dados['desfecho'] == 'negado'
    assert 'HTTP 403' in dados['message']
    assert 'A API nacional negou a chamada' not in resposta.get_data(as_text=True)


def test_sincronizar_exige_papel_de_operador(login_as, monkeypatch):
    chamada = {'quantidade': 0}

    def sincronizar(*, execution_id):
        chamada['quantidade'] += 1
        return _resultado()

    monkeypatch.setattr(nfse_api_adn, 'sincronizar', sincronizar)

    resposta = login_as('leitura').post('/nfse/emitidas/sincronizar')

    assert resposta.status_code == 403
    assert resposta.get_json()['status'] == 'error'
    assert chamada['quantidade'] == 0


def test_painel_exibe_acionamento_da_sincronizacao_adn(login_as):
    resposta = login_as('operador').get('/nfse')

    assert resposta.status_code == 200
    corpo = resposta.get_data(as_text=True)
    assert 'id="nfseAdnSincronizacao"' in corpo
    assert 'id="btnSincronizarAdn"' in corpo
    assert 'id="nfseAdnSincronizacaoEstado"' in corpo
    assert 'id="nfseAdnSincronizacaoResultado"' in corpo
    assert 'não emite documento fiscal' in corpo
