"""Testes do agendador core (spec 02, SCHED-02/04/07): lifecycle, reconciliação,
reprogramação e snapshot job."""
from datetime import date, datetime

import pytest

from app import db
from app.models import Certidao, ConfiguracaoSistema, SnapshotCertidao, TarefaEmissao
from app.services import agendador, snapshot_service


@pytest.fixture()
def sched_limpo(app, monkeypatch):
    # simula o processo que serve (filho do reloader): sem isto, o guard anti
    # duplo-start do reloader barra o init quando app.debug=True (.env FLASK_DEBUG=1)
    monkeypatch.setenv('WERKZEUG_RUN_MAIN', 'true')
    # religa o agendador (conftest desliga por padrao nos testes)
    monkeypatch.setitem(app.config, 'AGENDADOR_ENABLED', True)
    agendador._fluxos.clear()
    yield
    agendador.shutdown()
    agendador._fluxos.clear()


def _config(app, **kwargs):
    with app.app_context():
        cfg = ConfiguracaoSistema(**kwargs)
        db.session.add(cfg)
        db.session.commit()


def test_init_inicia_e_agenda_snapshot(app, ids, sched_limpo):
    sched = agendador.init(app)
    assert sched is not None
    assert sched.running
    assert sched.get_job(agendador._JOB_SNAPSHOT) is not None


def test_processo_servidor_forca_init_mesmo_sem_marcador_do_reloader(
        app, ids, sched_limpo, monkeypatch):
    """Regressão: o painel ficou servindo sem scheduler durante a madrugada.

    O guard do factory pode adiar o init por enxergar o processo pai. Quando o
    entrypoint confirma que este é o processo servidor, a inicialização não pode
    continuar dependendo do marcador interno do Werkzeug.
    """
    monkeypatch.delenv('WERKZEUG_RUN_MAIN', raising=False)
    monkeypatch.setitem(app.config, 'DEBUG', True)

    assert agendador.init(app) is None
    sched = agendador.garantir_iniciado_no_processo_servidor(app)

    assert sched.running
    assert {job.id for job in sched.get_jobs()} == {
        agendador._JOB_SNAPSHOT,
        agendador._JOB_RENOVACAO,
        agendador._JOB_VERIF_MUNICIPIOS,
        agendador._JOB_RECHECK_RECEITA,
        agendador._JOB_INVENTARIO_COFRE,
        agendador._JOB_RESUMO_DIARIO,
    }


def test_processo_servidor_recusa_scheduler_sem_job_obrigatorio(
        app, ids, sched_limpo, monkeypatch):
    monkeypatch.setattr(agendador, '_agendar_jobs', lambda _app: None)

    with pytest.raises(RuntimeError, match='Jobs obrigatórios ausentes'):
        agendador.garantir_iniciado_no_processo_servidor(app)


def test_init_idempotente_nao_duplica(app, ids, sched_limpo):
    s1 = agendador.init(app)
    s2 = agendador.init(app)
    assert s1 is s2
    # snapshot + renovacao (ativa por padrao) + verificacao de municipios (COV-05)
    # + recheck da situacao cadastral na Receita (spec 08) + inventario do cofre
    # + resumo do dia (AD-029). A invariante e "nao duplica": um job por id.
    ids_jobs = [j.id for j in s1.get_jobs()]
    assert sorted(ids_jobs) == sorted(set(ids_jobs))
    assert set(ids_jobs) == {
        agendador._JOB_SNAPSHOT,
        agendador._JOB_RENOVACAO,
        agendador._JOB_VERIF_MUNICIPIOS,
        agendador._JOB_RECHECK_RECEITA,
        agendador._JOB_INVENTARIO_COFRE,
        agendador._JOB_RESUMO_DIARIO,
    }


def test_init_reconcilia_orfas(app, ids, sched_limpo):
    with app.app_context():
        cert = db.session.get(Certidao, ids['fgts'])
        t = TarefaEmissao(tipo='FGTS', empresa_id=cert.empresa_id,
                          certidao_id=cert.id, status='rodando',
                          iniciada_em=datetime.now())
        db.session.add(t)
        db.session.commit()
        tid = t.id

    agendador.init(app)

    with app.app_context():
        assert db.session.get(TarefaEmissao, tid).status == 'pendente'


def test_reprogramar_muda_hora_sem_recriar(app, ids, sched_limpo):
    agendador.init(app)  # sem linha de config -> hora default 3
    job = agendador._scheduler.get_job(agendador._JOB_RENOVACAO)
    assert "hour='3'" in str(job.trigger)

    _config(app, agendador_hora=9, agendador_ativo=True)
    agendador.reprogramar(app)
    job = agendador._scheduler.get_job(agendador._JOB_RENOVACAO)
    assert "hour='9'" in str(job.trigger)


def test_ativo_false_nao_agenda_renovacao(app, ids, sched_limpo):
    _config(app, agendador_hora=3, agendador_ativo=False)
    agendador.init(app)
    # renovacao desligada, snapshot (sem custo) segue agendado
    assert agendador._scheduler.get_job(agendador._JOB_RENOVACAO) is None
    assert agendador._scheduler.get_job(agendador._JOB_SNAPSHOT) is not None


def test_reprogramar_ativo_false_remove_renovacao(app, ids, sched_limpo):
    _config(app, agendador_hora=3, agendador_ativo=True)
    agendador.init(app)
    assert agendador._scheduler.get_job(agendador._JOB_RENOVACAO) is not None

    with app.app_context():
        cfg = db.session.get(ConfiguracaoSistema, 1)
        cfg.agendador_ativo = False
        db.session.commit()
    agendador.reprogramar(app)
    assert agendador._scheduler.get_job(agendador._JOB_RENOVACAO) is None


