"""Integração do contrato adaptativo no piloto Trabalhista, sem portal real."""
from types import MappingProxyType
from threading import Lock
from unittest.mock import MagicMock

import pytest
from selenium.webdriver.common.by import By

from app import db
from app.automation import emissao, trabalhista
from app.automation.trabalhista_recon import ElementoInventariado, InventarioPortal
from app.automation.sites import SITES_CERTIDOES
from app.models import Usuario
from app.services import (
    batch_engine,
    contrato_portal,
    contrato_portal_preflight,
    emissao_service,
)
from app.services.contrato_portal_drift import AUTOATIVAVEL, REVISAO, comparar


def _ativar_baseline():
    usuario = Usuario(
        username='admin_piloto', senha_hash='hash-sintetico', papel='admin')
    db.session.add(usuario)
    db.session.commit()
    return contrato_portal.criar_baseline(
        fluxo=trabalhista.FLUXO_CONTRATO,
        alvo=trabalhista.ALVO_CONTRATO,
        definicao=trabalhista.definicao_baseline(),
        usuario_id=usuario.id,
    )


def _snapshot(**seletores):
    elementos = {}
    for item in trabalhista.definicao_baseline().elementos:
        elementos[item.chave] = contrato_portal_preflight.ElementoSnapshotPortal(
            chave=item.chave, etapa=item.etapa, papel=item.papel, acao=item.acao,
            seletor_tipo='id', seletor=seletores.get(item.chave, item.seletor))
    return contrato_portal_preflight.SnapshotContratoPortal(
        contrato_id=7, fluxo=trabalhista.FLUXO_CONTRATO,
        alvo=trabalhista.ALVO_CONTRATO, versao=3,
        fingerprint='f' * 64, host=trabalhista.HOST_CNDT,
        rota=trabalhista.ROTA_CNDT,
        elementos=MappingProxyType(elementos),
    )


def test_baseline_declarada_tem_quatro_controles_e_politica_fechada():
    definicao = trabalhista.definicao_baseline()
    por_chave = {item.chave: item for item in definicao.elementos}

    assert (definicao.host, definicao.rota, definicao.etapa) == (
        'cndt-certidao.tst.jus.br', '/gerarCertidao', 'formulario')
    assert tuple(por_chave) == (
        'documento', 'captcha_imagem', 'captcha_resposta', 'submeter')
    assert por_chave['documento'].autoajuste_seletor is True
    assert all(
        por_chave[chave].autoajuste_seletor is False
        for chave in ('captcha_imagem', 'captcha_resposta', 'submeter'))
    assert 'abrir_emissao' not in por_chave


def test_mapa_legado_aponta_para_portal_atual():
    config = SITES_CERTIDOES['TRABALHISTA']

    assert config['url'] == 'https://cndt-certidao.tst.jus.br/gerarCertidao'
    assert config['cnpj_field_id'] == 'cpfCnpj'
    assert config['captcha_img_id'] == 'captcha-imagem'
    assert config['captcha_input_id'] == 'captcha-resposta'
    assert config['submit_id'] == 'botao-emitir'
    assert 'pre_fill_click_id' not in config


@pytest.mark.parametrize(('chave', 'classificacao'), [
    ('documento', AUTOATIVAVEL),
    ('captcha_imagem', REVISAO),
    ('captcha_resposta', REVISAO),
    ('submeter', REVISAO),
])
def test_politica_da_baseline_so_autoajusta_documento(chave, classificacao):
    baseline = trabalhista.definicao_baseline()
    elementos = tuple(ElementoInventariado(
        tag=item.tag,
        tipo=item.tipo,
        id=(f'{item.seletor}-alterado' if item.chave == chave else item.seletor),
        name='',
        rotulo=item.rotulo,
        seletor_tipo='id',
        seletor=(f'{item.seletor}-alterado' if item.chave == chave else item.seletor),
        assinatura_formulario=item.assinatura_formulario,
        ordem_relativa=item.ordem_relativa,
        obrigatorio=item.obrigatorio,
        desabilitado=False,
        somente_leitura=item.somente_leitura,
        visivel=item.visivel,
    ) for item in baseline.elementos)
    inventario = InventarioPortal(
        host=baseline.host, rota=baseline.rota, etapa=baseline.etapa,
        elementos=elementos)

    resultado = comparar(baseline, inventario)

    assert resultado.classificacao == classificacao


def test_sem_contrato_ativo_preparacao_preserva_legado(app, ids):
    with app.app_context():
        driver = MagicMock()

        snapshot = trabalhista.preparar_execucao(
            driver, url_legada='https://legado.exemplo/entrada')

        assert snapshot is None
        driver.get.assert_called_once_with('https://legado.exemplo/entrada')
        driver.execute_script.assert_not_called()


