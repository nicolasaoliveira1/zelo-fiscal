"""Schema persistente dos contratos adaptativos de certidões."""
from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import (
    ContratoPortal,
    DiferencaContratoPortal,
    ElementoContratoPortal,
    IncidenteContratoPortal,
    Usuario,
)


AGORA = datetime(2026, 1, 15, 10, 30)


def _contrato(versao=1, **dados):
    valores = {
        'fluxo': 'trabalhista',
        'alvo': 'cndt',
        'versao': versao,
        'estado': 'candidata_revisao',
        'host': 'portal.exemplo.gov.br',
        'rota': '/certidao/emitir',
        'fingerprint': f'{versao:064d}',
        'origem': 'sistema',
        'criado_em': AGORA,
    }
    valores.update(dados)
    return ContratoPortal(**valores)


def _elemento(chave='documento', **dados):
    valores = {
        'chave': chave,
        'etapa': 'formulario',
        'papel': 'entrada_documento',
        'acao': 'preencher',
        'seletor_tipo': 'id',
        'seletor': 'documento-sintetico',
        'tag': 'input',
        'tipo': 'text',
        'rotulo': 'documento',
        'assinatura_formulario': 'f' * 64,
        'ordem_relativa': 1,
        'obrigatorio': True,
        'autoajuste_seletor': True,
    }
    valores.update(dados)
    return ElementoContratoPortal(**valores)


def _incidente(contrato, assinatura='a' * 64, **dados):
    valores = {
        'contrato_base': contrato,
        'assinatura': assinatura,
        'estado': 'aberto',
        'classificacao': 'revisao',
        'severidade': 'bloqueante',
        'etapa': 'formulario',
        'elemento_chave': 'documento',
        'mensagem': 'Mudança estrutural requer revisão.',
        'primeira_observacao_em': AGORA,
        'ultima_observacao_em': AGORA,
    }
    valores.update(dados)
    return IncidenteContratoPortal(**valores)


def test_contrato_persiste_identidade_estrutura_e_auditoria(app, ids):
    with app.app_context():
        usuario = Usuario(
            username='admin_sintetico', senha_hash='hash-sintetico',
            papel='admin')
        base = _contrato(estado='ativa', origem='usuario', criado_por=usuario,
                         ativado_por=usuario, classificado_em=AGORA,
                         ativado_em=AGORA)
        base.elementos.append(_elemento())
        candidata = _contrato(versao=2, base=base)
        db.session.add_all([base, candidata])
        db.session.commit()
        db.session.expire_all()

        gravado = db.session.get(ContratoPortal, base.id)
        elemento = gravado.elementos[0]
        assert (gravado.fluxo, gravado.alvo, gravado.versao) == (
            'trabalhista', 'cndt', 1)
        assert (gravado.host, gravado.rota) == (
            'portal.exemplo.gov.br', '/certidao/emitir')
        assert gravado.estado == 'ativa'
        assert gravado.ativa_unica is not None
        assert gravado.origem == 'usuario'
        assert (gravado.criado_em, gravado.classificado_em,
                gravado.ativado_em) == (AGORA, AGORA, AGORA)
        assert gravado.criado_por_id == usuario.id
        assert gravado.ativado_por_id == usuario.id
        assert candidata.base_id == gravado.id
        assert (
            elemento.chave, elemento.etapa, elemento.papel, elemento.acao,
            elemento.seletor_tipo, elemento.seletor,
        ) == (
            'documento', 'formulario', 'entrada_documento', 'preencher',
            'id', 'documento-sintetico',
        )
        assert (
            elemento.tag, elemento.tipo, elemento.rotulo,
            elemento.assinatura_formulario, elemento.ordem_relativa,
        ) == ('input', 'text', 'documento', 'f' * 64, 1)
        assert elemento.autoajuste_seletor is True
        assert ContratoPortal.__table__.c.estado.type.length == 30


def test_banco_impede_duas_versoes_ativas_do_mesmo_alvo(app, ids):
    with app.app_context():
        db.session.add_all([
            _contrato(versao=1, estado='ativa'),
            _contrato(versao=2, estado='ativa'),
        ])

        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

        db.session.add_all([
            _contrato(versao=1, estado='ativa'),
            _contrato(versao=1, estado='ativa', alvo='outro-alvo'),
        ])
        db.session.commit()
        assert ContratoPortal.query.filter_by(estado='ativa').count() == 2


def test_banco_impede_versao_chave_e_incidente_duplicados(app, ids):
    with app.app_context():
        base = _contrato()
        base.elementos.append(_elemento())
        base.incidentes.append(_incidente(base))
        db.session.add(base)
        db.session.commit()

        db.session.add(_contrato())
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

        db.session.add(_elemento(contrato_id=base.id))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

        db.session.add(_incidente(base, contrato_base_id=base.id))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_cascade_remove_elementos_incidentes_e_diferencas(app, ids):
    with app.app_context():
        contrato = _contrato()
        contrato.elementos.append(_elemento())
        incidente = _incidente(contrato)
        incidente.diferencas.append(DiferencaContratoPortal(
            dimensao='rota', esperado='/certidao/emitir',
            observado='/pagina-divergente',
            evidencia_sanitizada='metadados reconstruídos'))
        contrato.incidentes.append(incidente)
        db.session.add(contrato)
        db.session.commit()
        ids_filhos = (
            contrato.elementos[0].id,
            incidente.id,
            incidente.diferencas[0].id,
        )

        db.session.delete(contrato)
        db.session.commit()

        assert db.session.get(ElementoContratoPortal, ids_filhos[0]) is None
        assert db.session.get(IncidenteContratoPortal, ids_filhos[1]) is None
        assert db.session.get(DiferencaContratoPortal, ids_filhos[2]) is None


def test_set_null_preserva_historico_ao_remover_usuario_e_base(app, ids):
    with app.app_context():
        usuario = Usuario(
            username='revisor_sintetico', senha_hash='hash-sintetico',
            papel='admin')
        base = _contrato(
            estado='arquivada', criado_por=usuario, ativado_por=usuario)
        candidata = _contrato(versao=2, base=base)
        incidente = _incidente(
            candidata, assinatura='b' * 64, resolvido_por=usuario,
            estado='resolvido')
        candidata.incidentes.append(incidente)
        db.session.add_all([base, candidata])
        db.session.commit()
        base_id = base.id
        candidata_id = candidata.id
        incidente_id = incidente.id

        db.session.delete(usuario)
        db.session.commit()
        db.session.expire_all()

        base = db.session.get(ContratoPortal, base_id)
        incidente = db.session.get(IncidenteContratoPortal, incidente_id)
        assert base.criado_por_id is None
        assert base.ativado_por_id is None
        assert incidente.resolvido_por_id is None

        db.session.delete(base)
        db.session.commit()
        db.session.expire_all()

        assert db.session.get(ContratoPortal, candidata_id).base_id is None
        assert db.session.get(IncidenteContratoPortal, incidente_id) is not None


def test_schema_nao_oferece_coluna_para_valor_ou_dom_bruto(app, ids):
    nomes_elemento = set(ElementoContratoPortal.__table__.columns.keys())
    nomes_incidente = set(IncidenteContratoPortal.__table__.columns.keys())

    assert 'valor' not in nomes_elemento
    assert 'documento' not in nomes_elemento
    assert 'html_bruto' not in nomes_incidente
    assert 'artefato_sanitizado' in nomes_incidente
