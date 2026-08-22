"""Testes dos alertas de falha recorrente e saldo baixo (spec 03, NOTIF-03/04/05).

Cada um vira no maximo UMA entrada de pauta por janela anti-spam; saldo None
(API fora) nao gera falso-baixo. Quem envia e o resumo do dia (AD-029) — apurar
nao manda e-mail, e por isso nao depende de SMTP configurado.
"""
import re
from pathlib import Path

import pytest

from app import db
from app.models import ConfiguracaoSistema, NotificacaoLog, PautaNotificacao
from app.services import notificacoes

_ALERTA = {'error_type': 'SELECTOR', 'alvo': 'MUNI', 'ocorrencias': 3,
           'hipotese': 'Portal pode ter mudado.'}


@pytest.fixture()
def ctx(app):
    with app.app_context():
        db.create_all()
        db.session.add(ConfiguracaoSistema(id=1, notif_destinatarios='op@x.com',
                                           notif_cadencia='semanal'))
        db.session.commit()
        app.config.update(SMTP_HOST='smtp', SMTP_FROM='f@x.com',
                          CAPTCHA_2_SALDO_MINIMO=2.0, NOTIF_ALERTA_JANELA_HORAS=24)
        yield app
        db.session.rollback()
        db.session.remove()
        db.drop_all()


def _mock_envio(monkeypatch, enviado=True):
    monkeypatch.setattr(notificacoes.email_sender, 'smtp_configurado', lambda c: True)
    enviados = []
    monkeypatch.setattr(notificacoes.email_sender, 'enviar',
                        lambda cfg, dest, assunto, corpo: enviados.append((assunto, corpo))
                        or enviado)
    return enviados


def _sem_alertas(monkeypatch):
    monkeypatch.setattr(notificacoes.diagnostics, 'alertas_ativos', lambda: [])


def _saldo(monkeypatch, valor):
    monkeypatch.setattr(notificacoes.captcha_solver, 'consultar_saldo', lambda c: valor)


# --- falha recorrente ------------------------------------------------------

def test_falha_recorrente_anota_um_por_dia_e_repete_no_dia_seguinte(ctx, monkeypatch):
    enviados = _mock_envio(monkeypatch)
    monkeypatch.setattr(notificacoes.diagnostics, 'alertas_ativos', lambda: [_ALERTA])
    _saldo(monkeypatch, 10.0)  # saldo alto: sem alerta de saldo

    assert notificacoes.apurar_alertas(ctx) == 1
    assert PautaNotificacao.query.filter_by(tipo='alerta_falha').count() == 1
    assert enviados == []  # apurar nao envia: quem envia e o resumo do dia

    # 2a passada antes do resumo: ja esta na pauta, nao duplica
    assert notificacoes.apurar_alertas(ctx) == 0
    assert PautaNotificacao.query.filter_by(tipo='alerta_falha').count() == 1

    # depois do resumo a pauta esvazia, e o achado — que continua ativo — volta
    # a ser anotado: a janela anti-spam do NotificacaoLog nao segura mais nada
    # (ela virou o historico que decide o que e NOVO, nao o que aparece)
    notificacoes.enviar_resumo_diario(ctx)
    assert len(enviados) == 1
    assert notificacoes.apurar_alertas(ctx) == 1
    assert NotificacaoLog.query.filter_by(tipo='alerta_falha').count() == 1

    # e no resumo seguinte ele sai de novo, agora SEM marca de novo
    notificacoes.enviar_resumo_diario(ctx)
    assert len(enviados) == 2
    assert '[NOVO]' not in enviados[1][1]


def test_alerta_falha_contem_tipo_alvo_e_hipotese(ctx, monkeypatch):
    monkeypatch.setattr(notificacoes.diagnostics, 'alertas_ativos', lambda: [_ALERTA])
    _saldo(monkeypatch, 10.0)

    notificacoes.apurar_alertas(ctx)
    item = PautaNotificacao.query.filter_by(tipo='alerta_falha').one()
    assert 'SELECTOR' in item.titulo and 'MUNI' in item.titulo
    assert 'Portal pode ter mudado.' in item.corpo


# --- saldo 2captcha --------------------------------------------------------

def test_saldo_baixo_anota_um_alerta(ctx, monkeypatch):
    _sem_alertas(monkeypatch)
    _saldo(monkeypatch, 0.3)  # < 2.0

    assert notificacoes.apurar_alertas(ctx) == 1
    item = PautaNotificacao.query.filter_by(tipo='alerta_saldo').one()
    assert '0.30' in item.titulo


def test_saldo_none_api_fora_nao_gera_falso_baixo(ctx, monkeypatch):
    _sem_alertas(monkeypatch)
    _saldo(monkeypatch, None)

    assert notificacoes.apurar_alertas(ctx) == 0
    assert PautaNotificacao.query.filter_by(tipo='alerta_saldo').count() == 0


