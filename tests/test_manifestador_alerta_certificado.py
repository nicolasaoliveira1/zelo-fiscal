"""Selecao e alerta dos certificados que precisam de atencao (MANIF-26)."""
from datetime import datetime, timedelta

import pytest

from app import db
from app.models import (CertificadoEmpresa, ConfiguracaoSistema, DadosReceita,
                        Empresa, EstadoCertificado, NotificacaoLog,
                        PautaNotificacao)
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


def test_populacao_do_denominador_so_conta_vencimento_conhecido(app, ids):
    """De quantos sai o "nenhum vencendo" — e de quantos NAO sai.

    Certificado sem `not_after` (sem_arquivo/sem_pasta) e de empresa inativa
    ficam FORA da populacao. Conta-los faria a tela dizer "nenhum dos 47 vence",
    que e desconhecido vestido de alivio: dos 47, 20 nao se sabe nada.
    """
    with app.app_context():
        com_data = _empresa('COM DATA', '22.222.222/2233-01')
        _certificado(com_data, datetime.now() + timedelta(days=300))

        sem_data = _empresa('SEM ARQUIVO', '22.222.222/2233-02')
        _certificado(sem_data, None, EstadoCertificado.SEM_ARQUIVO)

        inativa = _empresa('BAIXADA', '22.222.222/2233-03', situacao='BAIXADA')
        _certificado(inativa, datetime.now() + timedelta(days=300))
        db.session.commit()

        resumo = manifestador_cofre.resumo_de_vencimento()

        assert resumo['com_vencimento'] == 1
        assert resumo['itens'] == []


def test_a_lista_do_alerta_e_a_mesma_do_resumo(app, ids):
    """`certificados_a_vencer` virou fachada de `resumo_de_vencimento`: o e-mail
    e a tela nao podem passar a ler listas diferentes."""
    with app.app_context():
        empresa = _empresa('NA JANELA', '22.222.222/2233-04')
        _certificado(empresa, datetime.now() + timedelta(days=3))
        db.session.commit()

        assert (manifestador_cofre.certificados_a_vencer()
                == manifestador_cofre.resumo_de_vencimento()['itens'])


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


# --- alerta no resumo do dia (T2, AD-029) ---------------------------------

@pytest.fixture()
def ctx_alerta(app):
    with app.app_context():
        db.create_all()
        db.session.add(ConfiguracaoSistema(id=1, notif_destinatarios='op@x.com',
                                           notif_cadencia='diaria'))
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
    """Captura os e-mails que o RESUMO mandar (os alertas nao mandam nenhum)."""
    enviados = []
    monkeypatch.setattr(notificacoes.email_sender, 'smtp_configurado', lambda c: True)
    monkeypatch.setattr(notificacoes.email_sender, 'enviar',
                        lambda cfg, dest, assunto, corpo: enviados.append((assunto, corpo))
                        or True)
    return enviados


def _resumir(ctx):
    """Fecha o dia: manda o resumo, o que carimba a pauta e grava o anti-spam."""
    return notificacoes.enviar_resumo_diario(ctx)


def test_anota_um_por_empresa_e_causa(ctx_alerta, monkeypatch):
    enviados = _mock_envio(monkeypatch)
    item = _item_alerta(101)

    assert notificacoes.alertar_certificados_vencendo(ctx_alerta, [item]) == 1
    # 2o job antes do resumo: o achado ja esta na pauta, nao duplica
    assert notificacoes.alertar_certificados_vencendo(ctx_alerta, [item]) == 0
    assert enviados == []

    assert _resumir(ctx_alerta) is True
    assert len(enviados) == 1
    assert NotificacaoLog.query.filter_by(
        chave='certificado_vencendo:101', tipo='alerta_certificado').count() == 1


def test_dez_certificados_saem_num_unico_email(ctx_alerta, monkeypatch):
    """O pedido que originou o AD-029: a carteira inteira num e-mail so.

    Antes eram dez e-mails no mesmo minuto — volume que faz o aviso ser ignorado,
    o oposto do que o alerta existe para fazer."""
    enviados = _mock_envio(monkeypatch)
    itens = [_item_alerta(100 + i) for i in range(10)]

    assert notificacoes.alertar_certificados_vencendo(ctx_alerta, itens) == 10
    assert _resumir(ctx_alerta) is True

    assert len(enviados) == 1
    assunto, corpo = enviados[0]
    assert '10 aviso' in assunto
    assert 'CERTIFICADOS DIGITAIS (10)' in corpo
    for item in itens:
        assert item['empresa_nome'] in corpo
    assert corpo.count('dia(s))\n\n  - [NOVO] Vence em') == 9


