"""Testes do wiring do agendador no boot (spec 02, SCHED-02/04)."""
import pytest

from app.services import agendador


@pytest.fixture(autouse=True)
def _limpa_scheduler():
    yield
    agendador.shutdown()


def test_create_app_chama_agendador_init(monkeypatch):
    chamado = {}
    monkeypatch.setattr(agendador, 'init', lambda app: chamado.setdefault('sim', True))
    from app import create_app
    create_app()
    assert chamado.get('sim') is True


def test_init_nao_inicia_quando_flag_desligada(app, monkeypatch):
    monkeypatch.setitem(app.config, 'AGENDADOR_ENABLED', False)
    monkeypatch.setenv('WERKZEUG_RUN_MAIN', 'true')
    agendador.shutdown()
    assert agendador.init(app) is None
    assert agendador._scheduler is None


def test_init_reloader_pai_nao_inicia(app, monkeypatch):
    # reloader ATIVO (debug on) + WERKZEUG_RUN_MAIN ausente = processo pai do
    # reloader: nao deve agendar (o filho o fara). Forca DEBUG no config para nao
    # depender do ambiente (o .env local tem FLASK_DEBUG=1; o CI nao tem .env).
    monkeypatch.delenv('WERKZEUG_RUN_MAIN', raising=False)
    monkeypatch.setitem(app.config, 'DEBUG', True)
    monkeypatch.setitem(app.config, 'AGENDADOR_ENABLED', True)
    agendador.shutdown()
    assert app.debug is True
    assert agendador.init(app) is None
    assert agendador._scheduler is None


def test_init_flask_cli_debug_sem_reload_inicia(app, monkeypatch):
    """Regressão: `flask run --debug --no-reload` é o próprio servidor.

    Não existe filho com WERKZEUG_RUN_MAIN nesse modo; adiar o scheduler deixa a
    aplicação atendendo normalmente, mas nenhum job roda na madrugada.
    """
    monkeypatch.delenv('WERKZEUG_RUN_MAIN', raising=False)
    monkeypatch.setenv('FLASK_RUN_FROM_CLI', 'true')
    monkeypatch.setattr(agendador.sys, 'argv', [
        'flask', '--app', 'run.py', 'run', '--debug', '--no-reload',
    ])
    monkeypatch.setitem(app.config, 'DEBUG', True)
    monkeypatch.setitem(app.config, 'AGENDADOR_ENABLED', True)
    agendador.shutdown()

    sched = agendador.init(app)

    assert sched is not None
    assert sched.running
    assert sched.get_job(agendador._JOB_RENOVACAO) is not None


def test_create_app_recusa_servir_quando_agendador_falha(monkeypatch):
    monkeypatch.setenv('FLASK_RUN_FROM_CLI', 'true')
    monkeypatch.delenv('WERKZEUG_RUN_MAIN', raising=False)
    monkeypatch.setattr(agendador.sys, 'argv', [
        'flask', '--app', 'run.py', 'run', '--debug', '--no-reload',
    ])
    monkeypatch.setattr(agendador, 'init',
                        lambda _app: (_ for _ in ()).throw(RuntimeError('falhou')))

    from app import create_app
    from config import Config

    class ConfigAgendadorLigado(Config):
        AGENDADOR_ENABLED = True

    with pytest.raises(RuntimeError, match='falhou'):
        create_app(ConfigAgendadorLigado)


def test_init_ignora_comando_de_migration(app, monkeypatch):
    monkeypatch.setenv('FLASK_RUN_FROM_CLI', 'true')
    monkeypatch.setattr(agendador.sys, 'argv', [
        'flask', '--app', 'run.py', 'db', 'upgrade', 'head',
    ])
    monkeypatch.setitem(app.config, 'AGENDADOR_ENABLED', True)
    agendador.shutdown()

    assert agendador.init(app) is None
    assert agendador._scheduler is None


def test_init_liga_no_processo_que_serve(app, ids, monkeypatch):
    monkeypatch.setitem(app.config, 'AGENDADOR_ENABLED', True)
    monkeypatch.setenv('WERKZEUG_RUN_MAIN', 'true')
    agendador.shutdown()
    sched = agendador.init(app)
    assert sched is not None
    assert sched.running


# --- migration no boot: quem migra é quem atende -----------------------------

def test_pai_do_reloader_nao_migra(app, monkeypatch):
    """Pai e filho rodam `create_app` inteiro; migrar duas vezes coloca os dois
    disputando a tabela de versão no boot."""
    import app as pacote_app

    monkeypatch.setitem(app.config, 'DEBUG', True)
    monkeypatch.setattr(app, 'debug', True, raising=False)
    monkeypatch.delenv('WERKZEUG_RUN_MAIN', raising=False)

    assert pacote_app._e_pai_do_reloader(app) is True


def test_processo_que_serve_migra(app, monkeypatch):
    import app as pacote_app

    monkeypatch.setattr(app, 'debug', True, raising=False)
    monkeypatch.setenv('WERKZEUG_RUN_MAIN', 'true')

    assert pacote_app._e_pai_do_reloader(app) is False


def test_sem_debug_migra(app, monkeypatch):
    import app as pacote_app

    monkeypatch.setattr(app, 'debug', False, raising=False)
    monkeypatch.delenv('WERKZEUG_RUN_MAIN', raising=False)

    assert pacote_app._e_pai_do_reloader(app) is False


def test_guard_que_falha_nao_impede_a_migration(app, monkeypatch):
    """Na dúvida, migra: schema velho com código novo é pior que migrar 2x."""
    import app as pacote_app

    monkeypatch.setattr(
        agendador, 'deve_adiar_para_reloader',
        lambda _app: (_ for _ in ()).throw(RuntimeError('guard quebrado')))

    assert pacote_app._e_pai_do_reloader(app) is False


def test_upgrade_registra_inicio_e_duracao(app, monkeypatch):
    """Sem o evento de INÍCIO, um banco que não responde trava o boot em
    silêncio — o log não dizia nem que ele havia chegado ali."""
    import app as pacote_app

    eventos = []
    monkeypatch.setattr(
        pacote_app, 'log_event',
        lambda evento, **campos: eventos.append((evento, campos)))
    monkeypatch.setattr(
        'flask_migrate.upgrade', lambda *a, **k: None)

    pacote_app._aplicar_migrations_pendentes()

    assert [e for e, _ in eventos] == [
        'startup_db_upgrade_inicio', 'startup_db_upgrade_ok']
    assert 'duration_ms' in eventos[1][1]
