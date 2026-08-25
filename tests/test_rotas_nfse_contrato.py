"""Rotas do contrato adaptativo com dados exclusivamente sintéticos."""

from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app import db
from app.automation.nfse_recon import OpcaoInventariada
from app.automation.batch_state import (
    NFSE_BATCH_STATE,
    definir_nfse_batch_opcoes,
)
from app.models import (
    Empresa,
    IncidenteContratoNfse,
    LoteNfse,
    NotaNfse,
    StatusNotaNfse,
)
from app.services import batch_engine
from app.services import nfse_contrato
from app.services.nfse_drift import CampoComparavel, Diferenca


@pytest.fixture(autouse=True)
def _estado_lote_limpo():
    batch_engine.reset_batch_state(NFSE_BATCH_STATE)
    definir_nfse_batch_opcoes('lote')
    yield
    batch_engine.reset_batch_state(NFSE_BATCH_STATE)
    definir_nfse_batch_opcoes('lote')


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


def _criar_nota_emitivel(app):
    with app.app_context():
        empresa = Empresa.query.first()
        lote = LoteNfse(nome_arquivo='lote-sintetico.csv', total=1)
        db.session.add(lote)
        db.session.flush()
        nota = NotaNfse(
            lote_id=lote.id,
            empresa_id=empresa.id,
            nome_csv='TOMADOR SINTETICO',
            documento='DOCUMENTO-SINTETICO',
            tipo_documento='cnpj',
            competencia='08/2026',
            valor_final=Decimal('12.34'),
            status=StatusNotaNfse.PRONTA,
        )
        db.session.add(nota)
        db.session.commit()
        return nota.id


def _criar_candidata(app):
    contrato_id, incidente_id = _criar_incidente(app)
    with app.app_context():
        candidato = nfse_contrato.configurar_incidente(
            incidente_id,
            {'origem': 'fixo', 'valor_fixo': 'OPCAO-SINTETICA'},
        )
        return contrato_id, candidato.id, incidente_id


def _marcar_validada(app, candidato_id, nota_id):
    with app.app_context():
        candidato = db.session.get(nfse_contrato.ContratoNfse, candidato_id)
        candidato.estado = 'validada'
        candidato.validado_em = datetime(2026, 8, 25, 12, 0)
        candidato.nota_validacao_id = nota_id
        db.session.commit()


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


def test_validar_exige_nota_emitivel_e_inicia_modo_individual(
    login_as, app, monkeypatch
):
    _contrato_id, candidato_id, _incidente_id = _criar_candidata(app)
    nota_id = _criar_nota_emitivel(app)
    sessao = MagicMock()
    sessao.adquirir.return_value = True
    sessao.ocupada = True
    inicializar = MagicMock(
        return_value={'ids': [nota_id], 'total': 1, 'vencidas': 0, 'a_vencer': 0}
    )
    monkeypatch.setattr('app.routes.nfse.SESSAO', sessao)
    monkeypatch.setattr('app.routes.nfse.automacao_em_curso', lambda: None)
    monkeypatch.setattr('app.routes.nfse.batch_engine', MagicMock(
        init_batch_run=inicializar,
    ))
    emitir = MagicMock()
    monkeypatch.setattr('app.routes.nfse.nfse_lote.automacao.emitir', emitir)

    resposta = login_as('operador').post(
        f'/nfse/contrato/{candidato_id}/validar',
        json={'nota_id': nota_id},
    )

    assert resposta.status_code == 200
    assert resposta.get_json()['modo'] == 'individual'
    assert resposta.get_json()['contrato_id'] == candidato_id
    assert inicializar.called
    emitir.assert_not_called()
    assert sessao.adquirir.called


def test_validar_libera_sessao_se_a_thread_nao_iniciar(login_as, app, monkeypatch):
    _contrato_id, candidato_id, _incidente_id = _criar_candidata(app)
    nota_id = _criar_nota_emitivel(app)
    sessao = MagicMock()
    sessao.adquirir.return_value = True
    monkeypatch.setattr('app.routes.nfse.SESSAO', sessao)
    monkeypatch.setattr('app.routes.nfse.automacao_em_curso', lambda: None)
    monkeypatch.setattr(
        'app.routes.nfse.batch_engine.init_batch_run',
        MagicMock(side_effect=RuntimeError('falha sintética')),
    )

    resposta = login_as('operador').post(
        f'/nfse/contrato/{candidato_id}/validar',
        json={'nota_id': nota_id},
    )

    assert resposta.status_code == 500
    sessao.liberar.assert_called_once()


def test_validar_nao_aceita_modo_automatico(login_as, app):
    _contrato_id, candidato_id, _incidente_id = _criar_candidata(app)
    nota_id = _criar_nota_emitivel(app)

    resposta = login_as('operador').post(
        f'/nfse/contrato/{candidato_id}/validar',
        json={'nota_id': nota_id, 'modo': 'automatico'},
    )

    assert resposta.status_code == 400
    assert resposta.get_json()['campo'] == 'modo'


