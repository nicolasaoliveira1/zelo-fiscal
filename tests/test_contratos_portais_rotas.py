"""Central de contratos dos portais no Diagnóstico (RAC-06, RAC-14).

Nenhum teste abre navegador: onde a rota observaria o portal, o criador de
driver é substituído. O que precisa ficar provado é a superfície — quem pode
chamar, o que a rota recusa e com qual código — e que promover contrato exige
confirmação explícita.
"""
from unittest.mock import MagicMock

import pytest

from app import db
from app.automation import trabalhista
from app.automation.batch_state import (
    TRABALHISTA_BATCH_LOCK,
    TRABALHISTA_BATCH_STATE,
)
from app.automation.trabalhista_recon import ElementoInventariado, InventarioPortal
from app.models import ContratoPortal, IncidenteContratoPortal, Usuario
from app.routes import contratos_portais
from app.services import (
    contrato_portal,
    contrato_portal_preflight,
    contrato_portal_recon,
)
from app.services.contrato_portal_drift import (
    COMPATIVEL,
    REVISAO,
    RemapeamentoSeletor,
    ResultadoComparacaoPortal,
    _diferenca,
    comparar,
)

BASE = '/diagnostico/contratos-portais'
FLUXO = trabalhista.FLUXO_CONTRATO
ALVO = trabalhista.ALVO_CONTRATO


def _admin_id():
    return Usuario.query.filter_by(papel='admin').first().id


def _baseline(app):
    with app.app_context():
        return contrato_portal.criar_baseline(
            fluxo=FLUXO, alvo=ALVO,
            definicao=trabalhista.definicao_baseline(),
            usuario_id=_admin_id()).id


def _incidente(app, contrato_id):
    with app.app_context():
        resultado = ResultadoComparacaoPortal(
            classificacao='revisao',
            diferencas=(_diferenca(
                'seletor_alterado', 'formulario', 'submeter',
                'botao-emitir', 'botao-emitir-2'),),
        )
        contrato_portal.registrar_incidente(contrato_id, resultado)
        return IncidenteContratoPortal.query.filter_by(
            contrato_base_id=contrato_id, estado='aberto').one().id


def _inventario_do_cndt(**trocas):
    """Inventário sintético no formato que `observar_passivo` devolveria.

    Os fatos aqui (assinatura do formulário, ordem relativa) são de propósito
    DIFERENTES dos que a declaração inventa: é o que prova que a baseline nasce
    da observação, não do código.
    """
    declaracao = trabalhista.definicao_baseline()
    assinatura = trocas.get('assinatura', 'ab' * 32)
    elementos = tuple(ElementoInventariado(
        tag=item.tag,
        tipo=item.tipo,
        id=item.seletor,
        name='',
        rotulo=item.rotulo,
        seletor_tipo='id',
        seletor=item.seletor,
        assinatura_formulario=assinatura,
        ordem_relativa=10 + ordem,
        obrigatorio=item.obrigatorio,
        desabilitado=False,
        somente_leitura=False,
        visivel=True,
    ) for ordem, item in enumerate(declaracao.elementos)
        if item.chave not in trocas.get('sem', ()))
    # A tela real tem MAIS controles que os declarados (ouvir captcha, enviar
    # por e-mail, validar). O contrato precisa representar a tela inteira.
    extras = tuple(ElementoInventariado(
        tag='input', tipo=tipo, id=seletor, name='', rotulo=rotulo,
        seletor_tipo='id', seletor=seletor,
        assinatura_formulario=assinatura, ordem_relativa=20 + ordem,
        obrigatorio=False, desabilitado=False, somente_leitura=False,
        visivel=visivel,
    ) for ordem, (seletor, tipo, rotulo, visivel) in enumerate((
        ('botao-ouvir-captcha', 'button', 'Ouvir caracteres do captcha.', True),
        ('campoEmail', 'email', 'Digite seu e-mail', False),
        ('botao-enviar', 'submit', 'Envia a certidão para o e-mail.', False),
    )) if seletor not in trocas.get('sem', ()))
    return InventarioPortal(
        host=trocas.get('host', declaracao.host),
        rota=trocas.get('rota', declaracao.rota),
        etapa=declaracao.etapa,
        elementos=elementos + (() if trocas.get('so_declarados') else extras),
    )


