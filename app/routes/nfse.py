"""Rotas da emissao de NFSe dos honorarios (NFSE-01..17).

Registra no blueprint "main" compartilhado (AD-013). Rotas finas: toda a
logica vive em `app/services/nfse_*`; aqui so entra validacao de entrada,
autorizacao e montagem da resposta.
"""
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask import render_template, request
from flask_login import current_user

from app import db
from app.auth import requer_papel
from app.models import (
    ApelidoNfse,
    Empresa,
    LoteNfse,
    NotaNfse,
    OrigemVinculoNfse,
    ServicoNfse,
    StatusNotaNfse,
)
from app.automation.batch_state import (
    NFSE_BATCH_LOCK,
    NFSE_BATCH_STATE,
    automacao_em_curso,
    definir_nfse_batch_opcoes,
    mensagem_automacao_em_curso,
    nfse_batch_opcoes,
)
from app.routes import _current_app_object, bp
from app.automation import nfse_emitidas as automacao_emitidas
from app.automation import nfse_recon
from app.services import (
    auditoria,
    batch_engine,
    nfse_config,
    nfse_emitidas,
    nfse_grupos,
    nfse_import,
    nfse_contrato,
    nfse_lote,
    nfse_service,
)
from app.services.nfse_extrato_inter import chave_descricao, normalizar_termo
from app.services.execution_logger import log_event
from app.services.nfse_session import SESSAO
from app.utils import (
    TIPO_CNPJ,
    TIPO_CPF,
    detectar_tipo_documento,
    documento_valido,
    formatar_documento,
    json_error,
)

# Uniao dos passes da recon assistida. Vive so em memoria, por processo: e
# memoria de uma sessao de observacao, nunca contrato persistido.
ACUMULADOR_RECON = nfse_recon.AcumuladorRecon()

# 5 MB cobre com folga os dois formatos: o CSV mensal tem ~7 KB e o PDF do
# Inter ~160 KB. O limite existe para recusar cedo o arquivo que obviamente nao
# e um extrato mensal, nao para apertar o caso normal.
TAMANHO_MAXIMO_CSV = 5 * 1024 * 1024

ORIGEM_AUTOMACAO = 'automacao'
ORIGEM_MANUAL = 'manual'


CATEGORIA_HONORARIOS = 'honorarios'
CATEGORIA_SERVICO = 'servico'
CATEGORIA_INDEFINIDA = 'indefinida'


# --- contrato adaptativo da NFS-e ------------------------------------------

@bp.route('/nfse/contrato')
@requer_papel('operador')
def nfse_contrato_estado():
    """Estado resumido do contrato, pronto para a central da interface."""

    return {'status': 'ok', **nfse_contrato.estado_painel()}


@bp.route('/nfse/contrato/<int:contrato_id>')
@requer_papel('operador')
def nfse_contrato_detalhe(contrato_id):
    try:
        detalhe = nfse_contrato.detalhe_contrato(contrato_id)
    except nfse_contrato.ContratoNfseNaoEncontradoError as exc:
        return json_error(str(exc), 404)
    return {'status': 'ok', 'contrato': detalhe}


@bp.route('/nfse/contrato/incidente/<int:incidente_id>/configurar', methods=['POST'])
@requer_papel('operador')
def nfse_contrato_configurar(incidente_id):
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        return json_error('Envie um objeto JSON de configuração.', 400, campo='corpo')

    permitidos = {
        'origem',
        'fonte',
        'valor_fixo',
        'confirmar_recomendacao',
        'chave_observada',
    }
    extras = set(dados) - permitidos
    if extras:
        campo = sorted(extras)[0]
        return json_error('Campo não permitido na configuração.', 400, campo=campo)

    confirmacao = dados.get('confirmar_recomendacao')
    if confirmacao is not None and not isinstance(confirmacao, bool):
        return json_error(
            'A confirmação da recomendação deve ser booleana.',
            400,
            campo='confirmar_recomendacao',
        )

    incidente = db.session.get(nfse_contrato.IncidenteContratoNfse, incidente_id)
    if incidente is None:
        return json_error('O incidente solicitado não existe.', 404)
    if incidente.estado != 'aberto':
        return json_error(
            'Este incidente já recebeu uma decisão e não pode ser configurado novamente.',
            409,
        )
    # A rota valida FORMA; a regra de recomendação é de `configurar_incidente`,
    # que a reaplica de qualquer jeito. Duplicá-la aqui custava duas consultas
    # e o scorer inteiro por request, e as cópias já discordavam — a rota
    # recusava `confirmar_recomendacao: false` num incidente sem recomendação,
    # que o serviço aceita.
    chave_observada = dados.get('chave_observada')
    if chave_observada is not None and not isinstance(chave_observada, str):
        return json_error(
            'O controle recomendado deve ser identificado por texto.',
            400,
            campo='chave_observada',
        )

    configuracao = {
        chave: dados[chave]
        for chave in ('origem', 'fonte', 'valor_fixo')
        if chave in dados
    }
    try:
        candidato = nfse_contrato.configurar_incidente(
            incidente_id,
            configuracao,
            usuario_id=current_user.id,
            chave_observada=chave_observada,
            confirmar_recomendacao=confirmacao is True,
        )
    except nfse_contrato.ConfiguracaoContratoInvalidaError as exc:
        return json_error(str(exc), 400, campo=getattr(exc, 'campo', 'origem'))
    except nfse_contrato.ContratoNfseNaoEncontradoError as exc:
        return json_error(str(exc), 404)
    except nfse_contrato.ContratoNfseTransicaoInvalidaError as exc:
        return json_error(str(exc), 409)
    except nfse_contrato.PersistenciaContratoError as exc:
        # Duas configuracoes simultaneas podem escolher a mesma proxima versao.
        # Sem este except o Flask devolve o 500 em HTML e o `chamar()` da tela
        # cai no `catch` sem `message`, mostrando "Falha na requisicao (500)" —
        # e esta e a rota que o operador usa em TODO incidente. As rotas de
        # descartar e ativar ja tratam; faltava a mais usada das tres.
        return json_error(str(exc), 500)
    return {
        'status': 'ok',
        'incidente_id': incidente_id,
        'contrato': nfse_contrato.detalhe_contrato(candidato.id),
    }


@bp.route('/nfse/contrato/incidente/<int:incidente_id>/reabrir', methods=['POST'])
@requer_papel('operador')
def nfse_contrato_reabrir_incidente(incidente_id):
    """Desfaz a decisão de UM incidente, preservando as outras.

    O "Descartar candidata" do topo continua existindo e continua sendo tudo ou
    nada; este é o par por linha, que era o que o botão da linha aparentava ser
    sem ser.
    """

    try:
        candidata = nfse_contrato.reabrir_incidente(
            incidente_id, usuario_id=current_user.id
        )
    except nfse_contrato.ContratoNfseNaoEncontradoError as exc:
        return json_error(str(exc), 404)
    except nfse_contrato.ContratoNfseTransicaoInvalidaError as exc:
        return json_error(str(exc), 409)
    except nfse_contrato.ConfiguracaoContratoInvalidaError as exc:
        return json_error(str(exc), 400, campo=getattr(exc, 'campo', 'origem'))
    except nfse_contrato.PersistenciaContratoError as exc:
        return json_error(str(exc), 500)
    return {
        'status': 'ok',
        'incidente_id': incidente_id,
        'contrato': (
            nfse_contrato.detalhe_contrato(candidata.id)
            if candidata is not None else None
        ),
        'estado': nfse_contrato.estado_painel(),
    }


@bp.route('/nfse/contrato/<int:contrato_id>/descartar', methods=['POST'])
@requer_papel('operador')
def nfse_contrato_descartar(contrato_id):
    """Arquiva a candidata e reabre seus incidentes, para reconfigurar."""

    try:
        reabertos = nfse_contrato.descartar_candidata(
            contrato_id, usuario_id=current_user.id
        )
    except nfse_contrato.ContratoNfseNaoEncontradoError as exc:
        return json_error(str(exc), 404)
    except nfse_contrato.ContratoNfseTransicaoInvalidaError as exc:
        return json_error(str(exc), 409)
    except nfse_contrato.PersistenciaContratoError as exc:
        return json_error(str(exc), 500)
    return {'status': 'ok', 'reabertos': reabertos}


@bp.route('/nfse/contrato/incidentes/descartar', methods=['POST'])
@requer_papel('operador')
def nfse_contrato_incidentes_descartar():
    """Descarta os incidentes abertos da versão ativa. Não altera o contrato."""

    try:
        descartados = nfse_contrato.descartar_incidentes(usuario_id=current_user.id)
    except nfse_contrato.ContratoNfseTransicaoInvalidaError as exc:
        return json_error(str(exc), 409)
    except nfse_contrato.ContratoNfseNaoEncontradoError as exc:
        return json_error(str(exc), 404)
    except nfse_contrato.PersistenciaContratoError as exc:
        return json_error(str(exc), 500)
    return {'status': 'ok', 'descartados': descartados}