def test_ativar_arquiva_ativo_e_resolve_somente_incidente_ligado(
    login_as, app, monkeypatch
):
    ativo_id, candidato_id, incidente_id = _criar_candidata(app)
    nota_id = _criar_nota_emitivel(app)
    _marcar_validada(app, candidato_id, nota_id)
    sessao = MagicMock()
    sessao.ocupada = False
    monkeypatch.setattr('app.routes.nfse.SESSAO', sessao)
    monkeypatch.setattr('app.routes.nfse.automacao_em_curso', lambda: None)
    emitir = MagicMock()
    monkeypatch.setattr('app.routes.nfse.nfse_lote.automacao.emitir', emitir)

    resposta = login_as('operador').post(
        f'/nfse/contrato/{candidato_id}/ativar',
        json={},
    )

    assert resposta.status_code == 200
    with app.app_context():
        ativo = db.session.get(nfse_contrato.ContratoNfse, ativo_id)
        candidato = db.session.get(nfse_contrato.ContratoNfse, candidato_id)
        incidente = db.session.get(IncidenteContratoNfse, incidente_id)
        assert ativo.estado == 'arquivada'
        assert candidato.estado == 'ativa'
        assert incidente.estado == 'resolvido'
    emitir.assert_not_called()


def test_ativar_recusa_candidata_nao_validada(login_as, app, monkeypatch):
    _ativo_id, candidato_id, _incidente_id = _criar_candidata(app)
    sessao = MagicMock()
    sessao.ocupada = False
    monkeypatch.setattr('app.routes.nfse.SESSAO', sessao)
    monkeypatch.setattr('app.routes.nfse.automacao_em_curso', lambda: None)

    resposta = login_as('operador').post(
        f'/nfse/contrato/{candidato_id}/ativar',
        json={},
    )

    assert resposta.status_code == 409


def test_ativar_recusa_automacao_alheia(login_as, app, monkeypatch):
    _ativo_id, candidato_id, _incidente_id = _criar_candidata(app)
    nota_id = _criar_nota_emitivel(app)
    _marcar_validada(app, candidato_id, nota_id)
    sessao = MagicMock()
    sessao.ocupada = False
    monkeypatch.setattr('app.routes.nfse.SESSAO', sessao)
    monkeypatch.setattr(
        'app.routes.nfse.automacao_em_curso',
        lambda: {'rotulo': 'lote sintético', 'tipo': 'lote', 'status': 'running'},
    )

    resposta = login_as('operador').post(
        f'/nfse/contrato/{candidato_id}/ativar',
        json={},
    )

    assert resposta.status_code == 409


def test_ativar_permite_a_validacao_da_propria_candidata_na_revisao(
    login_as, app, monkeypatch
):
    _ativo_id, candidato_id, _incidente_id = _criar_candidata(app)
    nota_id = _criar_nota_emitivel(app)
    _marcar_validada(app, candidato_id, nota_id)
    with app.app_context():
        nota = db.session.get(NotaNfse, nota_id)
        nota.status = StatusNotaNfse.AGUARDANDO_CONFIRMACAO
        db.session.commit()
    NFSE_BATCH_STATE.update(status='running', current_id=nota_id)
    definir_nfse_batch_opcoes(
        'individual', contrato_id=candidato_id,
        validacao_contrato_id=candidato_id,
    )
    sessao = MagicMock()
    sessao.ocupada = True
    monkeypatch.setattr('app.routes.nfse.SESSAO', sessao)
    monkeypatch.setattr(
        'app.routes.nfse.automacao_em_curso',
        lambda: {'rotulo': 'NFSe', 'tipo': 'lote', 'status': 'running'},
    )

    resposta = login_as('operador').post(
        f'/nfse/contrato/{candidato_id}/ativar',
        json={},
    )

    assert resposta.status_code == 200


def test_ativar_falha_de_commit_preserva_estado(login_as, app, monkeypatch):
    ativo_id, candidato_id, _incidente_id = _criar_candidata(app)
    nota_id = _criar_nota_emitivel(app)
    _marcar_validada(app, candidato_id, nota_id)
    sessao = MagicMock()
    sessao.ocupada = False
    monkeypatch.setattr('app.routes.nfse.SESSAO', sessao)
    monkeypatch.setattr('app.routes.nfse.automacao_em_curso', lambda: None)

    def falhar_commit():
        raise RuntimeError('falha sintética de commit')

    monkeypatch.setattr(db.session, 'commit', falhar_commit)
    resposta = login_as('operador').post(
        f'/nfse/contrato/{candidato_id}/ativar',
        json={},
    )

    assert resposta.status_code == 500
    with app.app_context():
        assert db.session.get(nfse_contrato.ContratoNfse, ativo_id).estado == 'ativa'
        assert db.session.get(nfse_contrato.ContratoNfse, candidato_id).estado == 'validada'