@pytest.fixture()
def observando(monkeypatch):
    """Observa uma tela sintética. NUNCA abre navegador: o portal é de governo,
    e teste não bate em portal real."""
    def _instalar(inventario=None):
        monkeypatch.setattr(
            contratos_portais, '_criar_driver_lote', lambda: MagicMock())
        monkeypatch.setattr(
            trabalhista, 'observar_passivo',
            lambda driver, contrato: inventario or _inventario_do_cndt())
    _instalar()
    return _instalar


# --- autorização ------------------------------------------------------------

@pytest.mark.parametrize('metodo, caminho', [
    ('get', BASE),
    ('post', f'{BASE}/{FLUXO}/{ALVO}/baseline'),
    ('post', f'{BASE}/{FLUXO}/{ALVO}/recon'),
    ('post', f'{BASE}/incidentes/1/aceitar'),
    ('post', f'{BASE}/incidentes/1/rejeitar'),
    ('post', f'{BASE}/1/restaurar'),
])
def test_operador_nao_alcanca_a_central(login_as, metodo, caminho):
    resposta = getattr(login_as('operador'), metodo)(caminho, json={})

    assert resposta.status_code == 403


def test_anonimo_e_redirecionado_para_o_login(client_anon):
    resposta = client_anon.get(BASE)

    assert resposta.status_code in (302, 401)


# --- leitura ----------------------------------------------------------------

def test_sem_contrato_o_alvo_aparece_com_acao_de_baseline(client):
    dados = client.get(BASE).get_json()

    assert dados['status'] == 'ok'
    alvo = dados['alvos'][0]
    assert alvo['estado'] == contrato_portal_recon.DESCONHECIDO
    assert alvo['pode_criar_baseline'] is True
    assert alvo['versao'] is None


def test_listagem_traz_incidentes_e_historico_do_alvo(app, client):
    contrato_id = _baseline(app)
    _incidente(app, contrato_id)

    alvo = client.get(BASE).get_json()['alvos'][0]

    assert alvo['estado'] == contrato_portal_recon.BLOQUEADO
    assert alvo['versao'] == 1
    assert [i['elemento'] for i in alvo['incidentes']] == ['submeter']
    assert [v['versao'] for v in alvo['historico']] == [1]


# --- baseline ---------------------------------------------------------------

def test_baseline_criada_por_acao_explicita_do_admin(app, client, observando):
    resposta = client.post(f'{BASE}/{FLUXO}/{ALVO}/baseline', json={})

    assert resposta.status_code == 201
    assert resposta.get_json()['versao'] == 1
    with app.app_context():
        ativa = ContratoPortal.query.filter_by(
            fluxo=FLUXO, alvo=ALVO, estado='ativa').one()
        assert ativa.origem == 'usuario'
        assert ativa.ativado_por_id is not None


def test_segunda_baseline_no_mesmo_alvo_e_409(app, client, observando):
    _baseline(app)

    resposta = client.post(f'{BASE}/{FLUXO}/{ALVO}/baseline', json={})

    assert resposta.status_code == 409
    assert resposta.get_json()['status'] == 'error'


def test_alvo_desconhecido_e_404(client):
    resposta = client.post(f'{BASE}/nfse/inexistente/baseline', json={})

    assert resposta.status_code == 404


# --- recon sob demanda ------------------------------------------------------

def test_recon_sob_demanda_devolve_o_resultado_da_observacao(app, client, monkeypatch):
    _baseline(app)
    monkeypatch.setattr(
        contratos_portais.contrato_portal_recon, 'recon_sob_demanda',
        lambda *a, **k: contrato_portal_recon.COMPATIVEL)

    resposta = client.post(f'{BASE}/{FLUXO}/{ALVO}/recon', json={})

    assert resposta.status_code == 200
    assert resposta.get_json()['resultado'] == contrato_portal_recon.COMPATIVEL