@bp.route('/nfse/contrato/recon/descartar', methods=['POST'])
@requer_papel('operador')
def nfse_contrato_recon_descartar():
    """Zera os passes acumulados. Não toca contrato, incidente nem nota."""

    ACUMULADOR_RECON.descartar()
    return {'status': 'ok', 'passe': 0, 'controles_acumulados': 0}


@bp.route('/nfse/contrato/recon', methods=['POST'])
@requer_papel('operador')
def nfse_contrato_recon():
    # `final` e o operador dizendo "percorri a etapa inteira". Sem ele nenhum
    # passe conclui ausencia — e sem conclusao de ausencia o remapeamento, que
    # so nasce de um `controle_removido`, fica inalcancavel pela Central.
    corpo = request.get_json(silent=True) or {}
    if not isinstance(corpo, dict) or set(corpo) - {'final'}:
        return json_error('A recon aceita somente o campo `final`.', 400, campo='corpo')
    final = corpo.get('final', False)
    if not isinstance(final, bool):
        return json_error('O campo `final` deve ser booleano.', 400, campo='final')
    if not NFSE_BATCH_LOCK.acquire(blocking=False):
        return json_error(
            'Há um lote de NFS-e em andamento. Aguarde ou pare o lote antes da recon.',
            409,
            motivo='lote_nfse_em_curso',
        )

    sessao_adquirida = False
    try:
        if NFSE_BATCH_STATE.get('status') in ('running', 'paused'):
            return json_error(
                'Há um lote de NFS-e em andamento. Aguarde ou pare o lote antes da recon.',
                409,
                motivo='lote_nfse_em_curso',
            )
        sessao_adquirida = SESSAO.adquirir()
    finally:
        NFSE_BATCH_LOCK.release()

    if not sessao_adquirida:
        return json_error(
            'A sessão da NFS-e está ocupada. Prepare a sessão e tente novamente.',
            409,
            motivo='sessao_nfse_ocupada',
        )

    try:
        driver = SESSAO.driver
        if driver is None:
            return json_error(
                'Não há sessão da NFS-e preparada. Prepare a sessão antes da recon.',
                409,
                motivo='sessao_nfse_ausente',
            )
        url_atual = driver.current_url
        etapa = nfse_recon.etapa_da_url(url_atual)
        if etapa is None:
            return json_error(
                'A tela atual não é uma etapa reconhecida da NFS-e.',
                409,
                motivo='etapa_nfse_desconhecida',
            )
        # A etapa de Pessoas revela campos conforme e preenchida. Cada clique em
        # "Recon da tela atual" e um passe: o que vale e a uniao dos passes desta
        # mesma DPS, nao o instantaneo do ultimo.
        inventario = nfse_recon.inventariar(driver, etapa)
        uniao = ACUMULADOR_RECON.acumular(
            nfse_recon.rascunho_da_url(url_atual),
            inventario,
            nfse_recon.preenchimento(driver),
        )
        # A uniao preserva o que ja foi observado, e e isso que se quer dela.
        # Mas se ESTE passe falhou, responder pela uniao diria "compativel"
        # sobre uma tela que nao foi lida agora — desfecho desconhecido virando
        # chute. O passe inconclusivo responde por si.
        if not inventario.conhecida:
            uniao = inventario
        contrato = nfse_contrato.carregar_execucao()
        observacao = nfse_contrato.observar(
            driver,
            contrato,
            etapa,
            'recon_assistida',
            modo='assistido',
            inventario=uniao,
            observacao_final=final,
        )
        return {
            'status': 'ok',
            'final': final,
            'passe': ACUMULADOR_RECON.passes(etapa),
            'controles_acumulados': len(uniao.controles),
            'sugestoes': [
                {
                    'chave': item.chave,
                    'rotulo': item.rotulo,
                    'interacao': item.interacao,
                    'obrigatorio': item.obrigatorio,
                    'sugestao': item.sugestao,
                    'motivo': item.motivo,
                }
                for item in ACUMULADOR_RECON.sugestoes(etapa)
            ],
            'observacao': {
                'contrato_id': observacao.contrato_id,
                'etapa': observacao.etapa,
                'momento': observacao.momento,
                'estado': observacao.estado,
                'compatibilidade': observacao.compatibilidade,
                'diferencas': list(observacao.diferencas),
                'evidencias': list(observacao.evidencias),
                'incidentes': observacao.incidentes,
            },
        }
    except nfse_recon.InventarioExcedidoError:
        return json_error(
            'A tela excede o limite seguro de controles ou opções para a recon.',
            409,
            motivo='inventario_excedido',
        )
    except nfse_recon.InventarioInconclusivoError:
        return json_error(
            'Não foi possível observar a tela com segurança. Tente novamente.',
            409,
            motivo='inventario_inconclusivo',
        )
    except nfse_contrato.ContratoNfseNaoEncontradoError as exc:
        return json_error(str(exc), 404)
    except nfse_contrato.PersistenciaContratoError as exc:
        return json_error(str(exc), 500)
    except Exception as exc:
        return json_error(exc=exc, code=500)
    finally:
        SESSAO.liberar()


def _validacao_propria_na_revisao(contrato_id):
    opcoes = nfse_batch_opcoes()
    if opcoes.get('validacao_contrato_id') != contrato_id:
        return False
    with NFSE_BATCH_LOCK:
        if NFSE_BATCH_STATE.get('status') not in ('running', 'paused'):
            return False
        nota_id = NFSE_BATCH_STATE.get('current_id')
    nota = db.session.get(NotaNfse, nota_id) if nota_id else None
    return nota is not None and nota.status == StatusNotaNfse.AGUARDANDO_CONFIRMACAO


def _pausa_abandonada_de_validacao():
    """A pausa que sobrou de uma validação já resolvida fora do navegador.

    A validação pausa o lote quando o formulário diverge (é o que faz o
    operador ir configurar os controles novos). Resolvido isso, o lote continua
    `paused` para sempre: o worker já morreu, ninguém vai retomar, e a pausa
    passa a barrar justamente a revalidação que ela pediu.

    NÃO olha o `contrato_id`: configurar o incidente ARQUIVA a candidata e cria
    outra, então a pausa que sobrou é sempre de uma versão diferente da que o
    operador está tentando validar agora. Casar por id nunca acertaria.

    Duas coisas protegem o que não pode ser descartado:

    - `validacao_contrato_id` só existe em lote de validação; um lote de
      emissão de verdade não tem, e não é tocado aqui;
    - nota em `aguardando_confirmacao` é DPS preenchida esperando o operador no
      portal. Parar ali abandonaria um documento em aberto, e documento fiscal
      não tem rollback (ND-005/ND-011).
    """
    if not nfse_batch_opcoes().get('validacao_contrato_id'):
        return False
    with NFSE_BATCH_LOCK:
        if NFSE_BATCH_STATE.get('status') != 'paused':
            return False
        nota_id = NFSE_BATCH_STATE.get('current_id')
    nota = db.session.get(NotaNfse, nota_id) if nota_id else None
    return nota is None or nota.status != StatusNotaNfse.AGUARDANDO_CONFIRMACAO


