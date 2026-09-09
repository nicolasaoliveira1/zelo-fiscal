"""Incidente de portal real carrega o inventário inteiro — e ele não cabia.

Achado real (2026-09-09): ativar a baseline do Trabalhista e observar o portal
derrubava a emissão com "Não foi possível registrar o incidente do portal".
A causa é a divergência SQLite×MySQL de largura de VARCHAR: o artefato é o JSON
de TODOS os elementos da página, e o SQLite ignora o limite que o InnoDB impõe.
"""
import json
from unittest.mock import MagicMock

import pytest

from app import db
from app.automation import trabalhista
from app.models import IncidenteContratoPortal, Usuario
from app.services import contrato_portal, contrato_portal_preflight
from app.services.contrato_portal_drift import ResultadoComparacaoPortal, _diferenca


def _artefato_realista():
    """JSON no formato do inventário, no tamanho de uma página de portal real."""
    elementos = [{
        'tag': 'input', 'tipo': 'text', 'id': f'campo-{i:03d}',
        'name': f'form:campo-{i:03d}', 'rotulo': 'Rótulo bem descritivo ' * 4,
        'seletor_tipo': 'id', 'seletor': f'campo-{i:03d}',
        'assinatura_formulario': 'f' * 64, 'ordem_relativa': i,
        'obrigatorio': False, 'desabilitado': False,
        'somente_leitura': False, 'visivel': True,
    } for i in range(40)]
    return json.dumps(
        {'host': 'portal.exemplo', 'rota': '/tela', 'etapa': 'formulario',
         'formularios': [], 'elementos': elementos},
        ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def test_incidente_aceita_o_inventario_inteiro(app, ids):
    """O artefato de um portal real passa de 2000 caracteres com folga."""
    artefato = _artefato_realista()
    assert len(artefato) > 2000

    with app.app_context():
        usuario = Usuario(
            username='admin_artefato', senha_hash='hash-sintetico', papel='admin')
        db.session.add(usuario)
        db.session.commit()
        base = contrato_portal.criar_baseline(
            fluxo=trabalhista.FLUXO_CONTRATO, alvo=trabalhista.ALVO_CONTRATO,
            definicao=trabalhista.definicao_baseline(), usuario_id=usuario.id)
        resultado = ResultadoComparacaoPortal(
            classificacao='revisao',
            diferencas=(_diferenca(
                'seletor_alterado', 'formulario', 'submeter',
                'botao-emitir', 'botao-emitir-novo', 'x' * 1500),),
        )

        contrato_portal.registrar_incidente(
            base.id, resultado, artefato_sanitizado=artefato)

        incidente = IncidenteContratoPortal.query.filter_by(
            contrato_base_id=base.id, estado='aberto').one()
        assert incidente.artefato_sanitizado == artefato
        assert len(incidente.diferencas) == 1


def _resultado_revisao():
    return ResultadoComparacaoPortal(
        classificacao='revisao',
        diferencas=(_diferenca(
            'seletor_alterado', 'formulario', 'submeter',
            'botao-emitir', 'botao-emitir-novo'),),
    )


def _inventario_falso():
    class _Inventario:
        artefato_sanitizado = '{}'
    return _Inventario()


def test_falha_ao_escriturar_ainda_bloqueia_a_execucao(app, ids, monkeypatch):
    """Escriturar é bookkeeping; bloquear é a decisão de segurança.

    Antes, o erro de persistência subia cru e a emissão morria em
    `emit_selenium_error` — achado real com o DataError de largura.
    """
    with app.app_context():
        usuario = Usuario(
            username='admin_bloqueio', senha_hash='hash-sintetico', papel='admin')
        db.session.add(usuario)
        db.session.commit()
        ativo = contrato_portal.criar_baseline(
            fluxo=trabalhista.FLUXO_CONTRATO, alvo=trabalhista.ALVO_CONTRATO,
            definicao=trabalhista.definicao_baseline(), usuario_id=usuario.id)
        monkeypatch.setattr(
            contrato_portal_preflight.contrato_portal, 'registrar_incidente',
            MagicMock(side_effect=contrato_portal.PersistenciaContratoPortalError(
                'Não foi possível registrar o incidente do portal.')))
        monkeypatch.setattr(
            contrato_portal_preflight, 'comparar',
            lambda *a, **k: _resultado_revisao())

        with pytest.raises(
                contrato_portal_preflight.ContratoPortalBloqueadoError):
            contrato_portal_preflight.executar(
                fluxo=trabalhista.FLUXO_CONTRATO,
                alvo=trabalhista.ALVO_CONTRATO,
                observar=lambda contrato: _inventario_falso(),
                contrato_ativo=ativo,
            )


def test_promocao_que_nao_persiste_falha_fechada(app, ids, monkeypatch):
    """Seguir com a versão antiga esbarraria no elemento mudado, captcha gasto."""
    with app.app_context():
        usuario = Usuario(
            username='admin_promocao', senha_hash='hash-sintetico', papel='admin')
        db.session.add(usuario)
        db.session.commit()
        ativo = contrato_portal.criar_baseline(
            fluxo=trabalhista.FLUXO_CONTRATO, alvo=trabalhista.ALVO_CONTRATO,
            definicao=trabalhista.definicao_baseline(), usuario_id=usuario.id)
        monkeypatch.setattr(
            contrato_portal_preflight, 'comparar',
            lambda *a, **k: ResultadoComparacaoPortal(classificacao='autoativavel'))
        monkeypatch.setattr(
            contrato_portal_preflight.contrato_portal, 'autoativar',
            MagicMock(side_effect=contrato_portal.PersistenciaContratoPortalError(
                'Não foi possível promover.')))

        with pytest.raises(
                contrato_portal_preflight.ContratoPortalBloqueadoError):
            contrato_portal_preflight.executar(
                fluxo=trabalhista.FLUXO_CONTRATO,
                alvo=trabalhista.ALVO_CONTRATO,
                observar=lambda contrato: _inventario_falso(),
                contrato_ativo=ativo,
            )
