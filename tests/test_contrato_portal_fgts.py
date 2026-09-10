"""Testes do adaptador FGTS sem navegador, rede ou emissão."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from selenium.common.exceptions import WebDriverException

from app.automation import emissao
from app.errors import ErrorType
from app.services import contrato_portal_fgts as fgts
from app.services import batch_engine
from app.services import emissao_service
from app.services import contrato_portal_preflight as preflight
from app.services import contrato_portal_recon
from app.services.contrato_portal_preflight import (
    ContratoPortalBloqueadoError,
    ElementoSnapshotPortal,
    SnapshotContratoPortal,
)
from app.services.contrato_portal_protocol import InventarioPortal


def _contrato():
    definicao = fgts.definicao_baseline()
    return SimpleNamespace(host=definicao.host, rota=definicao.rota)


def _snapshot(**seletores):
    elementos = {
        chave: ElementoSnapshotPortal(
            chave=chave, etapa=fgts.ETAPA_CONTRATO,
            papel='entrada' if chave == 'cnpj' else 'submissao',
            acao='preencher' if chave == 'cnpj' else 'submeter',
            seletor_tipo='id', seletor=seletor,
        )
        for chave, seletor in seletores.items()
    }
    return SnapshotContratoPortal(
        contrato_id=9, fluxo=fgts.FLUXO_CONTRATO, alvo=fgts.ALVO_CONTRATO,
        versao=2, fingerprint='c' * 64,
        host=_contrato().host, rota=_contrato().rota,
        elementos=elementos,
    )


def test_baseline_fgts_declara_controles_e_politica_por_alvo():
    definicao = fgts.definicao_baseline()

    assert (definicao.host, definicao.rota, definicao.etapa) == (
        'consulta-crf.caixa.gov.br',
        '/consultacrf/pages/consultaEmpregador.jsf',
        'formulario',
    )
    assert [item.chave for item in definicao.elementos] == [
        'cnpj', 'consultar', 'certificado', 'visualizar']
    assert definicao.elementos[0].autoajuste_seletor is True
    assert all(not item.autoajuste_seletor
               for item in definicao.elementos[1:])
    assert all(item.papel == 'submissao'
               for item in definicao.elementos[1:])


def test_adaptador_fgts_e_registrado_com_lock_e_health_proprios():
    adaptador = fgts.adaptador_fgts()

    assert adaptador.fluxo == fgts.FLUXO_CONTRATO
    assert adaptador.alvo == fgts.ALVO_CONTRATO
    assert adaptador.nome == 'FGTS (Caixa)'
    assert adaptador.chave_health == 'FGTS'
    assert adaptador.lock is not None
    assert adaptador.recon_passivo_seguro is True


def test_registry_expande_fgts_somente_quando_a_onda_e_habilitada():
    assert all(
        adaptador.fluxo != fgts.FLUXO_CONTRATO
        for adaptador in contrato_portal_recon.adaptadores_padrao())

    adaptadores = contrato_portal_recon.adaptadores_padrao(incluir_fgts=True)

    assert [adaptador.alvo for adaptador in adaptadores
            if adaptador.fluxo == fgts.FLUXO_CONTRATO] == [fgts.ALVO_CONTRATO]
    assert contrato_portal_recon.adaptador_por_alvo(
        fgts.FLUXO_CONTRATO, fgts.ALVO_CONTRATO).alvo == fgts.ALVO_CONTRATO


def test_observador_fgts_navega_bloqueia_download_e_so_inventa():
    driver = MagicMock()
    inventario = InventarioPortal(
        host=_contrato().host, rota=_contrato().rota,
        etapa=fgts.ETAPA_CONTRATO)

    with patch.object(fgts.dryrun_municipio, 'bloquear_downloads') as bloquear, \
            patch.object(fgts, 'inventariar', return_value=inventario) as inventariar:
        resultado = fgts.observar_passivo(driver, _contrato())

    assert resultado is inventario
    driver.set_page_load_timeout.assert_called_once_with(30)
    driver.get.assert_called_once_with(
        f'https://{_contrato().host}{_contrato().rota}')
    bloquear.assert_called_once_with(driver)
    inventariar.assert_called_once()
    driver.click.assert_not_called()
    driver.send_keys.assert_not_called()


def test_observador_fgts_falha_de_navegacao_fica_desconhecido():
    driver = MagicMock()
    driver.get.side_effect = WebDriverException('driver indisponível')

    with patch.object(fgts.dryrun_municipio, 'bloquear_downloads'):
        resultado = fgts.observar_passivo(driver, _contrato())

    assert resultado.conhecido is False
    assert resultado.estado == 'desconhecida'
    assert 'falha ao abrir' in resultado.motivo


def test_snapshot_fgts_fornece_localizadores_atualizados():
    snapshot = _snapshot(
        cnpj='cnpj-novo', consultar='consultar-novo',
        certificado='certificado-novo', visualizar='visualizar-novo')

    assert fgts.localizador(snapshot, 'cnpj') == ('id', 'cnpj-novo')
    assert fgts.localizador(snapshot, 'visualizar') == (
        'id', 'visualizar-novo')


def test_snapshot_fgts_incompleto_bloqueia_antes_da_navegacao():
    snapshot = _snapshot(cnpj='cnpj-novo')

    with pytest.raises(ContratoPortalBloqueadoError):
        fgts.preparar_execucao(
            MagicMock(), estado_lote={'contrato_snapshot': snapshot})


def test_preparar_fgts_sem_contrato_preserva_executor_legado():
    driver = MagicMock()

    with patch.object(preflight, 'buscar_ativo', return_value=None) as buscar:
        resultado = fgts.preparar_execucao(driver)

    assert resultado is None
    buscar.assert_called_once_with(
        fgts.FLUXO_CONTRATO, fgts.ALVO_CONTRATO, obrigatorio=False)
    driver.get.assert_not_called()


def test_preparar_fgts_fixa_snapshot_no_lote_e_nao_reobserva():
    driver = MagicMock()
    ativo = SimpleNamespace(
        fluxo=fgts.FLUXO_CONTRATO, alvo=fgts.ALVO_CONTRATO, estado='ativa')
    snapshot = _snapshot(
        cnpj='cnpj', consultar='consultar', certificado='certificado',
        visualizar='visualizar')
    estado = {'contrato_snapshot': None}

    with patch.object(preflight, 'buscar_ativo', return_value=ativo) as buscar, \
            patch.object(preflight, 'executar', return_value=snapshot) as executar:
        primeiro = fgts.preparar_execucao(
            driver, estado_lote=estado, execution_id='exec-sintetica')
        segundo = fgts.preparar_execucao(
            driver, estado_lote=estado, execution_id='exec-sintetica')

    assert primeiro is snapshot
    assert segundo is snapshot
    assert estado['contrato_snapshot'] is snapshot
    buscar.assert_called_once_with(
        fgts.FLUXO_CONTRATO, fgts.ALVO_CONTRATO, obrigatorio=False)
    executar.assert_called_once()
    driver.get.assert_called_once_with(
        f'https://{snapshot.host}{snapshot.rota}')


def test_lote_fgts_bloqueia_antes_de_preencher_cnpj(app, ids):
    driver = MagicMock()
    bloqueio = ContratoPortalBloqueadoError('estrutura mudou')

    with app.app_context(), \
            patch.object(emissao, '_fgts_stop_requested', return_value=False), \
            patch.object(fgts, 'preparar_execucao', side_effect=bloqueio):
        resultado = emissao._emitir_fgts_certidao(
            ids['fgts'], driver=driver, execution_id='exec-sintetica')

    assert resultado == (
        False, batch_engine.GRAVE_CONTRATO_PORTAL, str(bloqueio))
    driver.click.assert_not_called()
    driver.send_keys.assert_not_called()


def test_individual_fgts_transforma_bloqueio_em_erro_acionavel(app, ids):
    driver = MagicMock()
    certidao = ids['fgts']
    bloqueio = ContratoPortalBloqueadoError('estrutura mudou')
    cfg = {
        'tipo_certidao_chave': 'FGTS',
        'estado_emp': 'RS',
        'info_site': {'url': 'https://portal.exemplo/fgts'},
        'config_municipal': None,
        'usar_config_municipal': False,
        'cnpj_limpo': '00000000000191',
        'inscricao_limpa': '',
        'usar_rs_autoselect': False,
    }

    with app.app_context(), \
            patch.object(emissao_service, '_abrir_driver_baixar',
                         return_value=(driver, False)), \
            patch.object(fgts, 'preparar_execucao', side_effect=bloqueio):
        resultado = emissao_service._executar_automacao_baixar(
            emissao_service.db.session.get(emissao_service.Certidao, certidao),
            cfg)

    assert resultado['erro_acionavel']['code'] == 409
    assert resultado['erro_acionavel']['error_type'] == ErrorType.PORTAL.value
    assert 'FGTS' in resultado['erro_acionavel']['acao']
    driver.get.assert_not_called()
    driver.quit.assert_called_once()