@bp.route('/nfse/contrato/<int:contrato_id>/validar', methods=['POST'])
@requer_papel('operador')
def nfse_contrato_validar(contrato_id):
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        return json_error('Escolha uma nota para validar o contrato.', 400, campo='nota_id')
    extras = set(dados) - {'nota_id'}
    if extras:
        return json_error(
            'A validação aceita somente nota_id.',
            400,
            campo=sorted(extras)[0],
        )
    nota_id = dados.get('nota_id')
    if isinstance(nota_id, bool) or not isinstance(nota_id, int) or nota_id <= 0:
        return json_error('Escolha uma nota emitível.', 400, campo='nota_id')

    contrato = db.session.get(nfse_contrato.ContratoNfse, contrato_id)
    if contrato is None:
        return json_error('A versão do contrato não existe.', 404)
    if contrato.estado not in {'candidata', 'ativa'}:
        return json_error(
            'Somente uma versão candidata ou ativa pode ser validada.',
            409,
        )
    if contrato.estado == 'ativa':
        try:
            nfse_contrato.validar_revalidacao_ativa(contrato.id)
        except nfse_contrato.ContratoNfseTransicaoInvalidaError as exc:
            return json_error(str(exc), 409)
    try:
        nfse_lote.validar_nota_para_validacao(nota_id)
    except ValueError as exc:
        return json_error(str(exc), 400, campo='nota_id')

    em_curso = automacao_em_curso()
    if em_curso is not None and _pausa_abandonada_de_validacao():
        # `request_stop` grava `stopped` na hora, sem depender do worker — que
        # neste ponto ja morreu. Sem isto a pausa fica para sempre e so um
        # "Parar" manual libera, que e o que o operador nao tinha como saber.
        batch_engine.request_stop(NFSE_BATCH_LOCK, NFSE_BATCH_STATE)
        log_event('nfse_validacao_pausa_descartada', contrato_id=contrato_id)
        em_curso = automacao_em_curso()
    if em_curso is not None:
        # Mensagem COMPARTILHADA (`mensagem_automacao_em_curso`), como em
        # `routes/lotes.py`. A copia local dizia "Aguarde terminar", e para lote
        # pausado esse e o unico conselho que nunca funciona: pausa nao termina
        # sozinha, quem a fecha e retomar ou parar.
        return json_error(
            mensagem_automacao_em_curso(em_curso),
            409,
            motivo='automacao_em_curso',
        )
    if not SESSAO.adquirir():
        return json_error(
            'Já existe uma sessão da NFS-e em andamento. Aguarde terminar.',
            409,
        )
    with NFSE_BATCH_LOCK:
        em_andamento = NFSE_BATCH_STATE.get('status') in ('running', 'paused')
    if em_andamento:
        SESSAO.liberar()
        return json_error('Já existe um lote de NFS-e em andamento.', 409)

    # A validação preenche até a revisão e nunca emite (ND-005): o gate da
    # alíquota protege a EMISSÃO, e exigi-lo aqui bloqueia justamente a prova
    # que precede qualquer emissão. O aviso continua no log da execução.
    definir_nfse_batch_opcoes(
        nfse_lote.MODO_INDIVIDUAL,
        True,
        contrato_id=contrato_id,
        validacao_contrato_id=contrato_id,
    )
    nfse_lote.preparar_nova_fila()
    # Veredito da validação anterior não pode sobreviver à nova: o painel o
    # mostra em tempo real, e um resultado velho ali diria "validado" sobre uma
    # execução que ainda nem preencheu.
    nfse_lote.limpar_validacao_publicada()
    try:
        dados_lote = batch_engine.init_batch_run(
            NFSE_BATCH_LOCK,
            NFSE_BATCH_STATE,
            nota_id,
            lambda inicio: nfse_lote.calcular_alvos(nota_id=inicio),
            nfse_lote.worker,
            app_factory=_current_app_object,
        )
    except Exception as exc:
        SESSAO.liberar()
        return json_error(exc=exc, code=500)
    if dados_lote is None:
        SESSAO.liberar()
        return json_error('Já existe um lote de NFS-e em andamento.', 409)
    if not dados_lote:
        SESSAO.liberar()
        return json_error('A nota escolhida não está mais emitível.', 400, campo='nota_id')
    return {
        'status': 'ok',
        'modo': nfse_lote.MODO_INDIVIDUAL,
        'contrato_id': contrato_id,
        'nota_id': nota_id,
        'total': dados_lote['total'],
    }


@bp.route('/nfse/contrato/<int:contrato_id>/ativar', methods=['POST'])
@requer_papel('operador')
def nfse_contrato_ativar(contrato_id):
    em_curso = automacao_em_curso()
    propria_validacao = _validacao_propria_na_revisao(contrato_id)
    if em_curso is not None and not propria_validacao:
        return json_error(
            f"A automação {em_curso['rotulo']} ainda está em andamento.",
            409,
            motivo='automacao_em_curso',
        )
    if SESSAO.ocupada and not propria_validacao:
        return json_error(
            'A sessão da NFS-e está ocupada por outra execução.',
            409,
            motivo='sessao_ocupada',
        )
    try:
        contrato = nfse_contrato.ativar(contrato_id, usuario_id=current_user.id)
    except nfse_contrato.ContratoNfseNaoEncontradoError as exc:
        return json_error(str(exc), 404)
    except nfse_contrato.ContratoNfseTransicaoInvalidaError as exc:
        return json_error(str(exc), 409)
    except nfse_contrato.PersistenciaContratoError as exc:
        return json_error(str(exc), 500)
    return {
        'status': 'ok',
        'contrato': nfse_contrato.detalhe_contrato(contrato.id),
    }


@bp.route(
    '/nfse/contrato/<int:contrato_id>/liberar-automatico', methods=['POST']
)
@requer_papel('operador')
def nfse_contrato_liberar_automatico(contrato_id):
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        return json_error('Envie um objeto JSON para a liberação.', 400, campo='corpo')
    extras = set(dados) - {'liberar'}
    if extras:
        return json_error(
            'Campo não permitido na liberação.', 400, campo=sorted(extras)[0]
        )
    if not isinstance(dados.get('liberar'), bool):
        return json_error(
            'Informe se deseja liberar ou revogar o modo automático.',
            400,
            campo='liberar',
        )
    try:
        contrato = nfse_contrato.definir_liberacao_automatica(
            contrato_id, dados['liberar'], usuario_id=current_user.id
        )
    except nfse_contrato.ContratoNfseNaoEncontradoError as exc:
        return json_error(str(exc), 404)
    except nfse_contrato.ContratoNfseTransicaoInvalidaError as exc:
        return json_error(str(exc), 409)
    except nfse_contrato.PersistenciaContratoError as exc:
        return json_error(str(exc), 500)
    return {
        'status': 'ok',
        'contrato': nfse_contrato.detalhe_contrato(contrato.id),
    }


def _categoria(nota):
    """Como a linha aparece agrupada na tela: honorarios, servico ou indefinida.

    Sai de `descricao_servico` + `descricao_pendente`, e nao de um campo
    proprio, porque sao esses dois que decidem o texto da nota — uma terceira
    fonte poderia divergir do que sera efetivamente emitido."""
    if nota.descricao_pendente:
        return CATEGORIA_INDEFINIDA
    return CATEGORIA_SERVICO if nota.descricao_servico else CATEGORIA_HONORARIOS


def _descricao_prevista(nota):
    """O texto que a nota levara ao portal, para a tela mostrar antes de emitir.

    Vale a pena repetir aqui a decisao do `nfse_config.descricao_da_nota` em vez
    de chamar direto? Nao: chama-se direto. O que muda e so o desfecho quando
    ainda nao ha texto — na tela isso e um traco, no portal seria um erro."""
    if nota.descricao_pendente:
        return None
    from app.services import nfse_config
    try:
        return nfse_config.descricao_da_nota(nfse_config.get_config_nfse(), nota)
    except ValueError:
        # honorarios sem competencia: a nota nao tem o que descrever ainda
        return None


def _nota_para_json(nota):
    empresa = nota.empresa_id and db.session.get(Empresa, nota.empresa_id)
    return {
        'id': nota.id,
        'nome_csv': nota.nome_csv,
        'empresa': empresa.nome if empresa else None,
        'empresa_id': nota.empresa_id,
        'documento': nota.documento,
        'tipo_documento': nota.tipo_documento,
        'competencia': nota.competencia,
        'valor': f'{nota.valor_final:.2f}'.replace('.', ',') if nota.valor_final else None,
        'vencimento': nota.vencimento.strftime('%d/%m/%Y') if nota.vencimento else None,
        'status': nota.status,
        # A regra de "esta nota pode ser preenchida" mora em
        # `nfse_service.emitivel` — que barra proposta de agrupamento pendente e
        # duplicata não liberada, coisas que uma lista de status não vê. O
        # cliente consome o veredito; recriar a regra em JS ja tinha divergido.
        'emitivel': nfse_service.emitivel(nota),
        'origem_vinculo': nota.origem_vinculo,
        'score_match': nota.score_match,
        'divergencia_valor': nota.divergencia_valor,
        'duplicata_liberada': nota.duplicata_liberada,
        'erro': nota.erro,
        'origem_emissao': nota.origem_emissao,
        # extrato do Inter
        'origem_extrato': nota.origem_extrato,
        'categoria': _categoria(nota),
        'descricao_servico': nota.descricao_servico,
        'descricao_extrato': nota.descricao_extrato,
        'descricao_prevista': _descricao_prevista(nota),
        'valor_ajustado': nota.valor_ajustado,
        'grupo': _grupo_para_json(nota),
    }


def _grupo_para_json(nota):
    """O agrupamento desta linha, em qualquer um dos dois estados vivos.

    `pendente` = proposta esperando resposta; `confirmado` = ja aplicado e
    ainda desfazivel. As irmas carregam o token para a tela poder destaca-las
    junto, mas so a lider carrega a conta — repetida em cada linha, ela
    pareceria varias propostas."""
    pendente = nfse_grupos.tem_proposta_pendente(nota)
    if not pendente and not nfse_grupos.foi_agrupada(nota):
        return None
    return {
        'token': nota.grupo_sugerido,
        'pendente': pendente,
        'confirmado': bool(nota.grupo_confirmado),
        'lider': nota.grupo_valor_liquido is not None,
        'valor_liquido': (f'{nota.grupo_valor_liquido:.2f}'.replace('.', ',')
                          if nota.grupo_valor_liquido is not None else None),
        'detalhe': nota.grupo_detalhe,
        'descricao': nota.grupo_descricao,
    }


