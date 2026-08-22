"""Testes do resumo diario e do anti-spam durável (spec 03, NOTIF-01/04, AD-029).

Um e-mail por dia com tudo dentro: os numeros da carteira batem com a
classificacao do painel; a cadencia decide o resumo DE ROTINA mas nunca segura um
aviso; sem SMTP nao envia (loga) e a pauta fica intacta; 0/0/0 envia "tudo em
dia" salvo flag; envio so e registrado quando de fato ocorre.
"""
from datetime import date, datetime, timedelta

import pytest

from app import db
from app.models import (Certidao, ConfiguracaoSistema, Empresa, NotificacaoLog,
                        PautaNotificacao, StatusEspecial, TipoCertidao)
from app.services import notificacoes
from app.services.snapshot_service import classificar_status_certidao


def _empresa():
    emp = Empresa(nome='E', cnpj=f'00.000.000/000{Empresa.query.count()}-00',
                  estado='RS', cidade='Tramandai')
    db.session.add(emp)
    db.session.commit()
    return emp


def _cert(emp, tipo, *, validade=None, pendente=False):
    c = Certidao(tipo=tipo, empresa=emp, data_validade=validade,
                 status_especial=(StatusEspecial.PENDENTE if pendente else None))
    db.session.add(c)
    db.session.commit()
    return c


def _config(destinatarios='op@x.com', cadencia='semanal'):
    cfg = ConfiguracaoSistema(id=1, notif_destinatarios=destinatarios,
                              notif_cadencia=cadencia)
    db.session.add(cfg)
    db.session.commit()
    return cfg


def _pauta(tipo='alerta_certificado', titulo='Vencido — X', chave='k1'):
    db.session.add(PautaNotificacao(chave=chave, tipo=tipo, titulo=titulo,
                                    corpo='detalhe do aviso'))
    db.session.commit()


@pytest.fixture()
def ctx(app):
    with app.app_context():
        db.create_all()
        yield app
        db.session.rollback()
        db.session.remove()
        db.drop_all()


# --- _destinatarios --------------------------------------------------------

def test_destinatarios_parse_separadores_trim_e_dedup(ctx):
    cfg = _config('a@x.com, b@y.com; a@x.com\nc@z.com,  , semarroba')
    assert notificacoes._destinatarios(cfg) == ['a@x.com', 'b@y.com', 'c@z.com']


def test_destinatarios_vazio_quando_sem_config(ctx):
    assert notificacoes._destinatarios(None) == []
    assert notificacoes._destinatarios(_config('')) == []


# --- contagem bate com o painel (AC P1.2) ----------------------------------

def test_contagem_bate_com_classificacao_do_painel(ctx):
    emp = _empresa()
    hoje = date.today()
    _cert(emp, TipoCertidao.FGTS, validade=hoje + timedelta(days=3))   # a_vencer
    _cert(emp, TipoCertidao.ESTADUAL, validade=hoje + timedelta(days=2))  # a_vencer
    _cert(emp, TipoCertidao.FEDERAL, validade=hoje - timedelta(days=5))  # vencida
    _cert(emp, TipoCertidao.MUNICIPAL, pendente=True)                    # pendente
    _cert(emp, TipoCertidao.TRABALHISTA, pendente=True)                  # pendente
    _cert(emp, TipoCertidao.FGTS, validade=hoje + timedelta(days=365))   # valida (nao conta)

    _, _, resumo = notificacoes.montar_resumo()

    # referencia independente: reclassifica a carteira do zero
    esperado = {'a_vencer': 0, 'vencidas': 0, 'pendentes': 0,
                'validas': 0, 'sem_data': 0}
    for c in Certidao.query.all():
        esperado[classificar_status_certidao(c, hoje)] += 1
    assert resumo == esperado
    assert resumo == {'a_vencer': 2, 'vencidas': 1, 'pendentes': 2,
                      'validas': 1, 'sem_data': 0}


# --- montagem do corpo -----------------------------------------------------

