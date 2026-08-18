r"""Rotas do manifestador de NF-e (MANIF-09/11/14/21).

Registra no blueprint "main" compartilhado (AD-013). Rotas **finas**: toda a
logica vive em `app/services/manifestador_*` e `nfe_*`; aqui so entra validacao
de entrada, autorizacao e montagem da resposta.

Papel `operador` em tudo, com uma excecao: gravar senha de certificado exige
`admin` — e credencial de cliente, um degrau acima das demais acoes.
"""
from flask import render_template, request
from flask_login import current_user

from app import db
from app.auth import requer_papel
from app.automation.batch_state import (
    MANIF_BATCH_LOCK,
    MANIF_BATCH_STATE,
    definir_manif_opcoes,
)
from app.models import ChaveManifestacao, Empresa, EstadoCertificado
from app.routes import _current_app_object, bp
from app.services import (
    batch_engine,
    manifestador_cofre,
    manifestador_import,
    manifestador_lote,
    manifestador_service,
)
from app.services.execution_logger import log_event
from app.utils import json_error

# Colagem de um mes inteiro cabe folgado; acima disso e engano (arquivo errado
# arrastado para o campo, por exemplo).
TAMANHO_MAXIMO_COLAGEM = 2 * 1024 * 1024
TAMANHO_MAXIMO_XML = 50 * 1024 * 1024


def _chave_para_json(linha):
    return {
        'id': linha.id,
        'chave': linha.chave,
        'empresa_id': linha.empresa_id,
        'empresa': linha.empresa.nome if linha.empresa else None,
        'competencia': linha.competencia,
        'competencia_ajustada': linha.competencia_ajustada,
        'cnpj_emitente': linha.cnpj_emitente,
        'origem': linha.origem,
        'status': linha.status,
        'tipo_evento': linha.tipo_evento,
        'cstat': linha.cstat,
        'xmotivo': linha.xmotivo,
        'protocolo': linha.protocolo,
        'ja_existia': linha.ja_existia,
    }


# --- pagina -----------------------------------------------------------------

@bp.route('/manifestador')
@requer_papel('operador')
def manifestador_painel():
    # A lista de empresas vai no proprio HTML (mesmo padrao da NFSe): muda
    # raramente e nao merece uma rota nem uma ida a rede a cada carregamento.
    return render_template(
        'manifestador.html',
        empresas=[{'id': e.id, 'nome': e.nome}
                  for e in Empresa.query.order_by(Empresa.nome).all()])


# --- cofre de certificados (MANIF-21) ---------------------------------------

@bp.route('/manifestador/cofre')
@requer_papel('operador')
def manifestador_cofre_estado():
    """Pre-voo: quantas empresas estao prontas e quantas nao.

    Le BANCO, nunca rede — a varredura do drive custa ~135 s e nao pode
    acontecer a cada carregamento da pagina."""
    contagem = manifestador_cofre.estado_da_carteira()
    pendencias = (
        db.session.query(Empresa)
        .join(Empresa.certificado)
        .filter(Empresa.certificado.has())
        .all()
    )
    problemas = []
    for empresa in pendencias:
        certificado = empresa.certificado
        if certificado.estado == EstadoCertificado.PRONTO:
            continue
        problemas.append({
            'empresa_id': empresa.id,
            'empresa': empresa.nome,
            'estado': certificado.estado,
            'caminho': certificado.caminho,
            'detalhe': certificado.detalhe,
            'sugestao_senha': manifestador_cofre.sugerir_senha(certificado.caminho),
            'verificado_em': (certificado.verificado_em.isoformat()
                              if certificado.verificado_em else None),
        })

    return {
        'status': 'ok',
        'contagem': contagem,
        'prontas': contagem.get(EstadoCertificado.PRONTO, 0),
        'problemas': problemas,
        'inventariado': bool(contagem),
    }


@bp.route('/manifestador/cofre/inventariar', methods=['POST'])
@requer_papel('operador')
def manifestador_cofre_inventariar():
    """Varre o drive e reclassifica todas as empresas ativas."""
    try:
        resumo = manifestador_cofre.inventariar()
    except manifestador_cofre.CofreError as exc:
        return json_error(str(exc), 503)

    log_event('manifestador_inventario_pedido', **resumo)
    return {'status': 'ok', 'resumo': resumo}


@bp.route('/manifestador/cofre/senha/<int:empresa_id>', methods=['POST'])
@requer_papel('admin')
def manifestador_cofre_senha(empresa_id):
    """Grava a senha de um certificado, conferindo-a contra o arquivo antes.

    `admin` e nao `operador`: e credencial de cliente. A senha nunca volta na
    resposta."""
    empresa = db.session.get(Empresa, empresa_id)
    if empresa is None:
        return json_error('Empresa nao encontrada.', 404)

    senha = (request.get_json(silent=True) or {}).get('senha') or ''
    if not senha:
        return json_error('Informe a senha do certificado.', 400)

    try:
        aceita = manifestador_cofre.gravar_senha(empresa, senha)
    except manifestador_cofre.CofreError as exc:
        return json_error(str(exc), 500)

    if not aceita:
        return json_error(
            'Essa senha nao abre o certificado desta empresa (ou o certificado '
            'e de outro CNPJ). Confira e tente de novo.', 400)

    return {'status': 'ok', 'estado': empresa.certificado.estado}


