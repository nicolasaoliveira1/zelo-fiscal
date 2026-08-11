"""Testes da persistencia e das rotas do painel de diagnostico."""
from app import db
from app.services import diagnostics


def test_gravar_evento_e_historico(app, client):
    with app.app_context():
        diagnostics.gravar_evento({
            'event': 'fgts_emit_error', 'level': 'ERROR',
            'error_type': 'PORTAL', 'municipio': 'Imbe',
            'message': 'portal fora', 'request_id': 'r9',
        })
        hist = diagnostics.historico(limite=10)
        assert any(h['error_type'] == 'PORTAL' and h['mensagem'] == 'portal fora' for h in hist)
        # limpa a linha inserida para nao vazar entre testes
        from app.models import EventoDiagnostico
        EventoDiagnostico.query.delete()
        db.session.commit()


def test_rota_eventos_json(client):
    r = client.get('/diagnostico/eventos')
    assert r.status_code == 200
    j = r.get_json()
    assert j['status'] == 'ok'
    assert isinstance(j['eventos'], list)
    assert isinstance(j['alertas'], list)


def test_rota_pagina_diagnostico(client):
    r = client.get('/diagnostico')
    assert r.status_code == 200
    assert 'Diagn'.encode() in r.data


# === dead-letter da fila (spec 09, RESOP-01) ==============================

def _tarefa_falha(app, ids, erro='Timeout aguardando download.', **kwargs):
    from datetime import datetime
    from app.models import Certidao, TarefaEmissao
    cert = db.session.get(Certidao, ids['fgts'])
    dados = dict(tipo='FGTS', empresa_id=cert.empresa_id, certidao_id=cert.id,
                 status='falha', tentativas=3, erro=erro,
                 concluida_em=datetime.now())
    dados.update(kwargs)
    tarefa = TarefaEmissao(**dados)
    db.session.add(tarefa)
    db.session.commit()
    return tarefa


def test_rota_falhas_lista_agrupada(app, client, ids):
    with app.app_context():
        _tarefa_falha(app, ids)

        r = client.get('/diagnostico/fila/falhas')

        assert r.status_code == 200
        j = r.get_json()
        assert j['status'] == 'ok'
        assert j['total'] == 1
        assert j['grupos'][0]['error_type'] == 'TIMEOUT'
        assert j['grupos'][0]['titulo'] == 'Tempo esgotado'


def test_rota_falhas_vazia(client, ids):
    r = client.get('/diagnostico/fila/falhas')
    assert r.status_code == 200
    assert r.get_json() == {'status': 'ok', 'grupos': [], 'total': 0}


def test_rota_reprocessar_por_ids(app, client, ids):
    with app.app_context():
        tarefa = _tarefa_falha(app, ids)

        r = client.post('/diagnostico/fila/reprocessar', json={'ids': [tarefa.id]})

        assert r.status_code == 200
        j = r.get_json()
        assert j['devolvidas'] == [tarefa.id]
        assert j['recusadas'] == []
        from app.models import TarefaEmissao
        assert db.session.get(TarefaEmissao, tarefa.id).status == 'pendente'


def test_rota_reprocessar_por_motivo(app, client, ids):
    with app.app_context():
        tarefa = _tarefa_falha(app, ids)

        r = client.post('/diagnostico/fila/reprocessar', json={'error_type': 'TIMEOUT'})

        assert r.status_code == 200
        assert r.get_json()['devolvidas'] == [tarefa.id]


def test_rota_reprocessar_devolve_recusadas_nomeadas(app, client, ids):
    """Parcial por desenho: o que nao der volta com o motivo."""
    with app.app_context():
        tarefa = _tarefa_falha(app, ids, certidao_id=999999)

        r = client.post('/diagnostico/fila/reprocessar', json={'ids': [tarefa.id]})

        j = r.get_json()
        assert j['devolvidas'] == []
        assert j['recusadas'][0]['id'] == tarefa.id
        assert j['recusadas'][0]['motivo']


def test_rota_reprocessar_sem_alvo_e_400(client, ids):
    r = client.post('/diagnostico/fila/reprocessar', json={})
    assert r.status_code == 400
    assert r.get_json()['status'] == 'error'


def test_rota_reprocessar_registra_auditoria(app, client, ids):
    with app.app_context():
        tarefa = _tarefa_falha(app, ids)
        client.post('/diagnostico/fila/reprocessar', json={'ids': [tarefa.id]})

        from app.models import EventoAuditoria
        evento = EventoAuditoria.query.filter_by(acao='fila.reprocessar').first()
        assert evento is not None
        assert evento.usuario_nome == 'admin_test'


def test_rotas_da_fila_exigem_admin(login_as, client_anon, ids):
    for papel in ('operador', 'leitura'):
        c = login_as(papel)
        assert c.get('/diagnostico/fila/falhas').status_code == 403
        assert c.post('/diagnostico/fila/reprocessar',
                      json={'ids': [1]}).status_code == 403
    assert client_anon.get('/diagnostico/fila/falhas').status_code in (302, 401)


# === semaforo de saude dos portais (spec 09, RESOP-03) ====================

def _sem_rede(monkeypatch):
    from app.services import portal_health

    class _Resp:
        status_code = 200

    portal_health.limpar_cache()
    monkeypatch.setattr(portal_health.requests, 'get', lambda url, **kw: _Resp())


def test_rota_portais_lista_portais_e_breakers(client, ids, monkeypatch):
    _sem_rede(monkeypatch)

    r = client.get('/diagnostico/portais')

    assert r.status_code == 200
    j = r.get_json()
    assert j['status'] == 'ok'
    assert {p['chave'] for p in j['portais']} >= {'FGTS', 'Estadual RS', 'Trabalhista'}
    assert j['breakers'] == []


def test_rota_portais_mostra_breaker_aberto(client, ids, monkeypatch):
    from app.services import circuit_breaker
    _sem_rede(monkeypatch)
    circuit_breaker.limpar()
    for _ in range(3):
        circuit_breaker.registrar_falha('FGTS', 'portal fora')

    j = client.get('/diagnostico/portais').get_json()

    assert [b['alvo'] for b in j['breakers']] == ['FGTS']
    assert next(p for p in j['portais'] if p['chave'] == 'FGTS')['breaker_aberto'] is True
    circuit_breaker.limpar()


def test_rota_portais_nao_quebra_quando_a_medicao_falha(client, ids, monkeypatch):
    from app.services import portal_health
    portal_health.limpar_cache()
    monkeypatch.setattr(portal_health, 'snapshot',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom')))

    r = client.get('/diagnostico/portais')

    assert r.status_code == 200
    j = r.get_json()
    assert j['portais'] == [] and j['message']


def test_rota_portais_exige_admin(login_as, client_anon, ids, monkeypatch):
    _sem_rede(monkeypatch)
    for papel in ('operador', 'leitura'):
        assert login_as(papel).get('/diagnostico/portais').status_code == 403
    assert client_anon.get('/diagnostico/portais').status_code in (302, 401)
