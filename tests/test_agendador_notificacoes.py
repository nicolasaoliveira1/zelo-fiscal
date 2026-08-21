"""Enganche das notificacoes nos jobs do agendador (spec 03, NOTIF-01/03/05).

Os jobs ANOTAM achados na pauta; o unico e-mail do dia sai no `job_resumo_diario`,
agendado depois de todos eles (AD-029). Falha no envio nunca derruba o tick.
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


# --- resumo do dia em job proprio ------------------------------------------

def test_snapshot_job_nao_envia_email(app, ids, monkeypatch):
    """O snapshot volta a fazer uma coisa so. O e-mail e do `job_resumo_diario`,
    que roda depois dos produtores de achado — enviar aqui, no primeiro job do
    dia, contaria o dia pela metade."""
    chamou = []
    monkeypatch.setattr(notificacoes, 'enviar_resumo_diario',
                        lambda a: chamou.append(a) or True)
    agendador.job_snapshot_diario(app)
    assert chamou == []


def test_resumo_job_envia_o_resumo(app, ids, monkeypatch):
    chamou = []
    monkeypatch.setattr(notificacoes, 'enviar_resumo_diario',
                        lambda a: chamou.append(a) or True)
    assert agendador.job_resumo_diario(app) is True
    assert chamou == [app]


def test_resumo_job_nao_quebra_se_envio_falha(app, ids, monkeypatch):
    def explode(a):
        raise RuntimeError('smtp explodiu')
    monkeypatch.setattr(notificacoes, 'enviar_resumo_diario', explode)
    assert agendador.job_resumo_diario(app) is False


def test_resumo_e_o_ultimo_job_do_dia(app, monkeypatch):
    """A ordem e o que garante que o resumo conte o dia inteiro (AD-029)."""
    assert agendador._OFFSET_RESUMO_DIARIO_H > max(
        agendador._OFFSET_VERIFICACAO_H,
        agendador._OFFSET_RECHECK_RECEITA_H,
        agendador._OFFSET_INVENTARIO_COFRE_H)


# --- alertas no job de renovacao -------------------------------------------

def test_renovacao_job_dispara_alertas(app, ids, fluxos_limpos, monkeypatch):
    _registrar_stub(TipoCertidao.FGTS, [ids['fgts']])
    monkeypatch.setattr(agendador, '_avisar_saldo_baixo', lambda a: None)
    chamou = []
    monkeypatch.setattr(notificacoes, 'apurar_alertas',
                        lambda a: chamou.append(a) or 0)
    agendador.job_renovacao_diaria(app)
    assert chamou == [app]


def test_renovacao_job_nao_quebra_se_alertas_falham(app, ids, fluxos_limpos, monkeypatch):
    _registrar_stub(TipoCertidao.FGTS, [ids['fgts']])
    monkeypatch.setattr(agendador, '_avisar_saldo_baixo', lambda a: None)
    monkeypatch.setattr(notificacoes, 'apurar_alertas',
                        lambda a: (_ for _ in ()).throw(RuntimeError('boom')))
    agendador.job_renovacao_diaria(app)
    # o lote seguiu apesar da falha no envio de alertas
    with app.app_context():
        t = TarefaEmissao.query.filter_by(certidao_id=ids['fgts']).first()
        assert t.status == 'ok'


# --- inventario do cofre e alerta de certificado (MANIF-26) ----------------

def test_inventario_cofre_job_atualiza_e_alerta(app, ids, monkeypatch):
    """O job nao escolhe mais a janela: quem escolhe e `certificados_a_vencer`,
    pela mesma `janela_alerta_dias` que a Visao Geral usa (AD-029). Passar a
    janela aqui era o que deixava as duas telas discordarem."""
    resumo = {'pronto': 1}
    itens = [{'empresa_id': 1, 'causa': 'vencendo'}]
    recebeu = {}

    monkeypatch.setattr(manifestador_cofre, 'inventariar', lambda: resumo)
    monkeypatch.setattr(manifestador_cofre, 'certificados_a_vencer',
                        lambda: itens)
    monkeypatch.setattr(notificacoes, 'alertar_certificados_vencendo',
                        lambda contexto, selecionados: recebeu.update(
                            alerta=(contexto, selecionados)) or 1)

    assert agendador.job_inventario_cofre(app) == resumo
    assert recebeu == {'alerta': (app, itens)}


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
                        lambda: itens)
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
                        lambda: [])
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
    monkeypatch.setattr(manifestador_cofre, 'certificados_a_vencer', lambda: [])
    monkeypatch.setattr(notificacoes, 'alertar_certificados_vencendo',
                        lambda contexto, itens: (_ for _ in ()).throw(
                            RuntimeError('smtp indisponivel')))

    assert agendador.job_inventario_cofre(app) == resumo
