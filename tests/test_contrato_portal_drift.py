"""Comparação determinística dos contratos de portais."""
from dataclasses import replace

import pytest

from app.automation.trabalhista_recon import (
    ElementoInventariado,
    InventarioPortal,
)
from app.services.contrato_portal_drift import (
    AUTOATIVAVEL,
    COMPATIVEL,
    DESCONHECIDA,
    REVISAO,
    ContratoComparavel,
    ElementoContratoComparavel,
    comparar,
)


FORMULARIO = 'f' * 64


def _esperado(**dados):
    valores = {
        'chave': 'documento',
        'etapa': 'formulario',
        'papel': 'entrada',
        'acao': 'preencher',
        'seletor_tipo': 'id',
        'seletor': 'documento-antigo',
        'tag': 'input',
        'tipo': 'text',
        'rotulo': 'CPF ou CNPJ',
        'assinatura_formulario': FORMULARIO,
        'ordem_relativa': 1,
        'obrigatorio': True,
        'visivel': True,
        'somente_leitura': False,
        'desabilitado': False,
        'autoajuste_seletor': True,
    }
    valores.update(dados)
    return ElementoContratoComparavel(**valores)


def _observado(**dados):
    valores = {
        'tag': 'input',
        'tipo': 'text',
        'id': 'documento-antigo',
        'name': 'documento',
        'rotulo': 'CPF ou CNPJ',
        'seletor_tipo': 'id',
        'seletor': 'documento-antigo',
        'assinatura_formulario': FORMULARIO,
        'ordem_relativa': 1,
        'obrigatorio': True,
        'desabilitado': False,
        'somente_leitura': False,
        'visivel': True,
    }
    valores.update(dados)
    return ElementoInventariado(**valores)


def _contrato(*elementos, **dados):
    valores = {
        'host': 'portal.exemplo.gov.br',
        'rota': '/certidao/emitir',
        'etapa': 'formulario',
        'elementos': tuple(elementos or (_esperado(),)),
    }
    valores.update(dados)
    return ContratoComparavel(**valores)


def _inventario(*elementos, **dados):
    valores = {
        'host': 'portal.exemplo.gov.br',
        'rota': '/certidao/emitir',
        'etapa': 'formulario',
        'elementos': tuple(elementos or (_observado(),)),
    }
    valores.update(dados)
    return InventarioPortal(**valores)


def test_estrutura_identica_e_compativel_sem_nova_versao():
    resultado = comparar(_contrato(), _inventario())

    assert resultado.classificacao == COMPATIVEL
    assert resultado.diferencas == ()
    assert resultado.remapeamentos == ()


def test_troca_isolada_inequivoca_de_seletor_e_autoativavel():
    resultado = comparar(
        _contrato(),
        _inventario(_observado(
            id='documento-atual', seletor='documento-atual')))

    assert resultado.classificacao == AUTOATIVAVEL
    assert [item.tipo for item in resultado.diferencas] == ['seletor_alterado']
    assert resultado.remapeamentos[0].chave == 'documento'
    assert resultado.remapeamentos[0].seletor_anterior == 'documento-antigo'
    assert resultado.remapeamentos[0].seletor_novo == 'documento-atual'