def _resumo(notas):
    conta = {}
    categorias = {}
    for nota in notas:
        conta[nota.status] = conta.get(nota.status, 0) + 1
        chave = _categoria(nota)
        categorias[chave] = categorias.get(chave, 0) + 1
    return {
        'total': len(notas),
        'por_status': conta,
        'por_categoria': categorias,
        'divergencias': sum(1 for n in notas if n.divergencia_valor),
        # conta PROPOSTAS, nao notas: as tres linhas do grupo do estorno sao uma
        # decisao so para o operador
        'grupos_pendentes': len({n.grupo_sugerido for n in notas
                                 if nfse_grupos.tem_proposta_pendente(n)}),
    }


def _valor_json(valor):
    return f'{valor:.2f}'.replace('.', ',') if valor is not None else None


def _valor_extenso(valor):
    """29869.19 -> '29.869,19'. Com separador de milhar.

    So para os numeros que o operador CONFERE de cabeca — o total do mes, a
    conta do agrupamento. Na coluna da tabela o formato curto continua, porque
    la os valores estao alinhados e a comparacao e visual."""
    if valor is None:
        return None
    return (f'{valor:,.2f}'.replace(',', '\x00').replace('.', ',')
            .replace('\x00', '.'))


# --- pagina ----------------------------------------------------------------

ESCOPO_ULTIMA = 'ultima'


def _competencias_disponiveis():
    """Competencias com notas, da mais recente para a mais antiga.

    Ordena por (ano, mes) e nao pela string: 'MM/AAAA' ordenado como texto poe
    01/2027 antes de 12/2026."""
    valores = {c for (c,) in NotaNfse.query
               .with_entities(NotaNfse.competencia).distinct() if c}

    def _chave(competencia):
        mes, _, ano = competencia.partition('/')
        return (int(ano or 0), int(mes or 0))

    return sorted(valores, key=_chave, reverse=True)


def _notas_do_escopo(competencia=None):
    """Notas a mostrar, e o lote a que elas pertencem.

    Sem competencia, mostra a ULTIMA importacao — o que o operador acabou de
    trazer do banco. Com competencia, mostra o mes inteiro atravessando lotes:
    quem emite antes do fim do mes importa o extrato duas ou tres vezes, e as
    notas do mesmo mes ficam espalhadas por varias importacoes.
    """
    if competencia:
        notas = (NotaNfse.query.filter_by(competencia=competencia)
                 .order_by(NotaNfse.id).all())
        return notas, None

    lote = LoteNfse.query.order_by(LoteNfse.id.desc()).first()
    if lote is None:
        return [], None
    return (NotaNfse.query.filter_by(lote_id=lote.id)
            .order_by(NotaNfse.id).all()), lote


def _competencia_pedida(bruto):
    """Valida contra as competencias existentes: so aceita o que ha no banco,
    entao nao ha o que injetar pela querystring."""
    bruto = (bruto or '').strip()
    if not bruto or bruto == ESCOPO_ULTIMA:
        return None
    return bruto if bruto in _competencias_disponiveis() else None


@bp.route('/nfse')
@requer_papel('operador')
def nfse_painel():
    competencia = _competencia_pedida(request.args.get('competencia'))
    notas, lote = _notas_do_escopo(competencia)
    # o operador pode ter acabado de usar o atalho "Cadastrar": liga as linhas
    # cuja Empresa passou a existir, para a volta a pagina refletir o cadastro
    if nfse_import.reconciliar_com_cadastro(notas):
        notas, lote = _notas_do_escopo(competencia)
    return render_template(
        'nfse.html',
        lote=lote,
        notas=[_nota_para_json(n) for n in notas],
        resumo=_resumo(notas),
        competencia_atual=competencia or ESCOPO_ULTIMA,
        competencias=_competencias_disponiveis(),
        config=nfse_config.get_config_nfse(),
        empresas=[{'id': e.id, 'nome': e.nome, 'cnpj': e.cnpj}
                  for e in Empresa.query.order_by(Empresa.nome).all()],
        contrato_estado=nfse_contrato.estado_painel(),
        # A competência escolhida para a fila de honorários não interfere no
        # painel do portal. O formulário de datas é preenchido com o mês atual
        # apenas como ponto de partida para uma nova consulta.
        emitidas=_painel_emitidas(mes=_competencia_corrente()),
    )


@bp.route('/nfse/notas')
@requer_papel('operador')
def nfse_listar_notas():
    """Lista atual do lote importado, para a pagina se atualizar sem recarregar.

    A fila roda no servidor, entao o que a tabela mostra envelhece enquanto a
    emissao anda; e por aqui que ela volta a bater com o banco."""
    notas, _lote = _notas_do_escopo(
        _competencia_pedida(request.args.get('competencia')))
    return {
        'status': 'ok',
        'notas': [_nota_para_json(n) for n in notas],
        'resumo': _resumo(notas),
    }


# --- importacao (NFSE-01..07) ----------------------------------------------

@bp.route('/nfse/importar', methods=['POST'])
@requer_papel('operador')
def nfse_importar():
    enviados = [a for a in request.files.getlist('arquivo')
                if a is not None and (a.filename or '').strip()]
    if not enviados:
        return json_error(
            'Selecione ao menos um extrato: o CSV de cobrancas do Banrisul ou '
            'o PDF do extrato do Banco Inter.', 400)

    arquivos = []
    total = 0
    for arquivo in enviados:
        conteudo = arquivo.read()
        total += len(conteudo)
        if total > TAMANHO_MAXIMO_CSV:
            return json_error(
                'Arquivos grandes demais para serem extratos mensais de cobrancas.', 400)
        arquivos.append((arquivo.filename, conteudo))

    try:
        lote = nfse_import.importar(arquivos)
    except nfse_import.ArquivoInvalidoError as exc:
        return json_error(str(exc), 400)

    notas = NotaNfse.query.filter_by(lote_id=lote.id).order_by(NotaNfse.id).all()
    return {
        'status': 'ok',
        'lote_id': lote.id,
        'notas': [_nota_para_json(n) for n in notas],
        'resumo': _resumo(notas),
        'arquivos': len(arquivos),
        'ignoradas_duplicadas': getattr(lote, 'ignoradas_duplicadas', 0),
    }


# --- resolucao manual da empresa (NFSE-03, NFSE-22) ------------------------

@bp.route('/nfse/nota/<int:nota_id>/resolver', methods=['POST'])
@requer_papel('operador')
def nfse_resolver_empresa(nota_id):
    """Vincula a nota a uma empresa escolhida, ou a um CNPJ digitado.

    Ao vincular por empresa, salva o apelido: o mesmo nome do banco resolve
    sozinho no mes seguinte."""
    nota = db.session.get(NotaNfse, nota_id)
    if nota is None:
        return json_error('Nota nao encontrada.', 404)
    if nota.status == StatusNotaNfse.EMITIDA:
        return json_error('Esta nota ja foi emitida.', 409)

    dados = request.get_json(silent=True) or {}
    empresa_id = dados.get('empresa_id')
    documento = (dados.get('documento') or dados.get('cnpj') or '').strip()

    # Recusa entrada ambigua em vez de eleger um dos dois em silencio: escolher
    # sozinho ja vinculou nota ao tomador errado E memorizou o apelido errado
    # para os meses seguintes.
    if empresa_id and documento:
        return json_error(
            'Escolha uma empresa OU informe um CPF/CNPJ, nao os dois. '
            'Limpe o campo que nao quer usar.', 400)

    if empresa_id:
        empresa = db.session.get(Empresa, int(empresa_id))
        if empresa is None:
            return json_error('Empresa nao encontrada.', 404)
        _vincular(nota, empresa)
        _lembrar(nota.nome_csv_norm, empresa_id=empresa.id)
    elif documento:
        tipo = detectar_tipo_documento(documento)
        if not documento_valido(documento):
            rotulo = {TIPO_CPF: 'CPF', TIPO_CNPJ: 'CNPJ'}.get(tipo, 'CPF/CNPJ')
            return json_error(
                f'{rotulo} invalido: confira os digitos. Um digito trocado '
                'emite a nota no documento de outra pessoa.', 400)

        formatado = formatar_documento(documento)
        empresa = (Empresa.query.filter_by(cnpj=formatado).first()
                   if tipo == TIPO_CNPJ else None)
        if empresa is not None:
            _vincular(nota, empresa)
            _lembrar(nota.nome_csv_norm, empresa_id=empresa.id)
        else:
            # documento avulso: emite normalmente, sem cadastro. CPF e estado
            # final; CNPJ segue convidando a cadastrar nos proximos meses.
            nota.empresa_id = None
            nota.documento = formatado
            nota.tipo_documento = tipo
            nota.origem_vinculo = OrigemVinculoNfse.MANUAL
            nota.status = (StatusNotaNfse.PESSOA_FISICA if tipo == TIPO_CPF
                           else StatusNotaNfse.CADASTRO_PENDENTE)
            _lembrar(nota.nome_csv_norm, documento=formatado, tipo=tipo)
    else:
        return json_error('Informe uma empresa ou um CPF/CNPJ.', 400)

    db.session.commit()
    return {'status': 'ok', 'nota': _nota_para_json(nota)}


