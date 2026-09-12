"""Contrato HTTP da conferência sombra, sem portal ou API externa."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.services import nfse_api_adn, nfse_emitidas


def _resultado_adn(**alteracoes):
    valores = {
        'nsu_inicial': 10,
        'nsu_final': 18,
        'lidos': 3,
        'gravados': 2,
        'ignorados': 1,
        'falha': None,
        'desfecho': 'concluida',
        'nsu_falha': None,
    }
    valores.update(alteracoes)
    return nfse_api_adn.Resultado(**valores)


def _observacao(fonte, chave='CHAVE-SINTETICA-001', **alteracoes):
    valores = {
        'fonte': fonte,
        'chave': chave,
        'data_geracao': date(2026, 8, 12),
        'documento': 'DOC-TESTE-001',
        'nome_tomador': 'TOMADOR FICTICIO',
        'competencia_dps': '08/2026',
        'municipio': 'MUNICIPIO TESTE',
        'valor': Decimal('125.00'),
        'situacao_fonte': 'P100_GERADA' if fonte == 'portal' else '100',
    }
    valores.update(alteracoes)
    return SimpleNamespace(**valores)


def _sessao_livre(monkeypatch, rotas_nfse):
    estado = SimpleNamespace(adquiridas=0, liberadas=0)

    def adquirir():
        estado.adquiridas += 1
        return True

    def liberar():
        estado.liberadas += 1

    monkeypatch.setattr(
        rotas_nfse,
        'SESSAO',
        SimpleNamespace(adquirir=adquirir, liberar=liberar),
    )
    return estado


def test_sombra_executa_as_duas_fontes_e_separa_as_diferencas(
        client, monkeypatch):
    import app.routes.nfse as rotas_nfse

    estado = _sessao_livre(monkeypatch, rotas_nfse)
    chamadas = []
    portal = _observacao('portal')
    adn = _observacao('adn')
    somente_portal = _observacao('portal', 'CHAVE-SINTETICA-002')
    somente_adn = _observacao('adn', 'CHAVE-SINTETICA-003')
    divergencia_cancelamento = nfse_emitidas.DivergenciaEmitidaNfse(
        chave=portal.chave,
        portal=portal,
        adn=adn,
        situacao_portal='gerada',
        situacao_adn='cancelada',
    )

    def consultar(inicio, fim, *, execution_id):
        chamadas.append(('portal', inicio, fim, execution_id))
        return {
            'consulta_id': 11,
            'blocos': 1,
            'lidas': 2,
            'novas': 1,
            'atualizadas': 1,
        }

    def sincronizar(*, execution_id):
        chamadas.append(('adn', execution_id))
        return _resultado_adn()

    def comparar(inicio, fim):
        chamadas.append(('comparar', inicio, fim))
        return nfse_emitidas.Comparacao(
            so_no_portal=(somente_portal,),
            so_no_adn=(somente_adn,),
            divergentes=(divergencia_cancelamento,),
        )

    monkeypatch.setattr(nfse_emitidas, 'consultar', consultar)
    monkeypatch.setattr(nfse_api_adn, 'sincronizar', sincronizar)
    monkeypatch.setattr(nfse_emitidas, 'comparar_fontes', comparar)

    resposta = client.post('/nfse/emitidas/sombra', json={
        'inicio': '2026-08-01',
        'fim': '2026-08-31',
    })

    assert resposta.status_code == 200
    dados = resposta.get_json()
    sombra = dados['sombra']
    comparacao = sombra['comparacao']
    assert dados['status'] == 'ok'
    assert dados['execution_id'] == chamadas[0][3] == chamadas[1][1]
    assert sombra['status'] == 'concluida'
    assert sombra['periodo'] == {
        'inicio': '2026-08-01', 'fim': '2026-08-31'}
    assert comparacao['so_no_portal'][0]['chave'] == somente_portal.chave
    assert comparacao['so_no_adn'][0]['chave'] == somente_adn.chave
    assert len(comparacao['divergentes']) == 1
    assert len(comparacao['esperadas']) == 1
    assert comparacao['inesperadas'] == []
    divergente = comparacao['divergentes'][0]
    assert divergente['classificacao'] == 'esperada'
    assert divergente['situacao'] == {
        'portal': 'gerada',
        'adn': 'cancelada',
        'divergente': True,
        'explicacao': divergente['situacao']['explicacao'],
    }
    assert 'cancelamento' in divergente['situacao']['explicacao']
    assert chamadas[2] == ('comparar', date(2026, 8, 1), date(2026, 8, 31))
    assert estado.adquiridas == estado.liberadas == 1


def test_sombra_periodo_vazio_e_concluido_sem_afirmar_divergencia(
        client, monkeypatch):
    import app.routes.nfse as rotas_nfse

    _sessao_livre(monkeypatch, rotas_nfse)
    chamadas = []
    monkeypatch.setattr(
        nfse_emitidas,
        'consultar',
        lambda inicio, fim, *, execution_id: {
            'consulta_id': 12, 'blocos': 1, 'lidas': 0,
            'novas': 0, 'atualizadas': 0,
        },
    )
    monkeypatch.setattr(
        nfse_api_adn,
        'sincronizar',
        lambda *, execution_id: _resultado_adn(
            nsu_inicial=18, nsu_final=18, lidos=0, gravados=0,
            ignorados=0, desfecho='sem_documentos'),
    )

    def comparar(inicio, fim):
        chamadas.append((inicio, fim))
        return nfse_emitidas.Comparacao()

    monkeypatch.setattr(nfse_emitidas, 'comparar_fontes', comparar)

    resposta = client.post('/nfse/emitidas/sombra', json={
        'inicio': '2026-09-01', 'fim': '2026-09-30'})

    assert resposta.status_code == 200
    comparacao = resposta.get_json()['sombra']['comparacao']
    assert resposta.get_json()['sombra']['status'] == 'concluida'
    assert comparacao['so_no_portal'] == []
    assert comparacao['so_no_adn'] == []
    assert comparacao['divergentes'] == []
    assert comparacao['total_diferencas'] == 0
    assert chamadas == [(date(2026, 9, 1), date(2026, 9, 30))]


def test_sombra_falha_no_portal_fica_inconclusiva_e_nao_chama_adn(
        client, monkeypatch):
    import app.routes.nfse as rotas_nfse

    estado = _sessao_livre(monkeypatch, rotas_nfse)
    chamadas = []

    def consultar(inicio, fim, *, execution_id):
        chamadas.append('portal')
        raise RuntimeError('detalhe interno da automação')

    monkeypatch.setattr(nfse_emitidas, 'consultar', consultar)
    monkeypatch.setattr(
        nfse_api_adn,
        'sincronizar',
        lambda **kwargs: chamadas.append('adn'),
    )
    monkeypatch.setattr(
        nfse_emitidas,
        'comparar_fontes',
        lambda *args: chamadas.append('comparar'),
    )

    resposta = client.post('/nfse/emitidas/sombra', json={
        'inicio': '2026-08-01', 'fim': '2026-08-31'})

    assert resposta.status_code == 500
    dados = resposta.get_json()
    assert dados['status'] == 'error'
    assert dados['desfecho'] == 'inconclusiva'
    assert dados['fonte_falha'] == 'portal'
    assert dados['sombra']['status'] == 'inconclusiva'
    assert dados['sombra']['comparacao'] is None
    assert 'detalhe interno' not in resposta.get_data(as_text=True)
    assert chamadas == ['portal']
    assert estado.adquiridas == estado.liberadas == 1


def test_sombra_falha_no_adn_fica_inconclusiva_e_nao_compara(
        client, monkeypatch):
    import app.routes.nfse as rotas_nfse

    _sessao_livre(monkeypatch, rotas_nfse)
    chamadas = []
    monkeypatch.setattr(
        nfse_emitidas,
        'consultar',
        lambda inicio, fim, *, execution_id: {
            'consulta_id': 13, 'blocos': 1, 'lidas': 1,
            'novas': 1, 'atualizadas': 0,
        },
    )

    def sincronizar(*, execution_id):
        chamadas.append('adn')
        return _resultado_adn(
            nsu_final=None, nsu_falha=19, lidos=0, gravados=0,
            ignorados=0, falha='resposta interna do transporte',
            desfecho='indisponivel')

    monkeypatch.setattr(nfse_api_adn, 'sincronizar', sincronizar)
    monkeypatch.setattr(
        nfse_emitidas,
        'comparar_fontes',
        lambda *args: chamadas.append('comparar'),
    )

    resposta = client.post('/nfse/emitidas/sombra', json={
        'inicio': '2026-08-01', 'fim': '2026-08-31'})

    assert resposta.status_code == 503
    dados = resposta.get_json()
    assert dados['desfecho'] == 'inconclusiva'
    assert dados['fonte_falha'] == 'adn'
    assert dados['desfecho_fonte'] == 'indisponivel'
    assert dados['sombra']['fontes']['portal']['lidas'] == 1
    assert dados['sombra']['fontes']['adn']['nsu_falha'] == 19
    assert dados['sombra']['comparacao'] is None
    assert 'resposta interna do transporte' not in resposta.get_data(as_text=True)
    assert chamadas == ['adn']


def test_sombra_nao_compara_sincronizacao_no_teto(client, monkeypatch):
    import app.routes.nfse as rotas_nfse

    _sessao_livre(monkeypatch, rotas_nfse)
    chamadas = []
    monkeypatch.setattr(
        nfse_emitidas,
        'consultar',
        lambda inicio, fim, *, execution_id: {
            'consulta_id': 14, 'blocos': 1, 'lidas': 1,
            'novas': 1, 'atualizadas': 0,
        },
    )
    monkeypatch.setattr(
        nfse_api_adn,
        'sincronizar',
        lambda *, execution_id: _resultado_adn(
            nsu_final=17, desfecho='teto'),
    )
    monkeypatch.setattr(
        nfse_emitidas,
        'comparar_fontes',
        lambda *args: chamadas.append('comparar'),
    )

    resposta = client.post('/nfse/emitidas/sombra', json={
        'inicio': '2026-08-01', 'fim': '2026-08-31'})

    assert resposta.status_code == 409
    dados = resposta.get_json()
    assert dados['desfecho'] == 'inconclusiva'
    assert dados['fonte_falha'] == 'adn'
    assert dados['desfecho_fonte'] == 'teto'
    assert dados['sombra']['comparacao'] is None
    assert chamadas == []


def test_sombra_exige_papel_de_operador(login_as, monkeypatch):
    chamadas = []
    monkeypatch.setattr(
        nfse_emitidas,
        'consultar',
        lambda *args, **kwargs: chamadas.append('portal'),
    )
    monkeypatch.setattr(
        nfse_api_adn,
        'sincronizar',
        lambda *args, **kwargs: chamadas.append('adn'),
    )

    resposta = login_as('leitura').post('/nfse/emitidas/sombra', json={
        'inicio': '2026-08-01', 'fim': '2026-08-31'})

    assert resposta.status_code == 403
    assert resposta.get_json()['status'] == 'error'
    assert chamadas == []


def test_painel_exibe_acionamento_e_resultado_da_conferencia_sombra(login_as):
    resposta = login_as('operador').get('/nfse')

    assert resposta.status_code == 200
    corpo = resposta.get_data(as_text=True)
    assert 'id="nfseSombra"' in corpo
    assert 'id="btnExecutarSombra"' in corpo
    assert 'id="nfseSombraEstado"' in corpo
    assert 'id="nfseSombraResultado"' in corpo
    assert 'preserva os retratos separados' in corpo
