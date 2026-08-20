"""Selecao e alerta dos certificados que precisam de atencao (MANIF-26)."""
from datetime import datetime, timedelta

import pytest

from app import db
from app.models import (CertificadoEmpresa, ConfiguracaoSistema, DadosReceita,
                        Empresa, EstadoCertificado, NotificacaoLog)
from app.services import manifestador_cofre, notificacoes


def _empresa(nome, cnpj, situacao=None):
    empresa = Empresa(nome=nome, cnpj=cnpj, estado='RS', cidade='Imbe')
    if situacao:
        empresa.dados_receita = DadosReceita(situacao=situacao)
    db.session.add(empresa)
    db.session.flush()
    return empresa


def _certificado(empresa, not_after, estado=EstadoCertificado.PRONTO):
    db.session.add(CertificadoEmpresa(
        empresa_id=empresa.id, not_after=not_after, estado=estado))


def test_inclui_certificado_vencido_com_dias_negativos(app, ids):
    with app.app_context():
        empresa = _empresa('EMPRESA VENCIDA', '22.222.222/2222-22')
        _certificado(empresa, datetime.now() - timedelta(days=3))
        db.session.commit()

        itens = manifestador_cofre.certificados_a_vencer()

        item = next(item for item in itens if item['empresa_id'] == empresa.id)
        assert item['causa'] == 'vencido'
        assert item['dias_restantes'] < 0


def test_vencido_entra_independente_do_estado_gravado(app, ids):
    with app.app_context():
        empresa = _empresa('EMPRESA ESTADO ANTIGO', '22.222.222/2222-23')
        _certificado(empresa, datetime.now() - timedelta(days=1),
                     EstadoCertificado.PRONTO)
        db.session.commit()

        itens = manifestador_cofre.certificados_a_vencer()

        item = next(item for item in itens if item['empresa_id'] == empresa.id)
        assert item['estado'] == EstadoCertificado.PRONTO
        assert item['causa'] == 'vencido'


def test_inclui_certificado_dentro_da_janela(app, ids):
    with app.app_context():
        empresa = _empresa('EMPRESA PROXIMA', '22.222.222/2222-24')
        _certificado(empresa, datetime.now() + timedelta(days=10))
        db.session.commit()

        itens = manifestador_cofre.certificados_a_vencer(dias=10)

        item = next(item for item in itens if item['empresa_id'] == empresa.id)
        assert item['causa'] == 'vencendo'
        assert 0 <= item['dias_restantes'] <= 10


def test_exclui_certificado_fora_da_janela(app, ids):
    with app.app_context():
        empresa = _empresa('EMPRESA DISTANTE', '22.222.222/2222-25')
        _certificado(empresa, datetime.now() + timedelta(days=31))
        db.session.commit()

        itens = manifestador_cofre.certificados_a_vencer(dias=30)

        assert empresa.id not in {item['empresa_id'] for item in itens}


def test_exclui_certificado_sem_data_de_vencimento(app, ids):
    with app.app_context():
        empresa = _empresa('EMPRESA SEM DATA', '22.222.222/2222-26')
        _certificado(empresa, None)
        db.session.commit()

        itens = manifestador_cofre.certificados_a_vencer()

        assert empresa.id not in {item['empresa_id'] for item in itens}


def test_exclui_empresa_nao_ativa_na_receita(app, ids):
    with app.app_context():
        empresa = _empresa('EMPRESA BAIXADA', '22.222.222/2222-27', 'BAIXADA')
        _certificado(empresa, datetime.now() + timedelta(days=5))
        db.session.commit()

        itens = manifestador_cofre.certificados_a_vencer()

        assert empresa.id not in {item['empresa_id'] for item in itens}


def test_ordena_do_mais_critico_para_o_menos(app, ids):
    with app.app_context():
        mais_critica = _empresa('EMPRESA MAIS CRITICA', '22.222.222/2222-28')
        proxima = _empresa('EMPRESA PROXIMA', '22.222.222/2222-29')
        _certificado(mais_critica, datetime.now() - timedelta(days=4))
        _certificado(proxima, datetime.now() + timedelta(days=2))
        db.session.commit()

        itens = manifestador_cofre.certificados_a_vencer()
        selecionados = [item for item in itens if item['empresa_id'] in {
            mais_critica.id, proxima.id}]

        assert [item['empresa_id'] for item in selecionados] == [
            mais_critica.id, proxima.id]


# --- alerta por e-mail (T2) -----------------------------------------------

@pytest.fixture()
def ctx_alerta(app):
    with app.app_context():
        db.create_all()
        db.session.add(ConfiguracaoSistema(id=1, notif_destinatarios='op@x.com'))
        db.session.commit()
        app.config.update(SMTP_HOST='smtp', SMTP_FROM='f@x.com',
                          NOTIF_ALERTA_JANELA_HORAS=24)
        yield app
        db.session.rollback()
        db.session.remove()
        db.drop_all()


