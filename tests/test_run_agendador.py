"""Integração do entrypoint com o scheduler no modo usado pelo painel."""

import run
from app.services import agendador


def test_pai_do_reloader_nao_confirma_servicos(monkeypatch):
    monkeypatch.delenv('WERKZEUG_RUN_MAIN', raising=False)
    chamado = []
    monkeypatch.setattr(agendador, 'garantir_iniciado_no_processo_servidor',
                        lambda _app: chamado.append(True))

    assert run._garantir_servicos_recorrentes(debug=True) is None
    assert chamado == []


def test_filho_do_reloader_confirma_servicos(monkeypatch):
    monkeypatch.setenv('WERKZEUG_RUN_MAIN', 'true')
    sentinel = object()
    monkeypatch.setattr(agendador, 'garantir_iniciado_no_processo_servidor',
                        lambda app: sentinel if app is run.app else None)

    assert run._garantir_servicos_recorrentes(debug=True) is sentinel


def test_servidor_sem_debug_tambem_confirma_servicos(monkeypatch):
    monkeypatch.delenv('WERKZEUG_RUN_MAIN', raising=False)
    sentinel = object()
    monkeypatch.setattr(agendador, 'garantir_iniciado_no_processo_servidor',
                        lambda app: sentinel if app is run.app else None)

    assert run._garantir_servicos_recorrentes(debug=False) is sentinel
