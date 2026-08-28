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


class _LockFake:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


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
    monkeypatch.setattr(lotes, 'automacao_em_curso', lambda: None)
    lotes._fluxo_municipal_rodar(ctx, [1], wrap_emit=lambda emit: emit,
                                 execution_id='e1')
    assert kw['alvo_fn'] is lotes._alvo_breaker_municipal


def test_fluxo_agendador_fgts_usa_alvo_fixo(ctx, monkeypatch):
    kw = _capturar_kwargs(monkeypatch)
    monkeypatch.setattr(lotes, 'automacao_em_curso', lambda: None)
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
                        lambda app, alvo, motivo=None, causa='portal':
                        chamadas.append((alvo, motivo)))

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


def test_item_recusado_pelo_breaker_mantem_a_tarefa_pendente(ctx):
    """RESOP-02.6 ponta a ponta: com o breaker aberto o item nem chega ao emit,
    entao a TarefaEmissao continua `pendente` e NAO consome tentativa — portal
    fora nao pode gastar o orcamento de retry do item."""
    from app.models import TarefaEmissao
    from app.services import agendador, fila_emissao

    certidao = _certidao_municipal('Imbé')
    tarefa = fila_emissao.enfileirar(certidao.id, TipoCertidao.MUNICIPAL)
    assert tarefa.status == 'pendente'

    circuit_breaker.limpar()
    for _ in range(3):
        circuit_breaker.registrar_falha('IMBE', 'portal fora')

    emitidos = []
    state = batch_engine.batch_state_defaults()
    state.update(status='running', ids=[certidao.id], total=1)

    class _Lock:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    # mesmo empacotamento do agendador: o wrapper e quem transiciona a tarefa
    wrap = agendador._wrap_emit('exec-breaker')
    emit_fn = wrap(lambda cid, drv, eid: (emitidos.append(cid), (True, False, None))[1])

    batch_engine.run_batch_loop(
        ctx, lock=_Lock(), state=state, emit_fn=emit_fn,
        nome_lote='Municipal', curto='Municipal', tag=None,
        event_prefix='municipal_batch_worker',
        alvo_fn=lotes._alvo_breaker_municipal)

    assert emitidos == []
    # O loop roda no proprio app_context (outra sessao). Sem expirar, este get
    # devolveria o objeto do cache de identidade e a assercao passaria mesmo se o
    # loop tivesse consumido a tentativa — teste cego, nao teste.
    db.session.expire_all()
    tarefa_atual = db.session.get(TarefaEmissao, tarefa.id)
    assert tarefa_atual.status == 'pendente'
    assert tarefa_atual.tentativas == 0
    assert tarefa_atual.iniciada_em is None


# --- a pausa do breaker nao pode virar armadilha (code-review) --------------

def test_pausa_do_breaker_registra_o_alvo(ctx):
    """Sem saber QUEM pausou, ninguem consegue soltar depois."""
    circuit_breaker.limpar()
    for _ in range(3):
        circuit_breaker.registrar_falha('FGTS', 'portal fora')

    state = batch_engine.batch_state_defaults()
    state.update(status='running', ids=[1], total=1)
    batch_engine.run_batch_loop(
        ctx, lock=_LockFake(), state=state,
        emit_fn=lambda cid, drv, eid: (True, False, None),
        nome_lote='FGTS', curto='FGTS', tag='FGTS-LOTE',
        event_prefix='fgts_batch_worker', alvo_lote='FGTS')

    assert state['status'] == 'paused'
    assert state['pausado_por_breaker'] == 'FGTS'