def test_contrato_ativo_navega_pela_rota_e_fixa_snapshot(app, ids, monkeypatch):
    with app.app_context():
        baseline = _ativar_baseline()
        driver = MagicMock()
        esperado = _snapshot()
        chamadas = []
        monkeypatch.setattr(
            trabalhista.contrato_portal_preflight, 'executar',
            lambda **kwargs: chamadas.append(kwargs) or esperado)
        estado = {'contrato_snapshot': None}

        obtido = trabalhista.preparar_execucao(
            driver, url_legada='https://legado.exemplo/entrada',
            estado_lote=estado, execution_id='exec-sintetica')

        assert obtido is esperado
        assert estado['contrato_snapshot'] is esperado
        driver.get.assert_called_once_with(
            'https://cndt-certidao.tst.jus.br/gerarCertidao')
        assert chamadas[0]['fluxo'] == 'trabalhista'
        assert chamadas[0]['alvo'] == 'cndt'
        assert chamadas[0]['execution_id'] == 'exec-sintetica'
        assert chamadas[0]['contrato_ativo'].id == baseline.id


def test_snapshot_fixado_nao_relê_ativa_nem_reobserva(app, ids, monkeypatch):
    with app.app_context():
        fixado = _snapshot(documento='documento-fixado')
        estado = {'contrato_snapshot': fixado}
        monkeypatch.setattr(
            trabalhista.contrato_portal_preflight, 'buscar_ativo',
            lambda *args, **kwargs: pytest.fail('releu ativa'))
        monkeypatch.setattr(
            trabalhista.contrato_portal_preflight, 'executar',
            lambda **kwargs: pytest.fail('reobservou portal'))
        driver = MagicMock()

        obtido = trabalhista.preparar_execucao(
            driver, url_legada='https://legado.exemplo/entrada',
            estado_lote=estado)

        assert obtido is fixado
        driver.get.assert_called_once_with(
            'https://cndt-certidao.tst.jus.br/gerarCertidao')


def test_snapshot_incompleto_bloqueia_antes_de_navegar(app, ids, monkeypatch):
    with app.app_context():
        completo = _snapshot()
        elementos = dict(completo.elementos)
        del elementos['submeter']
        incompleto = contrato_portal_preflight.SnapshotContratoPortal(
            contrato_id=completo.contrato_id,
            fluxo=completo.fluxo,
            alvo=completo.alvo,
            versao=completo.versao,
            fingerprint=completo.fingerprint,
            host=completo.host,
            rota=completo.rota,
            elementos=MappingProxyType(elementos),
        )
        driver = MagicMock()
        falha_breaker = MagicMock()
        monkeypatch.setattr(
            trabalhista.circuit_breaker, 'registrar_falha', falha_breaker)

        with pytest.raises(contrato_portal_preflight.ContratoPortalBloqueadoError):
            trabalhista.preparar_execucao(
                driver,
                url_legada='https://legado.exemplo/entrada',
                estado_lote={'contrato_snapshot': incompleto},
            )

        driver.get.assert_not_called()
        falha_breaker.assert_called_once_with(
            'Trabalhista', 'Contrato Trabalhista ativo está incompleto.')


def test_resolver_captcha_usa_exclusivamente_snapshot(monkeypatch):
    snapshot = _snapshot(
        captcha_imagem='imagem-snapshot',
        captcha_resposta='resposta-snapshot',
        submeter='submit-snapshot')
    localizados = []
    submits = []
    monkeypatch.setattr(
        trabalhista, '_localizar',
        lambda driver, locator, timeout=10: localizados.append(locator) or MagicMock())
    monkeypatch.setattr(
        trabalhista, '_clicar_submit',
        lambda driver, locator: submits.append(locator) or True)
    monkeypatch.setattr(
        trabalhista.captcha_img, 'resolver_captcha_imagem',
        lambda *args, **kwargs: (True, 'resposta'))
    monkeypatch.setattr(trabalhista, '_aguardar_sucesso', lambda *args: True)

    resultado = trabalhista.resolver_captcha_e_submeter(
        MagicMock(), {}, lambda: True, snapshot=snapshot)

    assert resultado == (True, None)
    assert localizados == [(By.ID, 'imagem-snapshot'), (By.ID, 'resposta-snapshot')]
    assert submits == [(By.ID, 'submit-snapshot')]