def test_uma_empresa_nao_silencia_outra(ctx_alerta, monkeypatch):
    _mock_envio(monkeypatch)

    assert notificacoes.alertar_certificados_vencendo(
        ctx_alerta, [_item_alerta(101), _item_alerta(202)]) == 2

    assert {p.chave for p in PautaNotificacao.query.all()} == {
        'certificado_vencendo:101', 'certificado_vencendo:202'}


def test_transicao_para_vencido_realerta_com_chave_propria(ctx_alerta, monkeypatch):
    _mock_envio(monkeypatch)
    vencendo = _item_alerta(101)
    vencido = _item_alerta(101, causa='vencido', dias_restantes=-1)
    vencido['not_after'] = datetime.now() - timedelta(days=1)

    assert notificacoes.alertar_certificados_vencendo(ctx_alerta, [vencendo]) == 1
    assert notificacoes.alertar_certificados_vencendo(ctx_alerta, [vencido]) == 1
    assert {p.chave for p in PautaNotificacao.query.all()} == {
        'certificado_vencendo:101', 'certificado_vencido:101'}


def test_aviso_de_manifestacao_parada_fecha_secao_uma_vez(ctx_alerta, monkeypatch):
    enviados = _mock_envio(monkeypatch)
    itens = [
        _item_alerta(101, causa='vencido', dias_restantes=-2),
        _item_alerta(202, causa='vencido', dias_restantes=-3),
    ]
    itens[0]['not_after'] = datetime.now() - timedelta(days=2)
    itens[1]['not_after'] = datetime.now() - timedelta(days=3)

    notificacoes.alertar_certificados_vencendo(ctx_alerta, itens)

    anotados = PautaNotificacao.query.order_by(PautaNotificacao.id).all()
    assert all(not anotado.corpo for anotado in anotados)
    # Simula pautas duráveis criadas pela versão anterior e ainda pendentes no
    # momento da atualização.
    aviso_legado = 'A manifestacao dessa empresa esta parada ate renovar.'
    anotados[0].corpo = aviso_legado
    anotados[1].corpo = f'{aviso_legado}\nDetalhe preservado.'
    db.session.commit()

    assert _resumir(ctx_alerta) is True
    corpo = enviados[0][1]
    assert aviso_legado not in corpo
    assert 'Detalhe preservado.' in corpo
    assert corpo.count(
        'A manifestação das empresas com certificado vencido está parada até a renovação.'
    ) == 1


def test_texto_de_vencendo_tem_data_e_dias_restantes_no_titulo(ctx_alerta,
                                                               monkeypatch):
    """Uma linha por certificado: o titulo carrega data, empresa e quanto falta."""
    _mock_envio(monkeypatch)
    item = _item_alerta(101, dias_restantes=7)

    notificacoes.alertar_certificados_vencendo(ctx_alerta, [item])

    anotado = PautaNotificacao.query.one()
    assert item['not_after'].strftime('%d/%m/%Y') in anotado.titulo
    assert 'faltam 7 dia(s)' in anotado.titulo
    assert 'EMPRESA 101' in anotado.titulo


def test_conselho_de_renovacao_aparece_uma_vez_por_secao(ctx_alerta, monkeypatch):
    """Dez vencimentos nao viram dez copias do mesmo conselho: ele fecha a secao.

    Repetir por item trocaria o spam de e-mails por spam dentro do e-mail."""
    enviados = _mock_envio(monkeypatch)
    notificacoes.alertar_certificados_vencendo(
        ctx_alerta, [_item_alerta(100 + i) for i in range(10)])
    assert _resumir(ctx_alerta) is True

    corpo = enviados[0][1]
    assert corpo.count('rode o inventario do cofre') == 1


