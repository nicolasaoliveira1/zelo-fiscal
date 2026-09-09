"""Inventário sanitizado do piloto Trabalhista, sem navegador real."""
import json

from selenium.common.exceptions import WebDriverException

from app.automation import trabalhista_recon


class DriverFalso:
    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.scripts = []

    def execute_script(self, script):
        self.scripts.append(script)
        if len(self.respostas) > 1:
            return self.respostas.pop(0)
        return self.respostas[0]


def _payload(**sobrescritos):
    payload = {
        'estado': 'ok',
        'protocolo': 'https:',
        'host': 'portal.exemplo.gov.br',
        'porta': '',
        'caminho': '/certidao/emitir',
        'tem_query': False,
        'tem_fragmento': False,
        'estrutura_inacessivel': False,
        'formularios': [{
            'id': 'form-certidao',
            'name': 'certidao',
            'metodo': 'post',
            'acao_caminho': '/api/certidao',
            'ordem': 0,
        }],
        'elementos': [{
            'tag': 'input',
            'tipo': 'text',
            'id': 'documento-atual',
            'name': 'documento',
            'rotulo': 'CPF ou CNPJ',
            'formulario_ordem': 0,
            'ordem': 0,
            'obrigatorio': True,
            'desabilitado': False,
            'somente_leitura': False,
            'visivel': True,
            'href_caminho': '',
            'value': 'DOCUMENTO-SENTINELA-NAO-PERSISTIR',
            'outerHTML': '<input value="SEGREDO-SENTINELA">',
        }],
    }
    payload.update(sobrescritos)
    return payload


def _inventariar(*respostas, **kwargs):
    if not respostas:
        respostas = (_payload(), _payload())
    return trabalhista_recon.inventariar(
        DriverFalso(respostas),
        host_esperado='portal.exemplo.gov.br',
        rota_esperada='/certidao/emitir',
        etapa='formulario',
        **kwargs,
    )


def test_inventario_preserva_metadados_funcionais_e_reconstroi_artefato():
    inventario = _inventariar()
    elemento = inventario.elementos[0]
    formulario = inventario.formularios[0]
    artefato = json.loads(inventario.artefato_sanitizado)

    assert inventario.estado == 'ok'
    assert (inventario.host, inventario.rota, inventario.etapa) == (
        'portal.exemplo.gov.br', '/certidao/emitir', 'formulario')
    assert (formulario.metodo, formulario.acao_caminho) == (
        'post', '/api/certidao')
    assert len(formulario.assinatura) == 64
    assert (
        elemento.tag, elemento.tipo, elemento.id, elemento.name,
        elemento.rotulo, elemento.seletor_tipo, elemento.seletor,
    ) == (
        'input', 'text', 'documento-atual', 'documento',
        'CPF ou CNPJ', 'id', 'documento-atual',
    )
    assert elemento.assinatura_formulario == formulario.assinatura
    assert elemento.obrigatorio is True
    assert artefato['elementos'][0]['seletor'] == 'documento-atual'


def test_inventario_cobre_catalogo_relevante_e_atributos_funcionais():
    base = _payload()['elementos'][0]
    payload = _payload(elementos=[
        {**base, 'tag': 'input', 'tipo': 'text', 'desabilitado': True,
         'somente_leitura': True, 'visivel': False, 'ordem': 1},
        {**base, 'tag': 'select', 'tipo': 'select', 'id': '',
         'name': 'selecao', 'ordem': 2},
        {**base, 'tag': 'textarea', 'tipo': 'textarea', 'id': 'descricao',
         'ordem': 3},
        {**base, 'tag': 'button', 'tipo': 'submit', 'id': 'submeter',
         'ordem': 4},
        {**base, 'tag': 'a', 'tipo': 'a', 'id': 'abrir',
         'href_caminho': '/certidao/abrir', 'ordem': 5},
        {**base, 'tag': 'img', 'tipo': 'img', 'id': 'captcha-imagem',
         'ordem': 6},
    ])

    inventario = _inventariar(payload, payload)

    assert tuple(item.tag for item in inventario.elementos) == (
        'input', 'select', 'textarea', 'button', 'a', 'img')
    assert (
        inventario.elementos[0].desabilitado,
        inventario.elementos[0].somente_leitura,
        inventario.elementos[0].visivel,
        inventario.elementos[0].ordem_relativa,
    ) == (True, True, False, 1)
    assert (
        inventario.elementos[1].seletor_tipo,
        inventario.elementos[1].seletor,
    ) == ('name', 'selecao')
    assert inventario.elementos[4].href_caminho == '/certidao/abrir'


