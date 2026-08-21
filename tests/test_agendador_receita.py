"""Job diario de recheck da situacao cadastral e alerta (spec 08, DATA-02.6/02.8/02.9).

O job roda no scheduler que ja existe (AD-009) — nenhuma thread nova. A suite
nunca dorme de verdade nem bate em rede.
"""
from datetime import date, datetime, timedelta

from app import db
from app.models import (ConfiguracaoSistema, DadosReceita, Empresa,
                        NotificacaoLog, PautaNotificacao)
from app.services import agendador, notificacoes, receita_client, receita_service


def _dto(**over):
    base = dict(cnpj='33000167000101', situacao='ATIVA',
                situacao_data=date(2005, 11, 3), razao_social='RAZAO LTDA',
                municipio='Porto Alegre', uf='RS', fonte='brasilapi')
    base.update(over)
    return receita_client.DadosReceitaDTO(**base)


def _empresa(nome, cnpj, situacao=None, verificado_em=None):
    emp = Empresa(nome=nome, cnpj=cnpj, estado='RS', cidade='Porto Alegre')
    db.session.add(emp)
    db.session.commit()
    if situacao is not None or verificado_em is not None:
        emp.dados_receita = DadosReceita(situacao=situacao, verificado_em=verificado_em)
        db.session.commit()
    return emp


def _config(**valores):
    cfg = db.session.get(ConfiguracaoSistema, 1) or ConfiguracaoSistema(id=1)
    for chave, valor in valores.items():
        setattr(cfg, chave, valor)
    db.session.add(cfg)
    db.session.commit()
    return cfg


def _sem_rede_nem_sono(monkeypatch, dto=None, erro=None):
    monkeypatch.setattr(receita_service.time, 'sleep', lambda _s: None)
    monkeypatch.setattr(receita_service.receita_client, 'consultar',
                        lambda _cnpj: (dto, erro))


# --- agendamento do job (DATA-02.6) ------------------------------------------

def test_job_esta_registrado_no_scheduler_existente(app, ids):
    """AD-009: tarefa recorrente nova entra no BackgroundScheduler que ja existe."""
    assert hasattr(agendador, 'job_recheck_receita')
    assert agendador._JOB_RECHECK_RECEITA == 'agendador_recheck_receita'


def test_job_roda_deslocado_da_hora_do_agendador(app, ids):
    """AD-023: jobs longos nao disputam a mesma janela."""
    assert agendador._OFFSET_RECHECK_RECEITA_H != 0
    assert agendador._OFFSET_RECHECK_RECEITA_H != agendador._OFFSET_VERIFICACAO_H


# --- fatiamento e configuracao (DATA-02.6/02.7) ------------------------------

def test_job_desligado_e_no_op(app, ids, monkeypatch):
    chamou = []
    monkeypatch.setattr(receita_service, 'rechecar_lote',
                        lambda **kw: chamou.append(kw))
    with app.app_context():
        _config(receita_recheck_ativo=False)

    assert agendador.job_recheck_receita(app) is None
    assert chamou == []


def test_job_repassa_a_configuracao(app, ids, monkeypatch):
    recebido = {}

    def _fake(idade_dias, limite):
        recebido.update({'idade_dias': idade_dias, 'limite': limite})
        return {'verificadas': 0, 'falhas': 0, 'baixadas': []}

    monkeypatch.setattr(receita_service, 'rechecar_lote', _fake)
    with app.app_context():
        _config(receita_recheck_ativo=True, receita_recheck_idade_dias=7,
                receita_recheck_limite=5)

    agendador.job_recheck_receita(app)
    assert recebido == {'idade_dias': 7, 'limite': 5}


def test_config_invalida_cai_no_default(app, ids, monkeypatch):
    recebido = {}
    monkeypatch.setattr(receita_service, 'rechecar_lote',
                        lambda **kw: recebido.update(kw) or
                        {'verificadas': 0, 'falhas': 0, 'baixadas': []})
    with app.app_context():
        _config(receita_recheck_ativo=True, receita_recheck_idade_dias=0,
                receita_recheck_limite=0)

    agendador.job_recheck_receita(app)
    assert recebido == {'idade_dias': 30, 'limite': 50}


def test_job_respeita_limite_e_ordem(app, ids, monkeypatch):
    """Mais velhas primeiro, no maximo N por execucao."""
    _sem_rede_nem_sono(monkeypatch, dto=_dto())
    with app.app_context():
        _config(receita_recheck_ativo=True, receita_recheck_idade_dias=30,
                receita_recheck_limite=2)
        _empresa('Velha', '33.000.167/0001-01',
                 situacao='ATIVA', verificado_em=datetime.now() - timedelta(days=300))
        _empresa('Media', '11.222.333/0001-81',
                 situacao='ATIVA', verificado_em=datetime.now() - timedelta(days=60))
        _empresa('Recente', '11.444.777/0001-61',
                 situacao='ATIVA', verificado_em=datetime.now())

    resumo = agendador.job_recheck_receita(app)
    assert resumo['verificadas'] == 2


