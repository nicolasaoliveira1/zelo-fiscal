"""Testes da ligacao dos fluxos de lote ao circuit breaker (spec 09, RESOP-02).

O que importa aqui e o ALVO: portal unico usa um rotulo fixo, municipal usa a
cidade — se Imbe cair, Tramandai continua emitindo.
"""
import pytest

from app import db
from app.automation.batch_state import (
    FGTS_BATCH_STATE,
    MUNICIPAL_BATCH_STATE,
    RS_BATCH_STATE,
    TRABALHISTA_BATCH_STATE,
)
from app.models import Certidao, Empresa, TipoCertidao
from app.routes import lotes
from app.services import batch_engine, circuit_breaker


@pytest.fixture()
def ctx(app):
    with app.app_context():
        db.create_all()
        yield app
        db.session.rollback()
        db.session.remove()
        db.drop_all()


@pytest.fixture(autouse=True)
def _breaker_limpo():
    circuit_breaker.limpar()
    yield
    circuit_breaker.limpar()


@pytest.fixture(autouse=True)
def _estados_de_lote_limpos():
    """Os fluxos do agendador marcam o batch_state global como 'running' antes
    de chamar o loop; com o loop dublado ninguem o encerra. Sem esta limpeza o
    estado vaza para outros testes (lote 'em andamento' bloqueia /baixar)."""
    yield
    for estado in (FGTS_BATCH_STATE, RS_BATCH_STATE,
                   MUNICIPAL_BATCH_STATE, TRABALHISTA_BATCH_STATE):
        batch_engine.reset_batch_state(estado)


def _certidao_municipal(cidade):
    empresa = Empresa(nome=f'E {cidade}', cnpj=f'{abs(hash(cidade)) % 10**14:014d}',
                      cidade=cidade, estado='RS')
    db.session.add(empresa)
    db.session.commit()
    certidao = Certidao(empresa_id=empresa.id, tipo=TipoCertidao.MUNICIPAL)
    db.session.add(certidao)
    db.session.commit()
    return certidao


def _capturar_kwargs(monkeypatch):
    capturado = {}
    monkeypatch.setattr(batch_engine, 'run_batch_loop',
                        lambda app, **kw: capturado.update(kw))
    monkeypatch.setattr(lotes.batch_engine, 'run_batch_loop',
                        lambda app, **kw: capturado.update(kw))
    return capturado


# --- alvo por fluxo --------------------------------------------------------

def test_worker_fgts_usa_alvo_fixo(ctx, monkeypatch):
    kw = _capturar_kwargs(monkeypatch)
    lotes._fgts_batch_worker(ctx)
    assert kw['alvo_lote'] == 'FGTS'
    assert kw.get('alvo_fn') is None
    assert kw['on_breaker_aberto'] is lotes._alertar_breaker_aberto


def test_worker_rs_usa_alvo_fixo(ctx, monkeypatch):
    kw = _capturar_kwargs(monkeypatch)
    lotes._rs_batch_worker(ctx)
    assert kw['alvo_lote'] == 'Estadual RS'


def test_worker_trabalhista_usa_alvo_fixo(ctx, monkeypatch):
    kw = _capturar_kwargs(monkeypatch)
    lotes._trabalhista_batch_worker(ctx)
    assert kw['alvo_lote'] == 'Trabalhista'


def test_worker_municipal_usa_alvo_por_cidade(ctx, monkeypatch):
    kw = _capturar_kwargs(monkeypatch)
    lotes._municipal_batch_worker(ctx)
    assert kw['alvo_fn'] is lotes._alvo_breaker_municipal
    assert kw.get('alvo_lote') is None


def test_fluxo_agendador_municipal_usa_alvo_por_cidade(ctx, monkeypatch):
    """O lote municipal do agendador cobre varias cidades — e onde o alvo por
    municipio importa de verdade."""
    kw = _capturar_kwargs(monkeypatch)
    monkeypatch.setattr(lotes, 'emissao_individual_ativa', lambda: False)
    lotes._fluxo_municipal_rodar(ctx, [1], wrap_emit=lambda emit: emit,
                                 execution_id='e1')
    assert kw['alvo_fn'] is lotes._alvo_breaker_municipal