def _vincular(nota, empresa):
    nota.empresa_id = empresa.id
    nota.documento = empresa.cnpj
    nota.tipo_documento = detectar_tipo_documento(empresa.cnpj)
    nota.origem_vinculo = OrigemVinculoNfse.MANUAL
    nota.score_match = None
    if nota.status in (StatusNotaNfse.EMPRESA_PENDENTE,
                       StatusNotaNfse.CADASTRO_PENDENTE,
                       StatusNotaNfse.PESSOA_FISICA):
        nota.status = StatusNotaNfse.PRONTA


def _lembrar(nome_norm, empresa_id=None, documento=None, tipo=None):
    """Memoriza o que fazer com este nome do banco no proximo import.

    Guarda vinculo com Empresa OU documento avulso — para CPF e para CNPJ ainda
    nao cadastrado, que de outro modo seriam redigitados todo mes."""
    if not nome_norm:
        return
    apelido = ApelidoNfse.query.filter_by(nome_norm=nome_norm).first()
    if apelido is None:
        apelido = ApelidoNfse(nome_norm=nome_norm)
        db.session.add(apelido)
    apelido.empresa_id = empresa_id
    apelido.documento = documento
    apelido.tipo_documento = tipo


# --- resolucao manual da descricao (NFSE-26) -------------------------------

@bp.route('/nfse/nota/<int:nota_id>/descricao', methods=['POST'])
@requer_papel('operador')
def nfse_resolver_descricao(nota_id):
    """Diz o que a nota descreve, quando o Pix nao disse.

    Os dois campos sao INDEPENDENTES e combinaveis — ao contrario da resolucao
    de empresa, onde empresa e documento sao respostas concorrentes a mesma
    pergunta e uma tem de vencer. Aqui o servico e o TEXTO da nota e a
    competencia e o MES; travar um contra o outro obrigava o operador a salvar
    duas vezes (uma so para o mes, outra so para o texto) e, no meio do
    caminho, a nota exibia uma descricao que ele nao queria.

    Combinacoes, todas validas:

    - **so servico** — o texto muda, a competencia fica como estava;
    - **so competencia** — volta a ser honorarios (o campo de servico vazio
      significa "nao e servico avulso") com o mes informado;
    - **os dois** — o texto e o servico, e a competencia informada e a que
      passa a valer na coluna. O texto do servico NAO recebe o mes: quem
      descreve uma alteracao contratual nao diz "referente ao mes de".

    Em qualquer caso com servico o sistema MEMORIZA a decisao — o termo que veio
    no extrato passa a significar aquele servico, e o mesmo Pix abreviado se
    resolve sozinho no mes seguinte.
    """
    nota = db.session.get(NotaNfse, nota_id)
    if nota is None:
        return json_error('Nota nao encontrada.', 404)
    if nota.status in (StatusNotaNfse.EMITIDA, StatusNotaNfse.AGUARDANDO_CONFIRMACAO):
        return json_error(
            'Esta nota ja foi para o portal; a descricao nao pode mais mudar.', 409)

    dados = request.get_json(silent=True) or {}
    servico = (dados.get('descricao_servico') or '').strip()
    competencia = (dados.get('competencia') or '').strip()

    if not servico and not competencia:
        return json_error('Informe a competencia dos honorarios ou o servico.', 400)

    if competencia:
        if not _COMPETENCIA_VALIDA.match(competencia):
            return json_error(
                'Competencia invalida: use MM/AAAA (ex.: 06/2026).', 400)
        nota.competencia = competencia

    if servico:
        nota.descricao_servico = servico[:300]
        _lembrar_servico(nota, servico, (dados.get('termo') or '').strip() or None)
        if not nota.competencia and nota.data_pagamento:
            # a competencia do servico nao vai para o texto da nota; serve para
            # a linha aparecer no filtro de mes
            nota.competencia = (f'{nota.data_pagamento.month:02d}/'
                                f'{nota.data_pagamento.year}')
    else:
        # campo de servico vazio = a nota volta a ser honorarios, e a descricao
        # sai do template com a competencia
        nota.descricao_servico = None

    nota.descricao_pendente = False
    nota.status = nfse_import.recalcular_status(nota)
    db.session.commit()
    return {'status': 'ok', 'nota': _nota_para_json(nota)}


_COMPETENCIA_VALIDA = re.compile(r'^(0[1-9]|1[0-2])/20\d{2}$')


def _lembrar_servico(nota, descricao, termo=None):
    """Memoriza que este texto do extrato significa este servico.

    A chave e o que se repete no extrato do mes seguinte — o jeito como o
    cliente escreve —, nao a descricao oficial que o operador digitou. Duas
    formas, e a diferenca importa:

    - sem `termo`: a descricao INTEIRA do Pix (sem o prefixo 'Pix'). Serve para
      o Pix que so traz o nome do cliente e cujo significado o operador conhece.
      Casa por igualdade e nao consome o nome do tomador;
    - com `termo`: um fragmento ('ALT. CONTRATO'), que passa a ser reconhecido
      dentro de qualquer descricao e sai do nome do tomador.

    Em ambos os casos a chave passa pelo `chave_descricao`/`normalizar_termo` do
    leitor — a mesma funcao que a busca usa, senao o que se grava nunca e achado.
    """
    chave = (normalizar_termo(termo) if termo
             else chave_descricao(nota.descricao_extrato or nota.nome_csv or ''))
    if not chave:
        return
    servico = ServicoNfse.query.filter_by(termo_norm=chave[:140]).first()
    if servico is None:
        servico = ServicoNfse(termo_norm=chave[:140])
        db.session.add(servico)
    servico.descricao = descricao[:300]


# --- proposta de agrupamento (NFSE-27) -------------------------------------

@bp.route('/nfse/grupo/<token>/confirmar', methods=['POST'])
@requer_papel('operador')
def nfse_confirmar_grupo(token):
    """Junta os lancamentos propostos numa nota so, com o valor liquido."""
    dados = request.get_json(silent=True) or {}
    valor = dados.get('valor')

    if valor is not None:
        valor = str(valor).strip().replace('.', '').replace(',', '.')
        try:
            valor = Decimal(valor)
        except (InvalidOperation, ValueError):
            return json_error('Valor invalido: use o formato 900,00.', 400)

    try:
        nota = nfse_grupos.confirmar(token, valor,
                                     (dados.get('descricao') or '').strip() or None)
    except ValueError as exc:
        return json_error(str(exc), 400)
    except Exception as exc:
        db.session.rollback()
        return json_error(exc=exc, code=500)
    if nota is None:
        return json_error('Proposta de agrupamento nao encontrada.', 404)

    log_event('nfse_grupo_confirmado', nota_id=nota.id,
              valor=str(nota.valor_final), ajustado=nota.valor_ajustado)
    return {'status': 'ok', 'nota': _nota_para_json(nota)}


@bp.route('/nfse/grupo/<token>/descartar', methods=['POST'])
@requer_papel('operador')
def nfse_descartar_grupo(token):
    """Recusa a proposta: cada lancamento segue como nota propria."""
    notas = nfse_grupos.descartar(token)
    if not notas:
        return json_error('Proposta de agrupamento nao encontrada.', 404)
    log_event('nfse_grupo_descartado', total=len(notas))
    return {'status': 'ok', 'notas': [_nota_para_json(n) for n in notas]}


@bp.route('/nfse/grupo/<token>/desfazer', methods=['POST'])
@requer_papel('operador')
def nfse_desfazer_grupo(token):
    """Volta atras num agrupamento aplicado.

    Devolve a proposta ao estado de espera, e nao ao de descartada: desfazer
    nao e recusar — o operador pode querer juntar de novo com outro valor."""
    try:
        nota = nfse_grupos.desfazer(token)
    except ValueError as exc:
        return json_error(str(exc), 409)
    except Exception as exc:
        db.session.rollback()
        return json_error(exc=exc, code=500)
    if nota is None:
        return json_error('Agrupamento nao encontrado.', 404)

    log_event('nfse_grupo_desfeito', nota_id=nota.id, valor=str(nota.valor_final))
    return {'status': 'ok', 'nota': _nota_para_json(nota)}


# --- acao em massa sobre linhas selecionadas -------------------------------

# So o que e reversivel por um clique entra aqui. Preencher e EMITIR ficam de
# fora de proposito: emitir em massa e exatamente o que a ND-005 impede.
#
# `emitida_manual` nao contradiz isso — ela nao emite nada, registra o que o
# operador ja emitiu no portal por fora. E escrituracao do passado, nao acao
# sobre o portal, e desfaz-se pela mesma barra.
ACOES_EM_MASSA = {
    'cancelar': (lambda nota: _cancelar(nota), 'canceladas'),
    'restaurar': (lambda nota: _restaurar(nota), 'restauradas'),
    'emitida_manual': (lambda nota: _marcar_emitida(nota), 'marcadas como emitidas'),
    'desmarcar_emitida': (lambda nota: _desmarcar_emitida(nota), 'desmarcadas'),
}