def test_saldo_alto_nao_alerta(ctx, monkeypatch):
    _sem_alertas(monkeypatch)
    _saldo(monkeypatch, 50.0)

    assert notificacoes.apurar_alertas(ctx) == 0


def test_saldo_baixo_repete_enquanto_o_saldo_estiver_baixo(ctx, monkeypatch):
    """Saldo baixo persiste ate a recarga; o aviso persiste junto.

    Antes a janela de 24h o silenciava depois do primeiro resumo. Isso saiu: o
    problema continua sendo listado enquanto existir, e so o primeiro dia leva
    marca de novo."""
    _mock_envio(monkeypatch)
    _sem_alertas(monkeypatch)
    _saldo(monkeypatch, 0.3)

    assert notificacoes.apurar_alertas(ctx) == 1
    # antes do resumo, a pauta pendente evita duplicar
    assert notificacoes.apurar_alertas(ctx) == 0
    notificacoes.enviar_resumo_diario(ctx)
    # depois do resumo, volta a anotar
    assert notificacoes.apurar_alertas(ctx) == 1
    assert NotificacaoLog.query.filter_by(tipo='alerta_saldo').count() == 1


def test_falha_e_saldo_saem_no_mesmo_email(ctx, monkeypatch):
    """Dois alertas de naturezas diferentes, um e-mail so, secoes separadas."""
    enviados = _mock_envio(monkeypatch)
    monkeypatch.setattr(notificacoes.diagnostics, 'alertas_ativos', lambda: [_ALERTA])
    _saldo(monkeypatch, 0.3)

    assert notificacoes.apurar_alertas(ctx) == 2
    assert notificacoes.enviar_resumo_diario(ctx) is True

    assunto, corpo = enviados[0]
    assert len(enviados) == 1
    assert '2 aviso' in assunto
    assert 'FALHAS RECORRENTES (1)' in corpo
    assert 'SALDO DO 2CAPTCHA (1)' in corpo


# --- guarda de tamanho da coluna tipo --------------------------------------

def test_todo_tipo_de_alerta_cabe_nas_colunas_tipo():
    """Tipo maior que a coluna quebra o anti-spam EM SILENCIO.

    As gravacoes sao best-effort: no MySQL (strict mode) o INSERT longo demais
    levanta, a excecao e engolida e o registro nao entra — o achado nunca chega ao
    resumo, ou o anti-spam para de segurar. No SQLite entraria truncado, sem erro,
    e a falha so apareceria no job de MySQL do CI. Le os tipos da propria fonte
    para que um alerta novo tambem seja conferido. As DUAS tabelas sao conferidas:
    a pauta e o log guardam o mesmo vocabulario."""
    fonte = Path(notificacoes.__file__).read_text(encoding='utf-8')
    tipos = set(re.findall(r"'(alerta_\w+)'", fonte))
    limite = min(NotificacaoLog.__table__.c.tipo.type.length,
                 PautaNotificacao.__table__.c.tipo.type.length)

    assert tipos, 'nenhum tipo de alerta encontrado na fonte — regex desatualizada?'
    assert {t for t in tipos if len(t) > limite} == set()


def test_todo_tipo_de_alerta_tem_secao_no_resumo():
    """Tipo sem secao cai em "Outros avisos" — sai no e-mail, mas sem rotulo util.

    Le da fonte pelo mesmo motivo do teste acima: um alerta novo passa a ser
    conferido sem ninguem lembrar de atualizar a lista aqui."""
    fonte = Path(notificacoes.__file__).read_text(encoding='utf-8')
    tipos = set(re.findall(r"'(alerta_\w+)'", fonte))
    com_secao = {tipo for tipo, _ in notificacoes._SECOES}

    assert tipos - com_secao == set()


# --- sem SMTP --------------------------------------------------------------

def test_sem_smtp_ainda_apura(ctx, monkeypatch):
    """Apurar nao envia, entao nao depende de SMTP: o achado espera na pauta."""
    monkeypatch.setattr(notificacoes.email_sender, 'smtp_configurado', lambda c: False)
    monkeypatch.setattr(notificacoes.diagnostics, 'alertas_ativos', lambda: [_ALERTA])
    _saldo(monkeypatch, 0.3)
    chamou = []
    monkeypatch.setattr(notificacoes.email_sender, 'enviar',
                        lambda *a, **k: chamou.append(1) or True)

    assert notificacoes.apurar_alertas(ctx) == 2
    assert chamou == []
    assert notificacoes.enviar_resumo_diario(ctx) is False
    assert len(notificacoes.pauta_pendente()) == 2