def test_corpo_agrupa_avisos_por_secao_na_ordem_declarada(ctx):
    _config()
    _pauta(tipo='alerta_saldo', titulo='Saldo baixo', chave='saldo_baixo')
    _pauta(tipo='alerta_certificado', titulo='Vencido — Alfa', chave='c:1')
    _pauta(tipo='alerta_municipio', titulo='Tramandai — quebrou', chave='m:T')

    _, corpo, _ = notificacoes.montar_resumo(notificacoes.pauta_pendente())

    posicoes = [corpo.index(rotulo) for rotulo in
                ('CERTIFICADOS DIGITAIS (1)', 'MUNICIPIOS (1)', 'SALDO DO 2CAPTCHA (1)')]
    assert posicoes == sorted(posicoes)  # ordem de _SECOES, nao de insercao
    assert 'detalhe do aviso' in corpo


def test_tipo_sem_secao_conhecida_cai_em_outros(ctx):
    _config()
    _pauta(tipo='alerta_novo_em_folha', titulo='Coisa nova', chave='x:1')

    _, corpo, _ = notificacoes.montar_resumo(notificacoes.pauta_pendente())
    assert 'OUTROS AVISOS (1)' in corpo
    assert 'Coisa nova' in corpo


def test_assunto_conta_avisos_quando_ha_pauta(ctx):
    _config()
    _pauta(chave='c:1')
    _pauta(chave='c:2', titulo='Vencido — Y')

    assunto, _, _ = notificacoes.montar_resumo(notificacoes.pauta_pendente())
    assert '2 aviso' in assunto


# --- cadencia / dedup ------------------------------------------------------

def test_resumo_devido_quando_nunca_enviado(ctx):
    assert notificacoes._resumo_devido(_config(cadencia='semanal'), []) is True


def test_resumo_de_rotina_nao_devido_dentro_da_semana(ctx):
    _config(cadencia='semanal')
    db.session.add(NotificacaoLog(
        chave='digest', tipo='digest',
        enviada_em=datetime.now() - timedelta(days=3)))
    db.session.commit()
    assert notificacoes._resumo_devido(notificacoes._config(), []) is False


def test_resumo_devido_apos_intervalo_diario(ctx):
    _config(cadencia='diaria')
    db.session.add(NotificacaoLog(
        chave='digest', tipo='digest',
        enviada_em=datetime.now() - timedelta(days=1, hours=1)))
    db.session.commit()
    assert notificacoes._resumo_devido(notificacoes._config(), []) is True


def test_cadencia_nunca_segura_um_aviso(ctx, monkeypatch):
    """Cadencia semanal, resumo enviado ontem — mas ha aviso na pauta.

    A cadencia existe para nao encher a caixa com resumo de rotina. Aplicar ela a
    um alerta trocaria spam por silencio de ate uma semana, que e pior."""
    _config(cadencia='semanal')
    db.session.add(NotificacaoLog(
        chave='digest', tipo='digest',
        enviada_em=datetime.now() - timedelta(days=1)))
    db.session.commit()
    _pauta()

    assert notificacoes._resumo_devido(notificacoes._config(),
                                       notificacoes.pauta_pendente()) is True


# --- enviar_resumo_diario --------------------------------------------------

def test_sem_smtp_nao_envia_e_loga(ctx, monkeypatch):
    _config()
    ctx.config['SMTP_HOST'] = ''  # nao configurado
    chamou = []
    monkeypatch.setattr(notificacoes.email_sender, 'enviar',
                        lambda *a, **k: chamou.append(1) or True)
    assert notificacoes.enviar_resumo_diario(ctx) is False
    assert chamou == []  # nao tentou enviar (AC P1.3)


def test_vazio_envia_tudo_em_dia_por_padrao(ctx, monkeypatch):
    _config()
    ctx.config.update(SMTP_HOST='smtp', SMTP_FROM='f@x.com',
                      NOTIF_DIGEST_ENVIAR_VAZIO=True)
    capturado = {}
    monkeypatch.setattr(notificacoes.email_sender, 'smtp_configurado', lambda c: True)
    monkeypatch.setattr(notificacoes.email_sender, 'enviar',
                        lambda cfg, dest, assunto, corpo: capturado.update(
                            assunto=assunto, corpo=corpo) or True)
    assert notificacoes.enviar_resumo_diario(ctx) is True
    assert 'tudo em dia' in capturado['assunto']