def test_alvo_ocupado_por_emissao_responde_423(app, client, monkeypatch):
    _baseline(app)
    monkeypatch.setattr(
        contratos_portais.contrato_portal_recon, 'recon_sob_demanda',
        MagicMock(side_effect=contrato_portal_recon.AlvoOcupadoError(
            'Há uma emissão em curso neste portal. Tente novamente depois.')))

    resposta = client.post(f'{BASE}/{FLUXO}/{ALVO}/recon', json={})

    assert resposta.status_code == 423
    assert 'emissão em curso' in resposta.get_json()['message']


def test_falha_ao_observar_nao_vira_500(app, client, monkeypatch):
    _baseline(app)
    monkeypatch.setattr(
        contratos_portais.contrato_portal_recon, 'recon_sob_demanda',
        MagicMock(side_effect=RuntimeError('chromedriver não abriu')))

    resposta = client.post(f'{BASE}/{FLUXO}/{ALVO}/recon', json={})

    assert resposta.status_code == 409
    assert resposta.get_json()['status'] == 'error'
    # A causa técnica fica no log, não na tela.
    assert 'chromedriver' not in resposta.get_json()['message']


# --- revisão de incidente ---------------------------------------------------

def test_aceitar_sem_confirmacao_explicita_e_400(app, client):
    contrato_id = _baseline(app)
    incidente_id = _incidente(app, contrato_id)

    resposta = client.post(f'{BASE}/incidentes/{incidente_id}/aceitar', json={})

    assert resposta.status_code == 400
    assert 'confirmação' in resposta.get_json()['message']


def test_aceitar_incidente_inexistente_e_404(client):
    resposta = client.post(
        f'{BASE}/incidentes/99999/aceitar', json={'confirmado': True})

    assert resposta.status_code == 404


def test_aceitar_promove_a_estrutura_observada(app, client, monkeypatch):
    contrato_id = _baseline(app)
    incidente_id = _incidente(app, contrato_id)
    with app.app_context():
        nova = MagicMock(versao=2)
    monkeypatch.setattr(
        contratos_portais.contrato_portal_recon, 'aceitar_incidente',
        lambda *a, **k: nova)

    resposta = client.post(
        f'{BASE}/incidentes/{incidente_id}/aceitar', json={'confirmado': True})

    assert resposta.status_code == 200
    assert resposta.get_json()['versao'] == 2


def test_aceitar_recusa_quando_a_observacao_nao_sustenta_mais(
    app, client, monkeypatch,
):
    contrato_id = _baseline(app)
    incidente_id = _incidente(app, contrato_id)
    monkeypatch.setattr(
        contratos_portais.contrato_portal_recon, 'aceitar_incidente',
        MagicMock(side_effect=contrato_portal_recon.NadaParaRevisarError(
            'A observação de agora não reproduz a mudança registrada; '
            'rode o recon antes de aprovar.')))

    resposta = client.post(
        f'{BASE}/incidentes/{incidente_id}/aceitar', json={'confirmado': True})

    assert resposta.status_code == 409
    assert 'não reproduz' in resposta.get_json()['message']


def test_rejeitar_encerra_o_incidente_sem_promover(app, client):
    contrato_id = _baseline(app)
    incidente_id = _incidente(app, contrato_id)

    resposta = client.post(f'{BASE}/incidentes/{incidente_id}/rejeitar', json={})

    assert resposta.status_code == 200
    with app.app_context():
        incidente = db.session.get(IncidenteContratoPortal, incidente_id)
        assert incidente.estado == 'rejeitado'
        ativa = ContratoPortal.query.filter_by(
            fluxo=FLUXO, alvo=ALVO, estado='ativa').one()
        assert ativa.id == contrato_id
        assert ativa.versao == 1


def test_rejeitar_duas_vezes_e_404(app, client):
    contrato_id = _baseline(app)
    incidente_id = _incidente(app, contrato_id)
    client.post(f'{BASE}/incidentes/{incidente_id}/rejeitar', json={})

    resposta = client.post(f'{BASE}/incidentes/{incidente_id}/rejeitar', json={})

    assert resposta.status_code == 404


# --- restauração ------------------------------------------------------------