def test_fluxo_agendador_fgts_usa_alvo_fixo(ctx, monkeypatch):
    kw = _capturar_kwargs(monkeypatch)
    monkeypatch.setattr(lotes, 'emissao_individual_ativa', lambda: False)
    lotes._fluxo_fgts_rodar(ctx, [1], wrap_emit=lambda emit: emit, execution_id='e1')
    assert kw['alvo_lote'] == 'FGTS'


# --- resolucao do alvo municipal -------------------------------------------

def test_alvo_municipal_e_a_cidade_canonica(ctx):
    certidao = _certidao_municipal('Imbé')
    assert lotes._alvo_breaker_municipal(certidao.id) == 'IMBE'


def test_cidades_diferentes_dao_alvos_diferentes(ctx):
    imbe = _certidao_municipal('Imbé')
    tramandai = _certidao_municipal('Tramandaí')
    assert lotes._alvo_breaker_municipal(imbe.id) != \
        lotes._alvo_breaker_municipal(tramandai.id)


def test_alvo_municipal_sem_cidade_cai_no_generico(ctx):
    certidao = _certidao_municipal('')
    assert lotes._alvo_breaker_municipal(certidao.id) == 'Municipal'


def test_alvo_municipal_de_certidao_inexistente_nao_levanta(ctx):
    assert lotes._alvo_breaker_municipal(999999) == 'Municipal'


# --- efeito no lote municipal ----------------------------------------------

def test_municipio_aberto_nao_derruba_os_outros(ctx, monkeypatch):
    """RESOP-02.4: com Imbe aberto, o lote segue emitindo em Tramandai."""
    imbe = _certidao_municipal('Imbé')
    tramandai = _certidao_municipal('Tramandaí')
    circuit_breaker.limpar()
    for _ in range(3):
        circuit_breaker.registrar_falha('IMBE', 'portal fora')
    assert circuit_breaker.aberto('IMBE') is True

    emitidos = []
    state = batch_engine.batch_state_defaults()
    state.update(status='running', ids=[imbe.id, tramandai.id], total=2)

    class _Lock:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    batch_engine.run_batch_loop(
        ctx, lock=_Lock(), state=state,
        emit_fn=lambda cid, drv, eid: (emitidos.append(cid), (True, False, None))[1],
        nome_lote='Municipal', curto='Municipal', tag=None,
        event_prefix='municipal_batch_worker',
        alvo_fn=lotes._alvo_breaker_municipal)

    assert emitidos == [tramandai.id]
    assert state['status'] == 'completed'
    assert state['success'] == 1


def test_alerta_e_disparado_quando_o_breaker_abre(ctx, monkeypatch):
    """RESOP-02.7: abrir dispara o push por e-mail, uma vez."""
    from app.services import notificacoes
    chamadas = []
    monkeypatch.setattr(notificacoes, 'alertar_portal_fora',
                        lambda app, alvo, motivo=None: chamadas.append((alvo, motivo)))

    lotes._alertar_breaker_aberto('FGTS', 'timeout')

    assert chamadas == [('FGTS', 'timeout')]


def test_lote_dispara_o_alerta_uma_vez_ao_abrir(ctx):
    """Ponta a ponta no motor: 3 falhas seguidas abrem e avisam UMA vez, mesmo
    com mais itens falhando depois."""
    circuit_breaker.limpar()
    avisos = []
    state = batch_engine.batch_state_defaults()
    state.update(status='running', ids=[1, 2, 3, 4], total=4)

    class _Lock:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    batch_engine.run_batch_loop(
        ctx, lock=_Lock(), state=state,
        emit_fn=lambda cid, drv, eid: (False, False, 'portal fora'),
        nome_lote='FGTS', curto='FGTS', tag='FGTS-LOTE',
        event_prefix='fgts_batch_worker',
        alvo_lote='FGTS',
        on_breaker_aberto=lambda alvo, msg: avisos.append((alvo, msg)))

    assert avisos == [('FGTS', 'portal fora')]
    # abriu na 3a falha -> o 4o item nem foi emitido: o lote pausou
    assert state['status'] == 'paused'
    assert state['index'] == 3


def test_falha_do_alerta_nao_propaga(ctx, monkeypatch):
    from app.services import notificacoes

    def _explode(app, alvo, motivo=None):
        raise RuntimeError('smtp fora')

    monkeypatch.setattr(notificacoes, 'alertar_portal_fora', _explode)
    lotes._alertar_breaker_aberto('FGTS', 'timeout')  # nao levanta
