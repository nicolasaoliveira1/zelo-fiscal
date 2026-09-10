"""Recon passivo agendado dos contratos (RAC-07), sem portal real nem driver.

O que precisa ficar provado: o job é obrigatório e independe da renovação, alvo
ocupado adia em vez de quebrar, falha de um alvo não derruba os demais e o
painel passa a distinguir "responde" de "pronto para automatizar".
"""
from threading import Lock
from unittest.mock import MagicMock

import pytest

from app import db
from app.automation import trabalhista
from app.models import IncidenteContratoPortal, Usuario
from app.services import (
    agendador,
    circuit_breaker,
    contrato_portal,
    contrato_portal_preflight,
    contrato_portal_recon,
    portal_health,
)


@pytest.fixture(autouse=True)
def _limpo():
    portal_health.limpar_cache()
    circuit_breaker.limpar()
    yield
    portal_health.limpar_cache()
    circuit_breaker.limpar()


def _ativar_baseline():
    usuario = Usuario(
        username='admin_recon', senha_hash='hash-sintetico', papel='admin')
    db.session.add(usuario)
    db.session.commit()
    return contrato_portal.criar_baseline(
        fluxo=trabalhista.FLUXO_CONTRATO,
        alvo=trabalhista.ALVO_CONTRATO,
        definicao=trabalhista.definicao_baseline(),
        usuario_id=usuario.id,
    )


def _adaptador(**kwargs):
    base = {
        'fluxo': trabalhista.FLUXO_CONTRATO,
        'alvo': trabalhista.ALVO_CONTRATO,
        'nome': 'Trabalhista (CNDT/TST)',
        'chave_health': circuit_breaker.ALVO_TRABALHISTA,
        'observar': MagicMock(),
        'lock': Lock(),
        'recon_passivo_seguro': True,
    }
    base.update(kwargs)
    return contrato_portal_recon.AdaptadorRecon(**base)


# --- registry e agendamento -------------------------------------------------

def test_registry_do_piloto_so_tem_o_trabalhista_seguro(app):
    with app.app_context():
        adaptadores = contrato_portal_recon.adaptadores_padrao()

    assert [a.alvo for a in adaptadores] == [trabalhista.ALVO_CONTRATO]
    assert all(a.recon_passivo_seguro for a in adaptadores)
    assert adaptadores[0].chave_health == circuit_breaker.ALVO_TRABALHISTA
    assert adaptadores[0].observar is trabalhista.observar_passivo


def test_recon_e_agendado_mesmo_com_renovacao_desligada(app, ids, monkeypatch):
    """AC-07.3: o job não emite nem gasta captcha, então não depende do ativo."""
    monkeypatch.setattr(agendador, '_ler_config', lambda: (3, False))
    scheduler = MagicMock()
    scheduler.get_job.return_value = None
    monkeypatch.setattr(agendador, '_scheduler', scheduler)

    agendador._agendar_jobs(app)

    jobs = {c.kwargs['id']: c for c in scheduler.add_job.call_args_list}
    assert agendador._JOB_RECON_PORTAIS in jobs
    assert agendador._JOB_RENOVACAO not in jobs
    chamada = jobs[agendador._JOB_RECON_PORTAIS]
    assert chamada.args[0] is agendador.job_recon_portais
    assert chamada.kwargs['max_instances'] == 1
    assert chamada.kwargs['coalesce'] is True


def test_janela_do_recon_nao_colide_com_os_jobs_longos(app, ids, monkeypatch):
    monkeypatch.setattr(agendador, '_ler_config', lambda: (3, True))
    scheduler = MagicMock()
    scheduler.get_job.return_value = None
    monkeypatch.setattr(agendador, '_scheduler', scheduler)

    agendador._agendar_jobs(app)

    janelas = {}
    for chamada in scheduler.add_job.call_args_list:
        trigger = chamada.args[1]
        campos = {f.name: str(f) for f in trigger.fields}
        janelas[chamada.kwargs['id']] = (campos['hour'], campos['minute'])

    assert len(set(janelas.values())) == len(janelas)