# --- importacao de chaves (MANIF-09, MANIF-11) ------------------------------

@bp.route('/manifestador/importar', methods=['POST'])
@requer_papel('operador')
def manifestador_importar():
    """Cola um bloco de texto na fila de UMA empresa.

    A empresa e escolhida na tela porque a chave nao a revela: o CNPJ dentro
    dela e o do EMITENTE, nao o do destinatario."""
    dados = request.get_json(silent=True) or {}
    texto = dados.get('texto') or ''
    if not texto.strip():
        return json_error('Cole as chaves de acesso.', 400)
    if len(texto) > TAMANHO_MAXIMO_COLAGEM:
        return json_error('Texto grande demais para ser uma colagem de chaves.', 400)

    empresa = db.session.get(Empresa, dados.get('empresa_id') or 0)
    if empresa is None:
        return json_error('Escolha a empresa dona destas notas.', 400)

    balanco = manifestador_import.importar_colagem(empresa, texto)
    return {'status': 'ok', 'balanco': balanco.como_dict()}


@bp.route('/manifestador/importar/xml', methods=['POST'])
@requer_papel('operador')
def manifestador_importar_xml():
    """Importa XMLs de NF-e; a empresa sai do `dest/CNPJ` de cada arquivo."""
    enviados = [a for a in request.files.getlist('arquivo')
                if a is not None and (a.filename or '').strip()]
    if not enviados:
        return json_error('Selecione ao menos um XML de NF-e.', 400)

    arquivos = []
    total = 0
    for arquivo in enviados:
        conteudo = arquivo.read()
        total += len(conteudo)
        if total > TAMANHO_MAXIMO_XML:
            return json_error('Arquivos grandes demais para XMLs de NF-e.', 400)
        arquivos.append((arquivo.filename, conteudo))

    balanco = manifestador_import.importar_xmls(arquivos)
    return {'status': 'ok', 'balanco': balanco.como_dict()}


# --- lista e conferencia ----------------------------------------------------

@bp.route('/manifestador/chaves')
@requer_papel('operador')
def manifestador_chaves():
    """Lista filtrada. O filtro daqui e o MESMO que o lote enfileira."""
    consulta = ChaveManifestacao.query
    empresa_id = request.args.get('empresa_id', type=int)
    competencia = (request.args.get('competencia') or '').strip()
    status = (request.args.get('status') or '').strip()

    if empresa_id:
        consulta = consulta.filter_by(empresa_id=empresa_id)
    if competencia:
        consulta = consulta.filter_by(competencia=competencia)
    if status:
        consulta = consulta.filter_by(status=status)

    linhas = consulta.order_by(ChaveManifestacao.empresa_id,
                               ChaveManifestacao.id).all()
    return {
        'status': 'ok',
        'chaves': [_chave_para_json(linha) for linha in linhas],
        'total': len(linhas),
    }


@bp.route('/manifestador/chave/<int:chave_id>/competencia', methods=['POST'])
@requer_papel('operador')
def manifestador_ajustar_competencia(chave_id):
    linha = db.session.get(ChaveManifestacao, chave_id)
    if linha is None:
        return json_error('Chave nao encontrada.', 404)

    valor = (request.get_json(silent=True) or {}).get('competencia')
    if not manifestador_import.ajustar_competencia(linha, valor):
        return json_error('Competencia invalida. Use o formato AAAA-MM.', 400)

    return {'status': 'ok', 'chave': _chave_para_json(linha)}


@bp.route('/manifestador/chave/<int:chave_id>/liberar', methods=['POST'])
@requer_papel('operador')
def manifestador_liberar(chave_id):
    """Devolve uma duplicata a fila, resolvendo a empresa quando ha conflito."""
    linha = db.session.get(ChaveManifestacao, chave_id)
    if linha is None:
        return json_error('Chave nao encontrada.', 404)

    dados = request.get_json(silent=True) or {}
    empresa = None
    if dados.get('empresa_id'):
        empresa = db.session.get(Empresa, dados['empresa_id'])
        if empresa is None:
            return json_error('Empresa nao encontrada.', 400)

    liberou = manifestador_import.liberar_duplicata(
        linha, empresa=empresa,
        ator_id=getattr(current_user, 'id', None),
        confirmar=bool(dados.get('confirmar')))

    if not liberou:
        return json_error(
            'Esta nota ja foi manifestada. Confirme para devolve-la a fila.',
            409, motivo='confirmacao_necessaria')

    return {'status': 'ok', 'chave': _chave_para_json(linha)}


# --- lote (MANIF-14) --------------------------------------------------------

