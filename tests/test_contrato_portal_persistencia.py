"""Incidente de portal real carrega o inventário inteiro — e ele não cabia.

Achado real (2026-09-09): ativar a baseline do Trabalhista e observar o portal
derrubava a emissão com "Não foi possível registrar o incidente do portal".
A causa é a divergência SQLite×MySQL de largura de VARCHAR: o artefato é o JSON
de TODOS os elementos da página, e o SQLite ignora o limite que o InnoDB impõe.
"""
import json
from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from app import db
from app.automation import trabalhista
from app.models import ContratoPortal, IncidenteContratoPortal, Usuario
from app.services import contrato_portal, contrato_portal_preflight
from app.automation.trabalhista_recon import ElementoInventariado, InventarioPortal
from app.services.contrato_portal_drift import (
    COMPATIVEL,
    RemapeamentoSeletor,
    ResultadoComparacaoPortal,
    _diferenca,
    comparar,
)


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


# --- achados da revisão do PR #51 -------------------------------------------

def _baseline_com_controle_desabilitado(usuario_id):
    """Baseline igual à do CNDT, mas com o submeter desabilitado."""
    declaracao = trabalhista.definicao_baseline()
    elementos = tuple(
        replace(item, desabilitado=(item.chave == 'submeter'))
        for item in declaracao.elementos)
    return contrato_portal.criar_baseline(
        fluxo=trabalhista.FLUXO_CONTRATO, alvo=trabalhista.ALVO_CONTRATO,
        definicao=replace(declaracao, elementos=elementos),
        usuario_id=usuario_id)


def test_habilitacao_sobrevive_ao_banco_e_nao_vira_drift(app, ids):
    """A coluna faltava e o comparador comparava a dimensão mesmo assim.

    Recarregado, o elemento voltava sempre como habilitado — e um controle que
    nasce desabilitado na baseline gerava `habilitacao_alterada` espúrio em todo
    preflight seguinte, bloqueando o alvo para sempre.
    """
    with app.app_context():
        usuario = Usuario(
            username='admin_habilitacao', senha_hash='hash-sintetico',
            papel='admin')
        db.session.add(usuario)
        db.session.commit()
        ativo = _baseline_com_controle_desabilitado(usuario.id)
        db.session.expire_all()

        recarregado = contrato_portal_preflight.comparavel(
            db.session.get(ContratoPortal, ativo.id))
        por_chave = {item.chave: item for item in recarregado.elementos}

        assert por_chave['submeter'].desabilitado is True
        assert por_chave['documento'].desabilitado is False
        # A prova que importa: comparado com a tela de onde saiu, é compatível.
        assert comparar(
            recarregado, _inventario_da_declaracao(recarregado),
        ).classificacao == COMPATIVEL


def test_reobservar_o_mesmo_drift_nao_solta_a_candidata(app, ids):
    """Vínculo só se cria, nunca se apaga.

    O preflight reobserva sem candidata nenhuma; sobrescrever com None soltava a
    candidata em revisão, e o incidente ficava fora do alcance de
    `_resolver_incidentes` — aberto para sempre no painel.
    """
    with app.app_context():
        usuario = Usuario(
            username='admin_vinculo', senha_hash='hash-sintetico', papel='admin')
        db.session.add(usuario)
        db.session.commit()
        base = contrato_portal.criar_baseline(
            fluxo=trabalhista.FLUXO_CONTRATO, alvo=trabalhista.ALVO_CONTRATO,
            definicao=trabalhista.definicao_baseline(), usuario_id=usuario.id)
        resultado = _resultado_revisao()
        candidata = contrato_portal.criar_candidata_revisao(
            base.id,
            ajustes=(RemapeamentoSeletor(
                chave='submeter', seletor_tipo_anterior='id',
                seletor_anterior='botao-emitir', seletor_tipo_novo='id',
                seletor_novo='botao-emitir-novo'),),
            resultado=resultado,
            fingerprint_base=base.fingerprint,
            usuario_id=usuario.id)
        incidente_id = IncidenteContratoPortal.query.filter_by(
            contrato_base_id=base.id).one().id
        assert db.session.get(
            IncidenteContratoPortal, incidente_id).contrato_candidato_id == candidata.id

        # O preflight vê o mesmo drift de novo, sem candidata.
        contrato_portal.registrar_incidente(base.id, resultado)

        incidente = db.session.get(IncidenteContratoPortal, incidente_id)
        assert incidente.contrato_candidato_id == candidata.id
        assert incidente.observacoes == 2


def _inventario_da_declaracao(modelo):
    """Inventário que reproduz exatamente o contrato dado."""
    return InventarioPortal(
        host=modelo.host, rota=modelo.rota, etapa=modelo.etapa,
        elementos=tuple(ElementoInventariado(
            tag=item.tag, tipo=item.tipo, id=item.seletor, name='',
            rotulo=item.rotulo, seletor_tipo=item.seletor_tipo,
            seletor=item.seletor,
            assinatura_formulario=item.assinatura_formulario,
            ordem_relativa=item.ordem_relativa,
            obrigatorio=item.obrigatorio, desabilitado=item.desabilitado,
            somente_leitura=item.somente_leitura, visivel=item.visivel,
        ) for item in modelo.elementos))