@pytest.mark.parametrize(
    ('mudanca_contrato', 'mudanca_inventario', 'tipo_esperado'),
    [
        ({'host': 'outro.exemplo.gov.br'}, {}, 'host_alterado'),
        ({'rota': '/outra-rota'}, {}, 'rota_alterada'),
        ({'etapa': 'entrada'}, {}, 'etapa_alterada'),
        ({}, {'assinatura_formulario': 'a' * 64}, 'formulario_alterado'),
        ({}, {'tag': 'textarea'}, 'tag_alterada'),
        ({}, {'tipo': 'number'}, 'tipo_alterado'),
        ({}, {'rotulo': 'Outro documento'}, 'rotulo_alterado'),
        ({}, {'ordem_relativa': 2}, 'sequencia_alterada'),
        ({}, {'obrigatorio': False}, 'obrigatoriedade_alterada'),
        ({}, {'visivel': False}, 'visibilidade_alterada'),
        ({}, {'somente_leitura': True}, 'somente_leitura_alterada'),
        ({}, {'desabilitado': True}, 'habilitacao_alterada'),
    ],
)
def test_cada_gate_estrutural_falso_exige_revisao(
    mudanca_contrato, mudanca_inventario, tipo_esperado,
):
    esperado = _esperado()
    contrato = _contrato(esperado, **mudanca_contrato)
    observado = _observado(
        id='documento-atual', seletor='documento-atual',
        **mudanca_inventario)

    resultado = comparar(contrato, _inventario(observado))

    assert resultado.classificacao == REVISAO
    assert tipo_esperado in {item.tipo for item in resultado.diferencas}
    assert resultado.remapeamentos == ()


def test_papel_ou_acao_diferente_nunca_autoativa():
    observado = _observado(
        id='documento-atual', seletor='documento-atual')

    papel = comparar(
        _contrato(_esperado(papel='submissao')),
        _inventario(observado))
    acao = comparar(
        _contrato(_esperado(acao='submeter')),
        _inventario(observado))

    assert papel.classificacao == REVISAO
    assert 'papel_alterado' in {item.tipo for item in papel.diferencas}
    assert acao.classificacao == REVISAO
    assert 'acao_alterada' in {item.tipo for item in acao.diferencas}


@pytest.mark.parametrize(
    ('chave', 'papel', 'acao', 'tag', 'tipo'),
    [
        ('captcha_imagem', 'captcha', 'observar', 'img', 'img'),
        ('captcha_resposta', 'entrada', 'preencher', 'input', 'text'),
        ('abrir_emissao', 'navegacao', 'navegar', 'a', 'a'),
        ('submeter', 'submissao', 'submeter', 'button', 'submit'),
        ('baixar', 'download', 'download', 'a', 'a'),
    ],
)
def test_denylist_absoluta_impede_autoativacao(
    chave, papel, acao, tag, tipo,
):
    esperado = _esperado(
        chave=chave, papel=papel, acao=acao, tag=tag, tipo=tipo,
        rotulo=f'Controle {chave}', autoajuste_seletor=True)
    observado = _observado(
        tag=tag, tipo=tipo, rotulo=f'Controle {chave}',
        id='seletor-atual', seletor='seletor-atual')

    resultado = comparar(_contrato(esperado), _inventario(observado))

    assert resultado.classificacao == REVISAO
    assert resultado.remapeamentos == ()


def test_politica_default_falso_impede_autoativacao():
    resultado = comparar(
        _contrato(_esperado(autoajuste_seletor=False)),
        _inventario(_observado(
            id='documento-atual', seletor='documento-atual')))

    assert resultado.classificacao == REVISAO
    assert resultado.remapeamentos == ()


def test_dois_candidatos_visiveis_sao_ambiguidade():
    resultado = comparar(
        _contrato(),
        _inventario(
            _observado(id='candidato-a', seletor='candidato-a'),
            _observado(id='candidato-b', seletor='candidato-b')))

    assert resultado.classificacao == REVISAO
    assert [item.tipo for item in resultado.diferencas] == ['ambiguidade']
    assert resultado.remapeamentos == ()


def test_copia_oculta_da_mesma_identidade_nao_cria_ambiguidade():
    resultado = comparar(
        _contrato(),
        _inventario(
            _observado(visivel=False, ordem_relativa=0),
            _observado(visivel=True, ordem_relativa=1)))

    assert resultado.classificacao == COMPATIVEL
    assert resultado.diferencas == ()


