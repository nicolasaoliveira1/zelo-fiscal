"""Caracterização das rotas de lote (FGTS / Estadual RS / Municipal).

Trava o contrato HTTP (paths, status code, campo `status` e tokens das
mensagens). Exercita apenas caminhos seguros: status, info e erros 400 de
`iniciar` — nunca dispara um worker/Selenium. Usa as fixtures de conftest.py.
"""
PREFIXOS = {
    'fgts': '/fgts',
    'rs': '/estadual-rs',
    'municipal': '/municipal',
    'trabalhista': '/trabalhista',
}


def test_status_idle(client):
    for pref in PREFIXOS.values():
        r = client.get(f'{pref}/lote/status')
        assert r.status_code == 200, (pref, r.status_code)
        j = r.get_json()
        assert j['status'] == 'idle', (pref, j['status'])
        assert j['total'] == 0


def test_info(client, ids):
    chaves = {'ids', 'total', 'scope', 'vencidas', 'a_vencer', 'pendentes'}
    for k, pref in PREFIXOS.items():
        r = client.get(f'{pref}/lote/info/{ids[k]}')
        assert r.status_code == 200, (pref, r.status_code)
        j = r.get_json()
        assert j['status'] == 'ok', (pref, j)
        assert chaves <= set(j.keys()), (pref, set(j.keys()))


def test_iniciar_sem_certidao(client):
    for pref in PREFIXOS.values():
        r = client.post(f'{pref}/lote/iniciar', json={})
        assert r.status_code == 400, (pref, r.status_code)
        assert r.get_json()['status'] == 'error', pref


def test_iniciar_vazio_ou_precondicao(client, ids):
    # FGTS/Municipal: certidões sem data -> lote vazio (400, sem worker)
    r = client.post('/fgts/lote/iniciar', json={'certidao_id': ids['fgts']})
    assert r.status_code == 400, r.status_code
    m = r.get_json()['message']
    assert 'FGTS' in m and 'vencer' in m, m

    r = client.post('/municipal/lote/iniciar', json={'certidao_id': ids['municipal']})
    assert r.status_code == 400, r.status_code
    assert 'Municipal' in r.get_json()['message'], r.get_json()['message']

    # Estadual RS: flag desligada -> precondição barra antes do worker
    r = client.post('/estadual-rs/lote/iniciar', json={'certidao_id': ids['rs']})
    assert r.status_code == 400, r.status_code
    assert 'RS_ALTCHA_AUTOSOLVE_ENABLED' in r.get_json()['message'], r.get_json()['message']


def test_iniciar_bloqueado_por_emissao_individual(client, ids):
    from app.automation import batch_state
    batch_state.marcar_emissao_individual(True)
    try:
        r = client.post('/fgts/lote/iniciar', json={'certidao_id': ids['fgts']})
    finally:
        batch_state.marcar_emissao_individual(False)
    assert r.status_code == 409, r.status_code
    assert r.get_json()['status'] == 'error'
    assert 'individual' in r.get_json()['message'].lower()


# === guarda global de automacao ===========================================
# Ate o lote poder ser minimizado, quem impedia duas automacoes ao mesmo tempo
# era o overlay de tela cheia — a trava era a UI, por acidente. Estes testes
# fixam a trava explicita que tomou o lugar dela.

def test_iniciar_lote_bloqueado_por_lote_de_OUTRO_tipo(client, ids):
    """O caso que o servidor NAO barrava: FGTS rodando, RS tentando iniciar."""
    from app.automation import batch_state
    original = batch_state.FGTS_BATCH_STATE.get('status')
    batch_state.FGTS_BATCH_STATE['status'] = 'running'
    try:
        r = client.post('/estadual-rs/lote/iniciar', json={'certidao_id': ids['rs']})
    finally:
        batch_state.FGTS_BATCH_STATE['status'] = original
    assert r.status_code == 409, r.status_code
    # a mensagem precisa NOMEAR quem ocupa: com o lote minimizado o operador
    # nao ve mais qual esta rodando
    assert 'FGTS' in r.get_json()['message']


def test_lote_pausado_continua_ocupando(client, ids):
    """Pausado tem driver aberto e "Retomar" disponivel — ainda segura."""
    from app.automation import batch_state
    original = batch_state.FGTS_BATCH_STATE.get('status')
    batch_state.FGTS_BATCH_STATE['status'] = 'paused'
    try:
        r = client.post('/estadual-rs/lote/iniciar', json={'certidao_id': ids['rs']})
        em_curso = batch_state.automacao_em_curso()
    finally:
        batch_state.FGTS_BATCH_STATE['status'] = original
    assert r.status_code == 409
    assert em_curso['status'] == 'paused'
    assert 'pausado' in r.get_json()['message'].lower()


def test_automacao_em_curso_none_quando_tudo_parado():
    from app.automation import batch_state
    assert batch_state.automacao_em_curso() is None


def test_automacao_em_curso_nomeia_o_lote():
    from app.automation import batch_state
    original = batch_state.MUNICIPAL_BATCH_STATE.get('status')
    batch_state.MUNICIPAL_BATCH_STATE['status'] = 'running'
    try:
        em_curso = batch_state.automacao_em_curso()
    finally:
        batch_state.MUNICIPAL_BATCH_STATE['status'] = original
    assert em_curso == {'tipo': 'lote', 'rotulo': 'Municipal', 'status': 'running'}


def test_recusa_traz_motivo_para_a_ui(client, ids):
    """O dock minimizado pisca com base neste marcador.

    Sem ele o front so teria a mensagem em texto para adivinhar o motivo, e o
    operador veria um toast sem ligacao com o lote que esta fora de vista."""
    from app.automation import batch_state
    original = batch_state.FGTS_BATCH_STATE.get('status')
    batch_state.FGTS_BATCH_STATE['status'] = 'running'
    try:
        r = client.post('/estadual-rs/lote/iniciar', json={'certidao_id': ids['rs']})
    finally:
        batch_state.FGTS_BATCH_STATE['status'] = original
    corpo = r.get_json()
    assert corpo['motivo'] == 'automacao_em_curso'
    assert corpo['status'] == 'error'      # o envelope padrao nao muda
