"""Enganche das notificacoes nos jobs do agendador (spec 03, NOTIF-01/03/05).

O digest sai no job de snapshot (roda sempre); os alertas saem no job de
renovacao. Falha no envio nunca derruba o tick.
"""
import pytest

from app.models import TarefaEmissao, TipoCertidao
from app.services import agendador, manifestador_cofre, notificacoes


@pytest.fixture()
def fluxos_limpos():
    agendador._fluxos.clear()
    yield
    agendador._fluxos.clear()


def _registrar_stub(tipo_enum, ids_por_tipo):
    def calc_ids(app):
        return list(ids_por_tipo)

    def rodar_lote(app, ids, wrap_emit, execution_id):
        emit = wrap_emit(lambda cid, drv, eid: (True, False, 'ok'))
        for cid in ids:
            emit(cid, None, execution_id)

    agendador.registrar_fluxo(tipo_enum, {
        'tipo': tipo_enum, 'calc_ids': calc_ids, 'rodar_lote': rodar_lote})


# --- digest no job de snapshot ---------------------------------------------

def test_snapshot_job_dispara_digest(app, ids, monkeypatch):
    chamou = []
    monkeypatch.setattr(notificacoes, 'enviar_digest_se_devido',
                        lambda a: chamou.append(a) or True)
    agendador.job_snapshot_diario(app)
    assert chamou == [app]


def test_snapshot_job_nao_quebra_se_digest_falha(app, ids, monkeypatch):
    def explode(a):
        raise RuntimeError('smtp explodiu')
    monkeypatch.setattr(notificacoes, 'enviar_digest_se_devido', explode)
    # nao deve propagar — o snapshot ja rodou antes do envio
    agendador.job_snapshot_diario(app)


# --- alertas no job de renovacao -------------------------------------------

def test_renovacao_job_dispara_alertas(app, ids, fluxos_limpos, monkeypatch):
    _registrar_stub(TipoCertidao.FGTS, [ids['fgts']])
    monkeypatch.setattr(agendador, '_avisar_saldo_baixo', lambda a: None)
    chamou = []
    monkeypatch.setattr(notificacoes, 'enviar_alertas',
                        lambda a: chamou.append(a) or 0)
    agendador.job_renovacao_diaria(app)
    assert chamou == [app]


def test_renovacao_job_nao_quebra_se_alertas_falham(app, ids, fluxos_limpos, monkeypatch):
    _registrar_stub(TipoCertidao.FGTS, [ids['fgts']])
    monkeypatch.setattr(agendador, '_avisar_saldo_baixo', lambda a: None)
    monkeypatch.setattr(notificacoes, 'enviar_alertas',
                        lambda a: (_ for _ in ()).throw(RuntimeError('boom')))
    agendador.job_renovacao_diaria(app)
    # o lote seguiu apesar da falha no envio de alertas
    with app.app_context():
        t = TarefaEmissao.query.filter_by(certidao_id=ids['fgts']).first()
        assert t.status == 'ok'


# --- inventario do cofre e alerta de certificado (MANIF-26) ----------------

def test_inventario_cofre_job_atualiza_e_alerta_na_janela_configurada(
        app, ids, monkeypatch):
    resumo = {'pronto': 1}
    itens = [{'empresa_id': 1, 'causa': 'vencendo'}]
    recebeu = {}

    def selecionar(dias):
        recebeu['dias'] = dias
        return itens

    def alertar(contexto, selecionados):
        recebeu['alerta'] = (contexto, selecionados)
        return 1

    monkeypatch.setitem(app.config, 'MANIF_CERT_ALERTA_DIAS', 12)
    monkeypatch.setattr(manifestador_cofre, 'inventariar', lambda: resumo)
    monkeypatch.setattr(manifestador_cofre, 'certificados_a_vencer', selecionar)
    monkeypatch.setattr(notificacoes, 'alertar_certificados_vencendo', alertar)

    assert agendador.job_inventario_cofre(app) == resumo
    assert recebeu == {'dias': 12, 'alerta': (app, itens)}


def test_inventario_cofre_job_usa_janela_padrao_de_30_dias(app, ids, monkeypatch):
    recebeu = []
    monkeypatch.delitem(app.config, 'MANIF_CERT_ALERTA_DIAS', raising=False)
    monkeypatch.setattr(manifestador_cofre, 'inventariar', lambda: {})
    monkeypatch.setattr(manifestador_cofre, 'certificados_a_vencer',
                        lambda dias: recebeu.append(dias) or [])
    monkeypatch.setattr(notificacoes, 'alertar_certificados_vencendo',
                        lambda contexto, itens: 0)

    agendador.job_inventario_cofre(app)
    assert recebeu == [30]


def test_inventario_cofre_job_drive_fora_ainda_alerta_pelo_espelho(
        app, ids, monkeypatch):
    """Drive fora e ambiente, nao motivo para calar sobre um vencimento.

    `certificados_a_vencer` le so o BANCO: com o drive fora por alguns dias, o
    espelho anterior segue valido e um certificado vencendo continua vencendo.
    Calar aqui seria deixar de avisar por um motivo que nao tem relacao com o
    aviso."""
    itens = [{'empresa_id': 1, 'causa': 'vencido'}]
    chamou = []
    monkeypatch.setattr(manifestador_cofre, 'inventariar',
                        lambda: (_ for _ in ()).throw(
                            manifestador_cofre.CofreError('drive indisponivel')))
    monkeypatch.setattr(manifestador_cofre, 'certificados_a_vencer',
                        lambda dias: itens)
    monkeypatch.setattr(notificacoes, 'alertar_certificados_vencendo',
                        lambda contexto, recebidos: chamou.append(recebidos) or 1)

    # sem resumo (a varredura falhou), mas o alerta saiu
    assert agendador.job_inventario_cofre(app) is None
    assert chamou == [itens]


def test_inventario_cofre_job_nao_roda_com_varredura_em_curso(
        app, ids, monkeypatch):
    """Clique na tela + job diario: a segunda varredura nao comeca.

    Duas varreduras gravam a linha da MESMA empresa (`empresa_id` unique) e a
    perdedora derruba o commit unico do fim, perdendo a varredura inteira."""
    chamou = []
    monkeypatch.setattr(manifestador_cofre, 'inventariar',
                        lambda: chamou.append('varreu') or {})
    monkeypatch.setattr(manifestador_cofre, 'certificados_a_vencer',
                        lambda dias: [])
    monkeypatch.setattr(notificacoes, 'alertar_certificados_vencendo',
                        lambda contexto, itens: 0)

    assert manifestador_cofre._inventario_acquire() is True
    try:
        assert agendador.job_inventario_cofre(app) is None
        assert chamou == []          # nao varreu
    finally:
        manifestador_cofre._inventario_release()

    # liberado o lock, a proxima execucao varre normalmente
    agendador.job_inventario_cofre(app)
    assert chamou == ['varreu']


def test_inventario_cofre_job_nao_quebra_se_alerta_falha(app, ids, monkeypatch):
    resumo = {'vencido': 1}
    monkeypatch.setattr(manifestador_cofre, 'inventariar', lambda: resumo)
    monkeypatch.setattr(manifestador_cofre, 'certificados_a_vencer', lambda dias: [])
    monkeypatch.setattr(notificacoes, 'alertar_certificados_vencendo',
                        lambda contexto, itens: (_ for _ in ()).throw(
                            RuntimeError('smtp indisponivel')))

    assert agendador.job_inventario_cofre(app) == resumo