def test_vazio_omitido_quando_flag_desligada(ctx, monkeypatch):
    _config()
    ctx.config.update(SMTP_HOST='smtp', SMTP_FROM='f@x.com',
                      NOTIF_DIGEST_ENVIAR_VAZIO=False)
    chamou = []
    monkeypatch.setattr(notificacoes.email_sender, 'smtp_configurado', lambda c: True)
    monkeypatch.setattr(notificacoes.email_sender, 'enviar',
                        lambda *a, **k: chamou.append(1) or True)
    assert notificacoes.enviar_resumo_diario(ctx) is False
    assert chamou == []


def test_carteira_so_com_validas_continua_vazia_para_o_resumo(ctx, monkeypatch):
    """Carteira saudavel + pauta vazia = resumo vazio, MESMO havendo certidoes.

    Regressao do dia em que a contagem passou a devolver os cinco baldes: o
    `not any(resumo.values())` daqui virava verdadeiro assim que existisse UMA
    certidao valida, e o e-mail que devia ficar calado passaria a sair todo dia.
    Nenhum teste pegava, porque toda carteira de fixture deste arquivo estava
    vazia — o unico jeito de a assercao ser real e semear a certidao valida.
    """
    _config()
    ctx.config.update(SMTP_HOST='smtp', SMTP_FROM='f@x.com',
                      NOTIF_DIGEST_ENVIAR_VAZIO=False)
    emp = _empresa()
    _cert(emp, TipoCertidao.FGTS, validade=date.today() + timedelta(days=365))
    chamou = []
    monkeypatch.setattr(notificacoes.email_sender, 'smtp_configurado', lambda c: True)
    monkeypatch.setattr(notificacoes.email_sender, 'enviar',
                        lambda *a, **k: chamou.append(1) or True)

    assert notificacoes.enviar_resumo_diario(ctx) is False
    assert chamou == []


def test_flag_de_vazio_nao_cala_um_resumo_com_aviso(ctx, monkeypatch):
    """"Vazio" e carteira 0/0/0 E pauta vazia: um aviso torna o resumo nao-vazio."""
    _config()
    ctx.config.update(SMTP_HOST='smtp', SMTP_FROM='f@x.com',
                      NOTIF_DIGEST_ENVIAR_VAZIO=False)
    _pauta()
    monkeypatch.setattr(notificacoes.email_sender, 'smtp_configurado', lambda c: True)
    monkeypatch.setattr(notificacoes.email_sender, 'enviar', lambda *a, **k: True)

    assert notificacoes.enviar_resumo_diario(ctx) is True


def test_envio_ok_registra_e_nao_reenvia_na_cadencia(ctx, monkeypatch):
    _config(cadencia='semanal')
    _cert(_empresa(), TipoCertidao.FGTS,
          validade=date.today() + timedelta(days=3))  # resumo nao-vazio
    ctx.config.update(SMTP_HOST='smtp', SMTP_FROM='f@x.com')
    monkeypatch.setattr(notificacoes.email_sender, 'smtp_configurado', lambda c: True)
    monkeypatch.setattr(notificacoes.email_sender, 'enviar', lambda *a, **k: True)

    assert notificacoes.enviar_resumo_diario(ctx) is True
    assert NotificacaoLog.query.filter_by(chave='digest').count() == 1
    # 2a chamada no mesmo dia, sem aviso novo: cadencia semanal nao venceu
    assert notificacoes.enviar_resumo_diario(ctx) is False
    assert NotificacaoLog.query.filter_by(chave='digest').count() == 1


def test_envio_falho_nao_registra_permanece_devido(ctx, monkeypatch):
    _config(cadencia='semanal')
    _cert(_empresa(), TipoCertidao.FGTS,
          validade=date.today() + timedelta(days=3))  # resumo nao-vazio
    ctx.config.update(SMTP_HOST='smtp', SMTP_FROM='f@x.com')
    monkeypatch.setattr(notificacoes.email_sender, 'smtp_configurado', lambda c: True)
    monkeypatch.setattr(notificacoes.email_sender, 'enviar', lambda *a, **k: False)

    assert notificacoes.enviar_resumo_diario(ctx) is False
    assert NotificacaoLog.query.filter_by(chave='digest').count() == 0
    assert notificacoes._resumo_devido(notificacoes._config(), []) is True


