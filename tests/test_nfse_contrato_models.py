"""Schema persistente da recon adaptativa da NFS-e."""
from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import (
    CampoContratoNfse,
    ContratoNfse,
    IncidenteContratoNfse,
    OpcaoCampoContratoNfse,
    OpcaoIncidenteContratoNfse,
    Usuario,
)


def _contrato(versao=1, **dados):
    valores = {
        'versao': versao,
        'estado': 'candidata',
        'fingerprint': f'{versao:064d}',
        'criado_em': datetime(2026, 8, 25, 12, 0),
    }
    valores.update(dados)
    return ContratoNfse(**valores)


def _incidente(contrato, assinatura='a' * 64, **dados):
    valores = {
        'contrato_base': contrato,
        'assinatura': assinatura,
        'etapa': 'servico',
        'tipo': 'campo_novo',
        'severidade': 'fiscal',
        'estado': 'aberto',
        'primeira_observacao_em': datetime(2026, 8, 25, 12, 0),
        'ultima_observacao_em': datetime(2026, 8, 25, 12, 0),
        'mensagem': 'Controle novo requer decisão.',
    }
    valores.update(dados)
    return IncidenteContratoNfse(**valores)


def test_modelos_existem_com_estados_string(app, ids):
    with app.app_context():
        contrato = _contrato()
        contrato.campos.append(CampoContratoNfse(
            chave_semantica='servico.codigo', etapa='servico',
            seletor_tipo='id', seletor='campo-sintetico', rotulo='Código',
            tipo='select', interacao='select_direto'))
        db.session.add(contrato)
        db.session.commit()

        assert db.session.get(ContratoNfse, contrato.id).estado == 'candidata'
        assert contrato.campos[0].obrigatorio is False
        assert ContratoNfse.__table__.c.estado.type.length == 20


def test_uniques_de_contrato_campo_opcao_e_incidente(app, ids):
    with app.app_context():
        primeiro = _contrato()
        segundo = _contrato(versao=2)
        primeiro.campos.append(CampoContratoNfse(
            chave_semantica='servico.codigo', etapa='servico',
            seletor_tipo='id', seletor='campo-a', rotulo='Código',
            tipo='select', interacao='select_direto'))
        segundo.campos.append(CampoContratoNfse(
            chave_semantica='servico.codigo', etapa='servico',
            seletor_tipo='id', seletor='campo-b', rotulo='Código',
            tipo='select', interacao='select_direto'))
        primeiro.incidentes.append(_incidente(primeiro))
        db.session.add_all([primeiro, segundo])
        db.session.commit()

        primeiro.campos[0].opcoes.extend([
            OpcaoCampoContratoNfse(valor='A', rotulo='A'),
        ])
        db.session.commit()

        db.session.add(_contrato(versao=1))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

        db.session.add(CampoContratoNfse(
            contrato_id=primeiro.id, chave_semantica='servico.codigo',
            etapa='servico', seletor_tipo='id', seletor='outro', rotulo='Outro',
            tipo='select', interacao='select_direto'))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

        db.session.add(OpcaoCampoContratoNfse(
            campo_id=primeiro.campos[0].id, valor='A', rotulo='Outra'))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

        db.session.add(_incidente(primeiro))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_cascades_removem_filhos_e_opcoes(app, ids):
    with app.app_context():
        contrato = _contrato()
        campo = CampoContratoNfse(
            chave_semantica='servico.codigo', etapa='servico',
            seletor_tipo='id', seletor='campo', rotulo='Código',
            tipo='select', interacao='select_direto')
        campo.opcoes.append(OpcaoCampoContratoNfse(valor='A', rotulo='A'))
        contrato.campos.append(campo)
        incidente = _incidente(contrato)
        incidente.opcoes.append(OpcaoIncidenteContratoNfse(
            valor='B', rotulo='B'))
        contrato.incidentes.append(incidente)
        db.session.add(contrato)
        db.session.commit()
        campo_id = campo.id
        opcao_campo_id = campo.opcoes[0].id
        incidente_id = incidente.id

        db.session.delete(contrato)
        db.session.commit()

        assert db.session.get(CampoContratoNfse, campo_id) is None
        assert db.session.get(OpcaoCampoContratoNfse, opcao_campo_id) is None
        assert db.session.get(IncidenteContratoNfse, incidente_id) is None


def test_remover_usuario_preserva_historico_e_desliga_fks(app, ids):
    with app.app_context():
        usuario = Usuario(
            username='operador_sintetico', senha_hash='hash-sintetico',
            papel='operador')
        db.session.add(usuario)
        db.session.flush()
        contrato = _contrato(
            criado_por=usuario, ativado_por=usuario, estado='ativa')
        contrato.incidentes.append(_incidente(
            contrato, resolvido_por=usuario, estado='resolvido'))
        db.session.add(contrato)
        db.session.commit()
        contrato_id = contrato.id
        incidente_id = contrato.incidentes[0].id

        db.session.delete(usuario)
        db.session.commit()
        db.session.expire_all()

        preservado = db.session.get(ContratoNfse, contrato_id)
        incidente = db.session.get(IncidenteContratoNfse, incidente_id)
        assert preservado is not None
        assert preservado.criado_por_id is None
        assert preservado.ativado_por_id is None
        assert incidente is not None
        assert incidente.resolvido_por_id is None