@bp.route('/nfse/notas/acao', methods=['POST'])
@requer_papel('operador')
def nfse_acao_em_massa():
    """Aplica uma acao a varias linhas de uma vez.

    Parcial por desenho: o que der certo e aplicado e o que nao der volta
    nomeado em `recusadas`. Abortar tudo porque uma linha ja estava emitida
    obrigaria o operador a desmarcar a mao e repetir a selecao inteira.
    """
    dados = request.get_json(silent=True) or {}
    acao = (dados.get('acao') or '').strip()
    ids = dados.get('ids') or []

    if acao not in ACOES_EM_MASSA:
        return json_error(
            f'Ação inválida. Disponíveis: {", ".join(ACOES_EM_MASSA)}.', 400)
    if not isinstance(ids, list) or not ids:
        return json_error('Selecione ao menos uma linha.', 400)

    notas = NotaNfse.query.filter(NotaNfse.id.in_([int(i) for i in ids])).all()
    if not notas:
        return json_error('Nenhuma das linhas selecionadas foi encontrada.', 404)

    aplicar, rotulo = ACOES_EM_MASSA[acao]
    aplicadas, recusadas = [], []
    for nota in notas:
        erro = aplicar(nota)
        if erro:
            recusadas.append({'id': nota.id, 'nome': nota.nome_csv, 'motivo': erro})
        else:
            aplicadas.append(nota)

    if aplicadas:
        db.session.commit()
    else:
        db.session.rollback()

    log_event('nfse_acao_em_massa', acao=acao,
              aplicadas=len(aplicadas), recusadas=len(recusadas))
    return {
        'status': 'ok',
        'acao': acao,
        # o rotulo vem do servidor para a mensagem na tela nao repetir, no JS, a
        # lista de acoes que ja vive aqui
        'rotulo': rotulo,
        'aplicadas': [_nota_para_json(n) for n in aplicadas],
        'recusadas': recusadas,
    }


def _cancelar(nota):
    """Cancela a linha. Devolve o motivo da recusa, ou None se aplicou.

    Nucleo compartilhado pela rota de uma linha so e pela acao em massa: sem
    ele, a acao em massa poderia cancelar uma nota emitida que a rota
    individual recusa."""
    if nota.status == StatusNotaNfse.CANCELADA:
        return None                      # idempotente: ja esta como se pediu
    if nota.status == StatusNotaNfse.EMITIDA:
        return ('Já foi emitida; cancelar aqui não a cancela na prefeitura. '
                'Use o portal para isso.')
    if nota.status == StatusNotaNfse.AGUARDANDO_CONFIRMACAO:
        return ('Está preenchida no portal esperando confirmação. Resolva-a no '
                'navegador antes de cancelar a linha.')
    if nota.status == StatusNotaNfse.AGRUPADA:
        return 'Foi agrupada em outra nota; cancele a nota que a absorveu.'
    nota.status = StatusNotaNfse.CANCELADA
    # a falha da tentativa anterior nao vale mais: deixa-la ali mostraria a
    # linha como "Cancelada" com um erro embaixo, que nao quer dizer nada
    nota.erro = None
    return None


def _restaurar(nota):
    if nota.status != StatusNotaNfse.CANCELADA:
        return 'Não está cancelada.'
    nota.status = nfse_import.recalcular_status(nota)
    return None


def _marcar_emitida(nota):
    """Registra que o operador emitiu esta nota fora do sistema.

    Nao e emitir: e escrituracao do que ja aconteceu no portal — por isso cabe
    em acao em massa sem esbarrar na ND-005, que proibe a AUTOMACAO clicar em
    emitir. Marcar conta na trava de duplicidade: o mesmo tomador e competencia
    voltando no extrato do mes seguinte passam a ser avisados."""
    if nota.origem_emissao == ORIGEM_AUTOMACAO:
        return 'Foi emitida pela automação; não dá para marcar como manual.'
    if nota.status == StatusNotaNfse.AGRUPADA:
        return 'Virou parte de outra nota; marque a nota que a absorveu.'
    if nota.status == StatusNotaNfse.INVALIDA:
        return 'Veio incompleta do extrato e não chegou a ser emitível.'
    if nota.status == StatusNotaNfse.EMITIDA:
        return None                      # idempotente: ja esta como se pediu
    nota.status = StatusNotaNfse.EMITIDA
    nota.origem_emissao = ORIGEM_MANUAL
    nota.emitida_em = datetime.now()
    # a falha da tentativa anterior nao vale mais: deixa-la ali mostraria a
    # linha como "Emitida" com um erro embaixo, que nao quer dizer nada
    nota.erro = None
    return None


def _desmarcar_emitida(nota):
    """Desfaz a marcacao manual.

    So o que o operador marcou a mao: o que a automacao emitiu ela VIU
    acontecer na tela de confirmacao do portal, e desfazer por um clique
    afirmaria que uma nota fiscal existente nao existe."""
    if nota.origem_emissao != ORIGEM_MANUAL:
        return 'Só dá para desmarcar nota que você marcou como emitida à mão.'
    nota.status = nfse_import.recalcular_status(nota)
    nota.origem_emissao = None
    nota.emitida_em = None
    return None


# --- cancelar a linha (o contador dispensou a nota) ------------------------

@bp.route('/nfse/nota/<int:nota_id>/cancelar', methods=['POST'])
@requer_papel('operador')
def nfse_cancelar_nota(nota_id):
    """Marca/desmarca a linha que nao vira nota.

    Nao e "pular": pular e "nao agora" e a linha volta na proxima rodada do
    lote. Cancelar e uma decisao, e por isso OCUPA a competencia — reimportar o
    extrato traz a linha de volta como duplicata em vez de pronta, senao a
    decisao se perderia calada e a nota dispensada seria emitida.

    Reversivel pela mesma rota, como o "emitida na mao": o contador muda de
    ideia, e desfazer nao pode exigir reimportar o extrato.
    """
    nota = db.session.get(NotaNfse, nota_id)
    if nota is None:
        return json_error('Nota nao encontrada.', 404)

    dados = request.get_json(silent=True) or {}
    cancelar = dados.get('cancelar', True)

    # Mesma regra da acao em massa (`_cancelar`/`_restaurar`): duas copias
    # divergiriam, e a divergencia apareceria como "em massa cancelou o que a
    # linha sozinha recusa".
    erro = _cancelar(nota) if cancelar else _restaurar(nota)
    if erro:
        db.session.rollback()
        return json_error(erro, 409)

    db.session.commit()
    log_event('nfse_nota_cancelada' if cancelar else 'nfse_nota_descancelada',
              nota_id=nota.id)
    return {'status': 'ok', 'nota': _nota_para_json(nota)}


# --- liberar duplicata (ND-004) --------------------------------------------

@bp.route('/nfse/nota/<int:nota_id>/liberar-duplicata', methods=['POST'])
@requer_papel('operador')
def nfse_liberar_duplicata(nota_id):
    nota = db.session.get(NotaNfse, nota_id)
    if nota is None:
        return json_error('Nota nao encontrada.', 404)
    if nota.status != StatusNotaNfse.DUPLICATA:
        return json_error('Esta linha nao esta marcada como duplicata.', 400)

    nota.duplicata_liberada = True
    db.session.commit()
    return {'status': 'ok', 'nota': _nota_para_json(nota)}


# --- nota emitida fora do sistema ------------------------------------------

@bp.route('/nfse/nota/<int:nota_id>/emitida-manual', methods=['POST'])
@requer_papel('operador')
def nfse_marcar_emitida_manual(nota_id):
    """Marca/desmarca uma nota que o operador emitiu na mao.

    Marcar conta na trava de duplicidade: se o mesmo tomador e a mesma
    competencia voltarem no CSV do mes seguinte, o sistema avisa. A origem fica
    registrada para distinguir do que passou pela automacao.
    """
    nota = db.session.get(NotaNfse, nota_id)
    if nota is None:
        return json_error('Nota nao encontrada.', 404)

    dados = request.get_json(silent=True) or {}
    marcar = dados.get('marcar', True)

    # Mesma regra da acao em massa (`_marcar_emitida`/`_desmarcar_emitida`):
    # duas copias divergiriam, e a divergencia apareceria como "em massa marcou
    # o que a linha sozinha recusa".
    erro = _marcar_emitida(nota) if marcar else _desmarcar_emitida(nota)
    if erro:
        db.session.rollback()
        return json_error(erro, 409)

    db.session.commit()
    return {'status': 'ok', 'nota': _nota_para_json(nota)}