def test_lote_bloqueia_antes_de_preencher_e_solver(app, ids, monkeypatch):
    with app.app_context():
        _ativar_baseline()
        solver = MagicMock()
        wait = MagicMock()
        monkeypatch.setattr(emissao, 'WebDriverWait', lambda *args: wait)
        monkeypatch.setattr(
            emissao.trabalhista, 'preparar_execucao',
            MagicMock(side_effect=contrato_portal_preflight.ContratoPortalBloqueadoError(
                'revisao')))
        monkeypatch.setattr(
            emissao.trabalhista, 'resolver_captcha_e_submeter', solver)

        resultado = emissao._emitir_trabalhista_certidao(
            ids['trabalhista'], driver=MagicMock(), execution_id='exec-sintetica')

        assert resultado == (
            False, batch_engine.GRAVE_CONTRATO_PORTAL,
            'A estrutura do portal mudou e a emissão foi bloqueada para revisão.')
        wait.until.assert_not_called()
        solver.assert_not_called()


def test_individual_bloqueia_antes_de_preencher_e_mantem_captcha_manual(
    app, ids, monkeypatch,
):
    with app.test_request_context('/'):
        _ativar_baseline()
        driver = MagicMock()
        monkeypatch.setattr(
            emissao_service, '_abrir_driver_baixar', lambda *args: (driver, False))
        monkeypatch.setattr(
            emissao_service.trabalhista, 'preparar_execucao',
            MagicMock(side_effect=contrato_portal_preflight.ContratoPortalBloqueadoError(
                'revisao')))
        monitor = MagicMock()
        monkeypatch.setattr(emissao_service, '_baixar_monitorar_download', monitor)
        certidao = db.session.get(emissao.Certidao, ids['trabalhista'])
        cfg, erro = emissao_service._montar_config_baixar(certidao)

        resultado = emissao_service._executar_automacao_baixar(certidao, cfg)

        assert erro is None
        assert resultado['erro_acionavel']['message'] == (
            'A estrutura do portal mudou e a emissão foi bloqueada para revisão.')
        assert resultado['erro_acionavel']['code'] == 409
        monitor.assert_not_called()


def test_reset_de_lote_descarta_snapshot_anterior():
    estado = batch_engine.batch_state_defaults()
    estado['contrato_snapshot'] = _snapshot()

    batch_engine.reset_batch_state(estado)

    assert estado['contrato_snapshot'] is None


def test_bloqueio_estrutural_para_agendador_sem_duplicar_breaker(
    app, ids, monkeypatch,
):
    estado = batch_engine.batch_state_defaults()
    estado.update({
        'status': 'running', 'ids': [ids['trabalhista']], 'total': 1,
        'execution_id': 'exec-sintetica',
    })
    falhas_breaker = MagicMock()
    monkeypatch.setattr(batch_engine.circuit_breaker, 'registrar_falha', falhas_breaker)

    batch_engine.run_batch_loop(
        app,
        lock=Lock(),
        state=estado,
        emit_fn=lambda *args: (
            False,
            batch_engine.GRAVE_CONTRATO_PORTAL,
            'A estrutura do portal mudou e foi registrada pelo preflight.',
        ),
        nome_lote='Trabalhista',
        curto='Trabalhista',
        tag='TRABALHISTA-LOTE',
        event_prefix='trabalhista_teste',
        parar_em_grave=False,
        alvo_lote='Trabalhista',
    )

    assert estado['status'] == 'error'
    assert estado['index'] == 0
    assert estado['falhas'] == 0
    falhas_breaker.assert_not_called()


def test_estadual_rs_tolera_falha_de_preenchimento_sem_snapshot(
    app, ids, monkeypatch,
):
    """O RS entra por login com certificado e nunca fixa contrato; a falha ao

    preencher continua sendo engolida, sem esbarrar no snapshot do Trabalhista.
    """
    with app.test_request_context('/'):
        driver = MagicMock()
        wait = MagicMock()
        wait.until.side_effect = Exception('campo ausente')
        monkeypatch.setattr(
            emissao_service, '_abrir_driver_baixar', lambda *args: (driver, False))
        monkeypatch.setattr(emissao_service, 'WebDriverWait', lambda *args: wait)
        monkeypatch.setattr(
            emissao_service, '_login_certificado_rs', MagicMock())
        monkeypatch.setattr(
            emissao_service, '_baixar_executar_acao', MagicMock())
        monitor = MagicMock(return_value=('salvo', {
            'rs_estadual_classificacao': None, 'rs_estadual_msg': None,
            'municipal_pdf_classificacao': None, 'municipal_pdf_msg': None,
            'certidao_pdf_classificacao': None, 'certidao_pdf_msg': None,
            'pdf_invalida_msg': None,
        }))
        monkeypatch.setattr(emissao_service, '_baixar_monitorar_download', monitor)
        certidao = db.session.get(emissao.Certidao, ids['rs'])
        cfg, erro = emissao_service._montar_config_baixar(certidao)

        resultado = emissao_service._executar_automacao_baixar(certidao, cfg)

        assert erro is None
        assert not resultado.get('erro_acionavel')
        monitor.assert_called_once()
