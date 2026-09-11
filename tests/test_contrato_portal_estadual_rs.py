"""Testes contratuais do adaptador estadual RS, sem rede ou navegador real."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.automation import emissao
from app.errors import ErrorType
from app.services import batch_engine
from app.services import contrato_portal_estadual_rs as rs
from app.services import contrato_portal_preflight as preflight
from app.services import contrato_portal_recon
from app.services import emissao_service
from app.services.contrato_portal_preflight import (
    ContratoPortalBloqueadoError,
    ElementoSnapshotPortal,
    SnapshotContratoPortal,
)
from app.services.contrato_portal_protocol import InventarioPortal


def _contrato():
    definicao = rs.definicao_baseline()
    return SimpleNamespace(host=definicao.host, rota=definicao.rota)


def _snapshot(**seletores):
    papeis = {
        'cnpj': ('entrada', 'preencher'),
        'desafio': ('captcha', 'observar'),
        'enviar': ('submissao', 'submeter'),
    }
    elementos = {
        chave: ElementoSnapshotPortal(
            chave=chave,
            etapa=rs.ETAPA_CONTRATO,
            papel=papeis[chave][0],
            acao=papeis[chave][1],
            seletor_tipo='css_selector' if chave == 'desafio' else 'id',
            seletor=seletor,
        )
        for chave, seletor in seletores.items()
    }
    return SnapshotContratoPortal(
        contrato_id=12,
        fluxo=rs.FLUXO_CONTRATO,
        alvo=rs.ALVO_CONTRATO,
        versao=2,
        fingerprint='d' * 64,
        host=_contrato().host,
        rota=_contrato().rota,
        elementos=elementos,
    )


def _payload(**alteracoes):
    payload = {
        'estado': 'ok',
        'protocolo': 'https:',
        'host': _contrato().host,
        'porta': '',
        'caminho': _contrato().rota,
        'tem_query': False,
        'tem_fragmento': False,
        'estrutura_inacessivel': False,
        'formularios': [{
            'id': 'form-sintetico',
            'name': 'consulta',
            'metodo': 'post',
            'acao_caminho': _contrato().rota,
            'ordem': 0,
        }],
        'elementos': [
            {
                'tag': 'input', 'tipo': 'text', 'id': '',
                'name': 'campoCnpj', 'rotulo': 'CNPJ',
                'seletor_tipo': 'name', 'seletor': 'campoCnpj',
                'formulario_ordem': 0, 'ordem': 0,
                'obrigatorio': False, 'desabilitado': False,
                'somente_leitura': False, 'visivel': True,
                'href_caminho': '', 'href_tem_query': False,
            },
            {
                'tag': 'altcha-widget', 'tipo': 'altcha-widget', 'id': '',
                'name': '', 'rotulo': 'ALTCHA',
                'seletor_tipo': 'css_selector', 'seletor': 'altcha-widget',
                'formulario_ordem': 0, 'ordem': 1,
                'obrigatorio': False, 'desabilitado': False,
                'somente_leitura': False, 'visivel': True,
                'href_caminho': '', 'href_tem_query': False,
            },
            {
                'tag': 'button', 'tipo': 'submit', 'id': 'btnEnviar',
                'name': '', 'rotulo': 'Enviar',
                'seletor_tipo': 'id', 'seletor': 'btnEnviar',
                'formulario_ordem': 0, 'ordem': 2,
                'obrigatorio': False, 'desabilitado': False,
                'somente_leitura': False, 'visivel': True,
                'href_caminho': '', 'href_tem_query': False,
            },
        ],
    }
    payload.update(alteracoes)
    return payload


class _DriverInventario:
    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.scripts = []

    def execute_script(self, script):
        self.scripts.append(script)
        if len(self.respostas) > 1:
            return self.respostas.pop(0)
        return self.respostas[0]


def test_baseline_rs_declara_cnpj_altcha_e_envio_com_politica_fechada():
    definicao = rs.definicao_baseline()

    assert (definicao.host, definicao.rota, definicao.etapa) == (
        'www.sefaz.rs.gov.br', '/sat/CertidaoSitFiscalSolic.aspx',
        'formulario')
    assert [item.chave for item in definicao.elementos] == [
        'cnpj', 'desafio', 'enviar']
    assert definicao.elementos[0].autoajuste_seletor is True
    assert all(not item.autoajuste_seletor for item in definicao.elementos[1:])
    assert definicao.elementos[1].papel == 'captcha'
    assert definicao.elementos[2].papel == 'submissao'


def test_inventario_rs_inclui_host_altcha_sem_ler_valores():
    driver = _DriverInventario([_payload(), _payload()])

    with patch.object(rs.time, 'sleep'):
        inventario = rs.inventariar(
            driver,
            host_esperado=_contrato().host,
            rota_esperada=_contrato().rota,
        )

    assert inventario.conhecido is True
    assert [item.seletor for item in inventario.elementos] == [
        'campoCnpj', 'altcha-widget', 'btnEnviar']
    assert driver.scripts and all('.value' not in script for script in driver.scripts)
    assert all('click(' not in script for script in driver.scripts)
    assert all('send_keys' not in script for script in driver.scripts)


def test_observador_rs_autentica_somente_a_sessao_bloqueia_download_e_inventa():
    driver = MagicMock()
    inventario = SimpleNamespace(estado='ok')

    with patch.object(rs.dryrun_municipio, 'bloquear_downloads') as bloquear, \
            patch.object(rs, '_entrar_na_tela') as entrar, \
            patch.object(rs, 'inventariar', return_value=inventario) as inventariar:
        resultado = rs.observar_passivo(driver, _contrato())

    assert resultado is inventario
    driver.set_page_load_timeout.assert_called_once_with(30)
    bloquear.assert_called_once_with(driver)
    entrar.assert_called_once_with(driver, _contrato())
    inventariar.assert_called_once()
    driver.click.assert_not_called()
    driver.send_keys.assert_not_called()


def test_observador_rs_falha_de_sessao_fica_desconhecido():
    driver = MagicMock()

    with patch.object(rs.dryrun_municipio, 'bloquear_downloads'), \
            patch.object(rs, '_entrar_na_tela', side_effect=RuntimeError('sessão')):
        resultado = rs.observar_passivo(driver, _contrato())

    assert resultado.conhecido is False
    assert 'autenticar' in resultado.motivo


def test_preflight_rs_nao_classifica_falha_do_driver_como_drift():
    driver = MagicMock()
    inventario = InventarioPortal.desconhecido(
        rs.ETAPA_CONTRATO, 'falha do driver durante a observação RS')

    with patch.object(rs, '_entrar_na_tela'), \
            patch.object(rs, 'inventariar', return_value=inventario):
        with pytest.raises(
                rs.PortalObservacaoTemporariamenteIndisponivelError):
            rs._observar_tela(driver, _contrato())


def test_registry_rs_e_explicito_e_nao_cobre_ufs_nao_adaptadas():
    assert all(a.fluxo != rs.FLUXO_CONTRATO
               for a in contrato_portal_recon.adaptadores_padrao())

    adaptadores = contrato_portal_recon.adaptadores_padrao(
        incluir_estaduais=True)
    estaduais = [a for a in adaptadores if a.fluxo == rs.FLUXO_CONTRATO]

    assert [a.alvo for a in estaduais] == ['rs']
    assert estaduais[0].chave_health == 'Estadual RS'
    assert contrato_portal_recon.adaptador_por_alvo('estadual', 'rs') is not None
    assert contrato_portal_recon.adaptador_por_alvo('estadual', 'sp') is None
    assert contrato_portal_recon.adaptador_por_alvo('estadual', 'mt') is None
    assert contrato_portal_recon.adaptador_por_alvo('estadual', 'ms') is None


def test_snapshot_rs_fornece_seletores_e_incompleto_bloqueia():
    snapshot = _snapshot(
        cnpj='cnpj-atualizado', desafio='altcha-widget', enviar='enviar-atualizado')

    assert rs.localizador(snapshot, 'cnpj') == ('id', 'cnpj-atualizado')
    assert rs.localizador(snapshot, 'desafio') == (
        'css selector', 'altcha-widget')
    assert rs.localizador(snapshot, 'enviar') == (
        'id', 'enviar-atualizado')

    with pytest.raises(ContratoPortalBloqueadoError):
        rs.preparar_execucao(
            MagicMock(), estado_lote={'contrato_snapshot': _snapshot(cnpj='novo')})


def test_preparar_rs_sem_contrato_preserva_fluxo_legado():
    driver = MagicMock()

    with patch.object(preflight, 'buscar_ativo', return_value=None) as buscar, \
            patch.object(rs, '_entrar_na_tela') as entrar:
        resultado = rs.preparar_execucao(driver)

    assert resultado is None
    buscar.assert_called_once_with(
        rs.FLUXO_CONTRATO, rs.ALVO_CONTRATO, obrigatorio=False)
    entrar.assert_not_called()
    driver.get.assert_not_called()


def test_preflight_rs_nao_deixa_download_bloqueado_no_driver_da_emissao():
    """O `deny` do CDP vale pela sessão inteira e o RS baixa PDF de verdade.

    Bloquear download durante o preflight fazia todo item do lote esperar os
    180s de monitoramento por um arquivo que o Chrome estava recusando. A defesa
    continua no recon (`observar_passivo`), que usa driver descartável.
    """
    driver = MagicMock()
    snapshot = _snapshot(cnpj='cnpj', desafio='altcha-widget', enviar='enviar')

    with patch.object(rs.dryrun_municipio, 'bloquear_downloads') as bloquear, \
            patch.object(rs, '_entrar_na_tela'), \
            patch.object(rs, 'inventariar'), \
            patch.object(preflight, 'buscar_ativo',
                         return_value=SimpleNamespace(versao=1)), \
            patch.object(preflight, 'executar', return_value=snapshot):
        assert rs.preparar_execucao(driver) is snapshot

    bloquear.assert_not_called()


def test_preparar_rs_fixa_snapshot_e_nao_reautentica_no_item_seguinte():
    driver = MagicMock()
    snapshot = _snapshot(cnpj='cnpj', desafio='altcha-widget', enviar='enviar')
    estado = {'contrato_snapshot': snapshot}

    with patch.object(rs, '_entrar_na_tela') as entrar:
        primeiro = rs.preparar_execucao(driver, estado_lote=estado)
        segundo = rs.preparar_execucao(driver, estado_lote=estado)

    assert primeiro is snapshot
    assert segundo is snapshot
    entrar.assert_called_once_with(driver, snapshot)
    # Guarda o OBJETO, não `id()`: endereço de memória é reaproveitado depois do
    # GC e um driver novo podia herdar a sessão de um que já morreu.
    assert estado['contrato_sessao_driver'] is driver


def test_preparar_rs_reautentica_quando_o_driver_e_outro():
    """Driver recriado no meio do lote precisa passar pelo certificado de novo."""
    primeiro_driver = MagicMock()
    segundo_driver = MagicMock()
    snapshot = _snapshot(cnpj='cnpj', desafio='altcha-widget', enviar='enviar')
    estado = {'contrato_snapshot': snapshot}

    with patch.object(rs, '_entrar_na_tela') as entrar:
        rs.preparar_execucao(primeiro_driver, estado_lote=estado)
        rs.preparar_execucao(segundo_driver, estado_lote=estado)

    assert entrar.call_count == 2
    assert estado['contrato_sessao_driver'] is segundo_driver


def test_lote_rs_revalida_pagina_com_snapshot_em_cache(app, ids):
    driver = MagicMock()
    snapshot = _snapshot(cnpj='cnpj', desafio='altcha-widget', enviar='enviar')

    with app.app_context(), \
            patch.object(emissao, '_rs_batch_stop_requested', return_value=False), \
            patch.object(rs, 'preparar_execucao', return_value=snapshot), \
            patch.object(emissao, '_rs_garantir_pagina_solicitacao',
                         return_value=False) as garantir:
        resultado = emissao._emitir_estadual_rs_certidao(
            ids['rs'], driver=driver, execution_id='exec-sintetica')

    assert resultado == (
        False, True, 'Não foi possível abrir a página de solicitação da certidão RS.')
    garantir.assert_called_once()
    assert garantir.call_args.kwargs['localizador'] == ('id', 'cnpj')


def test_lote_rs_bloqueia_antes_de_preencher_cnpj(app, ids):
    driver = MagicMock()
    bloqueio = ContratoPortalBloqueadoError('estrutura mudou')

    with app.app_context(), \
            patch.object(emissao, '_rs_batch_stop_requested', return_value=False), \
            patch.object(rs, 'preparar_execucao', side_effect=bloqueio):
        resultado = emissao._emitir_estadual_rs_certidao(
            ids['rs'], driver=driver, execution_id='exec-sintetica')

    assert resultado == (
        False, batch_engine.GRAVE_CONTRATO_PORTAL, str(bloqueio))
    driver.click.assert_not_called()
    driver.send_keys.assert_not_called()


def test_observacao_transitoria_rs_nao_vira_bloqueio_de_contrato(app, ids):
    driver = MagicMock()
    erro = rs.PortalObservacaoTemporariamenteIndisponivelError(
        'portal RS temporariamente indisponível')

    with app.app_context(), \
            patch.object(emissao, '_rs_batch_stop_requested', return_value=False), \
            patch.object(rs, 'preparar_execucao', side_effect=erro), \
            patch.object(emissao.capture, 'capturar_contexto_falha'):
        resultado = emissao._emitir_estadual_rs_certidao(
            ids['rs'], driver=driver, execution_id='exec-sintetica')

    assert resultado[0] is False
    assert resultado[1] is True
    assert resultado[2] == 'Portal indisponivel: Confira se o portal esta no ar e tente novamente mais tarde.'
    assert resultado[1] != batch_engine.GRAVE_CONTRATO_PORTAL


def test_individual_rs_transforma_bloqueio_em_erro_acionavel(app, ids):
    driver = MagicMock()
    bloqueio = ContratoPortalBloqueadoError('estrutura mudou')
    cfg = {
        'tipo_certidao_chave': 'ESTADUAL',
        'estado_emp': 'RS',
        'info_site': {
            'url': 'https://www.sefaz.rs.gov.br/sat/CertidaoSitFiscalSolic.aspx',
            'login_cert_url': 'https://www.sefaz.rs.gov.br/Login/LoginEcacCert.aspx',
            'cnpj_field_id': 'campoCnpj',
            'by': 'name',
        },
        'config_municipal': None,
        'usar_config_municipal': False,
        'cnpj_limpo': '00000000000191',
        'inscricao_limpa': '',
        'usar_rs_autoselect': False,
    }

    with app.app_context(), \
            patch.object(emissao_service, '_abrir_driver_baixar',
                         return_value=(driver, False)), \
            patch.object(rs, 'preparar_execucao', side_effect=bloqueio):
        resultado = emissao_service._executar_automacao_baixar(
            emissao_service.db.session.get(emissao_service.Certidao, ids['rs']),
            cfg)

    assert resultado['erro_acionavel']['code'] == 409
    assert resultado['erro_acionavel']['error_type'] == ErrorType.PORTAL.value
    assert 'Estadual RS' in resultado['erro_acionavel']['acao']
    driver.get.assert_not_called()
    driver.quit.assert_called_once()