def test_sem_smtp_ainda_anota_e_nao_levanta(ctx_alerta, monkeypatch):
    """O achado espera na pauta ate o SMTP voltar, em vez de se perder."""
    chamou = []
    monkeypatch.setattr(notificacoes.email_sender, 'smtp_configurado', lambda c: False)
    monkeypatch.setattr(notificacoes.email_sender, 'enviar',
                        lambda *args: chamou.append(args) or True)

    assert notificacoes.alertar_certificados_vencendo(
        ctx_alerta, [_item_alerta(101)]) == 1
    assert chamou == []
    assert NotificacaoLog.query.count() == 0
    assert len(notificacoes.pauta_pendente()) == 1


def test_sem_destinatario_nao_envia_nem_levanta(ctx_alerta, monkeypatch):
    db.session.get(ConfiguracaoSistema, 1).notif_destinatarios = None
    db.session.commit()
    chamou = []
    monkeypatch.setattr(notificacoes.email_sender, 'smtp_configurado', lambda c: True)
    monkeypatch.setattr(notificacoes.email_sender, 'enviar',
                        lambda *args: chamou.append(args) or True)

    notificacoes.alertar_certificados_vencendo(ctx_alerta, [_item_alerta(101)])

    assert _resumir(ctx_alerta) is False
    assert chamou == []
    assert NotificacaoLog.query.count() == 0


# --- correcoes vindas do code-review ---------------------------------------

def test_vencendo_hoje_no_limite_do_dia_ja_conta_como_vencido(app, ids):
    """A causa sai da comparacao de datetime, como o `inventariar` que grava o
    estado. Decidir por DIA fazia o dia do vencimento dizer "faltam 0 dia(s),
    providencie a renovacao" para um certificado ja gravado como VENCIDO."""
    with app.app_context():
        empresa = _empresa('EMPRESA VENCE HOJE', '22.222.222/2222-40')
        _certificado(empresa, datetime.now() - timedelta(minutes=5))
        db.session.commit()

        item = next(i for i in manifestador_cofre.certificados_a_vencer()
                    if i['empresa_id'] == empresa.id)
        assert item['causa'] == 'vencido'
        assert item['dias_restantes'] == 0      # ainda e hoje


def test_certificado_repete_todo_dia_ate_a_renovacao(ctx_alerta, monkeypatch):
    """REVERTE a janela anti-spam deste alerta, a pedido do usuario.

    A janela (7 dias vencendo, 3 dias vencido) existia para o mesmo certificado
    nao voltar em todo resumo. Ela saiu: a condicao persiste por semanas e ver a
    lista inteira todo dia reforca. O que impede a repeticao de virar ruido nao e
    mais o silencio — e o [NOVO], que so o primeiro dia carrega.
    """
    enviados = _mock_envio(monkeypatch)
    item = _item_alerta(201)

    assert notificacoes.alertar_certificados_vencendo(ctx_alerta, [item]) == 1
    assert _resumir(ctx_alerta) is True
    assert '[NOVO]' in enviados[0][1]

    # no dia seguinte volta a anotar, e sai de novo — sem marca de novo
    assert notificacoes.alertar_certificados_vencendo(ctx_alerta, [item]) == 1
    assert _resumir(ctx_alerta) is True
    assert len(enviados) == 2
    assert 'EMPRESA 201' in enviados[1][1]
    assert '[NOVO]' not in enviados[1][1]


def test_transicao_para_vencido_reaparece_como_novo(ctx_alerta, monkeypatch):
    """A virada vencendo->vencido tem CHAVE propria, entao no dia em que
    acontece ela volta marcada — mesmo depois de semanas avisando o vencendo."""
    enviados = _mock_envio(monkeypatch)
    vencendo = _item_alerta(202)
    vencido = _item_alerta(202, causa='vencido', dias_restantes=-1)

    notificacoes.alertar_certificados_vencendo(ctx_alerta, [vencendo])
    _resumir(ctx_alerta)
    notificacoes.alertar_certificados_vencendo(ctx_alerta, [vencendo])
    _resumir(ctx_alerta)
    assert '[NOVO]' not in enviados[1][1]

    notificacoes.alertar_certificados_vencendo(ctx_alerta, [vencido])
    _resumir(ctx_alerta)

    corpo = enviados[2][1]
    assert '[NOVO]' in corpo
    assert 'Vencido em' in corpo
