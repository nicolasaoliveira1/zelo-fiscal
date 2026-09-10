"""Preflight único e snapshot fixado dos contratos dos portais."""
from dataclasses import FrozenInstanceError

import pytest

from app import db
from app.automation.trabalhista_recon import ElementoInventariado, InventarioPortal
from app.models import ContratoPortal, Usuario
from app.services import contrato_portal, contrato_portal_preflight
from app.services.contrato_portal_drift import (
    ContratoComparavel,
    ElementoContratoComparavel,
)


def _elemento_contrato(**dados):
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
        'assinatura_formulario': 'f' * 64,
        'ordem_relativa': 1,
        'obrigatorio': True,
        'visivel': True,
        'somente_leitura': False,
        'autoajuste_seletor': True,
    }
    valores.update(dados)
    return ElementoContratoComparavel(**valores)


def _inventario(*, seletor='documento-antigo', estado='ok', motivo=None):
    if estado != 'ok':
        return InventarioPortal.desconhecido('formulario', motivo or 'falha sintética')
    return InventarioPortal(
        host='portal.exemplo.gov.br', rota='/certidao/emitir', etapa='formulario',
        elementos=(ElementoInventariado(
            tag='input', tipo='text', id=seletor, name='',
            rotulo='CPF ou CNPJ', seletor_tipo='id', seletor=seletor,
            assinatura_formulario='f' * 64, ordem_relativa=1,
            obrigatorio=True, desabilitado=False, somente_leitura=False,
            visivel=True,
        ),),
        artefato_sanitizado='{"estrutura":"sintetica"}',
    )


def _baseline():
    usuario = Usuario(
        username='admin_preflight', senha_hash='hash-sintetico', papel='admin')
    db.session.add(usuario)
    db.session.commit()
    definicao = ContratoComparavel(
        host='portal.exemplo.gov.br', rota='/certidao/emitir',
        etapa='formulario', elementos=(_elemento_contrato(),))
    return contrato_portal.criar_baseline(
        fluxo='trabalhista', alvo='cndt', definicao=definicao,
        usuario_id=usuario.id)


def test_preflight_compativel_devolve_snapshot_imutavel_e_auditado(
    app, ids, monkeypatch,
):
    with app.app_context():
        baseline = _baseline()
        eventos = []
        monkeypatch.setattr(
            contrato_portal_preflight, 'log_event',
            lambda evento, **campos: eventos.append((evento, campos)))

        snapshot = contrato_portal_preflight.executar(
            fluxo='trabalhista', alvo='cndt',
            observar=lambda contrato: _inventario(), execution_id='exec-sintetica')

        assert (snapshot.contrato_id, snapshot.versao, snapshot.fingerprint) == (
            baseline.id, 1, baseline.fingerprint)
        assert snapshot.elemento('documento').seletor == 'documento-antigo'
        with pytest.raises(FrozenInstanceError):
            snapshot.versao = 2
        with pytest.raises(TypeError):
            snapshot.elementos['documento'] = snapshot.elemento('documento')
        evento, campos = eventos[-1]
        assert evento == 'contrato_portal_preflight'
        assert campos['fluxo'] == 'trabalhista'
        assert campos['alvo'] == 'cndt'
        assert campos['versao'] == 1
        assert campos['fingerprint'] == baseline.fingerprint[:12]
        assert campos['resultado'] == 'compativel'
        assert campos['origem'] == 'usuario'
        assert campos['execution_id'] == 'exec-sintetica'
        assert isinstance(campos['duracao_ms'], int)
        assert campos['duracao_ms'] >= 0


def test_preflight_autoativa_antes_de_fixar_snapshot(app, ids):
    with app.app_context():
        baseline = _baseline()

        snapshot = contrato_portal_preflight.executar(
            fluxo='trabalhista', alvo='cndt',
            observar=lambda contrato: _inventario(seletor='documento-atual'))

        db.session.expire_all()
        assert snapshot.versao == 2
        assert snapshot.elemento('documento').seletor == 'documento-atual'
        assert db.session.get(ContratoPortal, baseline.id).estado == 'arquivada'
        assert ContratoPortal.query.filter_by(estado='ativa').one().id == snapshot.contrato_id