def test_restaurar_sem_confirmacao_explicita_e_400(app, client):
    contrato_id = _baseline(app)

    resposta = client.post(f'{BASE}/{contrato_id}/restaurar', json={})

    assert resposta.status_code == 400


def test_restaurar_versao_inexistente_e_404(client):
    resposta = client.post(f'{BASE}/99999/restaurar', json={'confirmado': True})

    assert resposta.status_code == 404


def test_restaurar_a_propria_ativa_e_409(app, client):
    contrato_id = _baseline(app)

    resposta = client.post(
        f'{BASE}/{contrato_id}/restaurar', json={'confirmado': True})

    assert resposta.status_code == 409


def test_restaurar_devolve_a_versao_anterior_apos_autoajuste(app, client):
    contrato_id = _baseline(app)
    with app.app_context():
        base = db.session.get(ContratoPortal, contrato_id)
        resultado = ResultadoComparacaoPortal(
            classificacao='autoativavel',
            diferencas=(_diferenca(
                'seletor_alterado', 'formulario', 'documento',
                'cpfCnpj', 'cpfCnpj-2'),),
            remapeamentos=(RemapeamentoSeletor(
                chave='documento', seletor_tipo_anterior='id',
                seletor_anterior='cpfCnpj', seletor_tipo_novo='id',
                seletor_novo='cpfCnpj-2'),),
        )
        contrato_portal.autoativar(
            base.id, resultado, fingerprint_base=base.fingerprint)

    resposta = client.post(
        f'{BASE}/{contrato_id}/restaurar', json={'confirmado': True})

    assert resposta.status_code == 200
    with app.app_context():
        ativa = ContratoPortal.query.filter_by(
            fluxo=FLUXO, alvo=ALVO, estado='ativa').one()
        elementos = {e.chave: e.seletor for e in ativa.elementos}
        assert elementos['documento'] == 'cpfCnpj'


# --- ativação por observação ------------------------------------------------

def test_baseline_nasce_dos_fatos_observados_nao_dos_declarados(
    app, client, observando,
):
    """Declaração manda em identidade e política; a tela manda nos fatos.

    Achado real: a declaração inventava `assinatura_formulario` e
    `ordem_relativa`, e a primeira comparação acusava `formulario_alterado` nos
    quatro controles — o portal ficava bloqueado para sempre.
    """
    declarada = trabalhista.definicao_baseline()

    resposta = client.post(f'{BASE}/{FLUXO}/{ALVO}/baseline', json={})

    assert resposta.status_code == 201
    with app.app_context():
        ativa = ContratoPortal.query.filter_by(
            fluxo=FLUXO, alvo=ALVO, estado='ativa').one()
        por_chave = {e.chave: e for e in ativa.elementos}
        assinaturas_declaradas = {
            e.assinatura_formulario for e in declarada.elementos}

        # os fatos vieram da observação...
        assert {e.assinatura_formulario for e in ativa.elementos} == {'ab' * 32}
        assert not (
            {e.assinatura_formulario for e in ativa.elementos}
            & assinaturas_declaradas)
        assert sorted(e.ordem_relativa for e in ativa.elementos)[:4] == [10, 11, 12, 13]
        # ...e a política continuou vindo do código
        assert por_chave['documento'].autoajuste_seletor is True
        assert all(
            por_chave[chave].autoajuste_seletor is False
            for chave in ('captcha_imagem', 'captcha_resposta', 'submeter'))


def test_baseline_observada_nao_bloqueia_na_primeira_comparacao(
    app, client, observando,
):
    """O contrato recém-ativado bate com a tela de onde ele saiu."""
    inventario = _inventario_do_cndt()
    client.post(f'{BASE}/{FLUXO}/{ALVO}/baseline', json={})

    with app.app_context():
        ativa = ContratoPortal.query.filter_by(
            fluxo=FLUXO, alvo=ALVO, estado='ativa').one()
        resultado = comparar(
            contrato_portal_preflight.comparavel(ativa), inventario)

    assert resultado.classificacao == COMPATIVEL