def test_pauta_fechada_vira_historico_e_nao_volta_ao_proximo_resumo(ctx, monkeypatch):
    _config(cadencia='diaria')
    ctx.config.update(SMTP_HOST='smtp', SMTP_FROM='f@x.com')
    corpos = []
    monkeypatch.setattr(notificacoes.email_sender, 'smtp_configurado', lambda c: True)
    monkeypatch.setattr(notificacoes.email_sender, 'enviar',
                        lambda cfg, dest, assunto, corpo: corpos.append(corpo) or True)
    _pauta(titulo='Vencido — Alfa', chave='certificado_vencido:1')

    assert notificacoes.enviar_resumo_diario(ctx) is True
    assert 'Vencido — Alfa' in corpos[0]
    # a linha continua no banco (historico), mas carimbada
    item = PautaNotificacao.query.one()
    assert item.enviada_em is not None
    assert notificacoes.pauta_pendente() == []


def test_registrar_pauta_nao_duplica_enquanto_o_achado_espera(ctx):
    _config()
    assert notificacoes.registrar_pauta('k', 'alerta_falha', 'T', 'c') is True
    assert notificacoes.registrar_pauta('k', 'alerta_falha', 'T', 'c') is False
    assert PautaNotificacao.query.count() == 1


# --- o que e NOVO, e por que isso importa mais que o silencio ---------------

def _ja_saiu(chave):
    """Historico de um envio anterior daquela chave."""
    db.session.add(NotificacaoLog(chave=chave, tipo='alerta_certificado',
                                  enviada_em=datetime.now()))
    db.session.commit()


def test_marca_apenas_o_que_nunca_saiu_em_resumo(ctx):
    _config()
    _pauta(chave='c:novo', titulo='Vence em 25/08 — ALFA')
    _pauta(chave='c:velho', titulo='Vence em 26/08 — BETA')
    _ja_saiu('c:velho')

    _, corpo, _ = notificacoes.montar_resumo(notificacoes.pauta_pendente())

    assert '[NOVO] Vence em 25/08 — ALFA' in corpo
    assert '[NOVO] Vence em 26/08 — BETA' not in corpo
    assert 'Vence em 26/08 — BETA' in corpo  # continua listado, so nao marcado


def test_novos_vem_antes_dos_repetidos_na_secao(ctx):
    """O item que entrou hoje nao pode cair no meio da lista que o leitor ja
    percorreu ontem — e assim que a repeticao apaga o que deveria reforcar."""
    _config()
    _pauta(chave='c:1', titulo='Vence em 24/08 — ALFA')
    _pauta(chave='c:2', titulo='Vence em 25/08 — BETA')
    _pauta(chave='c:3', titulo='Vence em 26/08 — GAMA')
    _ja_saiu('c:1')
    _ja_saiu('c:2')

    _, corpo, _ = notificacoes.montar_resumo(notificacoes.pauta_pendente())

    assert corpo.index('GAMA') < corpo.index('ALFA')
    assert corpo.index('GAMA') < corpo.index('BETA')


def test_assunto_diz_quantos_sao_e_quantos_mudaram(ctx):
    """Numa carteira que repete os mesmos avisos por semanas, "12 avisos" nao
    distingue o dia em que apareceu o decimo terceiro do dia em que nada
    aconteceu."""
    _config()
    _pauta(chave='c:1')
    _pauta(chave='c:2', titulo='Vencido — Y')
    _ja_saiu('c:1')

    assunto, _, _ = notificacoes.montar_resumo(notificacoes.pauta_pendente())

    assert '2 aviso(s)' in assunto
    assert '1 novo(s)' in assunto


def test_assunto_diz_nenhum_novo_quando_todos_repetem(ctx):
    _config()
    _pauta(chave='c:1')
    _ja_saiu('c:1')

    assunto, _, _ = notificacoes.montar_resumo(notificacoes.pauta_pendente())

    assert 'nenhum novo' in assunto


def test_sem_historico_nada_e_marcado_como_novo(ctx, monkeypatch):
    """Se a consulta do historico falha, o seguro e NAO marcar: um [NOVO] errado
    ensina o leitor a desconfiar do marcador, e ai ele para de servir."""
    _config()
    _pauta(chave='c:1')

    def falhar(*a, **k):
        raise RuntimeError('historico indisponivel')

    monkeypatch.setattr(notificacoes.db.session, 'query', falhar)

    _, corpo, _ = notificacoes.montar_resumo(notificacoes.pauta_pendente())

    assert '[NOVO]' not in corpo