def test_preflight_falha_promocao_preserva_classificacao_e_mensagem(app, ids, monkeypatch):
    with app.app_context():
        _baseline()

        def falhar(*args, **kwargs):
            raise contrato_portal.ContratoPortalConflitoError(
                'conflito sintético')

        monkeypatch.setattr(contrato_portal_preflight.contrato_portal,
                            'autoativar', falhar)

        with pytest.raises(
                contrato_portal_preflight.ContratoPortalBloqueadoError,
                match='A estrutura mudou e o ajuste não pôde ser promovido') as erro:
            contrato_portal_preflight.executar(
                fluxo='trabalhista', alvo='cndt',
                observar=lambda contrato: _inventario(seletor='documento-atual'))

        assert erro.value.classificacao == 'revisao'


@pytest.mark.parametrize('inventario', [
    InventarioPortal(
        host='portal.exemplo.gov.br', rota='/certidao/emitir',
        etapa='formulario', elementos=(), artefato_sanitizado='{}'),
    _inventario(estado='desconhecida'),
])
def test_preflight_bloqueia_e_registra_uma_falha_estrutural_por_chamada(
    app, ids, monkeypatch, inventario,
):
    with app.app_context():
        _baseline()
        falhas = []
        monkeypatch.setattr(
            contrato_portal_preflight.circuit_breaker, 'registrar_falha',
            lambda alvo, mensagem: falhas.append((alvo, mensagem)))

        with pytest.raises(
            contrato_portal_preflight.ContratoPortalBloqueadoError,
            match='estrutura do portal',
        ) as erro:
            contrato_portal_preflight.executar(
                fluxo='trabalhista', alvo='cndt',
                observar=lambda contrato: inventario,
                alvo_breaker='Trabalhista')

        assert erro.value.classificacao in {'revisao', 'desconhecida'}
        assert len(falhas) == 1
        assert falhas[0][0] == 'Trabalhista'


def test_snapshot_fixado_no_lote_e_reutilizado_por_retry_sem_reler_ativa(
    app, ids, monkeypatch,
):
    with app.app_context():
        _baseline()
        estado = {'contrato_snapshot': None}
        observacoes = []

        primeiro = contrato_portal_preflight.obter_ou_fixar_no_lote(
            estado,
            lambda: contrato_portal_preflight.executar(
                fluxo='trabalhista', alvo='cndt',
                observar=lambda contrato: observacoes.append(contrato.id) or _inventario()))
        monkeypatch.setattr(
            contrato_portal_preflight, '_carregar_ativo',
            lambda *args, **kwargs: pytest.fail('retry releu o contrato ativo'))
        retry = contrato_portal_preflight.obter_ou_fixar_no_lote(
            estado, lambda: pytest.fail('retry refez o preflight'))

        assert retry is primeiro
        assert estado['contrato_snapshot'] is primeiro
        assert observacoes == [primeiro.contrato_id]


def test_promocao_posterior_nao_altera_snapshot_ja_fixado(app, ids):
    with app.app_context():
        baseline = _baseline()
        estado = {'contrato_snapshot': None}
        fixado = contrato_portal_preflight.obter_ou_fixar_no_lote(
            estado,
            lambda: contrato_portal_preflight.executar(
                fluxo='trabalhista', alvo='cndt',
                observar=lambda contrato: _inventario()))

        posterior = contrato_portal_preflight.executar(
            fluxo='trabalhista', alvo='cndt',
            observar=lambda contrato: _inventario(seletor='documento-atual'))

        assert posterior.versao == 2
        assert fixado.contrato_id == baseline.id
        assert fixado.versao == 1
        assert fixado.elemento('documento').seletor == 'documento-antigo'
        assert estado['contrato_snapshot'] is fixado


def test_preflight_sem_contrato_ativo_falha_fechado(app, ids):
    with app.app_context():
        with pytest.raises(
            contrato_portal_preflight.ContratoPortalAusenteError,
            match='não possui contrato ativo',
        ):
            contrato_portal_preflight.executar(
                fluxo='trabalhista', alvo='cndt',
                observar=lambda contrato: pytest.fail('não deveria observar'))