def test_job_sem_driver_registrado_nao_levanta(app, ids, monkeypatch):
    monkeypatch.setattr(agendador, '_criador_driver_recon', None)

    assert agendador.job_recon_portais(app) == {}


# --- execução do recon ------------------------------------------------------

def test_alvo_ocupado_por_lote_adia_sem_abrir_driver(app, ids):
    with app.app_context():
        _ativar_baseline()
        lock = Lock()
        lock.acquire()
        criar_driver = MagicMock()
        try:
            resultados = contrato_portal_recon.executar(
                [_adaptador(lock=lock)], criar_driver)
        finally:
            lock.release()

    assert resultados == {trabalhista.ALVO_CONTRATO: contrato_portal_recon.ADIADO}
    criar_driver.assert_not_called()


def test_sem_contrato_ativo_nao_observa_o_portal(app, ids):
    with app.app_context():
        criar_driver = MagicMock()
        resultados = contrato_portal_recon.executar([_adaptador()], criar_driver)

    assert resultados == {
        trabalhista.ALVO_CONTRATO: contrato_portal_recon.SEM_CONTRATO}
    criar_driver.assert_not_called()


def test_adaptador_sem_recon_seguro_fica_de_fora(app, ids):
    with app.app_context():
        criar_driver = MagicMock()
        resultados = contrato_portal_recon.executar(
            [_adaptador(recon_passivo_seguro=False)], criar_driver)

    assert resultados == {}
    criar_driver.assert_not_called()


def test_estrutura_compativel_fecha_o_driver_e_nao_toca_o_breaker(
    app, ids, monkeypatch,
):
    with app.app_context():
        ativo = _ativar_baseline()
        driver = MagicMock()
        snapshot = MagicMock(versao=ativo.versao)
        chamadas = []
        monkeypatch.setattr(
            contrato_portal_recon.contrato_portal_preflight, 'executar',
            lambda **kwargs: chamadas.append(kwargs) or snapshot)

        resultados = contrato_portal_recon.executar(
            [_adaptador()], lambda: driver)

        assert resultados == {
            trabalhista.ALVO_CONTRATO: contrato_portal_recon.COMPATIVEL}
        # O recon é antecipatório: quem abre o breaker é o preflight da emissão.
        assert chamadas[0]['alvo_breaker'] is None
        driver.quit.assert_called_once()
        assert circuit_breaker.abertos() == []


def test_autoativacao_e_reportada_pela_troca_de_versao(app, ids, monkeypatch):
    with app.app_context():
        ativo = _ativar_baseline()
        monkeypatch.setattr(
            contrato_portal_recon.contrato_portal_preflight, 'executar',
            lambda **kwargs: MagicMock(versao=ativo.versao + 1))

        resultados = contrato_portal_recon.executar(
            [_adaptador()], lambda: MagicMock())

    assert resultados == {
        trabalhista.ALVO_CONTRATO: contrato_portal_recon.AUTOAJUSTADO}


def test_bloqueio_estrutural_vira_bloqueado_e_libera_o_lock(app, ids, monkeypatch):
    with app.app_context():
        _ativar_baseline()
        lock = Lock()
        driver = MagicMock()
        monkeypatch.setattr(
            contrato_portal_recon.contrato_portal_preflight, 'executar',
            MagicMock(side_effect=contrato_portal_preflight
                      .ContratoPortalBloqueadoError('revisao')))

        resultados = contrato_portal_recon.executar(
            [_adaptador(lock=lock)], lambda: driver)

        assert resultados == {
            trabalhista.ALVO_CONTRATO: contrato_portal_recon.BLOQUEADO}
        driver.quit.assert_called_once()
        assert lock.acquire(blocking=False) is True
        lock.release()