def test_pausa_vencida_libera_o_tipo_sem_destruir_o_lote(ctx, monkeypatch):
    """O breaker fecha sozinho em 60 min; o tipo tem de voltar a aceitar lote e
    emissao individual. Mas o lote pausado NAO pode ser apagado no caminho: os
    itens restantes e o "Retomar" do operador continuam la."""
    from datetime import datetime, timedelta
    agora = datetime(2026, 8, 11, 3, 0, 0)
    monkeypatch.setattr(circuit_breaker, '_agora', lambda: agora)
    circuit_breaker.limpar()
    for _ in range(3):
        circuit_breaker.registrar_falha('FGTS', 'portal fora')

    state = batch_engine.batch_state_defaults()
    state.update(status='paused', pausado_por_breaker='FGTS',
                 ids=[1, 2, 3], index=1, total=3)

    # dentro da janela: o portal segue fora, o tipo segue ocupado
    assert batch_engine.lote_ocupa_o_tipo(state) is True

    # janela vencida: o tipo libera...
    monkeypatch.setattr(circuit_breaker, '_agora', lambda: agora + timedelta(minutes=61))
    assert batch_engine.lote_ocupa_o_tipo(state) is False
    # ...e o lote continua intacto (nada de reset por baixo)
    assert state['status'] == 'paused'
    assert state['ids'] == [1, 2, 3]
    assert state['index'] == 1


def test_pausa_manual_ocupa_o_tipo_para_sempre(ctx):
    """Pausa pedida pelo operador nao vence: quem solta e ele."""
    circuit_breaker.limpar()
    state = batch_engine.batch_state_defaults()
    state.update(status='paused', ids=[1], total=1)

    assert batch_engine.lote_ocupa_o_tipo(state) is True


def test_lote_rodando_ocupa_o_tipo(ctx):
    circuit_breaker.limpar()
    state = batch_engine.batch_state_defaults()
    state.update(status='running', pausado_por_breaker='FGTS', ids=[1], total=1)

    assert batch_engine.lote_ocupa_o_tipo(state) is True


def test_lote_idle_nao_ocupa_o_tipo(ctx):
    assert batch_engine.lote_ocupa_o_tipo(batch_engine.batch_state_defaults()) is False


def test_agendador_roda_o_ciclo_seguinte_apos_pausa_vencida(ctx, monkeypatch):
    """O caminho que o achado descrevia: lote pausado pelo breaker numa noite
    nao pode fazer o agendador pular todas as noites seguintes."""
    from app.automation.batch_state import FGTS_BATCH_STATE, FGTS_BATCH_LOCK
    from datetime import datetime, timedelta
    agora = datetime(2026, 8, 11, 3, 0, 0)
    monkeypatch.setattr(circuit_breaker, '_agora', lambda: agora)
    circuit_breaker.limpar()
    for _ in range(3):
        circuit_breaker.registrar_falha('FGTS', 'portal fora')

    with FGTS_BATCH_LOCK:
        batch_engine.reset_batch_state(FGTS_BATCH_STATE)
        FGTS_BATCH_STATE.update(status='paused', pausado_por_breaker='FGTS')

    kw = _capturar_kwargs(monkeypatch)
    monkeypatch.setattr(lotes, 'automacao_em_curso', lambda: None)

    # noite seguinte, ja fora da janela do breaker
    monkeypatch.setattr(circuit_breaker, '_agora', lambda: agora + timedelta(hours=24))
    lotes._fluxo_fgts_rodar(ctx, [1], wrap_emit=lambda emit: emit, execution_id='e2')

    assert kw.get('alvo_lote') == 'FGTS', 'o ciclo seguinte deveria ter rodado'


def test_causa_do_solver_sobrevive_a_palavra_timeout(ctx):
    """A mensagem REAL do 2captcha traz "Read timed out", e no catalogo a regra
    de TIMEOUT vem antes da de CAPTCHA — pelo catalogo, a falha do solver viraria
    "portal fora" e o operador iria depurar o site em vez da conta do solver."""
    real = ('Falha ao resolver ALTCHA no 2captcha: HTTPSConnectionPool'
            "(host='2captcha.com', port=443): Read timed out. (read timeout=120)")

    assert lotes._causa_do_breaker(real) == 'captcha'


def test_timeout_do_portal_continua_sendo_portal(ctx):
    assert lotes._causa_do_breaker(
        'Timeout aguardando download da certidao Estadual RS.') == 'portal'


