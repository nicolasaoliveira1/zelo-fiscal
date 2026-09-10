"""Serviço transacional de versões e incidentes dos portais."""
from datetime import datetime

import pytest

from app import db
from app.models import ContratoPortal, IncidenteContratoPortal, Usuario
from app.services import contrato_portal
from app.services.contrato_portal_drift import (
    AUTOATIVAVEL,
    COMPATIVEL,
    REVISAO,
    ContratoComparavel,
    DiferencaPortal,
    ElementoContratoComparavel,
    RemapeamentoSeletor,
    ResultadoComparacaoPortal,
)


AGORA = datetime(2026, 1, 15, 10, 30)


def _usuario():
    usuario = Usuario(
        username='admin_contratos', senha_hash='hash-sintetico', papel='admin')
    db.session.add(usuario)
    db.session.commit()
    return usuario


def _elemento(**dados):
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


def _definicao(**dados):
    valores = {
        'host': 'portal.exemplo.gov.br',
        'rota': '/certidao/emitir',
        'etapa': 'formulario',
        'elementos': (_elemento(),),
    }
    valores.update(dados)
    return ContratoComparavel(**valores)


def _diferenca(tipo='seletor_alterado', **dados):
    valores = {
        'tipo': tipo,
        'severidade': 'bloqueante',
        'etapa': 'formulario',
        'elemento_chave': 'documento',
        'esperado': 'documento-antigo',
        'observado': 'documento-atual',
        'evidencias': ('candidato único',),
        'assinatura': 'a' * 64,
    }
    valores.update(dados)
    return DiferencaPortal(**valores)


def _resultado(classificacao=AUTOATIVAVEL, *diferencas):
    diferencas = diferencas or (_diferenca(),)
    remapeamentos = ()
    if classificacao == AUTOATIVAVEL:
        remapeamentos = (RemapeamentoSeletor(
            chave='documento',
            seletor_tipo_anterior='id',
            seletor_anterior='documento-antigo',
            seletor_tipo_novo='id',
            seletor_novo='documento-atual'),)
    return ResultadoComparacaoPortal(
        classificacao=classificacao,
        diferencas=tuple(diferencas),
        remapeamentos=remapeamentos)


def _baseline(usuario):
    return contrato_portal.criar_baseline(
        fluxo='trabalhista', alvo='cndt', definicao=_definicao(),
        usuario_id=usuario.id, agora=AGORA)


def test_baseline_exige_humano_e_nasce_ativa_auditavel(app, ids):
    with app.app_context():
        usuario = _usuario()

        with pytest.raises(contrato_portal.BaselineRequerHumanoError):
            contrato_portal.criar_baseline(
                fluxo='trabalhista', alvo='cndt', definicao=_definicao(),
                usuario_id=None, agora=AGORA)

        baseline = _baseline(usuario)

        assert (baseline.versao, baseline.estado, baseline.origem) == (
            1, 'ativa', 'usuario')
        assert (baseline.criado_em, baseline.classificado_em,
                baseline.ativado_em) == (AGORA, AGORA, AGORA)
        assert baseline.criado_por_id == usuario.id
        assert baseline.ativado_por_id == usuario.id
        assert len(baseline.fingerprint) == 64
        assert baseline.elementos[0].seletor == 'documento-antigo'


def test_baseline_nao_substitui_alvo_ja_conhecido(app, ids):
    with app.app_context():
        usuario = _usuario()
        _baseline(usuario)

        with pytest.raises(contrato_portal.ContratoPortalConflitoError):
            _baseline(usuario)

        assert ContratoPortal.query.filter_by(
            fluxo='trabalhista', alvo='cndt').count() == 1


def test_incidente_repetido_atualiza_contagem_sem_duplicar(app, ids):
    with app.app_context():
        baseline = _baseline(_usuario())
        resultado = _resultado(REVISAO, _diferenca('rota_alterada'))

        primeiro = contrato_portal.registrar_incidente(
            baseline.id, resultado, agora=AGORA)
        segundo = contrato_portal.registrar_incidente(
            baseline.id, resultado,
            agora=datetime(2026, 1, 15, 11, 0))

        assert primeiro.id == segundo.id
        assert segundo.observacoes == 2
        assert segundo.ultima_observacao_em == datetime(2026, 1, 15, 11, 0)
        assert IncidenteContratoPortal.query.count() == 1
        assert [item.dimensao for item in segundo.diferencas] == ['rota_alterada']


