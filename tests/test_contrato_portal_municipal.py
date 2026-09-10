"""Testes do adaptador municipal sem navegador, rede ou emissão."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services import contrato_portal_municipal as municipal
from app.services import contrato_portal_preflight as preflight
from app.services import dryrun_municipio as dryrun
from app.services.contrato_portal_preflight import (
    ElementoSnapshotPortal,
    SnapshotContratoPortal,
)
from app.services.contrato_portal_protocol import (
    ElementoInventariado,
    InventarioPortal,
)


def _municipio(**alteracoes):
    valores = {
        'nome': 'Vila Contrato',
        'url_certidao': 'https://portal.exemplo/cnd',
        'automacao_ativa': True,
        'config_automacao': json.dumps({}),
        'cnpj_field_id': 'campoCnpj',
        'by': 'id',
        'inscricao_field_id': None,
        'inscricao_field_by': None,
        'pre_fill_click_id': None,
        'pre_fill_click_by': None,
    }
    valores.update(alteracoes)
    return SimpleNamespace(**valores)


def _inventario(url='https://portal.exemplo/cnd'):
    return InventarioPortal(
        host='portal.exemplo', rota='/cnd', etapa='formulario',
        elementos=(ElementoInventariado(
            tag='input', tipo='text', id='campoCnpj', name='',
            rotulo='CNPJ', seletor_tipo='id', seletor='campoCnpj',
            assinatura_formulario='f' * 64, ordem_relativa=0,
            obrigatorio=False, desabilitado=False, somente_leitura=False,
            visivel=True),),
        artefato_sanitizado='{"tela":"sintetica"}',
    )


def _relatorio(resultado=dryrun.OK):
    return {
        'municipio': 'Vila Contrato', 'resultado': resultado,
        'checagens': [{'etapa': 'url', 'status': dryrun.OK}],
        'quebrados': [], 'mensagem': None,
    }


def _payload_municipal(**alteracoes):
    payload = {
        'estado': 'ok', 'protocolo': 'https:', 'host': 'portal.exemplo',
        'porta': '', 'caminho': '/cnd', 'consulta': '', 'fragmento': '',
        'estrutura_inacessivel': False,
        'formularios': [{'id': 'form', 'name': 'cnd', 'metodo': 'get',
                         'acao_caminho': '/cnd', 'ordem': 0}],
        'elementos': [{
            'tag': 'input', 'tipo': 'text', 'id': 'campoCnpj', 'name': '',
            'rotulo': 'CNPJ', 'seletor_tipo': 'id', 'seletor': 'campoCnpj',
            'formulario_ordem': 0, 'ordem': 0, 'obrigatorio': False,
            'desabilitado': False, 'somente_leitura': False, 'visivel': True,
            'href_caminho': '',
        }],
    }
    payload.update(alteracoes)
    return payload


class _DriverInventario:
    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.scripts = []

    def execute_script(self, script, seletores):
        self.scripts.append(script)
        if len(self.respostas) > 1:
            return self.respostas.pop(0)
        return self.respostas[0]


def test_adaptador_declara_municipio_e_variante_sem_mudar_configuracao():
    original = {'before_cnpj': [], 'after_cnpj': []}
    municipio = _municipio(config_automacao=json.dumps(original))

    adaptadores = municipal.adaptadores_municipais([municipio])

    assert len(adaptadores) == 1
    adaptador = adaptadores[0]
    assert adaptador.alvo == 'municipio:vilacontrato:padrao'
    assert adaptador.fluxo == municipal.FLUXO_CONTRATO
    assert adaptador.recon_passivo_seguro is True
    assert [item.chave for item in adaptador.definicao().elementos] == ['cnpj']
    assert json.loads(municipio.config_automacao) == original


def test_inventario_municipal_aceita_rota_da_variante_sem_ler_valores():
    payload = _payload_municipal(consulta='?codigo=7', fragmento='#geral')
    driver = _DriverInventario([payload, copy.deepcopy(payload)])

    inventario = municipal.inventariar(
        driver,
        url_esperada='https://portal.exemplo/cnd?codigo=7#geral',
        seletores=(('id', 'campoCnpj'),),
    )

    assert inventario.conhecido is True
    assert inventario.rota == '/cnd?codigo=7#geral'
    assert 'value' not in ''.join(driver.scripts)
    assert 'send_keys' not in ''.join(driver.scripts)


def test_inventario_municipal_instavel_falha_fechado():
    driver = _DriverInventario([
        _payload_municipal(),
        _payload_municipal(elementos=[]),
    ])

    inventario = municipal.inventariar(
        driver, url_esperada='https://portal.exemplo/cnd')

    assert inventario.conhecido is False
    assert 'instável' in inventario.motivo


def test_adaptador_especial_cria_alvos_independentes_e_isola_variantes():
    config = {
        'before_cnpj': [], 'after_cnpj': [],
        'imbe_variantes': {'geral': {
            'url': 'https://portal.exemplo/cnd-geral',
            'cnpj_field_id': 'cnpjGeral', 'by': 'id',
        }},
    }
    municipio = _municipio(
        nome='Imbé',
        url_certidao='https://portal.exemplo/cnd-mobiliario',
        cnpj_field_id='cnpjMobiliario',
        config_automacao=json.dumps(config),
    )
    antes = copy.deepcopy(config)

    adaptadores = municipal.adaptadores_municipais([municipio])

    assert [a.alvo for a in adaptadores] == [
        'municipio:imbe:mobiliario', 'municipio:imbe:geral']
    assert adaptadores[0].definicao().rota == '/cnd-mobiliario'
    assert adaptadores[1].definicao().rota == '/cnd-geral'
    assert json.loads(municipio.config_automacao) == antes


def test_observador_passivo_reusa_dryrun_e_bloqueia_download():
    municipio = _municipio()
    adaptador = municipal.adaptadores_municipais([municipio])[0]
    driver = MagicMock()

    with patch.object(dryrun, 'verificar_municipio', return_value=_relatorio()) as verificar, \
            patch.object(dryrun, 'bloquear_downloads') as bloquear, \
            patch.object(municipal, 'inventariar', return_value=_inventario()) as inventariar:
        inventario = adaptador.observar(driver, None)

    bloquear.assert_called_once_with(driver)
    assert inventario.conhecido is True
    assert verificar.call_args.kwargs['modo_passivo'] is True
    assert verificar.call_args.kwargs['cnpj'] == dryrun.CNPJ_TESTE
    inventariar.assert_called_once()


def test_captcha_ou_preenchimento_fica_desconhecido_e_nao_autoativa():
    municipio = _municipio()
    adaptador = municipal.adaptadores_municipais([municipio])[0]

    with patch.object(dryrun, 'verificar_municipio',
                      return_value=_relatorio(dryrun.PARCIAL)), \
            patch.object(municipal, 'inventariar') as inventariar:
        inventario = adaptador.observar(MagicMock(), None)

    assert inventario.conhecido is False
    assert 'desconhecida' == inventario.estado
    inventariar.assert_not_called()


def test_snapshot_traduz_seletores_em_copia_sem_regravar_config():
    info = {
        'cnpj_field_id': 'campo-antigo', 'by': 'id',
        'pre_fill_click_id': 'radio-antigo', 'pre_fill_click_by': 'id',
    }
    config = {
        'before_cnpj': [{'tipo': 'click', 'by': 'id', 'locator': 'aba-antiga'}],
        'after_cnpj': [],
    }
    info_original = copy.deepcopy(info)
    config_original = copy.deepcopy(config)
    elementos = {
        'cnpj': ElementoSnapshotPortal(
            chave='cnpj', etapa='formulario', papel='entrada', acao='preencher',
            seletor_tipo='css_selector', seletor='#campo-novo'),
        'pre_fill_click': ElementoSnapshotPortal(
            chave='pre_fill_click', etapa='formulario', papel='entrada',
            acao='preencher', seletor_tipo='xpath', seletor='//input[@value="J"]'),
        'before_cnpj[1]': ElementoSnapshotPortal(
            chave='before_cnpj[1]', etapa='formulario', papel='navegacao',
            acao='navegar', seletor_tipo='name', seletor='aba-nova'),
    }
    snapshot = SnapshotContratoPortal(
        contrato_id=1, fluxo='municipal', alvo='municipio:vilacontrato:padrao',
        versao=2, fingerprint='a' * 64, host='portal.exemplo', rota='/cnd',
        elementos=elementos,
    )

    novo_info, novo_config = municipal.aplicar_snapshot(snapshot, info, config)

    assert novo_info['cnpj_field_id'] == '#campo-novo'
    assert novo_info['by'] == 'css_selector'
    assert novo_info['pre_fill_click_by'] == 'xpath'
    assert novo_config['before_cnpj'][0]['locator'] == 'aba-nova'
    assert info == info_original
    assert config == config_original


def test_dryrun_passivo_nao_executa_fill():
    municipio = _municipio(config_automacao=json.dumps({
        'skip_cnpj_fill': True,
        'before_cnpj': [{'tipo': 'fill', 'by': 'id', 'locator': 'cpfCnpj',
                         'value': 'cnpj'}],
    }))

    class DriverFalso:
        def get(self, url):
            self.url = url

    with patch.object(dryrun.steps_engine, 'executar_municipio') as executar:
        relatorio = dryrun.verificar_municipio(
            municipio, DriverFalso(), config=json.loads(municipio.config_automacao),
            modo_passivo=True)

    assert relatorio['resultado'] == dryrun.PARCIAL
    assert 'ação de formulário' in relatorio['mensagem']
    executar.assert_not_called()


def test_preparar_execucao_sem_contrato_preserva_fluxo_legado():
    municipio = _municipio()
    contexto = municipal.contexto(municipio, config={'before_cnpj': []})
    driver = MagicMock()

    with patch.object(preflight, 'buscar_ativo', return_value=None) as buscar:
        assert municipal.preparar_execucao(
            driver, contexto, estado_lote={'contrato_snapshots': {}}) is None

    buscar.assert_called_once_with(
        municipal.FLUXO_CONTRATO, contexto.alvo, obrigatorio=False)
    driver.get.assert_not_called()


def test_preparar_execucao_fixa_um_snapshot_por_variante():
    municipio = _municipio()
    contexto = municipal.contexto(municipio, config={'before_cnpj': []})
    contrato = SimpleNamespace(
        fluxo=municipal.FLUXO_CONTRATO, alvo=contexto.alvo, estado='ativa')
    snapshot = SnapshotContratoPortal(
        contrato_id=7, fluxo=municipal.FLUXO_CONTRATO, alvo=contexto.alvo,
        versao=3, fingerprint='b' * 64, host='portal.exemplo', rota='/cnd',
        elementos={},
    )
    estado = {'contrato_snapshots': {}}

    with patch.object(preflight, 'buscar_ativo', return_value=contrato), \
            patch.object(preflight, 'executar', return_value=snapshot) as executar:
        primeiro = municipal.preparar_execucao(
            MagicMock(), contexto, estado_lote=estado)
        segundo = municipal.preparar_execucao(
            MagicMock(), contexto, estado_lote=estado)

    assert primeiro is snapshot
    assert segundo is snapshot
    executar.assert_called_once()
    assert estado['contrato_snapshots'][contexto.alvo] is snapshot
