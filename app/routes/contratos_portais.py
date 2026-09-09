"""Central de contratos dos portais no Diagnóstico (RAC-06, RAC-14).

Rotas finas: quem observa, compara e promove é `contrato_portal_recon` e
`contrato_portal`. O que fica aqui é a tradução do que o portal decidiu para o
envelope JSON do projeto, e três recusas que a UI precisa distinguir — 404 (não
existe), 409 (o estado mudou debaixo da decisão) e 423 (o alvo está ocupado por
uma emissão).

Nada nesta superfície emite documento: toda ação chega, no máximo, à tela
observável do portal.
"""
from flask import jsonify, request
from flask_login import current_user

from app import db
from app.auth import requer_papel
from app.models import ContratoPortal, IncidenteContratoPortal
from app.routes import bp
from app.routes.lotes import _criar_driver_lote
from app.services import auditoria, contrato_portal, contrato_portal_recon
from app.services.contrato_portal_drift import BaselineNaoObservavelError
from app.services.execution_logger import log_event
from app.utils import json_error as _json_error


def _iso(valor):
    return valor.isoformat() if valor else None


def _incidentes_abertos(contrato_id):
    incidentes = (IncidenteContratoPortal.query
                  .filter_by(contrato_base_id=contrato_id, estado='aberto')
                  .order_by(IncidenteContratoPortal.ultima_observacao_em.desc())
                  .all())
    return [{
        'id': item.id,
        'classificacao': item.classificacao,
        'severidade': item.severidade,
        'etapa': item.etapa,
        'elemento': item.elemento_chave,
        'mensagem': item.mensagem,
        'primeira_observacao_em': _iso(item.primeira_observacao_em),
        'ultima_observacao_em': _iso(item.ultima_observacao_em),
    } for item in incidentes]


def _historico(fluxo, alvo, ativa_id):
    versoes = (ContratoPortal.query
               .filter_by(fluxo=fluxo, alvo=alvo)
               .order_by(ContratoPortal.versao.desc())
               .limit(20).all())
    return [{
        'id': item.id,
        'versao': item.versao,
        'estado': item.estado,
        # `sistema` é a autoativação; `usuario` é baseline, revisão aceita ou
        # restauração. É esse par que a tela chama de "histórico de autoativação".
        'origem': item.origem,
        'ativa': item.id == ativa_id,
        'criado_em': _iso(item.criado_em),
        'ativado_em': _iso(item.ativado_em),
    } for item in versoes]


def _alvo_para_painel(adaptador):
    estado = contrato_portal_recon._estado_do_alvo(adaptador)
    ativa = (ContratoPortal.query
             .filter_by(fluxo=adaptador.fluxo, alvo=adaptador.alvo,
                        estado='ativa')
             .one_or_none())
    return {
        'nome': adaptador.nome,
        'fluxo': adaptador.fluxo,
        'alvo': adaptador.alvo,
        'estado': estado['estado'],
        'mensagem': estado['mensagem'],
        'versao': estado['versao'],
        'contrato_id': ativa.id if ativa else None,
        'host': ativa.host if ativa else None,
        'rota': ativa.rota if ativa else None,
        'ativado_em': _iso(ativa.ativado_em) if ativa else None,
        'pode_criar_baseline': ativa is None and adaptador.definicao is not None,
        'incidentes': _incidentes_abertos(ativa.id) if ativa else [],
        'historico': _historico(
            adaptador.fluxo, adaptador.alvo, ativa.id if ativa else None),
    }


@bp.route('/diagnostico/contratos-portais')
@requer_papel('admin')
def diagnostico_contratos_portais():
    """Estado de cada alvo com contrato adaptativo. Só leitura."""
    alvos = [_alvo_para_painel(adaptador)
             for adaptador in contrato_portal_recon.adaptadores_padrao()]
    return jsonify({'status': 'ok', 'alvos': alvos})


def _adaptador_ou_404(fluxo, alvo):
    adaptador = contrato_portal_recon.adaptador_por_alvo(fluxo, alvo)
    if adaptador is None:
        return None, _json_error('Portal sem contrato adaptativo.', 404)
    return adaptador, None


@bp.route('/diagnostico/contratos-portais/<fluxo>/<alvo>/baseline',
          methods=['POST'])
@requer_papel('admin')
def diagnostico_contrato_baseline(fluxo, alvo):
    """Ativa a primeira versão a partir do que a tela realmente mostra.

    Ação humana explícita de propósito (AC-01.5): até ela acontecer, os três
    modos de emissão preservam o executor legado. A revisão humana está na
    DECLARAÇÃO (quais controles, qual política) e na recusa: a ativação só passa
    se a tela sustentar o que foi declarado, então não há como aprovar sem
    querer uma página que não é a esperada.
    """
    adaptador, erro = _adaptador_ou_404(fluxo, alvo)
    if erro:
        return erro
    try:
        contrato = contrato_portal_recon.criar_baseline_observada(
            adaptador, _criar_driver_lote, usuario_id=current_user.id)
    except contrato_portal_recon.AlvoOcupadoError as exc:
        return _json_error(str(exc), 423)
    except BaselineNaoObservavelError as exc:
        # Recusa que ensina: diz QUAIS controles não fecharam, para o operador
        # comparar com a tela em vez de adivinhar.
        return _json_error(str(exc), 409, faltantes=list(exc.faltantes))
    except contrato_portal.ContratoPortalError as exc:
        return _json_error(str(exc), 409)
    except Exception as exc:
        log_event('contrato_portal_baseline_falhou', level='ERROR',
                  fluxo=fluxo, alvo=alvo, error=str(exc))
        return _json_error('Não foi possível observar o portal agora.', 409)
    auditoria.registrar(
        'contrato_portal.baseline', alvo_tipo='contrato_portal',
        alvo_id=contrato.id,
        detalhe=f'{adaptador.fluxo}/{adaptador.alvo} v{contrato.versao}')
    return jsonify({'status': 'ok', 'versao': contrato.versao}), 201


