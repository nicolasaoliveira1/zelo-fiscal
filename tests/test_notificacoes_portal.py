"""Testes do alerta de portal fora (spec 09, RESOP-02.7/02.10; AD-029).

Uma entrada de pauta POR PORTAL com chave anti-spam propria: consertar (ou
desistir de) um portal nao pode silenciar o alerta de outro dentro da janela. O
alerta nao sai mais sozinho — ele espera o resumo do dia, que leva todos juntos.
"""
import pytest

from app import db
from app.models import ConfiguracaoSistema, NotificacaoLog, PautaNotificacao
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
    """Captura o(s) e-mail(s) que o resumo do dia mandar."""
    monkeypatch.setattr(notificacoes.email_sender, 'smtp_configurado', lambda c: True)
    enviados = []
    monkeypatch.setattr(notificacoes.email_sender, 'enviar',
                        lambda cfg, dest, assunto, corpo: enviados.append((assunto, corpo))
                        or enviado)
    return enviados


def _pauta(tipo=None):
    q = PautaNotificacao.query
    return q.filter_by(tipo=tipo).all() if tipo else q.all()


def test_anota_um_alerta_e_nao_repete_na_janela(ctx, monkeypatch):
    _mock_envio(monkeypatch)

    assert notificacoes.alertar_portal_fora(ctx, 'FGTS', 'Timeout no portal.') is True
    # 2a abertura do breaker no mesmo portal: ja esta na pauta, nao duplica
    assert notificacoes.alertar_portal_fora(ctx, 'FGTS', 'Timeout no portal.') is False
    assert len(_pauta('alerta_portal')) == 1


def test_um_portal_nao_silencia_outro(ctx, monkeypatch):
    _mock_envio(monkeypatch)

    notificacoes.alertar_portal_fora(ctx, 'FGTS', 'Timeout.')
    assert notificacoes.alertar_portal_fora(ctx, 'Imbe', 'Timeout.') is True
    assert {p.chave for p in _pauta()} == {'portal_fora:FGTS', 'portal_fora:Imbe'}


def test_dois_portais_saem_num_unico_email(ctx, monkeypatch):
    """O ponto do AD-029: dois achados, UM e-mail, com os dois dentro."""
    enviados = _mock_envio(monkeypatch)

    notificacoes.alertar_portal_fora(ctx, 'FGTS', 'Timeout.')
    notificacoes.alertar_portal_fora(ctx, 'Imbe', 'Timeout.')
    assert enviados == []  # nada saiu ainda

    assert notificacoes.enviar_resumo_diario(ctx) is True
    assert len(enviados) == 1
    _, corpo = enviados[0]
    assert 'FGTS' in corpo and 'Imbe' in corpo


def test_resumo_enviado_fecha_a_pauta_e_alimenta_o_anti_spam(ctx, monkeypatch):
    _mock_envio(monkeypatch)
    notificacoes.alertar_portal_fora(ctx, 'FGTS', 'Timeout.')
    notificacoes.enviar_resumo_diario(ctx)

    assert notificacoes.pauta_pendente() == []
    assert NotificacaoLog.query.filter_by(chave='portal_fora:FGTS').count() == 1
    # dentro da janela de 24h nao volta para a pauta
    assert notificacoes.alertar_portal_fora(ctx, 'FGTS', 'Timeout.') is False


def test_alerta_nomeia_o_portal_e_o_motivo(ctx, monkeypatch):
    enviados = _mock_envio(monkeypatch)

    notificacoes.alertar_portal_fora(ctx, 'Imbe', 'Timeout aguardando o portal.')
    notificacoes.enviar_resumo_diario(ctx)

    _, corpo = enviados[0]
    assert 'Imbe' in corpo
    assert 'Timeout aguardando o portal.' in corpo


def test_sem_smtp_ainda_anota_e_nao_levanta(ctx, monkeypatch):
    """Sem transporte o achado NAO se perde: fica na pauta ate o SMTP voltar.

    Antes o alerta era descartado quando o SMTP estava fora — perder em silencio
    exatamente o aviso que o operador precisava (AD-029)."""
    monkeypatch.setattr(notificacoes.email_sender, 'smtp_configurado', lambda c: False)

    assert notificacoes.alertar_portal_fora(ctx, 'FGTS', 'Timeout.') is True
    assert len(_pauta()) == 1
    assert notificacoes.enviar_resumo_diario(ctx) is False
    assert len(notificacoes.pauta_pendente()) == 1  # segue pendente


def test_falha_no_envio_deixa_a_pauta_intacta(ctx, monkeypatch):
    """SMTP recusou: nada e registrado e o achado sai no proximo resumo."""
    _mock_envio(monkeypatch, enviado=False)
    notificacoes.alertar_portal_fora(ctx, 'FGTS', 'Timeout.')

    assert notificacoes.enviar_resumo_diario(ctx) is False
    assert NotificacaoLog.query.count() == 0
    assert len(notificacoes.pauta_pendente()) == 1


def test_sem_motivo_nao_quebra(ctx, monkeypatch):
    _mock_envio(monkeypatch)

    assert notificacoes.alertar_portal_fora(ctx, 'FGTS') is True
    assert 'FGTS' in _pauta()[0].titulo


# --- causa: portal fora x solver de captcha falhando -----------------------

def test_falha_de_captcha_avisa_do_solver_nao_do_portal(ctx, monkeypatch):
    """Dizer 'portal fora' quando quem falhou foi o 2captcha manda o operador
    depurar o site errado."""
    _mock_envio(monkeypatch)

    assert notificacoes.alertar_portal_fora(
        ctx, 'Estadual RS', 'Falha ao resolver o captcha.', causa='captcha') is True

    item = _pauta('alerta_solver')[0]
    assert 'captcha' in item.titulo.lower()
    assert 'fora do ar' not in item.titulo.lower()
    assert '2captcha' in item.corpo
    assert 'O portal pode estar no ar' in item.corpo


def test_causas_diferentes_nao_se_silenciam(ctx, monkeypatch):
    """Sao problemas distintos, com acoes distintas: cada um tem chave propria."""
    _mock_envio(monkeypatch)

    notificacoes.alertar_portal_fora(ctx, 'FGTS', 'timeout', causa='portal')
    assert notificacoes.alertar_portal_fora(
        ctx, 'FGTS', 'captcha falhou', causa='captcha') is True

    assert {p.chave for p in _pauta()} == {'portal_fora:FGTS', 'solver_captcha:FGTS'}


def test_causa_desconhecida_cai_no_texto_de_portal(ctx, monkeypatch):
    _mock_envio(monkeypatch)

    notificacoes.alertar_portal_fora(ctx, 'FGTS', 'x', causa='???')

    assert 'fora do ar' in _pauta()[0].titulo.lower()
