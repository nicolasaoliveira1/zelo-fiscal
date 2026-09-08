"""Regressões de inicialização segura da NFS-e (REV-06)."""
from unittest.mock import patch

import app as modulo_app
from app.services import agendador


def test_comando_administrativo_nao_reconcilia_nfse(monkeypatch):
    monkeypatch.setenv('FLASK_RUN_FROM_CLI', 'true')
    monkeypatch.setattr(agendador, 'sys', type('Sys', (), {
        'argv': ['flask', 'db', 'upgrade'],
    })())

    assert modulo_app._deve_reconciliar_nfse_orfas(object()) is False


def test_pai_do_reloader_nao_reconcilia_nfse(monkeypatch):
    monkeypatch.delenv('FLASK_RUN_FROM_CLI', raising=False)
    monkeypatch.delenv('WERKZEUG_RUN_MAIN', raising=False)

    class ConfigDebug:
        debug = True

    assert modulo_app._deve_reconciliar_nfse_orfas(ConfigDebug()) is False


def test_processo_servidor_reconcilia_nfse_uma_vez(monkeypatch):
    monkeypatch.delenv('FLASK_RUN_FROM_CLI', raising=False)
    monkeypatch.setattr(agendador, 'comando_cli_sem_servidor', lambda: False)
    monkeypatch.setattr(agendador, 'deve_adiar_para_reloader', lambda _app: False)

    with patch.object(modulo_app, '_reconciliar_nfse_orfas') as reconciliar:
        modulo_app.create_app()

    reconciliar.assert_called_once_with()