@pytest.mark.parametrize('rotulo', ['', 'Campo'])
def test_rotulo_ausente_ou_generico_exige_revisao(rotulo):
    resultado = comparar(
        _contrato(_esperado(rotulo=rotulo)),
        _inventario(_observado(
            rotulo=rotulo, id='documento-atual',
            seletor='documento-atual')))

    assert resultado.classificacao == REVISAO
    assert 'rotulo_ambiguo' in {item.tipo for item in resultado.diferencas}


def test_similaridade_textual_nao_substitui_igualdade_do_rotulo():
    resultado = comparar(
        _contrato(),
        _inventario(_observado(
            rotulo='CPF / CNPJ do contribuinte',
            id='documento-atual', seletor='documento-atual')))

    assert resultado.classificacao == REVISAO
    assert resultado.remapeamentos == ()


def test_normalizacao_de_acento_e_espaco_preserva_rotulo_semantico():
    resultado = comparar(
        _contrato(_esperado(rotulo='Número do documento')),
        _inventario(_observado(
            rotulo='  Numero   do documento ',
            id='documento-atual', seletor='documento-atual')))

    assert resultado.classificacao == AUTOATIVAVEL
    assert [item.tipo for item in resultado.diferencas] == ['seletor_alterado']


def test_acento_e_espaco_do_rotulo_sao_normalizados_sem_score():
    resultado = comparar(
        _contrato(_esperado(rotulo='Número do documento')),
        _inventario(_observado(
            rotulo='  Numero   do documento ',
            id='documento-atual', seletor='documento-atual')))

    assert resultado.classificacao == AUTOATIVAVEL
    assert [item.tipo for item in resultado.diferencas] == ['seletor_alterado']


def test_elemento_ausente_ou_novo_exige_revisao():
    ausente = comparar(_contrato(), _inventario(elementos=()))
    novo = comparar(
        _contrato(),
        _inventario(
            _observado(),
            _observado(id='controle-novo', seletor='controle-novo',
                       rotulo='Controle adicional')))

    assert ausente.classificacao == REVISAO
    assert [item.tipo for item in ausente.diferencas] == ['elemento_ausente']
    assert novo.classificacao == REVISAO
    assert 'elemento_novo' in {item.tipo for item in novo.diferencas}


def test_duas_trocas_de_seletor_nao_sao_promovidas_parcialmente():
    segundo_esperado = replace(
        _esperado(), chave='outro_documento', seletor='outro-antigo',
        ordem_relativa=2)
    segundo_observado = _observado(
        id='outro-atual', name='outro', seletor='outro-atual',
        ordem_relativa=2)

    resultado = comparar(
        _contrato(_esperado(), segundo_esperado),
        _inventario(
            _observado(id='documento-atual', seletor='documento-atual'),
            segundo_observado))

    assert resultado.classificacao == REVISAO
    assert [item.tipo for item in resultado.diferencas] == [
        'seletor_alterado', 'seletor_alterado']
    assert resultado.remapeamentos == ()


def test_inventario_desconhecido_permanece_desconhecido():
    inventario = InventarioPortal.desconhecido(
        'formulario', 'DOM instável durante a observação')

    resultado = comparar(_contrato(), inventario)

    assert resultado.classificacao == DESCONHECIDA
    assert resultado.diferencas[0].tipo == 'observacao_desconhecida'
    assert resultado.remapeamentos == ()


def test_assinatura_da_diferenca_e_estavel_e_muda_com_a_dimensao():
    primeiro = comparar(
        _contrato(),
        _inventario(_observado(
            id='documento-atual', seletor='documento-atual')))
    repetido = comparar(
        _contrato(),
        _inventario(_observado(
            id='documento-atual', seletor='documento-atual')))
    tipo_alterado = comparar(
        _contrato(),
        _inventario(_observado(tipo='number')))

    assert primeiro.diferencas[0].assinatura == repetido.diferencas[0].assinatura
    assert primeiro.diferencas[0].assinatura != tipo_alterado.diferencas[0].assinatura
