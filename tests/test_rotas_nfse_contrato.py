"""Rotas do contrato adaptativo com dados exclusivamente sintéticos."""

from datetime import datetime

import pytest

from app import db
from app.automation.nfse_recon import OpcaoInventariada
from app.models import IncidenteContratoNfse
from app.services import nfse_contrato
from app.services.nfse_drift import CampoComparavel, Diferenca


def _criar_incidente(app, *, recomendado=False):
    with app.app_context():
        contrato = nfse_contrato.garantir_contrato_inicial()
        campo = CampoComparavel(
            chave_semantica='campo.sintetico',
            etapa='servico',
            rotulo='Campo sintético',
            tipo='select',
            interacao='select_direto',
            obrigatorio=True,
            opcoes=(
                OpcaoInventariada(
                    'OPCAO-SINTETICA',
                    'Opção sintética',
                    0,
                ),
            ),
        )
        diferenca = Diferenca(
            etapa='servico',
            tipo='controle_novo',
            severidade='critica',
            chave_esperada='campo.esperado' if recomendado else None,
            chave_observada='campo.sintetico',
            observado=campo,
            mensagem='Diferença sintética requer decisão.',
        )
        incidente = nfse_contrato.registrar_incidentes(
            contrato.id,
            [diferenca],
            agora=datetime(2026, 8, 25, 12, 0),
        )[0]
        return contrato.id, incidente.id


def test_get_estado_e_detalhe_sao_sanitizados(login_as, app):
    contrato_id, incidente_id = _criar_incidente(app)
    operador = login_as('operador')

    estado = operador.get('/nfse/contrato')
    detalhe = operador.get(f'/nfse/contrato/{contrato_id}')

    assert estado.status_code == 200
    assert detalhe.status_code == 200
    dados_estado = estado.get_json()
    dados_detalhe = detalhe.get_json()['contrato']
    corpo = estado.get_data(as_text=True) + detalhe.get_data(as_text=True)
    assert dados_estado['ativo']['id'] == contrato_id
    assert any(item['id'] == incidente_id for item in dados_estado['incidentes'])
    assert dados_detalhe['campos']
    assert 'seletor' not in corpo
    assert 'VALOR-NOTA-SINTETICO' not in corpo
    assert 'HTML-BRUTO-SINTETICO' not in corpo
    assert 'CAMINHO-CLIENTE-SINTETICO' not in corpo


def test_configurar_incidente_cria_candidata_sem_mudar_ativo(login_as, app):
    contrato_id, incidente_id = _criar_incidente(app)
    resposta = login_as('operador').post(
        f'/nfse/contrato/incidente/{incidente_id}/configurar',
        json={
            'origem': 'fixo',
            'fonte': None,
            'valor_fixo': 'OPCAO-SINTETICA',
        },
    )

    assert resposta.status_code == 200
    dados = resposta.get_json()
    assert dados['status'] == 'ok'
    candidato_id = dados['contrato']['id']
    with app.app_context():
        ativo = db.session.get(nfse_contrato.ContratoNfse, contrato_id)
        candidato = db.session.get(nfse_contrato.ContratoNfse, candidato_id)
        assert ativo.estado == 'ativa'
        assert candidato.estado == 'candidata'
        assert candidato.id != ativo.id


def test_payload_extra_e_recomendacao_sem_confirmacao_sao_recusados(login_as, app):
    _contrato_id, incidente_id = _criar_incidente(app)
    operador = login_as('operador')

    extra = operador.post(
        f'/nfse/contrato/incidente/{incidente_id}/configurar',
        json={'origem': 'fixo', 'valor_fixo': 'OPCAO-SINTETICA', 'seletor': 'DOM'},
    )
    assert extra.status_code == 400
    assert extra.get_json()['campo'] == 'seletor'

    _contrato_id, recomendado_id = _criar_incidente(app, recomendado=True)
    recomendacao = operador.post(
        f'/nfse/contrato/incidente/{recomendado_id}/configurar',
        json={'origem': 'fixo', 'valor_fixo': 'OPCAO-SINTETICA'},
    )
    assert recomendacao.status_code == 400
    assert recomendacao.get_json()['campo'] == 'confirmar_recomendacao'


def test_incidente_decidido_retorna_conflito(login_as, app):
    _contrato_id, incidente_id = _criar_incidente(app)
    operador = login_as('operador')
    payload = {'origem': 'fixo', 'valor_fixo': 'OPCAO-SINTETICA'}

    assert operador.post(
        f'/nfse/contrato/incidente/{incidente_id}/configurar', json=payload
    ).status_code == 200
    conflito = operador.post(
        f'/nfse/contrato/incidente/{incidente_id}/configurar', json=payload
    )

    assert conflito.status_code == 409
    with app.app_context():
        assert db.session.get(IncidenteContratoNfse, incidente_id).estado == 'configurado'


@pytest.mark.parametrize('rota,metodo', [
    ('/nfse/contrato', 'get'),
    ('/nfse/contrato/1', 'get'),
    ('/nfse/contrato/incidente/1/configurar', 'post'),
])
def test_papel_leitura_nao_acessa_contrato(login_as, rota, metodo):
    resposta = getattr(login_as('leitura'), metodo)(rota, json={}
                                                     if metodo == 'post' else None)
    assert resposta.status_code == 403


def test_anonimo_e_barrado_no_contrato(client_anon):
    resposta = client_anon.get('/nfse/contrato')
    assert resposta.status_code in (302, 401, 403)


def test_contrato_inexistente_devolve_404_json(login_as):
    resposta = login_as('operador').get('/nfse/contrato/99999')
    assert resposta.status_code == 404
    assert resposta.get_json()['status'] == 'error'