def test_causa_do_alerta_separa_captcha_de_portal(ctx, monkeypatch):
    """O lote classifica a causa antes de avisar (RESOP-02.7)."""
    from app.services import notificacoes
    chamadas = []
    monkeypatch.setattr(notificacoes, 'alertar_portal_fora',
                        lambda app, alvo, motivo=None, causa='portal':
                        chamadas.append((alvo, causa)))

    lotes._alertar_breaker_aberto('Estadual RS', 'Falha ao resolver o captcha.')
    lotes._alertar_breaker_aberto('FGTS', 'Erro ao carregar pagina FGTS.')

    assert chamadas == [('Estadual RS', 'captcha'), ('FGTS', 'portal')]


def test_pausa_manual_apaga_a_marca_do_breaker(ctx):
    """Cenario do code-review: breaker pausa -> operador retoma -> operador
    pausa. Se a marca do breaker sobrevivesse, a pausa do operador seria tratada
    como pausa de breaker e o tipo seria liberado pelas costas dele."""
    circuit_breaker.limpar()
    lock = _LockFake()
    state = batch_engine.batch_state_defaults()
    state.update(status='paused', pausado_por_breaker='FGTS',
                 ids=[1, 2, 3], index=1, total=3)

    batch_engine.resume_batch(lock, state, lambda app: None, lambda: ctx)
    assert state['pausado_por_breaker'] is None

    batch_engine.request_pause(lock, state)
    assert state['pausado_por_breaker'] is None

    # a pausa agora e manual: segue ocupando o tipo, com o progresso intacto
    assert batch_engine.lote_ocupa_o_tipo(state) is True
    assert state['status'] == 'paused'
    assert state['ids'] == [1, 2, 3]
    assert state['index'] == 1


def test_pausa_manual_sobre_pausa_do_breaker_troca_o_motivo(ctx):
    """Pausar um lote que o BREAKER ja pausou nao e pedido redundante.

    Recusar por "nao esta rodando" deixava a marca do breaker no estado, e a
    pausa do breaker se solta sozinha (`pausa_de_breaker_vencida`): vencida a
    janela, o tipo era liberado e o proximo lote resetava, em silencio, a fila
    que o operador tinha mandado parar. Pausa manual nao se solta sozinha.
    """
    circuit_breaker.limpar()
    state = batch_engine.batch_state_defaults()
    state.update(status='paused', pausado_por_breaker='FGTS',
                 ids=[1, 2, 3], index=1, total=3)

    assert batch_engine.solicitar_pausa_se_rodando(_LockFake(), state) is True

    assert state['pausado_por_breaker'] is None
    assert state['status'] == 'paused'
    assert batch_engine.lote_ocupa_o_tipo(state) is True
    assert state['index'] == 1


def test_pausa_recusada_em_lote_que_nao_esta_em_curso(ctx):
    """A porta segue fechada para o que nao ha o que pausar — a abertura e so
    para a pausa do breaker, nao para qualquer status."""
    circuit_breaker.limpar()
    for status in ('completed', 'stopped', 'idle'):
        state = batch_engine.batch_state_defaults()
        state.update(status=status, ids=[1], total=1)
        assert batch_engine.solicitar_pausa_se_rodando(
            _LockFake(), state
        ) is False
        assert state['status'] == status


def test_parar_tambem_apaga_a_marca_do_breaker(ctx):
    circuit_breaker.limpar()
    state = batch_engine.batch_state_defaults()
    state.update(status='paused', pausado_por_breaker='FGTS', ids=[1], total=1)

    batch_engine.request_stop(_LockFake(), state)

    assert state['pausado_por_breaker'] is None


def test_pausar_sozinho_ja_apaga_a_marca_do_breaker(ctx):
    """Isola o contrato de `request_pause`: uma pausa manual e MANUAL, mesmo que
    o lote estivesse marcado pelo breaker. (O teste acima passa pelo resume
    antes, entao nao provaria esta linha sozinha.)"""
    circuit_breaker.limpar()
    state = batch_engine.batch_state_defaults()
    state.update(status='running', pausado_por_breaker='FGTS',
                 ids=[1, 2, 3], index=1, total=3)

    batch_engine.request_pause(_LockFake(), state)

    assert state['status'] == 'paused'
    assert state['pausado_por_breaker'] is None
    assert batch_engine.lote_ocupa_o_tipo(state) is True
    assert state['index'] == 1