def test_job_nao_dorme_antes_da_primeira_chamada(app, ids, monkeypatch):
    pausas = []
    monkeypatch.setattr(receita_service.time, 'sleep', lambda s: pausas.append(s))
    monkeypatch.setattr(receita_service.receita_client, 'consultar',
                        lambda _cnpj: (_dto(), None))
    with app.app_context():
        _config(receita_recheck_ativo=True, receita_recheck_limite=1)
        _empresa('Unica', '33.000.167/0001-01')

    agendador.job_recheck_receita(app)
    assert pausas == []


# --- robustez (DATA-02.8) ----------------------------------------------------

def test_falha_de_uma_empresa_nao_derruba_o_job(app, ids, monkeypatch):
    monkeypatch.setattr(receita_service.time, 'sleep', lambda _s: None)

    def _consultar(cnpj):
        if '33000167' in (cnpj or '').replace('.', '').replace('/', '').replace('-', ''):
            raise RuntimeError('boom')
        return _dto(), None

    monkeypatch.setattr(receita_service.receita_client, 'consultar', _consultar)
    with app.app_context():
        _config(receita_recheck_ativo=True)
        _empresa('Explode', '33.000.167/0001-01')
        _empresa('Ok', '11.222.333/0001-81')

    resumo = agendador.job_recheck_receita(app)
    assert resumo['falhas'] >= 1
    assert resumo['verificadas'] >= 1


def test_falha_do_alerta_nao_derruba_o_job(app, ids, monkeypatch):
    """AD-011: notificacao e best-effort; nunca propaga."""
    _sem_rede_nem_sono(monkeypatch, dto=_dto())
    monkeypatch.setattr(notificacoes, 'alertar_empresas_baixadas',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('smtp')))
    with app.app_context():
        _config(receita_recheck_ativo=True)
        _empresa('Alvo', '33.000.167/0001-01')

    resumo = agendador.job_recheck_receita(app)
    assert resumo is not None


def test_api_fora_nao_derruba_o_job(app, ids, monkeypatch):
    _sem_rede_nem_sono(monkeypatch, dto=None, erro=receita_client.ERRO_INDISPONIVEL)
    with app.app_context():
        _config(receita_recheck_ativo=True)
        _empresa('Sem Resposta', '33.000.167/0001-01')

    resumo = agendador.job_recheck_receita(app)
    assert resumo['verificadas'] == 0
    assert resumo['falhas'] >= 1


# --- alerta de empresa baixada (DATA-02.9) -----------------------------------

def _com_smtp(app, monkeypatch, destinatarios='fiscal@escritorio.test'):
    """Prepara o envio do RESUMO. O alerta em si nao manda e-mail: anota na
    pauta, e o resumo do dia leva tudo junto (AD-029)."""
    monkeypatch.setattr(notificacoes.email_sender, 'smtp_configurado',
                        lambda _cfg: True)
    enviados = []
    monkeypatch.setattr(notificacoes.email_sender, 'enviar',
                        lambda cfg, dest, assunto, corpo: (
                            enviados.append((dest, assunto, corpo)) or True))
    with app.app_context():
        _config(notif_destinatarios=destinatarios, notif_cadencia='diaria')
    return enviados


def _anotados():
    return PautaNotificacao.query.filter(
        PautaNotificacao.enviada_em.is_(None)).all()


def test_alerta_anotado_por_empresa(app, ids, monkeypatch):
    enviados = _com_smtp(app, monkeypatch)
    with app.app_context():
        total = notificacoes.alertar_empresas_baixadas(
            app, [(1, 'Alpha LTDA', 'BAIXADA'), (2, 'Beta ME', 'SUSPENSA')])

        assert total == 2
        assert enviados == []           # nenhum e-mail avulso
        titulos = ' '.join(p.titulo for p in _anotados())
        assert 'Alpha LTDA' in titulos and 'Beta ME' in titulos


def test_as_duas_empresas_saem_num_unico_email(app, ids, monkeypatch):
    enviados = _com_smtp(app, monkeypatch)
    with app.app_context():
        notificacoes.alertar_empresas_baixadas(
            app, [(1, 'Alpha LTDA', 'BAIXADA'), (2, 'Beta ME', 'SUSPENSA')])
        assert notificacoes.enviar_resumo_diario(app) is True

    assert len(enviados) == 1
    corpo = enviados[0][2]
    assert 'SITUACAO NA RECEITA (2)' in corpo
    assert 'Alpha LTDA' in corpo and 'Beta ME' in corpo