def test_job_snapshot_diario_gera_snapshot(app, ids, sched_limpo):
    snapshot_service._ULTIMO_SNAPSHOT_DIA = None
    agendador.job_snapshot_diario(app)
    with app.app_context():
        assert SnapshotCertidao.query.filter_by(data=date.today()).count() > 0
    snapshot_service._ULTIMO_SNAPSHOT_DIA = None


# --- o relogio do agendador, legivel de fora (VGC-10/17) --------------------

def test_janela_usa_a_passagem_de_hoje_quando_a_hora_ja_passou(app, ids):
    _config(app, agendador_hora=3)
    with app.app_context():
        inicio, _corte = agendador.janela_ultima_passagem(
            agora=datetime(2026, 8, 22, 9, 30))

        assert inicio == datetime(2026, 8, 22, 3, 0)


def test_janela_recua_um_dia_quando_a_hora_ainda_nao_chegou(app, ids):
    _config(app, agendador_hora=3)
    with app.app_context():
        inicio, _corte = agendador.janela_ultima_passagem(
            agora=datetime(2026, 8, 22, 1, 15))

        assert inicio == datetime(2026, 8, 21, 3, 0)


def test_janela_sem_linha_de_config_usa_o_default_sem_levantar(app, ids):
    with app.app_context():
        inicio, _corte = agendador.janela_ultima_passagem(
            agora=datetime(2026, 8, 22, 9, 0))

        assert inicio.hour == 3  # default de `_ler_config`


def test_corte_converte_por_duracao_e_nao_pelo_relogio_local(app, ids, monkeypatch):
    """O achado local x UTC, em forma de teste — com o offset INJETADO.

    O job e agendado por `CronTrigger(hour=hora)`, hora LOCAL (AD-004/AD-009); o
    `ExecucaoLote.iniciado_em` tem `default=utcnow_naive`, UTC naive. Comparar a
    hora local direto com a coluna erra pelo offset, e erra no caso NORMAL — o
    lote comeca exatamente na hora do corte.

    O offset e simulado (maquina em UTC-3: o relogio UTC esta 3h a frente do
    local) em vez de herdado do ambiente, senao o teste nao discriminaria nada no
    CI, que roda em UTC. Implementacao ingenua devolveria `corte == 03:00`.
    """
    _config(app, agendador_hora=3)
    monkeypatch.setattr(agendador, 'utcnow_naive',
                        lambda: datetime(2026, 8, 22, 12, 0))
    with app.app_context():
        inicio, corte = agendador.janela_ultima_passagem(
            agora=datetime(2026, 8, 22, 9, 0))

        assert inicio == datetime(2026, 8, 22, 3, 0)   # o que a tela exibe
        assert corte == datetime(2026, 8, 22, 6, 0)    # 12:00 UTC - 6h decorridas


def test_corte_separa_o_lote_de_dentro_do_de_fora_da_passagem(app, ids, monkeypatch):
    """O que o corte significa para quem consulta: 03:01 local entra, 02:59 nao."""
    _config(app, agendador_hora=3)
    monkeypatch.setattr(agendador, 'utcnow_naive',
                        lambda: datetime(2026, 8, 22, 12, 0))
    with app.app_context():
        _inicio, corte = agendador.janela_ultima_passagem(
            agora=datetime(2026, 8, 22, 9, 0))

        # carimbos como o modelo carimba (UTC naive), em UTC-3
        assert datetime(2026, 8, 22, 6, 1) >= corte    # rodou 03:01 local
        assert datetime(2026, 8, 22, 5, 59) < corte    # rodou 02:59 local


def test_proxima_execucao_e_hoje_quando_a_hora_ainda_nao_chegou(app, ids):
    _config(app, agendador_hora=20)
    with app.app_context():
        assert agendador.proxima_execucao(
            agora=datetime(2026, 8, 22, 9, 0)) == datetime(2026, 8, 22, 20, 0)


def test_proxima_execucao_e_amanha_quando_a_hora_ja_passou(app, ids):
    _config(app, agendador_hora=3)
    with app.app_context():
        assert agendador.proxima_execucao(
            agora=datetime(2026, 8, 22, 9, 0)) == datetime(2026, 8, 23, 3, 0)


def test_na_hora_exata_a_proxima_e_a_de_amanha(app, ids):
    """O disparo de agora ja aconteceu — "proxima" nao pode ser ele mesmo."""
    _config(app, agendador_hora=3)
    with app.app_context():
        assert agendador.proxima_execucao(
            agora=datetime(2026, 8, 22, 3, 0)) == datetime(2026, 8, 23, 3, 0)


def test_agendador_desligado_nao_tem_proxima_execucao(app, ids):
    """`None` nao e "nunca": e "nao ha proxima", que a tela mostra como
    desligado. Devolver uma data faria a pagina prometer uma execucao que o
    `_agendar_jobs` nem chega a registrar."""
    _config(app, agendador_hora=3, agendador_ativo=False)
    with app.app_context():
        assert agendador.proxima_execucao(agora=datetime(2026, 8, 22, 9, 0)) is None


def test_o_relogio_legivel_nao_consulta_o_scheduler(app, ids):
    """A prova de que a leitura vem da config: nenhuma das duas funcoes referencia
    `_scheduler`, que e None em teste e num processo recem-subido.

    Olha `co_names` — os globais que o bytecode realmente usa — e nao o texto do
    fonte: os docstrings CITAM `_scheduler` para explicar por que nao o usam, e
    uma busca textual reprovaria a explicacao junto com o erro.
    """
    for funcao in (agendador.proxima_execucao, agendador.janela_ultima_passagem):
        usados = funcao.__code__.co_names
        assert '_scheduler' not in usados
        assert '_ler_config' in usados