def test_inventario_nunca_le_valores_nem_executa_efeitos():
    sentinela = 'DOCUMENTO-SENTINELA-NAO-PERSISTIR'
    driver = DriverFalso([_payload(), _payload()])

    inventario = trabalhista_recon.inventariar(
        driver,
        host_esperado='portal.exemplo.gov.br',
        rota_esperada='/certidao/emitir',
        etapa='formulario',
    )

    assert sentinela not in repr(inventario)
    assert sentinela not in inventario.artefato_sanitizado
    assert 'SEGREDO-SENTINELA' not in inventario.artefato_sanitizado
    assert len(driver.scripts) == 2
    assert all('.value' not in script for script in driver.scripts)
    assert all('click(' not in script for script in driver.scripts)
    assert all('send_keys' not in script for script in driver.scripts)
    assert all('page_source' not in script for script in driver.scripts)


def test_query_fragmento_host_ou_rota_nao_aprovados_falham_fechado():
    casos = [
        _payload(tem_query=True),
        _payload(tem_fragmento=True),
        _payload(host='portal-outro.exemplo.gov.br'),
        _payload(caminho='/certidao/outra-rota'),
    ]

    for payload in casos:
        inventario = _inventariar(payload, payload)
        assert inventario.estado == 'desconhecida'
        assert inventario.elementos == ()


def test_host_unicode_homografo_nao_e_aceito_como_host_aprovado():
    payload = _payload(host='pórtal.exemplo.gov.br')

    inventario = _inventariar(payload, payload)

    assert inventario.estado == 'desconhecida'
    assert 'host' in inventario.motivo


def test_iframe_shadow_root_ou_dom_marcado_inacessivel_falha_fechado():
    payload = _payload(estrutura_inacessivel=True)

    inventario = _inventariar(payload, payload)

    assert inventario.estado == 'desconhecida'
    assert inventario.elementos == ()
    assert 'estrutura' in inventario.motivo


def test_dom_alterado_entre_as_duas_coletas_falha_fechado():
    primeira = _payload()
    segunda = _payload(elementos=[{
        **primeira['elementos'][0],
        'id': 'documento-alterado-durante-coleta',
    }])

    inventario = _inventariar(primeira, segunda)

    assert inventario.estado == 'desconhecida'
    assert inventario.elementos == ()
    assert 'instável' in inventario.motivo


def test_limites_de_formularios_elementos_e_rotulos_falham_fechado():
    casos = [
        _payload(formularios=[{}] * (trabalhista_recon.MAX_FORMULARIOS + 1)),
        _payload(elementos=[{}] * (trabalhista_recon.MAX_ELEMENTOS + 1)),
        _payload(elementos=[{
            **_payload()['elementos'][0],
            'rotulo': 'x' * (trabalhista_recon.MAX_ROTULO + 1),
        }]),
    ]

    for payload in casos:
        inventario = _inventariar(payload, payload)
        assert inventario.estado == 'desconhecida'
        assert inventario.elementos == ()
        assert 'limite' in inventario.motivo


def test_elementos_duplicados_permanecem_visiveis_para_o_comparador():
    original = _payload()['elementos'][0]
    payload = _payload(elementos=[
        {**original, 'visivel': False, 'ordem': 0},
        {**original, 'visivel': True, 'ordem': 1},
    ])

    inventario = _inventariar(payload, payload)

    assert len(inventario.elementos) == 2
    assert [elemento.visivel for elemento in inventario.elementos] == [False, True]
    assert [elemento.ordem_relativa for elemento in inventario.elementos] == [0, 1]


def test_falha_do_driver_ou_payload_invalido_resulta_desconhecido():
    class DriverComFalha:
        def execute_script(self, script):
            raise WebDriverException('sessão encerrada')

    falha = trabalhista_recon.inventariar(
        DriverComFalha(),
        host_esperado='portal.exemplo.gov.br',
        rota_esperada='/certidao/emitir',
        etapa='formulario',
    )
    invalido = _inventariar('não-é-json', 'não-é-json')

    assert falha.estado == 'desconhecida'
    assert 'driver' in falha.motivo
    assert invalido.estado == 'desconhecida'
    assert 'payload' in invalido.motivo