def test_alerta_explica_a_consequencia(app, ids, monkeypatch):
    _com_smtp(app, monkeypatch)
    with app.app_context():
        notificacoes.alertar_empresas_baixadas(app, [(1, 'Alpha LTDA', 'BAIXADA')])

        corpo = _anotados()[0].corpo
        assert 'FORA do lote automatico' in corpo
        assert 'emissao individual' in corpo


def test_alerta_nao_repete_na_mesma_janela(app, ids, monkeypatch):
    """Anti-spam duravel (AD-011): sobrevive a restart."""
    _com_smtp(app, monkeypatch)
    with app.app_context():
        notificacoes.alertar_empresas_baixadas(app, [(1, 'Alpha LTDA', 'BAIXADA')])
        # antes do resumo: ja esta na pauta
        assert notificacoes.alertar_empresas_baixadas(
            app, [(1, 'Alpha LTDA', 'BAIXADA')]) == 0
        # depois do resumo: a janela do NotificacaoLog e que segura
        notificacoes.enviar_resumo_diario(app)
        assert notificacoes.alertar_empresas_baixadas(
            app, [(1, 'Alpha LTDA', 'BAIXADA')]) == 0
        assert PautaNotificacao.query.count() == 1


def test_alerta_de_uma_empresa_nao_silencia_outra(app, ids, monkeypatch):
    """Chave anti-spam POR empresa, como no alerta de municipio."""
    _com_smtp(app, monkeypatch)
    with app.app_context():
        notificacoes.alertar_empresas_baixadas(app, [(1, 'Alpha LTDA', 'BAIXADA')])
        notificacoes.alertar_empresas_baixadas(app, [(2, 'Beta ME', 'BAIXADA')])

        assert {p.chave for p in _anotados()} == {
            'empresa_baixada:1', 'empresa_baixada:2'}


def test_alerta_registra_no_log_duravel_apos_o_resumo(app, ids, monkeypatch):
    _com_smtp(app, monkeypatch)
    with app.app_context():
        notificacoes.alertar_empresas_baixadas(app, [(7, 'Gama SA', 'INAPTA')])
        notificacoes.enviar_resumo_diario(app)
        registro = NotificacaoLog.query.filter_by(chave='empresa_baixada:7').first()

        assert registro is not None
        assert registro.tipo == 'alerta_empresa_baixada'


def test_sem_smtp_ainda_anota(app, ids, monkeypatch):
    """O achado espera na pauta ate o SMTP voltar, em vez de se perder."""
    monkeypatch.setattr(notificacoes.email_sender, 'smtp_configurado',
                        lambda _cfg: False)
    with app.app_context():
        _config(notif_destinatarios='fiscal@escritorio.test')
        assert notificacoes.alertar_empresas_baixadas(
            app, [(1, 'Alpha', 'BAIXADA')]) == 1
        assert notificacoes.enviar_resumo_diario(app) is False
        assert len(_anotados()) == 1


def test_lista_vazia_nao_anota(app, ids, monkeypatch):
    enviados = _com_smtp(app, monkeypatch)
    with app.app_context():
        assert notificacoes.alertar_empresas_baixadas(app, []) == 0
        assert notificacoes.alertar_empresas_baixadas(app, None) == 0
        assert _anotados() == []
    assert enviados == []


def test_job_alerta_apenas_a_transicao(app, ids, monkeypatch):
    """Ponta a ponta: quem ja estava baixada nao gera alerta — senao ligar a
    feature dispararia um e-mail por empresa da carteira."""
    enviados = _com_smtp(app, monkeypatch)
    monkeypatch.setattr(receita_service.time, 'sleep', lambda _s: None)
    monkeypatch.setattr(receita_service.receita_client, 'consultar',
                        lambda _cnpj: (_dto(situacao='BAIXADA'), None))
    with app.app_context():
        _config(receita_recheck_ativo=True, receita_recheck_idade_dias=0,
                notif_destinatarios='fiscal@escritorio.test')
        _empresa('Ja Baixada', '33.000.167/0001-01', situacao='BAIXADA')
        _empresa('Vai Baixar', '11.222.333/0001-81', situacao='ATIVA')

    agendador.job_recheck_receita(app)
    with app.app_context():
        notificacoes.enviar_resumo_diario(app)

    corpos = ' '.join(c for _d, _a, c in enviados)
    assert 'Vai Baixar' in corpos
    assert 'Ja Baixada' not in corpos
