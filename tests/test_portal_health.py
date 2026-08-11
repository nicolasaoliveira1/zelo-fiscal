"""Testes do semaforo de saude dos portais (spec 09, RESOP-03).

Sem rede: `requests.get` mockado. O que importa e (a) verde/vermelho honesto,
(b) municipio vindo do dry-run e nao de ping, e (c) cache que impede o painel de
martelar o portal.
"""
import pytest

from app.services import circuit_breaker, dryrun_municipio, portal_health


@pytest.fixture(autouse=True)
def _limpo():
    portal_health.limpar_cache()
    dryrun_municipio.limpar_resultados()
    circuit_breaker.limpar()
    yield
    portal_health.limpar_cache()
    dryrun_municipio.limpar_resultados()
    circuit_breaker.limpar()


class _Resposta:
    def __init__(self, status_code=200):
        self.status_code = status_code


def _mock_get(monkeypatch, resposta=None, erro=None):
    chamadas = []

    def _get(url, **kwargs):
        chamadas.append((url, kwargs))
        if erro is not None:
            raise erro
        return resposta or _Resposta()

    monkeypatch.setattr(portal_health.requests, 'get', _get)
    return chamadas


def _portais(app, monkeypatch, **kw):
    with app.app_context():
        return portal_health.snapshot(app.config, **kw)


# --- ping dos portais de tipo unico ----------------------------------------

def test_portal_que_responde_fica_ok(app, monkeypatch):
    _mock_get(monkeypatch, _Resposta(200))

    snap = _portais(app, monkeypatch)

    fgts = next(p for p in snap['portais'] if p['chave'] == 'FGTS')
    assert fgts['estado'] == 'ok'
    assert fgts['fonte'] == 'ping'
    assert fgts['latencia_ms'] >= 0
    assert fgts['medido_em']


def test_status_4xx_conta_como_responde(app, monkeypatch):
    """403/404 e o portal recusando, nao o portal caido."""
    _mock_get(monkeypatch, _Resposta(403))

    snap = _portais(app, monkeypatch)

    assert all(p['estado'] == 'ok' for p in snap['portais'] if p['fonte'] == 'ping')


def test_status_5xx_fica_fora(app, monkeypatch):
    _mock_get(monkeypatch, _Resposta(503))

    snap = _portais(app, monkeypatch)

    fgts = next(p for p in snap['portais'] if p['chave'] == 'FGTS')
    assert fgts['estado'] == 'fora'
    assert '503' in (fgts['mensagem'] or '')


def test_timeout_fica_fora_e_nao_levanta(app, monkeypatch):
    _mock_get(monkeypatch, erro=Exception('timeout ao conectar'))

    snap = _portais(app, monkeypatch)

    fgts = next(p for p in snap['portais'] if p['chave'] == 'FGTS')
    assert fgts['estado'] == 'fora'
    assert 'timeout' in (fgts['mensagem'] or '').lower()


def test_ping_manda_user_agent_e_timeout(app, monkeypatch):
    chamadas = _mock_get(monkeypatch)

    _portais(app, monkeypatch)

    _url, kwargs = chamadas[0]
    assert kwargs['headers']['User-Agent']
    assert kwargs['timeout'] == app.config['PORTAL_PING_TIMEOUT_S']


def test_urls_vem_do_mapa_de_sites(app, monkeypatch):
    """Nao hardcodar URL: o ping usa o mesmo SITES_CERTIDOES da automacao."""
    from app.automation import SITES_CERTIDOES
    chamadas = _mock_get(monkeypatch)

    _portais(app, monkeypatch)

    urls = {url for url, _ in chamadas}
    assert SITES_CERTIDOES['FGTS']['url'] in urls
    assert SITES_CERTIDOES['TRABALHISTA']['url'] in urls
    assert SITES_CERTIDOES['ESTADUAL']['RS']['url'] in urls
    assert SITES_CERTIDOES['FEDERAL']['url'] in urls


# --- cache -----------------------------------------------------------------

def test_cache_evita_segunda_requisicao_na_janela(app, monkeypatch):
    chamadas = _mock_get(monkeypatch)

    _portais(app, monkeypatch)
    n_primeira = len(chamadas)
    _portais(app, monkeypatch)

    assert len(chamadas) == n_primeira


def test_forcar_refaz_a_medicao(app, monkeypatch):
    chamadas = _mock_get(monkeypatch)

    _portais(app, monkeypatch)
    n_primeira = len(chamadas)
    _portais(app, monkeypatch, forcar=True)

    assert len(chamadas) == 2 * n_primeira


def test_cache_expira_apos_o_ttl(app, monkeypatch):
    from datetime import datetime, timedelta
    agora = datetime(2026, 8, 10, 10, 0, 0)
    monkeypatch.setattr(portal_health, '_agora', lambda: agora)
    chamadas = _mock_get(monkeypatch)

    _portais(app, monkeypatch)
    n_primeira = len(chamadas)
    monkeypatch.setattr(portal_health, '_agora',
                        lambda: agora + timedelta(minutes=6))
    _portais(app, monkeypatch)

    assert len(chamadas) == 2 * n_primeira


