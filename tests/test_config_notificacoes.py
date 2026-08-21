"""Testes da config de notificacoes na pagina de configuracoes (spec 03, NOTIF-01).

Destinatarios, cadencia e a janela de aviso de certificado (AD-029) sao editaveis
sem mexer em codigo; valor invalido e rejeitado; POST parcial (sem a secao) nao
apaga o que ja estava salvo.
"""
from app import db
from app.models import ConfiguracaoSistema
from app.services import manifestador_cofre


def _post(client, **overrides):
    dados = {'a_vencer_dias': '7', 'notif_cadencia': 'semanal',
             'notif_destinatarios': 'op@x.com', 'cert_alerta_dias': '10'}
    dados.update(overrides)
    return client.post('/configuracoes', data=dados, follow_redirects=True)


def test_salva_destinatarios_e_cadencia(client, app):
    _post(client, notif_cadencia='diaria',
          notif_destinatarios='a@x.com, b@y.com')
    with app.app_context():
        cfg = db.session.get(ConfiguracaoSistema, 1)
        assert cfg.notif_cadencia == 'diaria'
        assert cfg.notif_destinatarios == 'a@x.com, b@y.com'


def test_cadencia_invalida_rejeitada(client, app):
    _post(client, notif_cadencia='mensal')
    with app.app_context():
        cfg = db.session.get(ConfiguracaoSistema, 1)
        # invalida nao persiste; permanece no default 'semanal'
        assert cfg.notif_cadencia == 'semanal'


def test_destinatarios_vazio_vira_none(client, app):
    _post(client, notif_destinatarios='   ')
    with app.app_context():
        cfg = db.session.get(ConfiguracaoSistema, 1)
        assert cfg.notif_destinatarios is None


def test_post_parcial_nao_apaga_notificacoes(client, app):
    _post(client, notif_cadencia='diaria', notif_destinatarios='keep@x.com')
    # POST sem a secao de notificacoes (sem notif_cadencia) nao deve mexer
    client.post('/configuracoes', data={'a_vencer_dias': '7'},
                follow_redirects=True)
    with app.app_context():
        cfg = db.session.get(ConfiguracaoSistema, 1)
        assert cfg.notif_cadencia == 'diaria'
        assert cfg.notif_destinatarios == 'keep@x.com'


def test_get_mostra_campos_notificacoes(client):
    corpo = client.get('/configuracoes').get_data(as_text=True)
    assert 'notif_destinatarios' in corpo
    assert 'notif_cadencia' in corpo
    assert 'cert_alerta_dias' in corpo


# --- janela de aviso de vencimento de certificado (AD-029) ------------------

def test_salva_janela_do_certificado(client, app):
    _post(client, cert_alerta_dias='10')
    with app.app_context():
        assert db.session.get(ConfiguracaoSistema, 1).cert_alerta_dias == 10


def test_janela_do_certificado_fora_do_intervalo_e_rejeitada(client, app):
    _post(client, cert_alerta_dias='10')
    for invalido in ('0', '91', 'dez'):
        _post(client, cert_alerta_dias=invalido)
        with app.app_context():
            assert db.session.get(ConfiguracaoSistema, 1).cert_alerta_dias == 10


def test_post_parcial_nao_apaga_a_janela_do_certificado(client, app):
    _post(client, cert_alerta_dias='15')
    client.post('/configuracoes', data={'a_vencer_dias': '7'},
                follow_redirects=True)
    with app.app_context():
        assert db.session.get(ConfiguracaoSistema, 1).cert_alerta_dias == 15


def test_config_manda_no_alerta_e_na_visao_geral(client, app):
    """A tela e o resumo leem a MESMA janela: config -> env -> piso (AD-029).

    Duas fontes fariam a Visao Geral listar um certificado que o e-mail nao cita
    (ou o contrario), e nao ha como o operador saber qual esta certa."""
    _post(client, cert_alerta_dias='10')
    with app.app_context():
        assert manifestador_cofre.janela_alerta_dias() == 10

    _post(client, cert_alerta_dias='45')
    with app.app_context():
        assert manifestador_cofre.janela_alerta_dias() == 45


def test_sem_linha_de_config_cai_no_env(client, app):
    client.get('/configuracoes')          # garante o schema e a linha id=1
    with app.app_context():
        db.session.query(ConfiguracaoSistema).delete()
        db.session.commit()
        app.config['MANIF_CERT_ALERTA_DIAS'] = 22
        assert manifestador_cofre.janela_alerta_dias() == 22
