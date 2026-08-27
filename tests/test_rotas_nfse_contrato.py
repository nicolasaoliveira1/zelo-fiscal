"""Rotas do contrato adaptativo com dados exclusivamente sintéticos."""

from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app import db
from app.automation.nfse_recon import OpcaoInventariada
from app.automation.batch_state import (
    NFSE_BATCH_LOCK,
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
from app.automation import nfse_recon as nfse_recon_modulo
from app.routes import nfse as rotas_nfse
from app.services import nfse_contrato
from app.services.nfse_drift import CampoComparavel, Diferenca


@pytest.fixture(autouse=True)
def _estado_lote_limpo():
    batch_engine.reset_batch_state(NFSE_BATCH_STATE)
    definir_nfse_batch_opcoes('lote')
    # O acumulador de passes vive no processo: sem zerar, um teste herda os
    # controles observados pelo anterior.
    rotas_nfse.ACUMULADOR_RECON.descartar()
    yield
    batch_engine.reset_batch_state(NFSE_BATCH_STATE)
    definir_nfse_batch_opcoes('lote')
    rotas_nfse.ACUMULADOR_RECON.descartar()


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
        diferencas = [Diferenca(
            etapa='servico',
            tipo='controle_novo',
            severidade='critica',
            chave_observada='campo.sintetico',
            observado=campo,
            mensagem='Diferença sintética requer decisão.',
        )]
        if recomendado:
            esperado = CampoComparavel(
                **{
                    **campo.__dict__,
                    'chave_semantica': 'ServicoPrestado_Descricao',
                }
            )
            diferencas.insert(0, Diferenca(
                etapa='servico',
                tipo='controle_removido',
                severidade='critica',
                chave_esperada='ServicoPrestado_Descricao',
                esperado=esperado,
                mensagem='Controle sintético anterior não foi encontrado.',
            ))
        incidente = nfse_contrato.registrar_incidentes(
            contrato.id,
            diferencas,
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
            documento='DOC-SINTETICO',
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


class _DriverRecon:
    def __init__(self, url='/EmissorNacional/DPS/Pessoas'):
        self._url = url
        self.current_url_reads = 0
        self.execute_script_calls = 0
        self.get_calls = 0
        self.click_calls = 0

    @property
    def current_url(self):
        self.current_url_reads += 1
        return self._url

    def execute_script(self, _script):
        self.execute_script_calls += 1
        return {'estado': 'ok', 'controles': []}

    def get(self, _url):
        self.get_calls += 1


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


def test_pagina_exibe_central_com_aria_e_estado_vazio(login_as):
    resposta = login_as('operador').get('/nfse')

    assert resposta.status_code == 200
    corpo = resposta.get_data(as_text=True)
    assert 'id="nfseContratoCentral"' in corpo
    assert 'id="nfseContratoStatus"' in corpo
    assert 'role="status"' in corpo
    assert 'aria-live="polite"' in corpo
    assert 'Não há incidentes no contrato ativo.' in corpo
    assert 'id="modalConfigContrato"' in corpo
    assert 'id="modalValidarContrato"' in corpo
    assert 'aria-label="Fechar"' in corpo
    assert 'class="btn btn-ghost btn-sm"' in corpo
    assert 'id="nfseNotaValidacao"' in corpo
    assert '<option value="" selected disabled>Escolha uma nota…</option>' in corpo


def test_pagina_exibe_incidente_sem_seletor_e_com_acao(login_as, app):
    _contrato_id, incidente_id = _criar_incidente(app)

    resposta = login_as('operador').get('/nfse')

    assert resposta.status_code == 200
    corpo = resposta.get_data(as_text=True)
    assert f'data-incidente-id="{incidente_id}"' in corpo
    assert 'Campo sintético' in corpo
    assert 'Opção sintética' in corpo
    assert 'Configurar alteração' in corpo
    assert 'SENTINELA' not in corpo
    inicio = corpo.index(f'data-incidente-id="{incidente_id}"')
    trecho_incidente = corpo[inicio:corpo.index('</article>', inicio)]
    assert 'seletor' not in trecho_incidente.lower()
    assert 'xpath' not in trecho_incidente.lower()


def test_papel_leitura_nao_recebe_central_no_template(login_as):
    resposta = login_as('leitura').get('/nfse')

    assert resposta.status_code == 403
    assert 'nfseContratoCentral' not in resposta.get_data(as_text=True)


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


def test_recomendacao_real_confirmada_cria_candidata_e_cobre_os_dois_incidentes(
    login_as, app
):
    _contrato_id, removido_id = _criar_incidente(app, recomendado=True)

    resposta = login_as('operador').post(
        f'/nfse/contrato/incidente/{removido_id}/configurar',
        json={
            'origem': 'fixo',
            'valor_fixo': 'OPCAO-SINTETICA',
            'confirmar_recomendacao': True,
            'chave_observada': 'campo.sintetico',
        },
    )

    assert resposta.status_code == 200
    candidato_id = resposta.get_json()['contrato']['id']
    with app.app_context():
        vinculados = IncidenteContratoNfse.query.filter_by(
            contrato_candidato_id=candidato_id,
            estado='configurado',
        ).all()
        assert len(vinculados) == 2


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


def test_recon_sem_sessao_orienta_preparar(login_as, monkeypatch):
    sessao = MagicMock()
    sessao.adquirir.return_value = True
    sessao.driver = None
    monkeypatch.setattr('app.routes.nfse.SESSAO', sessao)

    resposta = login_as('operador').post('/nfse/contrato/recon', json={})

    assert resposta.status_code == 409
    assert resposta.get_json()['motivo'] == 'sessao_nfse_ausente'
    assert 'Prepare a sessão' in resposta.get_json()['message']
    sessao.liberar.assert_called_once()


def test_recon_nao_consulta_selenium_se_lote_segura_lock(login_as, monkeypatch):
    driver = _DriverRecon()
    sessao = MagicMock()
    sessao.driver = driver
    monkeypatch.setattr('app.routes.nfse.SESSAO', sessao)
    assert NFSE_BATCH_LOCK.acquire(blocking=False)
    try:
        resposta = login_as('operador').post('/nfse/contrato/recon', json={})
    finally:
        NFSE_BATCH_LOCK.release()

    assert resposta.status_code == 409
    assert resposta.get_json()['motivo'] == 'lote_nfse_em_curso'
    assert not sessao.adquirir.called
    assert driver.current_url_reads == 0
    assert driver.execute_script_calls == 0


def test_recon_observa_tela_atual_sem_navegar_nem_escrever(
    login_as, app, monkeypatch
):
    _contrato_id, _incidente_id = _criar_incidente(app)
    driver = _DriverRecon()
    sessao = MagicMock()
    sessao.adquirir.return_value = True
    sessao.driver = driver
    monkeypatch.setattr('app.routes.nfse.SESSAO', sessao)

    resposta = login_as('operador').post('/nfse/contrato/recon', json={})

    assert resposta.status_code == 200
    observacao = resposta.get_json()['observacao']
    assert observacao['etapa'] == 'pessoas'
    # A recon assistida nunca é a observação final: o operador ainda pode
    # preencher um campo e revelar os que faltam. A ausência é provisória — não
    # conclui incompatibilidade e, sobretudo, NÃO vira incidente aberto, que
    # fecharia o gate do modo automático por um fato que não aconteceu.
    assert observacao['compatibilidade'] == 'compativel'
    assert observacao['incidentes'] == 0
    assert resposta.get_json()['passe'] == 1
    assert driver.current_url_reads == 1
    # Dois scripts por passe: o inventário estrutural e o de preenchimento,
    # que devolve só booleanos. Nenhum navega nem escreve.
    assert driver.execute_script_calls == 2
    assert driver.get_calls == 0
    assert driver.click_calls == 0
    sessao.liberar.assert_called_once()


def test_recon_libera_sessao_se_observacao_falhar(login_as, monkeypatch):
    driver = _DriverRecon()
    sessao = MagicMock()
    sessao.adquirir.return_value = True
    sessao.driver = driver
    monkeypatch.setattr('app.routes.nfse.SESSAO', sessao)
    monkeypatch.setattr(
        'app.routes.nfse.nfse_contrato.observar',
        MagicMock(side_effect=RuntimeError('falha sintética')),
    )

    resposta = login_as('operador').post('/nfse/contrato/recon', json={})

    assert resposta.status_code == 500
    sessao.liberar.assert_called_once()


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


def test_descartar_candidata_reabre_os_incidentes_para_reconfigurar(login_as, app):
    """Configurar não pode ser via de mão única."""

    contrato_id, candidato_id, incidente_id = _criar_candidata(app)
    operador = login_as('operador')

    resposta = operador.post(f'/nfse/contrato/{candidato_id}/descartar', json={})

    assert resposta.status_code == 200
    assert resposta.get_json()['reabertos'] >= 1
    depois = operador.get('/nfse/contrato').get_json()
    assert depois['candidatas'] == []
    assert any(
        item['id'] == incidente_id and item['estado'] == 'aberto'
        for item in depois['incidentes']
    )
    assert depois['ativo']['id'] == contrato_id


def test_descartar_recusa_versao_que_nao_e_candidata(login_as, app):
    contrato_id, _incidente_id = _criar_incidente(app)

    resposta = login_as('operador').post(
        f'/nfse/contrato/{contrato_id}/descartar', json={}
    )

    assert resposta.status_code == 409


def test_validacao_nao_exige_aliquota_conferida(login_as, app, monkeypatch):
    """A validação preenche até a revisão e nunca emite (ND-005). Exigir o gate
    aqui bloqueava a prova que precede qualquer emissão — e o operador via o
    lote terminar em 2s sem explicação."""

    from app.automation.batch_state import nfse_batch_opcoes

    _contrato_id, candidato_id, _incidente_id = _criar_candidata(app)
    nota_id = _criar_nota_emitivel(app)
    sessao = MagicMock()
    sessao.adquirir.return_value = True
    sessao.aliquota_confirmada = False
    monkeypatch.setattr('app.routes.nfse.SESSAO', sessao)
    monkeypatch.setattr('app.routes.nfse.automacao_em_curso', lambda: None)
    monkeypatch.setattr(
        'app.services.batch_engine.run_worker', lambda worker_fn, app_factory: None
    )

    resposta = login_as('operador').post(
        f'/nfse/contrato/{candidato_id}/validar', json={'nota_id': nota_id}
    )

    assert resposta.status_code == 200
    assert nfse_batch_opcoes()['ignorar_aliquota'] is True


def test_passe_inconclusivo_nao_se_esconde_atras_da_uniao(login_as, app, monkeypatch):
    """Responder pela união quando ESTE passe falhou diria "compatível" sobre
    uma tela que não foi lida agora: desfecho desconhecido virando chute."""

    _contrato_id, _incidente_id = _criar_incidente(app)
    driver = _DriverRecon()
    sessao = MagicMock()
    sessao.adquirir.return_value = True
    sessao.driver = driver
    monkeypatch.setattr('app.routes.nfse.SESSAO', sessao)

    operador = login_as('operador')
    operador.post('/nfse/contrato/recon', json={})

    monkeypatch.setattr(
        'app.routes.nfse.nfse_recon.inventariar',
        lambda *_a, **_k: nfse_recon_modulo.InventarioEtapa.desconhecido(
            'pessoas', 'a janela do portal não respondeu à observação'
        ),
    )

    observacao = operador.post('/nfse/contrato/recon', json={}).get_json()['observacao']

    assert observacao['estado'] == 'desconhecida'
    assert observacao['compatibilidade'] == 'desconhecida'