# --- municipios (dry-run, sem ping) ----------------------------------------

def test_municipio_sem_dryrun_fica_desconhecido(app, monkeypatch):
    _mock_get(monkeypatch)
    with app.app_context():
        from app import db
        from app.models import Municipio
        db.create_all()
        db.session.add(Municipio(nome='Imbe', url_certidao='http://imbe.example'))
        db.session.commit()

        snap = portal_health.snapshot(app.config)

        imbe = next(p for p in snap['portais'] if p.get('municipio') == 'Imbe')
        assert imbe['estado'] == 'desconhecido'
        assert imbe['fonte'] == 'dry-run'
        db.session.remove()
        db.drop_all()


def test_municipio_usa_o_resultado_do_dryrun(app, monkeypatch):
    chamadas = _mock_get(monkeypatch)
    dryrun_municipio.registrar_resultado(
        {'municipio': 'Imbe', 'resultado': dryrun_municipio.QUEBRADO,
         'quebrados': ['campo_cnpj'], 'mensagem': 'seletor nao resolve'})

    with app.app_context():
        from app import db
        from app.models import Municipio
        db.create_all()
        db.session.add(Municipio(nome='Imbe', url_certidao='http://imbe.example'))
        db.session.commit()

        snap = portal_health.snapshot(app.config)

        imbe = next(p for p in snap['portais'] if p.get('municipio') == 'Imbe')
        assert imbe['estado'] == 'fora'
        assert 'http://imbe.example' not in {url for url, _ in chamadas}
        db.session.remove()
        db.drop_all()


def test_municipio_ok_no_dryrun_fica_verde(app, monkeypatch):
    _mock_get(monkeypatch)
    dryrun_municipio.registrar_resultado(
        {'municipio': 'Imbe', 'resultado': dryrun_municipio.OK,
         'quebrados': [], 'mensagem': None})

    with app.app_context():
        from app import db
        from app.models import Municipio
        db.create_all()
        db.session.add(Municipio(nome='Imbe', url_certidao='http://imbe.example'))
        db.session.commit()

        snap = portal_health.snapshot(app.config)

        assert next(p for p in snap['portais']
                    if p.get('municipio') == 'Imbe')['estado'] == 'ok'
        db.session.remove()
        db.drop_all()


# --- breaker no mesmo payload ----------------------------------------------

def test_snapshot_marca_portal_com_breaker_aberto(app, monkeypatch):
    _mock_get(monkeypatch)
    for _ in range(3):
        circuit_breaker.registrar_falha('FGTS', 'portal fora')

    snap = _portais(app, monkeypatch)

    fgts = next(p for p in snap['portais'] if p['chave'] == 'FGTS')
    assert fgts['breaker_aberto'] is True
    assert [b['alvo'] for b in snap['breakers']] == ['FGTS']


@pytest.mark.parametrize('alvo', [
    circuit_breaker.ALVO_FGTS,
    circuit_breaker.ALVO_ESTADUAL_RS,
    circuit_breaker.ALVO_TRABALHISTA,
    circuit_breaker.ALVO_FEDERAL,
])
def test_chave_do_portal_e_o_alvo_do_breaker(app, monkeypatch, alvo):
    """Se as duas pontas usarem nomes diferentes, o painel mostra verde para um
    portal que o lote parou de tentar — e ninguem percebe."""
    _mock_get(monkeypatch)
    for _ in range(3):
        circuit_breaker.registrar_falha(alvo, 'portal fora')

    snap = _portais(app, monkeypatch)

    portal = next(p for p in snap['portais'] if p['chave'] == alvo)
    assert portal['breaker_aberto'] is True


def test_chave_do_municipio_e_o_alvo_do_breaker_municipal(app, monkeypatch):
    """O lote municipal abre o breaker na cidade canonica ('IMBE'); o painel
    precisa reconhecer o municipio cadastrado como 'Imbé'."""
    from app.routes import lotes
    _mock_get(monkeypatch)

    with app.app_context():
        from app import db
        from app.models import Certidao, Empresa, Municipio, TipoCertidao
        db.create_all()
        db.session.add(Municipio(nome='Imbé', url_certidao='http://imbe.example'))
        empresa = Empresa(nome='E', cnpj='00000000000191', cidade='Imbé', estado='RS')
        db.session.add(empresa)
        db.session.commit()
        certidao = Certidao(empresa_id=empresa.id, tipo=TipoCertidao.MUNICIPAL)
        db.session.add(certidao)
        db.session.commit()

        alvo = lotes._alvo_breaker_municipal(certidao.id)
        for _ in range(3):
            circuit_breaker.registrar_falha(alvo, 'portal fora')

        snap = portal_health.snapshot(app.config)

        imbe = next(p for p in snap['portais'] if p.get('municipio') == 'Imbé')
        assert imbe['chave'] == alvo
        assert imbe['breaker_aberto'] is True
        db.session.remove()
        db.drop_all()


def test_snapshot_sem_breaker_aberto(app, monkeypatch):
    _mock_get(monkeypatch)

    snap = _portais(app, monkeypatch)

    assert snap['breakers'] == []
    assert all(p['breaker_aberto'] is False for p in snap['portais'])