def test_controle_declarado_ausente_recusa_e_diz_qual(app, client, observando):
    observando(_inventario_do_cndt(sem=('submeter',)))

    resposta = client.post(f'{BASE}/{FLUXO}/{ALVO}/baseline', json={})

    assert resposta.status_code == 409
    corpo = resposta.get_json()
    assert corpo['faltantes'] == ['submeter']
    assert 'submeter' in corpo['message']
    with app.app_context():
        assert ContratoPortal.query.filter_by(fluxo=FLUXO, alvo=ALVO).count() == 0


def test_tela_de_outra_rota_nao_vira_baseline(app, client, observando):
    observando(_inventario_do_cndt(rota='/outraTela'))

    resposta = client.post(f'{BASE}/{FLUXO}/{ALVO}/baseline', json={})

    assert resposta.status_code == 409
    assert 'rota declarada' in resposta.get_json()['message']
    with app.app_context():
        assert ContratoPortal.query.filter_by(fluxo=FLUXO, alvo=ALVO).count() == 0


def test_baseline_com_emissao_em_curso_responde_423(app, client, monkeypatch):
    monkeypatch.setattr(
        contratos_portais.contrato_portal_recon, 'criar_baseline_observada',
        MagicMock(side_effect=contrato_portal_recon.AlvoOcupadoError(
            'Há uma emissão em curso neste portal. Tente novamente depois.')))

    resposta = client.post(f'{BASE}/{FLUXO}/{ALVO}/baseline', json={})

    assert resposta.status_code == 423


# --- descarte ---------------------------------------------------------------

def test_descartar_sem_confirmacao_explicita_e_400(app, client):
    _baseline(app)

    resposta = client.post(f'{BASE}/{FLUXO}/{ALVO}/descartar', json={})

    assert resposta.status_code == 400
    with app.app_context():
        assert ContratoPortal.query.filter_by(
            fluxo=FLUXO, alvo=ALVO, estado='ativa').count() == 1


def test_descartar_sem_contrato_ativo_e_409(client):
    resposta = client.post(
        f'{BASE}/{FLUXO}/{ALVO}/descartar', json={'confirmado': True})

    assert resposta.status_code == 409


def test_descartar_arquiva_a_versao_e_encerra_incidentes(app, client):
    """Descartar não apaga história: arquiva, e fecha o que ficou pendente."""
    contrato_id = _baseline(app)
    incidente_id = _incidente(app, contrato_id)

    resposta = client.post(
        f'{BASE}/{FLUXO}/{ALVO}/descartar', json={'confirmado': True})

    assert resposta.status_code == 200
    with app.app_context():
        assert ContratoPortal.query.filter_by(
            fluxo=FLUXO, alvo=ALVO, estado='ativa').count() == 0
        arquivada = db.session.get(ContratoPortal, contrato_id)
        assert arquivada.estado == 'arquivada'
        assert arquivada.ativa_unica is None
        assert len(arquivada.elementos) == 4
        assert db.session.get(
            IncidenteContratoPortal, incidente_id).estado == 'rejeitado'


def test_apos_descartar_o_alvo_volta_a_aceitar_baseline(app, client, observando):
    _baseline(app)
    client.post(f'{BASE}/{FLUXO}/{ALVO}/descartar', json={'confirmado': True})

    resposta = client.post(f'{BASE}/{FLUXO}/{ALVO}/baseline', json={})

    assert resposta.status_code == 201
    # Versão nova, não v1 de novo: a história do descarte continua legível.
    assert resposta.get_json()['versao'] == 2
    with app.app_context():
        assert ContratoPortal.query.filter_by(fluxo=FLUXO, alvo=ALVO).count() == 2


def test_painel_volta_a_oferecer_baseline_depois_do_descarte(app, client):
    _baseline(app)
    client.post(f'{BASE}/{FLUXO}/{ALVO}/descartar', json={'confirmado': True})

    alvo = client.get(BASE).get_json()['alvos'][0]

    assert alvo['estado'] == contrato_portal_recon.DESCONHECIDO
    assert alvo['pode_criar_baseline'] is True
    assert alvo['incidentes'] == []