def test_falha_de_infra_e_desconhecida_e_nao_vira_drift(app, ids):
    """Timeout no portal não diz nada sobre a estrutura: nada de incidente.

    Passa pelo preflight real de propósito — mockar o preflight provaria só o
    mock.
    """
    with app.app_context():
        _ativar_baseline()
        observar = MagicMock(side_effect=TimeoutError('o portal não respondeu'))

        resultados = contrato_portal_recon.executar(
            [_adaptador(observar=observar)], lambda: MagicMock())

        assert resultados == {
            trabalhista.ALVO_CONTRATO: contrato_portal_recon.DESCONHECIDO}
        assert IncidenteContratoPortal.query.count() == 0
        assert circuit_breaker.abertos() == []


def test_falha_de_um_alvo_nao_derruba_os_demais(app, ids, monkeypatch):
    with app.app_context():
        _ativar_baseline()
        quebrado = _adaptador(alvo='alvo-quebrado')
        saudavel = _adaptador()
        monkeypatch.setattr(
            contrato_portal_recon, '_recon_de_um',
            lambda ad, *a, **k: (
                (_ for _ in ()).throw(RuntimeError('driver morreu'))
                if ad.alvo == 'alvo-quebrado'
                else contrato_portal_recon.COMPATIVEL))

        resultados = contrato_portal_recon.executar(
            [quebrado, saudavel], lambda: MagicMock())

    assert resultados == {
        'alvo-quebrado': contrato_portal_recon.DESCONHECIDO,
        trabalhista.ALVO_CONTRATO: contrato_portal_recon.COMPATIVEL,
    }


# --- saúde composta ---------------------------------------------------------

def test_health_sem_contrato_ativo_fica_desconhecido_sem_reprovar(app, ids, monkeypatch):
    monkeypatch.setattr(portal_health.requests, 'get',
                        lambda url, **kwargs: MagicMock(status_code=200))
    with app.app_context():
        resultado = portal_health.snapshot({}, forcar=True)

    trab = next(p for p in resultado['portais']
                if p['chave'] == circuit_breaker.ALVO_TRABALHISTA)
    assert trab['contrato_estado'] == contrato_portal_recon.DESCONHECIDO
    assert trab['pronto_para_automatizar'] is True


def test_health_separa_responde_de_pronto_para_automatizar(app, ids, monkeypatch):
    """AC-07.4: HTTP 200 com a estrutura bloqueada não é automação saudável."""
    monkeypatch.setattr(portal_health.requests, 'get',
                        lambda url, **kwargs: MagicMock(status_code=200))
    monkeypatch.setattr(
        portal_health.contrato_portal_recon, 'estado_por_alvo',
        lambda: {circuit_breaker.ALVO_TRABALHISTA: {
            'estado': contrato_portal_recon.BLOQUEADO, 'versao': 3,
            'mensagem': 'mudança estrutural aguardando revisão'}})
    with app.app_context():
        resultado = portal_health.snapshot({}, forcar=True)

    trab = next(p for p in resultado['portais']
                if p['chave'] == circuit_breaker.ALVO_TRABALHISTA)
    assert trab['estado'] == 'ok'
    assert trab['contrato_estado'] == contrato_portal_recon.BLOQUEADO
    assert trab['pronto_para_automatizar'] is False


def test_estado_por_alvo_distingue_bloqueado_de_autoajustado(app, ids):
    with app.app_context():
        ativo = _ativar_baseline()
        adaptador = contrato_portal_recon.adaptadores_padrao()[0]

        assert contrato_portal_recon._estado_do_alvo(adaptador)['estado'] == (
            contrato_portal_recon.COMPATIVEL)

        ativo.origem = 'sistema'
        db.session.commit()
        assert contrato_portal_recon._estado_do_alvo(adaptador)['estado'] == (
            contrato_portal_recon.AUTOAJUSTADO)