@bp.route('/manifestador/lote/iniciar', methods=['POST'])
@requer_papel('operador')
def manifestador_lote_iniciar():
    """Poe chaves na fila, no modo e no tipo de evento escolhidos na tela.

    O tipo de evento e OBRIGATORIO no payload: Confirmacao da Operacao e
    irreversivel e nao deve sair por omissao."""
    dados = request.get_json(silent=True) or {}

    modo = dados.get('modo')
    if modo not in manifestador_lote.MODOS:
        return json_error('Modo de manifestacao desconhecido.', 400)

    tipo_evento = dados.get('tipo_evento')
    if tipo_evento not in manifestador_service.DESCRICOES:
        return json_error(
            'Escolha o tipo de evento. Manifestacao nao sai por omissao.', 400)

    chave_id = dados.get('chave_id')
    if modo == manifestador_lote.MODO_INDIVIDUAL and not chave_id:
        return json_error('Escolha a chave que deve ser manifestada.', 400)

    empresa_id = dados.get('empresa_id')
    if modo == manifestador_lote.MODO_EMPRESA and not empresa_id:
        return json_error('Escolha a empresa.', 400)

    if not manifestador_cofre.estado_da_carteira():
        return json_error(
            'O cofre de certificados ainda nao foi inventariado. Rode o '
            'inventario antes de manifestar.', 409, motivo='cofre_vazio')

    with MANIF_BATCH_LOCK:
        em_andamento = MANIF_BATCH_STATE.get('status') in ('running', 'paused')
    if em_andamento:
        return json_error('Ja existe uma manifestacao em andamento.', 409)

    competencia = (dados.get('competencia') or '').strip() or None
    definir_manif_opcoes(modo=modo, tipo_evento=tipo_evento,
                         empresa_id=empresa_id, competencia=competencia,
                         chave_id=chave_id)

    try:
        dados_lote = batch_engine.init_batch_run(
            MANIF_BATCH_LOCK, MANIF_BATCH_STATE, chave_id,
            lambda _inicio: manifestador_lote.calcular_alvos(
                modo=modo, chave_id=chave_id, empresa_id=empresa_id,
                competencia=competencia),
            manifestador_lote.worker, app_factory=_current_app_object,
        )
    except Exception as exc:
        return json_error(exc=exc, code=500)

    if dados_lote is None:
        return json_error('Ja existe uma manifestacao em andamento.', 409)
    if not dados_lote:
        return json_error('Nenhuma chave desta lista esta pronta para '
                          'manifestacao.', 400)

    pulados = manifestador_lote.grupos_sem_certificado(dados_lote['ids'])
    log_event('manifestador_lote_iniciado', modo=modo, tipo_evento=tipo_evento,
              total=dados_lote['total'],
              execution_id=MANIF_BATCH_STATE.get('execution_id'))

    return {
        'status': 'ok',
        'total': dados_lote['total'],
        'modo': modo,
        'tipo_evento': tipo_evento,
        # Nomeados na resposta: o operador ve na hora quem ficou de fora e por
        # que, em vez de descobrir no fim do lote.
        'empresas_puladas': pulados,
    }


@bp.route('/manifestador/lote/status')
@requer_papel('operador')
def manifestador_lote_status():
    return {'status': 'ok', 'lote': manifestador_lote.status()}


@bp.route('/manifestador/lote/pausar', methods=['POST'])
@requer_papel('operador')
def manifestador_lote_pausar():
    batch_engine.request_pause(MANIF_BATCH_LOCK, MANIF_BATCH_STATE)
    return {'status': 'ok', 'message': 'Manifestacao pausada.'}


@bp.route('/manifestador/lote/parar', methods=['POST'])
@requer_papel('operador')
def manifestador_lote_parar():
    batch_engine.request_stop(MANIF_BATCH_LOCK, MANIF_BATCH_STATE)
    return {'status': 'ok', 'message': 'Manifestacao interrompida.'}


@bp.route('/manifestador/lote/retomar', methods=['POST'])
@requer_papel('operador')
def manifestador_lote_retomar():
    """Recomeca pela chave onde parou — o motor nao avanca o indice ao pausar."""
    if not batch_engine.resume_batch(MANIF_BATCH_LOCK, MANIF_BATCH_STATE,
                                     manifestador_lote.worker,
                                     app_factory=_current_app_object):
        return json_error('Nao ha manifestacao pausada para retomar.', 409)
    return {'status': 'ok', 'message': 'Manifestacao retomada.'}


@bp.route('/manifestador/chave/<int:chave_id>/reprocessar', methods=['POST'])
@requer_papel('operador')
def manifestador_reprocessar(chave_id):
    """Devolve a fila uma chave rejeitada ou indefinida — sem manifestar nada.

    Quem executa e o lote seguinte. Recusa `manifestada`: aquilo ja e fato
    fiscal, e voltar sozinha a fila esconderia que a nota saiu."""
    from app.models import StatusManifestacao

    linha = db.session.get(ChaveManifestacao, chave_id)
    if linha is None:
        return json_error('Chave nao encontrada.', 404)

    if linha.status not in (StatusManifestacao.REJEITADA,
                            StatusManifestacao.INDEFINIDA):
        return json_error(
            f'Chave em "{linha.status}" nao entra em reprocessamento.', 400)

    linha.status = StatusManifestacao.PENDENTE
    linha.cstat = None
    linha.xmotivo = None
    db.session.commit()
    return {'status': 'ok', 'chave': _chave_para_json(linha)}
