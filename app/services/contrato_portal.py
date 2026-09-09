"""Persistência transacional dos contratos adaptativos de certidões."""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app import db
from app.models import (
    ContratoPortal,
    DiferencaContratoPortal,
    ElementoContratoPortal,
    IncidenteContratoPortal,
)
from app.services.contrato_portal_drift import (
    AUTOATIVAVEL,
    COMPATIVEL,
    REVISAO,
    ContratoComparavel,
    RemapeamentoSeletor,
    ResultadoComparacaoPortal,
)
from app.services.execution_logger import log_event
from app.utils import utcnow_naive


class ContratoPortalError(RuntimeError):
    pass


class ContratoPortalNaoEncontradoError(ContratoPortalError):
    pass


class ContratoPortalConflitoError(ContratoPortalError):
    pass


class ContratoPortalTransicaoError(ContratoPortalError):
    pass


class BaselineRequerHumanoError(ContratoPortalError):
    pass


class PersistenciaContratoPortalError(ContratoPortalError):
    pass


def _agora(valor=None):
    return valor or utcnow_naive()


def _carregar(contrato_id, *, bloquear=False):
    consulta = ContratoPortal.query.options(
        selectinload(ContratoPortal.elementos))
    if bloquear:
        consulta = consulta.with_for_update()
    contrato = consulta.filter_by(id=contrato_id).one_or_none()
    if contrato is None:
        raise ContratoPortalNaoEncontradoError('Contrato de portal não encontrado.')
    return contrato


def _proxima_versao(fluxo, alvo):
    atual = db.session.query(func.max(ContratoPortal.versao)).filter_by(
        fluxo=fluxo, alvo=alvo).scalar()
    return int(atual or 0) + 1


def _dados_elemento(elemento):
    return {
        'chave': elemento.chave,
        'etapa': elemento.etapa,
        'papel': elemento.papel,
        'acao': elemento.acao,
        'seletor_tipo': elemento.seletor_tipo,
        'seletor': elemento.seletor,
        'tag': elemento.tag,
        'tipo': elemento.tipo,
        'rotulo': elemento.rotulo,
        'assinatura_formulario': elemento.assinatura_formulario,
        'ordem_relativa': elemento.ordem_relativa,
        'obrigatorio': elemento.obrigatorio,
        'visivel': elemento.visivel,
        'somente_leitura': elemento.somente_leitura,
        'autoajuste_seletor': elemento.autoajuste_seletor,
    }