def test_autoativacao_clona_e_promove_sem_mutar_versao_anterior(app, ids):
    with app.app_context():
        baseline = _baseline(_usuario())
        fingerprint_base = baseline.fingerprint

        nova = contrato_portal.autoativar(
            baseline.id, _resultado(), fingerprint_base=fingerprint_base,
            agora=datetime(2026, 1, 15, 11, 0))
        db.session.expire_all()
        anterior = db.session.get(ContratoPortal, baseline.id)
        incidente = IncidenteContratoPortal.query.one()

        assert (anterior.estado, anterior.elementos[0].seletor) == (
            'arquivada', 'documento-antigo')
        assert (nova.versao, nova.estado, nova.origem) == (2, 'ativa', 'sistema')
        assert nova.base_id == anterior.id
        assert nova.elementos[0].seletor == 'documento-atual'
        assert nova.fingerprint != fingerprint_base
        assert (incidente.estado, incidente.contrato_candidato_id) == (
            'resolvido', nova.id)
        assert ContratoPortal.query.filter_by(estado='ativa').count() == 1


def test_fingerprint_obsoleto_bloqueia_segunda_promocao(app, ids):
    with app.app_context():
        baseline = _baseline(_usuario())
        fingerprint_obsoleto = baseline.fingerprint
        contrato_portal.autoativar(
            baseline.id, _resultado(),
            fingerprint_base=fingerprint_obsoleto, agora=AGORA)

        with pytest.raises(contrato_portal.ContratoPortalConflitoError):
            contrato_portal.autoativar(
                baseline.id, _resultado(),
                fingerprint_base=fingerprint_obsoleto, agora=AGORA)

        assert ContratoPortal.query.filter_by(estado='ativa').count() == 1


def test_autoativacao_recusa_resultado_forjado_com_diferenca_extra(app, ids):
    with app.app_context():
        baseline = _baseline(_usuario())
        forjado = ResultadoComparacaoPortal(
            classificacao=AUTOATIVAVEL,
            diferencas=(
                _diferenca(),
                _diferenca('rota_alterada', assinatura='b' * 64),
            ),
            remapeamentos=_resultado().remapeamentos,
        )

        with pytest.raises(contrato_portal.ContratoPortalTransicaoError):
            contrato_portal.autoativar(
                baseline.id, forjado,
                fingerprint_base=baseline.fingerprint, agora=AGORA)

        assert db.session.get(ContratoPortal, baseline.id).estado == 'ativa'
        assert ContratoPortal.query.count() == 1


def test_falha_de_promocao_faz_rollback_e_preserva_ativa(
    app, ids, monkeypatch,
):
    with app.app_context():
        baseline = _baseline(_usuario())

        def falhar(*args, **kwargs):
            raise RuntimeError('falha sintética depois da promoção')

        monkeypatch.setattr(contrato_portal, '_resolver_incidentes', falhar)

        with pytest.raises(contrato_portal.PersistenciaContratoPortalError):
            contrato_portal.autoativar(
                baseline.id, _resultado(),
                fingerprint_base=baseline.fingerprint, agora=AGORA)

        db.session.expire_all()
        assert db.session.get(ContratoPortal, baseline.id).estado == 'ativa'
        assert ContratoPortal.query.count() == 1
        assert IncidenteContratoPortal.query.count() == 0


