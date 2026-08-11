"""Testes do alerta de portal fora (spec 09, RESOP-02.7/02.10).

Um alerta POR PORTAL com chave anti-spam propria: consertar (ou desistir de) um
portal nao pode silenciar o alerta de outro dentro da janela.
"""
import pytest

from app import db
from app.models import ConfiguracaoSistema, NotificacaoLog
from app.services import notificacoes


@pytest.fixture()
def ctx(app):
    with app.app_context():
        db.create_all()
        db.session.add(ConfiguracaoSistema(id=1, notif_destinatarios='op@x.com',
                                           notif_cadencia='semanal'))
        db.session.commit()
        app.config.update(SMTP_HOST='smtp', SMTP_FROM='f@x.com',
                          NOTIF_ALERTA_JANELA_HORAS=24)
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


def test_envia_um_alerta_e_nao_reenvia_na_janela(ctx, monkeypatch):
    enviados = _mock_envio(monkeypatch)

    assert notificacoes.alertar_portal_fora(ctx, 'FGTS', 'Timeout no portal.') is True
    assert notificacoes.alertar_portal_fora(ctx, 'FGTS', 'Timeout no portal.') is False
    assert len(enviados) == 1
    assert NotificacaoLog.query.filter_by(tipo='alerta_portal').count() == 1


def test_um_portal_nao_silencia_outro(ctx, monkeypatch):
    enviados = _mock_envio(monkeypatch)

    notificacoes.alertar_portal_fora(ctx, 'FGTS', 'Timeout.')
    assert notificacoes.alertar_portal_fora(ctx, 'Imbe', 'Timeout.') is True
    assert len(enviados) == 2
    chaves = {n.chave for n in NotificacaoLog.query.all()}
    assert chaves == {'portal_fora:FGTS', 'portal_fora:Imbe'}


def test_alerta_nomeia_o_portal_e_o_motivo(ctx, monkeypatch):
    enviados = _mock_envio(monkeypatch)

    notificacoes.alertar_portal_fora(ctx, 'Imbe', 'Timeout aguardando o portal.')

    assunto, corpo = enviados[0]
    assert 'Imbe' in assunto
    assert 'Timeout aguardando o portal.' in corpo


def test_sem_smtp_nao_envia_e_nao_levanta(ctx, monkeypatch):
    monkeypatch.setattr(notificacoes.email_sender, 'smtp_configurado', lambda c: False)

    assert notificacoes.alertar_portal_fora(ctx, 'FGTS', 'Timeout.') is False
    assert NotificacaoLog.query.count() == 0


def test_falha_no_envio_nao_registra_log(ctx, monkeypatch):
    """Se o SMTP recusar, nada e registrado — o alerta tenta de novo depois."""
    _mock_envio(monkeypatch, enviado=False)

    assert notificacoes.alertar_portal_fora(ctx, 'FGTS', 'Timeout.') is False
    assert NotificacaoLog.query.count() == 0


def test_sem_motivo_nao_quebra(ctx, monkeypatch):
    enviados = _mock_envio(monkeypatch)

    assert notificacoes.alertar_portal_fora(ctx, 'FGTS') is True
    assert 'FGTS' in enviados[0][0]