def test_descartar_com_emissao_em_curso_responde_423(app, client):
    _baseline(app)
    lock = contrato_portal_recon.adaptadores_padrao()[0].lock
    lock.acquire()
    try:
        resposta = client.post(
            f'{BASE}/{FLUXO}/{ALVO}/descartar', json={'confirmado': True})
    finally:
        lock.release()

    assert resposta.status_code == 423
    with app.app_context():
        assert ContratoPortal.query.filter_by(
            fluxo=FLUXO, alvo=ALVO, estado='ativa').count() == 1


def test_descartar_durante_pinning_do_contrato_responde_423(app, client):
    _baseline(app)
    with TRABALHISTA_BATCH_LOCK:
        TRABALHISTA_BATCH_STATE['contrato_preflight_em_andamento'] = True
    try:
        resposta = client.post(
            f'{BASE}/{FLUXO}/{ALVO}/descartar', json={'confirmado': True})
    finally:
        with TRABALHISTA_BATCH_LOCK:
            TRABALHISTA_BATCH_STATE['contrato_preflight_em_andamento'] = False

    assert resposta.status_code == 423
    with app.app_context():
        assert ContratoPortal.query.filter_by(
            fluxo=FLUXO, alvo=ALVO, estado='ativa').count() == 1


def test_contrato_guarda_a_tela_inteira_nao_so_os_declarados(
    app, client, observando,
):
    """Achado real: o contrato guardava 4 controles e a tela tinha 10.

    Um minuto depois de ativar, "Verificar agora" acusava `elemento_novo` nos
    seis restantes e bloqueava o portal.
    """
    client.post(f'{BASE}/{FLUXO}/{ALVO}/baseline', json={})

    with app.app_context():
        ativa = ContratoPortal.query.filter_by(
            fluxo=FLUXO, alvo=ALVO, estado='ativa').one()
        por_chave = {e.chave: e for e in ativa.elementos}

    assert len(por_chave) == 7
    # os declarados mantêm a chave e a política
    assert por_chave['documento'].autoajuste_seletor is True
    assert por_chave['submeter'].autoajuste_seletor is False
    # os demais entram pelo seletor e NUNCA autoajustam
    assert 'botao-ouvir-captcha' in por_chave
    assert 'campoEmail' in por_chave
    assert all(
        por_chave[chave].autoajuste_seletor is False
        for chave in ('botao-ouvir-captcha', 'campoEmail', 'botao-enviar'))


def test_observar_de_novo_logo_apos_ativar_nao_acusa_drift(
    app, client, observando,
):
    """A regressão em uma linha: a tela de onde a baseline saiu é compatível."""
    inventario = _inventario_do_cndt()
    client.post(f'{BASE}/{FLUXO}/{ALVO}/baseline', json={})

    with app.app_context():
        ativa = ContratoPortal.query.filter_by(
            fluxo=FLUXO, alvo=ALVO, estado='ativa').one()
        resultado = comparar(
            contrato_portal_preflight.comparavel(ativa), inventario)

    assert resultado.classificacao == COMPATIVEL
    assert resultado.diferencas == ()


def test_controle_novo_na_tela_depois_da_aprovacao_ainda_e_drift(
    app, client, observando,
):
    """Guardar a tela inteira não pode custar a detecção do que É novo."""
    client.post(f'{BASE}/{FLUXO}/{ALVO}/baseline', json={})
    com_novidade = _inventario_do_cndt()
    novo = ElementoInventariado(
        tag='input', tipo='submit', id='botao-emitir-turbo', name='',
        rotulo='Emitir mais rápido', seletor_tipo='id',
        seletor='botao-emitir-turbo', assinatura_formulario='ab' * 32,
        ordem_relativa=99, obrigatorio=False, desabilitado=False,
        somente_leitura=False, visivel=True)

    with app.app_context():
        ativa = ContratoPortal.query.filter_by(
            fluxo=FLUXO, alvo=ALVO, estado='ativa').one()
        resultado = comparar(
            contrato_portal_preflight.comparavel(ativa),
            InventarioPortal(
                host=com_novidade.host, rota=com_novidade.rota,
                etapa=com_novidade.etapa,
                elementos=com_novidade.elementos + (novo,)))

    assert resultado.classificacao == REVISAO
    assert [d.observado for d in resultado.diferencas] == ['botao-emitir-turbo']