def _item_alerta(empresa_id, causa='vencendo', dias_restantes=10):
    return {
        'empresa_id': empresa_id,
        'empresa_nome': f'EMPRESA {empresa_id}',
        'not_after': datetime.now() + timedelta(days=dias_restantes),
        'estado': EstadoCertificado.PRONTO,
        'dias_restantes': dias_restantes,
        'causa': causa,
    }


def _mock_envio(monkeypatch):
    enviados = []
    monkeypatch.setattr(notificacoes.email_sender, 'smtp_configurado', lambda c: True)
    monkeypatch.setattr(notificacoes.email_sender, 'enviar',
                        lambda cfg, dest, assunto, corpo: enviados.append((assunto, corpo))
                        or True)
    return enviados


def test_alerta_certificado_envia_um_por_empresa_e_causa(ctx_alerta, monkeypatch):
    enviados = _mock_envio(monkeypatch)
    item = _item_alerta(101)

    assert notificacoes.alertar_certificados_vencendo(ctx_alerta, [item]) == 1
    assert notificacoes.alertar_certificados_vencendo(ctx_alerta, [item]) == 0
    assert len(enviados) == 1
    assert NotificacaoLog.query.filter_by(
        chave='certificado_vencendo:101', tipo='alerta_certificado').count() == 1


def test_alerta_certificado_uma_empresa_nao_silencia_outra(ctx_alerta, monkeypatch):
    enviados = _mock_envio(monkeypatch)

    assert notificacoes.alertar_certificados_vencendo(
        ctx_alerta, [_item_alerta(101), _item_alerta(202)]) == 2

    assert len(enviados) == 2
    assert {registro.chave for registro in NotificacaoLog.query.all()} == {
        'certificado_vencendo:101', 'certificado_vencendo:202'}


def test_transicao_para_vencido_realerta_com_chave_propria(ctx_alerta, monkeypatch):
    enviados = _mock_envio(monkeypatch)
    vencendo = _item_alerta(101)
    vencido = _item_alerta(101, causa='vencido', dias_restantes=-1)
    vencido['not_after'] = datetime.now() - timedelta(days=1)

    assert notificacoes.alertar_certificados_vencendo(ctx_alerta, [vencendo]) == 1
    assert notificacoes.alertar_certificados_vencendo(ctx_alerta, [vencido]) == 1
    assert len(enviados) == 2
    assert {registro.chave for registro in NotificacaoLog.query.all()} == {
        'certificado_vencendo:101', 'certificado_vencido:101'}


def test_texto_de_vencido_diz_que_manifestacao_esta_parada(ctx_alerta, monkeypatch):
    enviados = _mock_envio(monkeypatch)
    item = _item_alerta(101, causa='vencido', dias_restantes=-2)
    item['not_after'] = datetime.now() - timedelta(days=2)

    notificacoes.alertar_certificados_vencendo(ctx_alerta, [item])

    assunto, corpo = enviados[0]
    assert 'vencido' in assunto.lower()
    assert 'manifestacao dessa empresa esta parada' in corpo


def test_texto_de_vencendo_tem_data_e_dias_restantes(ctx_alerta, monkeypatch):
    enviados = _mock_envio(monkeypatch)
    item = _item_alerta(101, dias_restantes=7)

    notificacoes.alertar_certificados_vencendo(ctx_alerta, [item])

    _, corpo = enviados[0]
    assert item['not_after'].strftime('%d/%m/%Y') in corpo
    assert 'Faltam 7 dia(s)' in corpo


def test_sem_smtp_nao_envia_nem_levanta(ctx_alerta, monkeypatch):
    chamou = []
    monkeypatch.setattr(notificacoes.email_sender, 'smtp_configurado', lambda c: False)
    monkeypatch.setattr(notificacoes.email_sender, 'enviar',
                        lambda *args: chamou.append(args) or True)

    assert notificacoes.alertar_certificados_vencendo(
        ctx_alerta, [_item_alerta(101)]) == 0
    assert chamou == []
    assert NotificacaoLog.query.count() == 0


def test_sem_destinatario_nao_envia_nem_levanta(ctx_alerta, monkeypatch):
    db.session.get(ConfiguracaoSistema, 1).notif_destinatarios = None
    db.session.commit()
    chamou = []
    monkeypatch.setattr(notificacoes.email_sender, 'smtp_configurado', lambda c: True)
    monkeypatch.setattr(notificacoes.email_sender, 'enviar',
                        lambda *args: chamou.append(args) or True)

    assert notificacoes.alertar_certificados_vencendo(
        ctx_alerta, [_item_alerta(101)]) == 0
    assert chamou == []
    assert NotificacaoLog.query.count() == 0