def _fingerprint(host, rota, elementos):
    estrutura = {
        'host': host,
        'rota': rota,
        'elementos': sorted(
            (_dados_elemento(item) for item in elementos),
            key=lambda item: (item['etapa'], item['ordem_relativa'], item['chave'])),
    }
    bruto = json.dumps(
        estrutura, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(bruto).hexdigest()


def _novo_contrato(
    *, fluxo, alvo, host, rota, estado, origem, criado_em,
    criado_por_id=None, base=None,
):
    return ContratoPortal(
        fluxo=fluxo,
        alvo=alvo,
        versao=_proxima_versao(fluxo, alvo),
        estado=estado,
        host=host,
        rota=rota,
        fingerprint='0' * 64,
        base=base,
        origem=origem,
        criado_em=criado_em,
        classificado_em=criado_em,
        criado_por_id=criado_por_id,
    )


def criar_baseline(
    *, fluxo: str, alvo: str, definicao: ContratoComparavel,
    usuario_id: int | None, agora=None,
) -> ContratoPortal:
    """Cria a primeira versão somente como decisão humana explícita."""
    if usuario_id is None:
        raise BaselineRequerHumanoError(
            'A baseline inicial exige revisão humana.')
    instante = _agora(agora)
    try:
        existente = ContratoPortal.query.filter_by(
            fluxo=fluxo, alvo=alvo).first()
        if existente is not None:
            raise ContratoPortalConflitoError(
                'O alvo já possui histórico de contrato.')
        contrato = _novo_contrato(
            fluxo=fluxo, alvo=alvo, host=definicao.host,
            rota=definicao.rota, estado='ativa', origem='usuario',
            criado_em=instante, criado_por_id=usuario_id)
        contrato.ativado_em = instante
        contrato.ativado_por_id = usuario_id
        contrato.elementos = [
            ElementoContratoPortal(**_dados_elemento(elemento))
            for elemento in definicao.elementos
        ]
        contrato.fingerprint = _fingerprint(
            contrato.host, contrato.rota, contrato.elementos)
        db.session.add(contrato)
        db.session.commit()
        log_event(
            'contrato_portal_baseline_criada', fluxo=fluxo, alvo=alvo,
            versao=contrato.versao,
            fingerprint=contrato.fingerprint[:12], origem='usuario')
        return contrato
    except ContratoPortalError:
        db.session.rollback()
        raise
    except IntegrityError as erro:
        db.session.rollback()
        raise ContratoPortalConflitoError(
            'Conflito ao criar a baseline do portal.') from erro
    except Exception as erro:
        db.session.rollback()
        raise PersistenciaContratoPortalError(
            'Não foi possível criar a baseline do portal.') from erro


def _assinatura_resultado(resultado):
    assinaturas = sorted(item.assinatura for item in resultado.diferencas)
    return hashlib.sha256('|'.join(assinaturas).encode('ascii')).hexdigest()


def _registrar_incidente(
    base, resultado, *, instante, candidata=None, artefato_sanitizado=None,
):
    if not resultado.diferencas:
        raise ContratoPortalTransicaoError(
            'Resultado sem diferenças não gera incidente.')
    assinatura = _assinatura_resultado(resultado)
    incidente = IncidenteContratoPortal.query.filter_by(
        contrato_base_id=base.id, assinatura=assinatura).one_or_none()
    if incidente is not None:
        incidente.observacoes += 1
        incidente.ultima_observacao_em = instante
        incidente.estado = 'aberto'
        incidente.resolvido_em = None
        incidente.resolvido_por_id = None
        incidente.contrato_candidato = candidata
        return incidente

    primeira = resultado.diferencas[0]
    incidente = IncidenteContratoPortal(
        contrato_base=base,
        contrato_candidato=candidata,
        assinatura=assinatura,
        estado='aberto',
        classificacao=resultado.classificacao,
        severidade='bloqueante',
        etapa=primeira.etapa,
        elemento_chave=primeira.elemento_chave,
        mensagem='A estrutura observada diverge do contrato ativo.',
        artefato_sanitizado=artefato_sanitizado,
        primeira_observacao_em=instante,
        ultima_observacao_em=instante,
    )
    for ordem, diferenca in enumerate(resultado.diferencas):
        incidente.diferencas.append(DiferencaContratoPortal(
            ordem=ordem,
            dimensao=diferenca.tipo,
            esperado=diferenca.esperado,
            observado=diferenca.observado,
            evidencia_sanitizada=json.dumps(
                diferenca.evidencias, ensure_ascii=False),
        ))
    db.session.add(incidente)
    return incidente


def registrar_incidente(
    contrato_base_id, resultado, *, agora=None, artefato_sanitizado=None,
):
    instante = _agora(agora)
    try:
        base = _carregar(contrato_base_id, bloquear=True)
        incidente = _registrar_incidente(
            base, resultado, instante=instante,
            artefato_sanitizado=artefato_sanitizado)
        db.session.commit()
        log_event(
            'contrato_portal_drift', fluxo=base.fluxo, alvo=base.alvo,
            versao=base.versao, classificacao=resultado.classificacao,
            assinatura=incidente.assinatura[:12])
        return incidente
    except ContratoPortalError:
        db.session.rollback()
        raise
    except IntegrityError as erro:
        db.session.rollback()
        raise ContratoPortalConflitoError(
            'Conflito ao registrar incidente do portal.') from erro
    except Exception as erro:
        db.session.rollback()
        # A causa fica no log: a mensagem que sobe é a mesma para qualquer
        # falha de persistência, e sem isto o diagnóstico começa do zero — foi
        # o que custou horas no DataError de largura de coluna.
        log_event(
            'contrato_portal_incidente_persistencia_falhou', level='ERROR',
            contrato_base_id=contrato_base_id,
            classificacao=resultado.classificacao,
            error_type=type(erro).__name__, error=str(erro)[:300])
        raise PersistenciaContratoPortalError(
            'Não foi possível registrar o incidente do portal.') from erro


def _validar_base_ativa(base, fingerprint_base):
    if base.estado != 'ativa' or base.fingerprint != fingerprint_base:
        raise ContratoPortalConflitoError(
            'O contrato ativo mudou desde a observação.')


def _clonar(
    fonte, *, estado, origem, instante, ajustes=(),
    criado_por_id=None, base=None,
):
    por_chave = {item.chave: item for item in ajustes}
    chaves_fonte = {item.chave for item in fonte.elementos}
    if set(por_chave) - chaves_fonte:
        raise ContratoPortalTransicaoError(
            'O ajuste referencia elemento que não pertence ao contrato.')
    candidata = _novo_contrato(
        fluxo=fonte.fluxo, alvo=fonte.alvo, host=fonte.host,
        rota=fonte.rota, estado=estado, origem=origem,
        criado_em=instante, criado_por_id=criado_por_id,
        base=base or fonte)
    for elemento in fonte.elementos:
        dados = _dados_elemento(elemento)
        ajuste = por_chave.get(elemento.chave)
        if ajuste is not None:
            if (
                ajuste.seletor_tipo_anterior != elemento.seletor_tipo
                or ajuste.seletor_anterior != elemento.seletor
            ):
                raise ContratoPortalConflitoError(
                    'O seletor-base mudou desde a comparação.')
            dados['seletor_tipo'] = ajuste.seletor_tipo_novo
            dados['seletor'] = ajuste.seletor_novo
        candidata.elementos.append(ElementoContratoPortal(**dados))
    candidata.fingerprint = _fingerprint(
        candidata.host, candidata.rota, candidata.elementos)
    db.session.add(candidata)
    db.session.flush()
    return candidata


def _promover(base_ativa, candidata, *, instante, usuario_id=None):
    base_ativa.estado = 'arquivada'
    db.session.flush()
    candidata.estado = 'ativa'
    candidata.ativado_em = instante
    candidata.ativado_por_id = usuario_id
    db.session.flush()


def _resolver_incidentes(candidata, *, instante, usuario_id=None, estado='resolvido'):
    for incidente in candidata.incidentes_candidata:
        incidente.estado = estado
        incidente.resolvido_em = instante
        incidente.resolvido_por_id = usuario_id


def autoativar(
    contrato_base_id,
    resultado: ResultadoComparacaoPortal,
    *, fingerprint_base: str,
    agora=None,
) -> ContratoPortal:
    if (
        resultado.classificacao != AUTOATIVAVEL
        or len(resultado.diferencas) != 1
        or resultado.diferencas[0].tipo != 'seletor_alterado'
        or len(resultado.remapeamentos) != 1
        or resultado.diferencas[0].elemento_chave
        != resultado.remapeamentos[0].chave
    ):
        raise ContratoPortalTransicaoError(
            'Somente comparação autoativável pode ser promovida automaticamente.')
    instante = _agora(agora)
    try:
        base = _carregar(contrato_base_id, bloquear=True)
        _validar_base_ativa(base, fingerprint_base)
        candidata = _clonar(
            base, estado='candidata_auto', origem='sistema',
            instante=instante, ajustes=resultado.remapeamentos)
        _registrar_incidente(
            base, resultado, instante=instante, candidata=candidata)
        _promover(base, candidata, instante=instante)
        _resolver_incidentes(candidata, instante=instante)
        db.session.commit()
        log_event(
            'contrato_portal_autoativado', fluxo=base.fluxo, alvo=base.alvo,
            versao_base=base.versao, versao_nova=candidata.versao,
            fingerprint=candidata.fingerprint[:12], origem='sistema')
        return candidata
    except ContratoPortalError:
        db.session.rollback()
        raise
    except IntegrityError as erro:
        db.session.rollback()
        raise ContratoPortalConflitoError(
            'Outra promoção alterou o contrato ativo.') from erro
    except Exception as erro:
        db.session.rollback()
        log_event(
            'contrato_portal_promocao_falhou', level='ERROR',
            contrato_id=contrato_base_id)
        raise PersistenciaContratoPortalError(
            'Não foi possível promover o contrato do portal.') from erro


def criar_candidata_revisao(
    contrato_base_id,
    *,
    ajustes: tuple[RemapeamentoSeletor, ...],
    resultado: ResultadoComparacaoPortal,
    fingerprint_base: str,
    usuario_id: int,
    agora=None,
) -> ContratoPortal:
    if resultado.classificacao != REVISAO:
        raise ContratoPortalTransicaoError(
            'A candidata manual exige uma comparação em revisão.')
    instante = _agora(agora)
    try:
        base = _carregar(contrato_base_id, bloquear=True)
        _validar_base_ativa(base, fingerprint_base)
        candidata = _clonar(
            base, estado='candidata_revisao', origem='usuario',
            instante=instante, ajustes=ajustes, criado_por_id=usuario_id)
        _registrar_incidente(
            base, resultado, instante=instante, candidata=candidata)
        db.session.commit()
        return candidata
    except ContratoPortalError:
        db.session.rollback()
        raise
    except IntegrityError as erro:
        db.session.rollback()
        raise ContratoPortalConflitoError(
            'Conflito ao criar candidata para revisão.') from erro
    except Exception as erro:
        db.session.rollback()
        raise PersistenciaContratoPortalError(
            'Não foi possível criar a candidata do portal.') from erro


def aceitar_candidata(
    contrato_candidato_id,
    *, fingerprint_base: str,
    revalidacao: ResultadoComparacaoPortal,
    usuario_id: int,
    agora=None,
) -> ContratoPortal:
    if revalidacao.classificacao != COMPATIVEL:
        db.session.rollback()
        raise ContratoPortalTransicaoError(
            'A candidata precisa ser revalidada contra observação recente.')
    instante = _agora(agora)
    try:
        candidata = _carregar(contrato_candidato_id, bloquear=True)
        if candidata.estado != 'candidata_revisao' or candidata.base_id is None:
            raise ContratoPortalTransicaoError(
                'A versão não está disponível para aprovação.')
        base = _carregar(candidata.base_id, bloquear=True)
        _validar_base_ativa(base, fingerprint_base)
        _promover(base, candidata, instante=instante, usuario_id=usuario_id)
        _resolver_incidentes(
            candidata, instante=instante, usuario_id=usuario_id)
        db.session.commit()
        log_event(
            'contrato_portal_ativado', fluxo=base.fluxo, alvo=base.alvo,
            versao_base=base.versao, versao_nova=candidata.versao,
            fingerprint=candidata.fingerprint[:12], origem='usuario')
        return candidata
    except ContratoPortalError:
        db.session.rollback()
        raise
    except IntegrityError as erro:
        db.session.rollback()
        raise ContratoPortalConflitoError(
            'Outra promoção alterou o contrato ativo.') from erro
    except Exception as erro:
        db.session.rollback()
        raise PersistenciaContratoPortalError(
            'Não foi possível aprovar a candidata do portal.') from erro


def rejeitar_candidata(
    contrato_candidato_id, *, usuario_id: int, agora=None,
) -> ContratoPortal:
    instante = _agora(agora)
    try:
        candidata = _carregar(contrato_candidato_id, bloquear=True)
        if candidata.estado != 'candidata_revisao':
            raise ContratoPortalTransicaoError(
                'A versão não está disponível para rejeição.')
        candidata.estado = 'rejeitada'
        _resolver_incidentes(
            candidata, instante=instante, usuario_id=usuario_id,
            estado='rejeitado')
        db.session.commit()
        return candidata
    except ContratoPortalError:
        db.session.rollback()
        raise
    except Exception as erro:
        db.session.rollback()
        raise PersistenciaContratoPortalError(
            'Não foi possível rejeitar a candidata do portal.') from erro


def restaurar(
    contrato_historico_id,
    *, fingerprint_ativa: str,
    usuario_id: int,
    agora=None,
) -> ContratoPortal:
    instante = _agora(agora)
    try:
        historico = _carregar(contrato_historico_id, bloquear=True)
        ativa = ContratoPortal.query.options(
            selectinload(ContratoPortal.elementos)).filter_by(
                fluxo=historico.fluxo, alvo=historico.alvo,
                estado='ativa').with_for_update().one_or_none()
        if ativa is None:
            raise ContratoPortalConflitoError('O alvo não possui contrato ativo.')
        if ativa.id == historico.id:
            raise ContratoPortalTransicaoError(
                'A versão já é a ativa do alvo.')
        _validar_base_ativa(ativa, fingerprint_ativa)
        restaurada = _clonar(
            historico, estado='candidata_revisao', origem='usuario',
            instante=instante, criado_por_id=usuario_id, base=historico)
        _promover(ativa, restaurada, instante=instante, usuario_id=usuario_id)
        db.session.commit()
        log_event(
            'contrato_portal_restaurado', fluxo=ativa.fluxo, alvo=ativa.alvo,
            versao_base=ativa.versao, versao_nova=restaurada.versao,
            fingerprint=restaurada.fingerprint[:12], origem='usuario')
        return restaurada
    except ContratoPortalError:
        db.session.rollback()
        raise
    except IntegrityError as erro:
        db.session.rollback()
        raise ContratoPortalConflitoError(
            'Outra promoção alterou o contrato ativo.') from erro
    except Exception as erro:
        db.session.rollback()
        raise PersistenciaContratoPortalError(
            'Não foi possível restaurar o contrato do portal.') from erro