@bp.route('/nfse/nota/<int:nota_id>/liberar-preenchimento', methods=['POST'])
@requer_papel('operador')
def nfse_liberar_preenchimento(nota_id):
    """O operador declara que NAO emitiu: a nota volta para a fila.

    Par simetrico do "Emiti no portal". A ND-011 impede o SISTEMA de adivinhar
    o desfecho de um preenchimento cujo navegador foi fechado, e continua
    valendo — aqui quem declara e o humano, que sabe.

    Confere no espelho do portal antes, quando ha navegador aberto. Achando
    nota que pode ser esta, responde 409 com as candidatas em vez de liberar: o
    operador olha valor e data e reenvia com `confirmado: true` se nao for ela.
    """
    nota = db.session.get(NotaNfse, nota_id)
    if nota is None:
        return json_error('Nota nao encontrada.', 404)

    dados = request.get_json(silent=True) or {}
    erro, evidencias = nfse_service.liberar_preenchimento(
        nota, confirmado=bool(dados.get('confirmado')))
    if erro:
        db.session.rollback()
        return json_error(erro, 409)
    if evidencias:
        db.session.rollback()
        return json_error(
            'O portal registra nota emitida para este tomador nesta '
            'competência. Confira antes de liberar: emitir de novo cria '
            'duplicata na prefeitura.', 409,
            emitidas=[_emitida_para_json(e) for e in evidencias])

    db.session.commit()
    auditoria.registrar(
        'nfse.nota.liberar_preenchimento',
        alvo_tipo='nota_nfse', alvo_id=nota.id,
        detalhe=f'nota_id={nota.id};status={nota.status}',
    )
    return {'status': 'ok', 'nota': _nota_para_json(nota)}


# --- notas emitidas no portal (NFSE-28) ------------------------------------

def _emitida_para_json(emitida):
    return {
        'id': emitida.id,
        'chave': emitida.chave,
        'data_geracao': (emitida.data_geracao.strftime('%d/%m/%Y')
                         if emitida.data_geracao else None),
        'documento': emitida.documento,
        'nome_tomador': emitida.nome_tomador,
        'competencia_dps': emitida.competencia_dps,
        'municipio': emitida.municipio,
        'valor': _valor_json(emitida.valor),
        'situacao': emitida.situacao,
        'nota_id': emitida.nota_id,
    }


def _competencia_corrente():
    hoje = datetime.now()
    return f'{hoje.month:02d}/{hoje.year}'


def _painel_vazio(mes=None):
    """Estado honesto quando ainda não há uma consulta completa persistida."""
    return {
        'consulta_id': None,
        'inicio': None,
        'fim': None,
        'mes_geracao': mes,
        'nunca_consultado': True,
        'quantidade': 0,
        'total': None,
        'outras_situacoes': {},
        'consultado_em': None,
        'sem_nota': [],
        'sem_extrato': [],
        'nao_conferiveis': 0,
        'valor_diferente': [],
        'ambigua': [],
    }


def _painel_emitidas(consulta=None, mes=None):
    """Total e divergências da consulta completa indicada pelo identificador."""
    if isinstance(consulta, str) and mes is None:
        mes = consulta
        consulta = None
    if consulta is None and mes:
        consulta = nfse_emitidas.ultima_consulta(mes=mes)
    if consulta is None:
        return _painel_vazio(mes)

    resumo = nfse_emitidas.resumo_periodo(consulta.inicio, consulta.fim)
    divergentes = nfse_emitidas.divergencias(consulta.inicio, consulta.fim)
    return {
        'consulta_id': consulta.id,
        'inicio': consulta.inicio.isoformat(),
        'fim': consulta.fim.isoformat(),
        'mes_geracao': nfse_emitidas.competencia_do_bloco(consulta.inicio),
        'nunca_consultado': False,
        'quantidade': resumo['quantidade'],
        'total': _valor_extenso(resumo['total']),
        'outras_situacoes': resumo['outras_situacoes'],
        'consultado_em': (consulta.consultado_em.strftime('%d/%m/%Y %H:%M')
                          if consulta.consultado_em else None),
        'sem_nota': [_nota_para_json(n) for n in divergentes['sem_nota']],
        'sem_extrato': [_emitida_para_json(e) for e in divergentes['sem_extrato']],
        'nao_conferiveis': divergentes['nao_conferiveis'],
        'valor_diferente': [
            {'nota': _nota_para_json(n), 'emitida': _emitida_para_json(e)}
            for n, e in divergentes['valor_diferente']],
        'ambigua': [
            {'emitida': _emitida_para_json(item['emitida']),
             'candidatas': [_nota_para_json(n) for n in item['candidatas']]}
            for item in divergentes['ambigua']],
    }


@bp.route('/nfse/emitidas/consultar', methods=['POST'])
@requer_papel('operador')
def nfse_consultar_emitidas():
    """Le a listagem do portal no periodo pedido e grava o espelho.

    Sincrona, ao contrario do preenchimento (ND-009): aqui nao ha espera por
    confirmacao humana — sao poucas navegacoes (6 paginas para 80 notas) e o
    operador fica olhando o resultado aparecer.
    """
    dados = request.get_json(silent=True) or {}
    try:
        inicio = _data_pedida(dados.get('inicio'))
        fim = _data_pedida(dados.get('fim'))
    except ValueError as exc:
        return json_error(str(exc), 400)

    if inicio > fim:
        return json_error('A data inicial não pode ser depois da final.', 400)

    # Toma a sessao como todo fluxo que dirige o navegador. Sem isto, consultar
    # durante um lote levaria o MESMO Chrome para /Notas/Emitidas no meio do
    # preenchimento de uma DPS — o assistente perderia o que ja estava digitado
    # e a nota falharia, sem nada na tela explicando por que.
    if not SESSAO.adquirir():
        return json_error(
            'A sessão do navegador está ocupada com uma emissão. Aguarde '
            'terminar e consulte depois.', 409)

    try:
        resultado = nfse_emitidas.consultar(inicio, fim)
    except automacao_emitidas.TotalDivergenteError as exc:
        # o portal anunciou N e a leitura terminou com outro numero: recusar e
        # mais seguro que devolver um total fiscal a menos
        return json_error(str(exc), 502)
    except Exception as exc:
        return json_error(exc=exc, code=500)
    finally:
        # O navegador FICA ABERTO de proposito: e a mesma sessao autenticada que
        # o preenchimento usa, e fecha-la aqui faria o certificado ser pedido de
        # novo na proxima nota. Quem fecha e o "Encerrar sessão".
        SESSAO.liberar()

    consulta = nfse_emitidas.ultima_consulta(
        consulta_id=resultado['consulta_id'])
    return {
        'status': 'ok',
        'blocos': resultado['blocos'],
        'lidas': resultado['lidas'],
        'novas': resultado['novas'],
        'atualizadas': resultado['atualizadas'],
        'consulta_id': resultado['consulta_id'],
        'painel': _painel_emitidas(consulta=consulta),
    }


@bp.route('/nfse/emitidas')
@requer_papel('operador')
def nfse_painel_emitidas():
    """Estado atual do espelho, para a tela atualizar sem reconsultar o portal."""
    bruto_id = (request.args.get('consulta_id') or '').strip()
    if bruto_id:
        try:
            consulta_id = int(bruto_id)
        except ValueError:
            return json_error('O identificador da consulta é inválido.', 400)
        consulta = nfse_emitidas.ultima_consulta(consulta_id=consulta_id)
        if consulta is None:
            return json_error('A consulta solicitada não foi encontrada.', 404)
        return {'status': 'ok', 'painel': _painel_emitidas(consulta=consulta)}

    mes = (request.args.get('mes') or '').strip()
    if not _COMPETENCIA_VALIDA.match(mes):
        return json_error('Informe o mês no formato MM/AAAA.', 400)
    return {'status': 'ok', 'painel': _painel_emitidas(mes=mes)}


def _data_pedida(bruto):
    texto = (bruto or '').strip()
    if not texto:
        raise ValueError('Informe as datas inicial e final do período.')
    for formato in ('%Y-%m-%d', '%d/%m/%Y'):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    raise ValueError(f'Data inválida: "{texto}". Use dd/mm/aaaa.')


# --- configuracao (NFSE-08/09) ---------------------------------------------

@bp.route('/nfse/configuracao', methods=['POST'])
@requer_papel('operador')
def nfse_salvar_configuracao():
    dados = request.get_json(silent=True) or request.form.to_dict()
    try:
        config = nfse_config.salvar(dados)
    except nfse_config.ConfiguracaoInvalidaError as exc:
        return json_error(exc.mensagem, 400, campo=exc.campo)
    return {
        'status': 'ok',
        'config': {campo: getattr(config, campo)
                   for campo in nfse_config.CAMPOS_OBRIGATORIOS},
    }


# --- sessao do navegador (NFSE-11/12/15) -----------------------------------