def test_revisao_manual_exige_revalidacao_compativel(app, ids):
    with app.app_context():
        usuario = _usuario()
        baseline = _baseline(usuario)
        candidata = contrato_portal.criar_candidata_revisao(
            baseline.id,
            ajustes=(RemapeamentoSeletor(
                chave='documento', seletor_tipo_anterior='id',
                seletor_anterior='documento-antigo',
                seletor_tipo_novo='id', seletor_novo='documento-revisado'),),
            resultado=_resultado(REVISAO, _diferenca('rotulo_alterado')),
            fingerprint_base=baseline.fingerprint,
            usuario_id=usuario.id,
            agora=AGORA)

        assert candidata.estado == 'candidata_revisao'
        assert candidata.origem == 'usuario'
        assert candidata.elementos[0].seletor == 'documento-revisado'
        assert candidata.incidentes_candidata[0].estado == 'aberto'

        with pytest.raises(contrato_portal.ContratoPortalTransicaoError):
            contrato_portal.aceitar_candidata(
                candidata.id, fingerprint_base=baseline.fingerprint,
                revalidacao=_resultado(REVISAO, _diferenca('rotulo_alterado')),
                usuario_id=usuario.id, agora=AGORA)

        ativada = contrato_portal.aceitar_candidata(
            candidata.id, fingerprint_base=baseline.fingerprint,
            revalidacao=ResultadoComparacaoPortal(COMPATIVEL),
            usuario_id=usuario.id, agora=AGORA)

        assert ativada.estado == 'ativa'
        assert ativada.origem == 'usuario'
        assert ativada.ativado_por_id == usuario.id
        assert db.session.get(ContratoPortal, baseline.id).estado == 'arquivada'


def test_rejeitar_candidata_preserva_ativa_e_resolve_incidente(app, ids):
    with app.app_context():
        usuario = _usuario()
        baseline = _baseline(usuario)
        candidata = contrato_portal.criar_candidata_revisao(
            baseline.id, ajustes=(),
            resultado=_resultado(REVISAO, _diferenca('rota_alterada')),
            fingerprint_base=baseline.fingerprint,
            usuario_id=usuario.id, agora=AGORA)

        contrato_portal.rejeitar_candidata(
            candidata.id, usuario_id=usuario.id, agora=AGORA)
        db.session.expire_all()

        assert db.session.get(ContratoPortal, baseline.id).estado == 'ativa'
        assert db.session.get(ContratoPortal, candidata.id).estado == 'rejeitada'
        assert candidata.incidentes_candidata[0].estado == 'rejeitado'


def test_restauracao_clona_historica_como_nova_versao(app, ids):
    with app.app_context():
        usuario = _usuario()
        baseline = _baseline(usuario)
        segunda = contrato_portal.autoativar(
            baseline.id, _resultado(),
            fingerprint_base=baseline.fingerprint, agora=AGORA)

        restaurada = contrato_portal.restaurar(
            baseline.id, fingerprint_ativa=segunda.fingerprint,
            usuario_id=usuario.id, agora=AGORA)

        assert (restaurada.versao, restaurada.estado) == (3, 'ativa')
        assert restaurada.id not in {baseline.id, segunda.id}
        assert restaurada.base_id == baseline.id
        assert restaurada.elementos[0].seletor == 'documento-antigo'
        assert db.session.get(ContratoPortal, baseline.id).estado == 'arquivada'
        assert db.session.get(ContratoPortal, segunda.id).estado == 'arquivada'


def test_evento_de_autoativacao_nao_expoe_valores_de_formulario(
    app, ids, monkeypatch,
):
    with app.app_context():
        baseline = _baseline(_usuario())
        eventos = []
        monkeypatch.setattr(
            contrato_portal, 'log_event',
            lambda evento, **campos: eventos.append((evento, campos)))

        contrato_portal.autoativar(
            baseline.id, _resultado(),
            fingerprint_base=baseline.fingerprint, agora=AGORA)

        evento, campos = eventos[-1]
        serializado = repr(campos)
        assert evento == 'contrato_portal_autoativado'
        assert campos['fluxo'] == 'trabalhista'
        assert campos['alvo'] == 'cndt'
        assert campos['versao_base'] == 1
        assert campos['versao_nova'] == 2
        assert len(campos['fingerprint']) == 12
        assert 'documento-atual' not in serializado
        assert 'CPF ou CNPJ' not in serializado
