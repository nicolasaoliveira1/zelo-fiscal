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


def _capturar_ator(contexto):
    """Copia a identidade autenticada para o snapshot interno do lote."""
    return {
        'ator_id': current_user.id,
        'ator_nome': current_user.username,
        'ator_papel': current_user.papel,
        'ator_contexto': contexto,
    }


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
        'tentativas': linha.tentativas,
        # Aviso de prazo (Ajuste SINIEF 14/2026: 90 dias). Calculado na leitura
        # porque depende de HOJE — guardar em coluna congelaria a resposta.
        'fora_do_prazo': manifestador_import.fora_do_prazo(linha.chave),
        'no_teto': not manifestador_service.manifestavel(linha)
                   and linha.status in manifestador_service.STATUS_MANIFESTAVEIS,
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
    """Pre-voo: quantas empresas estao prontas, quais nao, e o que vence.

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

    # Os vencimentos vem do MESMO servico que alimenta o cartao da Visao Geral
    # (`resumo_de_vencimento`), e nao de uma segunda contagem aqui: o cartao e a
    # regua dizem a mesma frase porque leem a mesma fonte. Quem chega pelo
    # cartao espera reencontrar exatamente aqueles nomes.
    vencimento = manifestador_cofre.resumo_de_vencimento()

    return {
        'status': 'ok',
        'contagem': contagem,
        'prontas': contagem.get(EstadoCertificado.PRONTO, 0),
        'problemas': problemas,
        'inventariado': bool(contagem),
        'vencimentos': {
            'itens': [{
                'empresa_id': item['empresa_id'],
                'empresa_nome': item['empresa_nome'],
                'not_after': item['not_after'].isoformat(),
                'dias_restantes': item['dias_restantes'],
                'causa': item['causa'],
            } for item in vencimento['itens']],
            'com_vencimento': vencimento['com_vencimento'],
            'janela_dias': vencimento['janela_dias'],
        },
    }


@bp.route('/manifestador/cofre/inventariar', methods=['POST'])
@requer_papel('operador')
def manifestador_cofre_inventariar():
    """Varre o drive e reclassifica todas as empresas ativas.

    409 imediato quando ja ha varredura em curso (o job diario, ou outro clique):
    duas varreduras gravam a linha da MESMA empresa e a perdedora derruba o
    commit unico do fim. Lock nao-bloqueante, como a `NfseSession`.
    """
    if not manifestador_cofre._inventario_acquire():
        return json_error(
            'Ja existe um inventario do cofre em andamento. Aguarde ele '
            'terminar e tente de novo.', 409)
    try:
        resumo = manifestador_cofre.inventariar()
    except manifestador_cofre.CofreError as exc:
        return json_error(str(exc), 503)
    finally:
        manifestador_cofre._inventario_release()

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

    # A competencia vem do OPERADOR: a chave so tem o mes de EMISSAO, e uma nota
    # emitida dia 30 e recebida dia 1o pertence ao mes seguinte.
    try:
        balanco = manifestador_import.importar_colagem(
            empresa, texto, competencia=dados.get('competencia'))
    except ValueError as exc:
        return json_error(str(exc), 400)
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

    chave_ids = dados.get('chave_ids')
    if chave_ids is not None:
        ids_validos = (
            isinstance(chave_ids, list)
            and bool(chave_ids)
            and all(isinstance(chave_id, int) and not isinstance(chave_id, bool)
                    and chave_id > 0 for chave_id in chave_ids)
            and len(set(chave_ids)) == len(chave_ids)
        )
        if not ids_validos:
            return json_error('A seleção de chaves é inválida.', 400)
        if modo == manifestador_lote.MODO_INDIVIDUAL and len(chave_ids) != 1:
            return json_error(
                'O modo individual exige exatamente uma chave selecionada.', 400)
        # `chave_id` é opção do lote individual; a seleção explícita inteira
        # continua sendo a fonte dos alvos, inclusive no modo carteira.
        chave_id = (chave_ids[0]
                    if modo == manifestador_lote.MODO_INDIVIDUAL else None)
    else:
        chave_id = dados.get('chave_id')
        if modo == manifestador_lote.MODO_INDIVIDUAL and not chave_id:
            return json_error('Escolha a chave que deve ser manifestada.', 400)

    empresa_id = dados.get('empresa_id')
    # A combinação é aceita para chamadas diretas à API: com seleção explícita,
    # `empresa_id` é compatível, mas não pode restringir nem ampliar os alvos.
    if (modo == manifestador_lote.MODO_EMPRESA and chave_ids is None
            and not empresa_id):
        return json_error('Escolha a empresa.', 400)

    if not manifestador_cofre.estado_da_carteira():
        return json_error(
            'O cofre de certificados ainda nao foi inventariado. Rode o '
            'inventario antes de manifestar.', 409, motivo='cofre_vazio')

    competencia = (dados.get('competencia') or '').strip() or None
    opcoes_execucao = {
        'modo': modo,
        'tipo_evento': tipo_evento,
        'empresa_id': empresa_id,
        'competencia': competencia,
        'chave_id': chave_id,
    }
    opcoes_execucao.update(_capturar_ator('iniciador'))

    try:
        dados_lote = batch_engine.init_batch_run(
            MANIF_BATCH_LOCK, MANIF_BATCH_STATE, chave_id,
            lambda _inicio: manifestador_lote.calcular_alvos(
                modo=modo, chave_id=chave_id, empresa_id=empresa_id,
                competencia=competencia, chave_ids=chave_ids),
            manifestador_lote.worker, app_factory=_current_app_object,
            state_values={'opcoes_execucao': opcoes_execucao},
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
    if not batch_engine.solicitar_pausa_se_rodando(
        MANIF_BATCH_LOCK, MANIF_BATCH_STATE
    ):
        return json_error('Não há manifestação em andamento para pausar.', 409)
    return {
        'status': 'ok',
        'message': 'Pausa solicitada; a chave em andamento será concluída '
                   'antes de pausar.',
    }


@bp.route('/manifestador/lote/parar', methods=['POST'])
@requer_papel('operador')
def manifestador_lote_parar():
    if not batch_engine.solicitar_parada_se_ativa(
        MANIF_BATCH_LOCK, MANIF_BATCH_STATE
    ):
        return json_error('Não há manifestação em andamento para interromper.', 409)
    return {
        'status': 'ok',
        'message': 'Interrupção solicitada; a chave em andamento será concluída '
                   'antes de parar.',
    }


@bp.route('/manifestador/lote/retomar', methods=['POST'])
@requer_papel('operador')
def manifestador_lote_retomar():
    """Recomeca pela chave onde parou — o motor nao avanca o indice ao pausar."""
    ator = _capturar_ator('retomada')
    if not batch_engine.resume_batch(MANIF_BATCH_LOCK, MANIF_BATCH_STATE,
                                     manifestador_lote.worker,
                                     app_factory=_current_app_object,
                                     state_values={'opcoes_execucao': ator}):
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


@bp.route('/manifestador/chave/<int:chave_id>/recuperar', methods=['POST'])
@requer_papel('operador')
def manifestador_recuperar_envio(chave_id):
    """Registra o desfecho desconhecido de envio abandonado, sem reenviar."""
    from app.models import StatusManifestacao

    linha = db.session.get(ChaveManifestacao, chave_id)
    if linha is None:
        return json_error('Chave não encontrada.', 404)
    with MANIF_BATCH_LOCK:
        ativo = MANIF_BATCH_STATE.get('worker_active') and \
            MANIF_BATCH_STATE.get('current_id') == chave_id
    if ativo:
        return json_error('Esta chave ainda está sendo processada.', 409)
    if linha.status != StatusManifestacao.ENVIANDO:
        return json_error('Somente chave em envio interrompido pode ser recuperada.', 400)
    linha.status = StatusManifestacao.INDEFINIDA
    linha.xmotivo = 'Envio interrompido; confirme na SEFAZ antes de reprocessar.'
    db.session.commit()
    log_event('manifestador_envio_recuperado', chave_id=chave_id)
    return {'status': 'ok', 'chave': _chave_para_json(linha)}