@bp.route('/nfse/sessao/preparar', methods=['POST'])
@requer_papel('operador')
def nfse_preparar_sessao():
    """Abre o navegador, loga com certificado e le a aliquota do Simples.

    Nao libera emissao: quem libera e a confirmacao explicita do operador."""
    if not SESSAO.adquirir():
        return json_error(
            'Ja existe uma sessao da NFSe em andamento nesta maquina.', 409)
    try:
        return {'status': 'ok', **nfse_service.preparar_sessao()}
    except Exception as exc:
        SESSAO.encerrar()
        return json_error(exc=exc, code=500)
    finally:
        SESSAO.liberar()


@bp.route('/nfse/sessao/confirmar-aliquota', methods=['POST'])
@requer_papel('operador')
def nfse_confirmar_aliquota():
    dados = request.get_json(silent=True) or {}
    SESSAO.confirmar_aliquota(dados.get('aliquota'))
    return {'status': 'ok', 'aliquota': SESSAO.aliquota,
            'aliquota_confirmada': SESSAO.aliquota_confirmada}


@bp.route('/nfse/sessao/encerrar', methods=['POST'])
@requer_papel('operador')
def nfse_encerrar_sessao():
    """Idempotente: encerrar sem sessao aberta e sucesso, nao erro."""
    SESSAO.encerrar()
    return {'status': 'ok'}


@bp.route('/nfse/sessao/status')
@requer_papel('operador')
def nfse_status_sessao():
    return {
        'status': 'ok',
        'ativa': SESSAO.driver_vivo(),
        'ocupada': SESSAO.ocupada,
        'aliquota': SESSAO.aliquota,
        'aliquota_confirmada': SESSAO.aliquota_confirmada,
    }


# O preenchimento de uma nota nao tem mais rota propria: os dois modos entram
# por /nfse/lote/iniciar. Uma rota sincrona nao serviria ao modo individual, que
# agora espera o operador conferir e emitir — a requisicao ficaria pendurada por
# minutos — e manter as duas seria dois caminhos para a mesma coisa.


# --- emissao assistida: fila de uma nota ou do lote (NFSE-19/20) ------------

@bp.route('/nfse/lote/iniciar', methods=['POST'])
@requer_papel('operador')
def nfse_lote_iniciar():
    """Poe notas na fila da emissao assistida, no modo escolhido na pagina.

    `individual` enfileira so a nota da linha clicada e fecha o navegador
    quando ela sai; `lote` enfileira todas as emitiveis e mantem a janela
    autenticada entre uma nota e outra. Nos dois a automacao para na revisao —
    quem clica em emitir e o operador.
    """
    dados = request.get_json(silent=True) or {}
    modo = dados.get('modo') or nfse_lote.MODO_LOTE
    if modo not in nfse_lote.MODOS:
        return json_error('Modo de emissao desconhecido.', 400)

    nota_id = dados.get('nota_id')
    if modo == nfse_lote.MODO_INDIVIDUAL and not nota_id:
        return json_error('Escolha a linha que deve ser preenchida.', 400)

    ignorar_aliquota = bool(dados.get('ignorar_aliquota'))
    try:
        nfse_service.checar_aliquota(ignorar_aliquota)
    except nfse_service.AliquotaNaoConfirmadaError as exc:
        # a interface transforma isso num aviso confirmavel, nao num bloqueio
        return json_error(str(exc), 409, motivo='aliquota_nao_confirmada',
                          aliquota=SESSAO.aliquota)

    try:
        contrato = nfse_lote.validar_contrato_para_modo(modo)
        contrato = contrato or nfse_contrato.contrato_ativo()
    except nfse_contrato.ContratoNfseNaoElegivelError as exc:
        return json_error(str(exc), 409, motivo='contrato_nfse_nao_elegivel')
    except ValueError as exc:
        return json_error(str(exc), 400, campo='modo')

    # Toma a sessao aqui e nao no worker para que "ja tem emissao rodando" seja
    # decidido antes de qualquer thread nascer. Quem devolve o lock e o
    # `on_teardown` do worker (ou os caminhos de erro logo abaixo).
    if not SESSAO.adquirir():
        return json_error(
            'Ja existe uma emissao da NFSe em andamento. Aguarde terminar.', 409)

    # Recusar ANTES de gravar as opcoes. `init_batch_run` tambem recusa lote em
    # andamento, mas la o modo ja teria sido trocado: um inicio individual
    # rejeitado viraria o modo de um lote PAUSADO para individual, e o Retomar
    # fecharia o navegador depois da primeira nota. As duas checagens sao
    # seguras juntas porque a sessao ja esta tomada acima.
    with NFSE_BATCH_LOCK:
        em_andamento = NFSE_BATCH_STATE.get('status') in ('running', 'paused')
    if em_andamento:
        SESSAO.liberar()
        return json_error('Ja existe um lote de NFSe em andamento.', 409)

    definir_nfse_batch_opcoes(
        modo,
        ignorar_aliquota,
        contrato_id=contrato.id,
    )
    nfse_lote.preparar_nova_fila()

    # a fila e o que a pagina mostra, nao "o ultimo lote": com um mes filtrado
    # na tela, enfileirar o ultimo lote emitiria notas que nao estao a vista
    competencia = _competencia_pedida(dados.get('competencia'))
    lote = None if competencia else LoteNfse.query.order_by(LoteNfse.id.desc()).first()

    try:
        dados_lote = batch_engine.init_batch_run(
            NFSE_BATCH_LOCK, NFSE_BATCH_STATE, nota_id,
            lambda inicio: nfse_lote.calcular_alvos(
                inicio, lote_id=lote.id if lote else None,
                competencia=competencia),
            nfse_lote.worker, app_factory=_current_app_object,
        )
    except Exception as exc:
        SESSAO.liberar()
        return json_error(exc=exc, code=500)

    if dados_lote is None:
        SESSAO.liberar()
        return json_error('Ja existe um lote de NFSe em andamento.', 409)
    if not dados_lote:
        SESSAO.liberar()
        return json_error(
            'Nenhuma nota desta lista esta pronta para emissao.', 400)

    log_event('nfse_lote_iniciado', modo=modo, total=dados_lote['total'],
              execution_id=NFSE_BATCH_STATE.get('execution_id'))
    return {'status': 'ok', 'modo': modo, 'total': dados_lote['total']}


@bp.route('/nfse/lote/pular', methods=['POST'])
@requer_papel('operador')
def nfse_lote_pular():
    """Abandona a nota que esta na tela e segue para a proxima da fila."""
    if not nfse_lote.pedir_pular():
        return json_error('Nao ha emissao em andamento para pular.', 400)
    return {'status': 'ok'}


@bp.route('/nfse/lote/pausar', methods=['POST'])
@requer_papel('operador')
def nfse_lote_pausar():
    if not nfse_lote.pedir_pausa():
        return json_error('Não há emissão em andamento para pausar.', 400)
    return {'status': 'ok', 'message': 'Pausa solicitada.'}


@bp.route('/nfse/lote/parar', methods=['POST'])
@requer_papel('operador')
def nfse_lote_parar():
    if not nfse_lote.pedir_parada():
        return json_error('Não há emissão em andamento para parar.', 400)
    return {'status': 'ok', 'message': 'Fila interrompida.'}


@bp.route('/nfse/lote/retomar', methods=['POST'])
@requer_papel('operador')
def nfse_lote_retomar():
    """Recomeca pela nota onde parou — o motor nao avanca o indice ao pausar."""
    with NFSE_BATCH_LOCK:
        pausado = NFSE_BATCH_STATE.get('status') == 'paused'
    if not pausado:
        return json_error('A emissão não está pausada.', 400)

    opcoes = nfse_batch_opcoes()
    contrato = None
    if opcoes['modo'] == nfse_lote.MODO_AUTOMATICO:
        try:
            # Um desvio pausa o lote sobre a nota atual. Depois que o operador
            # configura, valida e ativa a nova versão, a retomada precisa usar
            # essa versão — manter o id fixado no início repetiria o desvio.
            contrato = nfse_contrato.validar_contrato_automatico()
        except nfse_contrato.ContratoNfseNaoElegivelError as exc:
            return json_error(
                str(exc), 409, motivo='contrato_nfse_nao_elegivel'
            )

    if not SESSAO.adquirir():
        return json_error(
            'Ja existe uma emissao da NFSe em andamento. Aguarde terminar.', 409)
    # Só depois de tomar a sessão: uma retomada que morre em 409 não pode ter
    # trocado o contrato fixado da fila que continua pausada.
    if contrato is not None:
        definir_nfse_batch_opcoes(
            opcoes['modo'],
            opcoes['ignorar_aliquota'],
            contrato_id=contrato.id,
        )
    nfse_lote.preparar_nova_fila()
    if not batch_engine.resume_batch(NFSE_BATCH_LOCK, NFSE_BATCH_STATE,
                                     nfse_lote.worker,
                                     app_factory=_current_app_object):
        SESSAO.liberar()
        return json_error('A emissão não está pausada.', 400)
    return {'status': 'ok'}


@bp.route('/nfse/lote/status')
@requer_papel('operador')
def nfse_lote_status():
    return {'status': 'ok', 'lote': nfse_lote.status()}