@bp.route('/diagnostico/contratos-portais/<fluxo>/<alvo>/recon',
          methods=['POST'])
@requer_papel('admin')
def diagnostico_contrato_recon(fluxo, alvo):
    """Antecipa o recon do alvo. Passivo: abre a tela e observa, nada mais."""
    adaptador, erro = _adaptador_ou_404(fluxo, alvo)
    if erro:
        return erro
    try:
        resultado = contrato_portal_recon.recon_sob_demanda(
            adaptador, _criar_driver_lote)
    except contrato_portal_recon.AlvoOcupadoError as exc:
        return _json_error(str(exc), 423)
    except Exception as exc:
        log_event('contrato_portal_recon_sob_demanda_falhou', level='ERROR',
                  fluxo=fluxo, alvo=alvo, error=str(exc))
        return _json_error('Não foi possível observar o portal agora.', 409)
    return jsonify({'status': 'ok', 'resultado': resultado})


def _incidente_ou_404(incidente_id):
    incidente = db.session.get(IncidenteContratoPortal, incidente_id)
    if incidente is None or incidente.estado != 'aberto':
        return None, _json_error('Incidente não encontrado ou já resolvido.', 404)
    return incidente, None


@bp.route('/diagnostico/contratos-portais/incidentes/<int:incidente_id>/aceitar',
          methods=['POST'])
@requer_papel('admin')
def diagnostico_contrato_incidente_aceitar(incidente_id):
    """Aprova a estrutura observada e a promove a versão ativa.

    Reobserva antes de promover: aprovar sobre a leitura de ontem promoveria
    uma tela que talvez já tenha mudado de novo.
    """
    dados = request.get_json(silent=True) or {}
    if dados.get('confirmado') is not True:
        return _json_error(
            'Ative um contrato apenas com confirmação explícita.', 400)

    incidente, erro = _incidente_ou_404(incidente_id)
    if erro:
        return erro
    base = incidente.contrato_base
    adaptador, erro = _adaptador_ou_404(base.fluxo, base.alvo)
    if erro:
        return erro
    try:
        nova = contrato_portal_recon.aceitar_incidente(
            incidente, adaptador, _criar_driver_lote,
            usuario_id=current_user.id)
    except contrato_portal_recon.AlvoOcupadoError as exc:
        return _json_error(str(exc), 423)
    except contrato_portal_recon.NadaParaRevisarError as exc:
        return _json_error(str(exc), 409)
    except contrato_portal.ContratoPortalError as exc:
        return _json_error(str(exc), 409)
    auditoria.registrar(
        'contrato_portal.aceitar', alvo_tipo='contrato_portal', alvo_id=nova.id,
        detalhe=f'{base.fluxo}/{base.alvo} v{base.versao}→v{nova.versao}')
    return jsonify({'status': 'ok', 'versao': nova.versao})


@bp.route('/diagnostico/contratos-portais/incidentes/<int:incidente_id>/rejeitar',
          methods=['POST'])
@requer_papel('admin')
def diagnostico_contrato_incidente_rejeitar(incidente_id):
    """Recusa a mudança: a versão ativa continua sendo a que já estava."""
    incidente, erro = _incidente_ou_404(incidente_id)
    if erro:
        return erro
    candidata_id = incidente.contrato_candidato_id
    try:
        if candidata_id is not None:
            contrato_portal.rejeitar_candidata(
                candidata_id, usuario_id=current_user.id)
        else:
            # Incidente do preflight não tem candidata: recusar é encerrar o
            # incidente, sem promover nada.
            incidente.estado = 'rejeitado'
            db.session.commit()
    except contrato_portal.ContratoPortalError as exc:
        return _json_error(str(exc), 409)
    auditoria.registrar(
        'contrato_portal.rejeitar', alvo_tipo='contrato_portal',
        alvo_id=incidente.contrato_base_id,
        detalhe=f'incidente {incidente.classificacao}')
    return jsonify({'status': 'ok'})


@bp.route('/diagnostico/contratos-portais/<int:contrato_id>/restaurar',
          methods=['POST'])
@requer_papel('admin')
def diagnostico_contrato_restaurar(contrato_id):
    """Volta uma versão do histórico a ativa (rollback do autoajuste)."""
    dados = request.get_json(silent=True) or {}
    if dados.get('confirmado') is not True:
        return _json_error(
            'Restaure uma versão apenas com confirmação explícita.', 400)

    historico = db.session.get(ContratoPortal, contrato_id)
    if historico is None:
        return _json_error('Versão não encontrada.', 404)
    ativa = (ContratoPortal.query
             .filter_by(fluxo=historico.fluxo, alvo=historico.alvo,
                        estado='ativa')
             .one_or_none())
    if ativa is None:
        return _json_error('O portal não possui contrato ativo.', 409)
    try:
        nova = contrato_portal.restaurar(
            historico.id, fingerprint_ativa=ativa.fingerprint,
            usuario_id=current_user.id)
    except contrato_portal.ContratoPortalError as exc:
        return _json_error(str(exc), 409)
    auditoria.registrar(
        'contrato_portal.restaurar', alvo_tipo='contrato_portal',
        alvo_id=nova.id,
        detalhe=f'{historico.fluxo}/{historico.alvo} v{historico.versao}')
    return jsonify({'status': 'ok', 'versao': nova.versao})
