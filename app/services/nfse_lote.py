"""Emissao assistida de NFSe em fila, nos dois modos (NFSE-19/20).

Os dois modos que o operador escolhe na pagina sao o MESMO laco; mudam so duas
coisas:

- **individual**: a fila tem uma nota so, escolhida na linha da tabela, e o
  navegador fecha assim que a emissao e detectada;
- **lote**: a fila tem todas as notas emitiveis, e o navegador fica aberto e
  autenticado do inicio ao fim — e isso que evita repetir certificado e
  aliquota a cada nota.

Em nenhum dos dois a automacao clica em emitir (ND-005): ela preenche, para na
revisao e ESPERA o operador conferir e emitir. O que a espera observa e o
portal, nao um aviso da interface — a nota so vira `emitida` quando a tela de
confirmacao aparece de verdade.

O laco, o estado e os comandos pausar/parar vem do `batch_engine`, como os
lotes de certidao. A diferenca e que aqui o `create_driver` do motor fica None
de proposito: o dono do navegador e a `NfseSession`, e deixar o motor
`quit()`-ar no finally fecharia a sessao compartilhada pelas costas dela.
"""
import time
from datetime import date, datetime
from threading import Lock
from decimal import Decimal, InvalidOperation

from app import db
from app.automation import nfse as automacao
from app.automation import nfse_recon as automacao_recon
from app.automation.capture import salvar_artefato_sanitizado
from app.automation.batch_state import (
    NFSE_BATCH_LOCK,
    NFSE_BATCH_STATE,
    nfse_batch_opcoes,
)
from app.models import NotaNfse, StatusNotaNfse
from app.services import batch_engine, nfse_config, nfse_contrato, nfse_service
from app.services.execution_logger import log_event
from app.services.nfse_session import SESSAO

MODO_INDIVIDUAL = 'individual'
MODO_LOTE = 'lote'
# P3 (NFSE-24): a automacao tambem clica em emitir, depois de reler a tela de
# revisao e conferir tomador, valor e descricao. Qualquer divergencia — inclusive
# "nao consegui ler" — para o lote sem emitir.
MODO_AUTOMATICO = 'automatico'
MODOS = (MODO_INDIVIDUAL, MODO_LOTE, MODO_AUTOMATICO)

# Modos que percorrem a lista inteira; individual e o unico que emite uma so.
MODOS_DE_FILA = (MODO_LOTE, MODO_AUTOMATICO)

ORIGEM_AUTOMACAO = 'automacao'

# Quanto esperar o operador conferir e emitir uma nota. Generoso de proposito:
# quem revisa documento fiscal as vezes sai para confirmar um valor. Estourar
# nao perde nada — pausa o lote na nota atual, que segue esperando confirmacao.
TIMEOUT_CONFIRMACAO = 15 * 60
INTERVALO_CONFIRMACAO = 1.0

# Desfechos da espera pela confirmacao humana.
EMITIDA = 'emitida'
PULADA = 'pulada'
CANCELADA = 'cancelada'
TIMEOUT = 'timeout'
JANELA_FECHADA = 'janela_fechada'


# --- espera pela confirmacao humana ----------------------------------------

def aguardar_confirmacao(driver, timeout=None, agora=None, dormir=None):
    """Fica observando a janela ate a nota ser emitida ou o operador desistir.

    Devolve um dos desfechos do modulo. A ordem das verificacoes nao e
    arbitraria: `EMITIDA` vem primeiro porque a emissao e a unica verdade
    irreversivel aqui. Se o operador emitir e clicar em "pular" quase junto,
    tratar como pulada gravaria no banco que a nota nao saiu — e ela voltaria
    no CSV do mes seguinte para ser emitida de novo, gerando nota duplicada
    para o mesmo tomador e competencia.

    `agora`/`dormir` sao injetaveis so para o teste nao gastar tempo real.
    """
    agora = agora or time.monotonic
    dormir = dormir or time.sleep
    timeout = TIMEOUT_CONFIRMACAO if timeout is None else timeout
    limite = agora() + timeout

    # Sem driver nao ha o que observar. Cair aqui e o mesmo caso de janela
    # fechada: nao da para afirmar se a nota saiu.
    if driver is None:
        return JANELA_FECHADA

    while True:
        if automacao.detectar_emitida(driver):
            return EMITIDA

        # Janela fechada: nao da para saber se emitiu antes de fechar, entao o
        # desfecho e "nao sei" — quem decide e o operador, pela marcacao manual.
        if not SESSAO.driver_vivo():
            return JANELA_FECHADA

        with NFSE_BATCH_LOCK:
            if NFSE_BATCH_STATE.get('pular_atual'):
                NFSE_BATCH_STATE['pular_atual'] = False
                return PULADA
            if NFSE_BATCH_STATE.get('stop_requested'):
                return CANCELADA

        if agora() >= limite:
            return TIMEOUT

        dormir(INTERVALO_CONFIRMACAO)


def preparar_nova_fila():
    """Zera pedidos pendurados antes de uma execucao comecar.

    `pular_atual` nao e chave do `batch_state_defaults()`, entao
    `reset_batch_state` nao a apaga: um "pular" que chegou tarde demais na
    execucao anterior ficaria guardado e pularia a PRIMEIRA nota da proxima
    fila, sem ninguem pedir."""
    with NFSE_BATCH_LOCK:
        NFSE_BATCH_STATE['pular_atual'] = False


def pedir_pular():
    """Manda a espera abandonar a nota atual e seguir para a proxima."""
    with NFSE_BATCH_LOCK:
        if NFSE_BATCH_STATE.get('status') != 'running':
            return False
        NFSE_BATCH_STATE['pular_atual'] = True
        return True


def validar_nota_para_validacao(nota_id):
    """Aceita somente uma nota que a regra única de emissão aprove."""

    nota = db.session.get(NotaNfse, nota_id)
    if nota is None or not nfse_service.emitivel(nota):
        raise ValueError("escolha uma nota emitível para validar o contrato")
    return nota


def validar_contrato_para_modo(
    modo, *, contrato_id=None, validacao_contrato_id=None
):
    """Aplica os gates de contrato antes de iniciar uma execução."""

    if validacao_contrato_id is not None and modo != MODO_INDIVIDUAL:
        raise ValueError("a validação de contrato usa somente o modo individual assistido")
    if modo == MODO_AUTOMATICO:
        if validacao_contrato_id is not None:
            raise ValueError("o modo automático não aceita validação de candidata")
        return nfse_contrato.validar_contrato_automatico(contrato_id)
    return None


# --- desfecho de uma nota --------------------------------------------------

def _marcar_emitida(nota):
    nota.status = StatusNotaNfse.EMITIDA
    nota.origem_emissao = ORIGEM_AUTOMACAO
    nota.emitida_em = datetime.now()
    nota.erro = None
    db.session.commit()


def _marcar_pulada(nota):
    nota.status = StatusNotaNfse.PULADA
    db.session.commit()


def _falhar(nota_id, exc, execution_id):
    """Marca a nota como falha com texto legivel e devolve a mensagem."""
    mensagem = nfse_service.mensagem_da_falha(exc)
    nota = db.session.get(NotaNfse, nota_id)
    if nota is not None:
        nota.status = StatusNotaNfse.FALHA
        nota.erro = mensagem[:300]
        db.session.commit()
    log_event('nfse_lote_erro', level='ERROR', nota_id=nota_id,
              error=str(exc), execution_id=execution_id)
    return mensagem


def _emitir_nota(nota_id, driver_do_motor, execution_id):
    """`emit_fn` do motor: preenche uma nota e espera o operador emitir.

    Assinatura ditada pelo `batch_engine`; devolve (sucesso, grave, mensagem).
    O `driver_do_motor` chega None de proposito (ver docstring do modulo) — o
    navegador vem da sessao.
    """
    opcoes = nfse_batch_opcoes()

    if _ja_esta_na_revisao(nota_id):
        # Retomada: esta nota ja foi preenchida antes da pausa e continua na
        # tela de revisao do portal. Preencher de novo abriria uma SEGUNDA DPS
        # para o mesmo tomador; e `preencher_nota` recusaria o status
        # `aguardando_confirmacao`, o motor contaria pendente e passaria para a
        # proxima — deixando sem vigia justamente a nota que o operador vai
        # emitir. O certo e voltar a esperar.
        log_event('nfse_lote_retomada_na_revisao', nota_id=nota_id,
                  execution_id=execution_id)
        return _esperar_e_registrar(nota_id, opcoes, execution_id)

    try:
        resultado = nfse_service.preencher_nota(
            nota_id, execution_id=execution_id,
            ignorar_aliquota=opcoes['ignorar_aliquota'],
            contrato_id=opcoes.get('contrato_id'),
            validacao_contrato_id=opcoes.get('validacao_contrato_id'),
            modo=opcoes['modo'],
        )
    except nfse_service.NotaNaoEmitivelError as exc:
        # linha que nao devia estar na fila (ja emitida, sem empresa...): nao e
        # falha tecnica, e motivo para pular sem sujar a contagem de erros
        batch_engine.marcar_resultado_pendente(NFSE_BATCH_STATE, NFSE_BATCH_LOCK)
        return False, None, str(exc)
    except Exception as exc:
        # `preencher_nota` trata os erros de DENTRO do preenchimento, mas abre a
        # sessao antes disso: uma falha de login (certificado ausente, portal
        # fora) sai por aqui. Sem este except ela atravessaria o `run_batch_loop`
        # e mataria a thread do worker — o lote ficaria eternamente `running`
        # (toda nova emissao virando 409) e a nota travada em `preenchendo`, que
        # nao tem acao nenhuma na interface.
        return False, batch_engine.GRAVE_FATAL, _falhar(nota_id, exc, execution_id)

    if resultado.get('status') == 'error':
        # `preencher_nota` ja gravou o status FALHA e a mensagem curta na nota
        if resultado.get('pausar_lote'):
            batch_engine.request_pause(NFSE_BATCH_LOCK, NFSE_BATCH_STATE)
            batch_engine.marcar_resultado_pendente(NFSE_BATCH_STATE, NFSE_BATCH_LOCK)
        return False, None, resultado.get('message')

    if opcoes.get('validacao_contrato_id'):
        # Fora do `try` que existe para impedir uma excecao de matar a thread
        # do worker, qualquer falha aqui (contrato sumido, campo desconhecido,
        # WebDriver caido, persistencia) atravessa o `run_batch_loop`: o lote
        # fica `running` para sempre — toda emissao seguinte responde 409 — e a
        # nota trava em `preenchendo`, que nao tem acao nenhuma na interface.
        try:
            _registrar_validacao_candidata(
                nota_id,
                opcoes['validacao_contrato_id'],
                execution_id,
            )
        except Exception as exc:
            log_event(
                'nfse_validacao_contrato_falhou',
                level='ERROR',
                contrato_id=opcoes['validacao_contrato_id'],
                nota_id=nota_id,
                error_type=type(exc).__name__,
                error=str(exc),
                execution_id=execution_id,
            )

    return _esperar_e_registrar(nota_id, opcoes, execution_id)


def _registrar_validacao_candidata(nota_id, contrato_id, execution_id):
    """Relê a revisão, registra a candidata e nunca dispara emissão automática."""

    nota = db.session.get(NotaNfse, nota_id)
    contrato = nfse_contrato.carregar_execucao(contrato_id)
    config = nfse_config.get_config_nfse()
    documento, valor, descricao = _valores_basicos_revisao(
        contrato, nota, config
    )
    regras = _regras_autorrevisao_contrato(contrato, nota, config)
    resultado = automacao.conferir_revisao(
        SESSAO.driver,
        documento,
        valor,
        descricao,
        regras_adicionais=regras,
    )
    nfse_contrato.registrar_validacao(
        contrato_id, nota_id, resultado,
        # O que a divergencia cita da tela e dado de cliente, e `erro_validacao`
        # vive no CONTRATO, que sobrevive a nota e aparece na Central.
        valores_sensiveis=(documento, str(valor), descricao),
    )
    _publicar_validacao(contrato_id, nota_id, resultado)
    if resultado:
        _capturar_revisao_da_validacao(contrato_id, nota_id, execution_id)
    log_event(
        'nfse_validacao_contrato_registrada',
        contrato_id=contrato_id,
        nota_id=nota_id,
        elegivel_automatico=bool(
            getattr(resultado, 'elegivel_automatico', False)
        ),
        divergencias=len(resultado),
        execution_id=execution_id,
    )
    return resultado


# Desfecho da ultima validacao, para o status poder contar enquanto o navegador
# ainda esta aberto. Fica FORA de NFSE_BATCH_STATE pelo mesmo motivo das opcoes:
# `init_batch_run` chama `reset_batch_state` e apagaria isto.
_VALIDACAO_LOCK = Lock()
_ULTIMA_VALIDACAO = {}


def _publicar_validacao(contrato_id, nota_id, resultado):
    """Publica o desfecho ASSIM QUE ele existe, não no fim do lote.

    A validação acontece logo depois do preenchimento e antes da espera pelo
    operador — ela nunca dependeu de emitir. Só que o resultado ficava invisível
    até alguém recarregar a Central, e o operador, na dúvida, emitia uma nota de
    verdade para "fechar" a validação. Documento fiscal não é ferramenta de
    diagnóstico.
    """
    with _VALIDACAO_LOCK:
        _ULTIMA_VALIDACAO.clear()
        _ULTIMA_VALIDACAO.update({
            'contrato_id': contrato_id,
            'nota_id': nota_id,
            'divergencias': list(resultado or ()),
            'aprovada': not resultado,
        })


def validacao_em_curso():
    with _VALIDACAO_LOCK:
        return dict(_ULTIMA_VALIDACAO) if _ULTIMA_VALIDACAO else None


def limpar_validacao_publicada():
    with _VALIDACAO_LOCK:
        _ULTIMA_VALIDACAO.clear()


def _capturar_revisao_da_validacao(contrato_id, nota_id, execution_id):
    """Guarda a revisão sanitizada quando a autorrevisão reprova.

    "Não consegui ler o CPF/CNPJ do tomador na revisão" nomeia o sintoma e não
    deixa nada para investigar: a nota é emitida, a tela some, e no dia seguinte
    não há como saber o que mudou no portal. O inventário estrutural é o mesmo
    das fronteiras de etapa — só rótulo, tipo e visibilidade, sem valor de
    cliente.

    Nunca derruba a validação: o resultado já está registrado, e uma falha ao
    guardar evidência não pode virar falha do fluxo que ela documenta.
    """
    try:
        inventario = automacao_recon.inventariar(SESSAO.driver, 'revisao')
        salvar_artefato_sanitizado(
            f'nfse_revisao_validacao_{contrato_id}',
            automacao_recon.inventario_para_html(inventario),
            execution_id=execution_id,
        )
    except Exception as exc:
        log_event(
            'nfse_captura_revisao_falhou',
            level='WARNING',
            contrato_id=contrato_id,
            nota_id=nota_id,
            error_type=type(exc).__name__,
            execution_id=execution_id,
        )


def _regras_autorrevisao_contrato(contrato, nota, config):
    """Materializa leitores e esperados sem persistir valores da nota."""

    regras = []
    for campo in contrato.campos:
        if campo.etapa == 'revisao':
            # Documento, valor e descrição já são conferidos pelos leitores
            # históricos logo antes destas regras adicionais.
            continue
        if not (
            campo.revisao_secao
            or campo.revisao_rotulo
            or not campo.conferivel_automatico
            or (campo.origem == 'padrao_portal' and campo.obrigatorio)
        ):
            continue
        valor_esperado = nfse_contrato.resolver_valor(
            campo, nota, config, date.today()
        )
        if isinstance(valor_esperado, (tuple, list)) and valor_esperado:
            valor_esperado = valor_esperado[0]
        regras.append({**campo.__dict__, 'valor_esperado': valor_esperado})
    return tuple(regras)


def _valores_basicos_revisao(contrato, nota, config):
    def resolver(chave):
        campo = contrato.campo(chave)
        valor = nfse_contrato.resolver_valor(
            campo, nota, config, date.today()
        )
        if isinstance(valor, (tuple, list)) and valor:
            return valor[0]
        return valor

    documento = resolver('Tomador_Inscricao')
    valor_cru = resolver('Valores_ValorServico')
    descricao = resolver('ServicoPrestado_Descricao')
    if not isinstance(valor_cru, Decimal):
        texto = str(valor_cru or '').replace('R$', '').strip()
        if ',' in texto:
            texto = texto.replace('.', '').replace(',', '.')
        try:
            valor_cru = Decimal(texto)
        except (InvalidOperation, ValueError):
            # Um valor contratado ilegível jamais pode fazer a revisão voltar
            # silenciosamente ao valor original da nota. NaN força divergência
            # determinística sem autorizar emissão nem derrubar o worker.
            valor_cru = Decimal('NaN')
    return documento, valor_cru, descricao


def _emitir_sozinho(nota, execution_id, contrato_id=None):
    """Confere a tela de revisao e, so entao, emite (NFSE-24).

    A conferencia e a unica coisa entre a automacao e uma nota fiscal errada,
    entao ela falha para o lado seguro: divergencia OU campo ilegivel param o
    lote naquela nota, sem emitir. Parar em vez de pular e proposital — a tela
    de revisao fica na frente do operador, que decide ali mesmo. Seguir para a
    proxima abriria outra DPS e deixaria esta como rascunho orfao no portal.
    """
    driver = SESSAO.driver
    config = nfse_config.get_config_nfse()
    contrato = nfse_contrato.carregar_execucao(contrato_id)
    documento, valor, descricao = _valores_basicos_revisao(
        contrato, nota, config
    )
    regras = _regras_autorrevisao_contrato(contrato, nota, config)

    divergencias = automacao.conferir_revisao(
        driver,
        documento,
        valor,
        descricao,
        regras_adicionais=regras,
    )
    if getattr(divergencias, 'elegivel_automatico', None) is False:
        divergencias.append(
            'O contrato ativo permite somente emissão assistida.'
        )
    if divergencias:
        motivo = ' '.join(divergencias)
        log_event('nfse_autorrevisao_recusou', level='WARNING', nota_id=nota.id,
                  divergencias=motivo, execution_id=execution_id)
        nota.erro = motivo[:300]
        db.session.commit()
        batch_engine.request_pause(NFSE_BATCH_LOCK, NFSE_BATCH_STATE)
        batch_engine.marcar_resultado_pendente(NFSE_BATCH_STATE, NFSE_BATCH_LOCK)
        return False, None, (
            f'Nota {nota.id} NAO emitida — a revisao no portal nao bate com a '
            f'nota. {motivo} Lote pausado nesta nota.')

    log_event('nfse_autorrevisao_ok', nota_id=nota.id, execution_id=execution_id)

    if not automacao.emitir(driver):
        # O clique saiu; a confirmacao nao apareceu a tempo. Pode ter emitido ou
        # nao — os dois chutes erram feio, entao quem decide e o operador.
        nota.erro = ('Cliquei em emitir e a confirmacao nao apareceu a tempo. '
                     'Confira no portal se a nota saiu.')
        db.session.commit()
        batch_engine.request_pause(NFSE_BATCH_LOCK, NFSE_BATCH_STATE)
        batch_engine.marcar_resultado_pendente(NFSE_BATCH_STATE, NFSE_BATCH_LOCK)
        return False, None, f'Nota {nota.id}: {nota.erro} Lote pausado.'

    _marcar_emitida(nota)
    return True, None, f'Nota {nota.id} conferida e emitida.'


def _ja_esta_na_revisao(nota_id):
    nota = db.session.get(NotaNfse, nota_id)
    return nota is not None and nota.status == StatusNotaNfse.AGUARDANDO_CONFIRMACAO


def _esperar_e_registrar(nota_id, opcoes, execution_id):
    """Espera o operador emitir e grava o desfecho na nota."""
    nota = db.session.get(NotaNfse, nota_id)

    if opcoes['modo'] == MODO_AUTOMATICO:
        return _emitir_sozinho(
            nota, execution_id, contrato_id=opcoes.get('contrato_id')
        )

    desfecho = aguardar_confirmacao(SESSAO.driver)
    log_event('nfse_lote_desfecho', nota_id=nota_id, desfecho=desfecho,
              modo=opcoes['modo'], execution_id=execution_id)

    if desfecho == EMITIDA:
        _marcar_emitida(nota)
        if opcoes['modo'] == MODO_INDIVIDUAL:
            # o combinado do modo individual: emitiu, fecha o navegador. No modo
            # lote a janela continua aberta e autenticada para a proxima nota.
            SESSAO.encerrar()
        return True, None, f'Nota {nota_id} emitida.'

    if desfecho == PULADA:
        _marcar_pulada(nota)
        batch_engine.marcar_resultado_pendente(NFSE_BATCH_STATE, NFSE_BATCH_LOCK)
        return False, None, f'Nota {nota_id} pulada pelo operador.'

    if desfecho == JANELA_FECHADA:
        # A nota fica AGUARDANDO_CONFIRMACAO: pode ter sido emitida antes de
        # fechar, e marcar qualquer um dos dois lados por conta propria erra
        # feio — ou perde uma nota emitida, ou emite de novo mes que vem.
        batch_engine.marcar_resultado_pendente(NFSE_BATCH_STATE, NFSE_BATCH_LOCK)
        validacao = validacao_em_curso()
        if opcoes.get('validacao_contrato_id') and validacao is not None:
            # Numa VALIDACAO fechar sem emitir e o desfecho previsto, nao um
            # acidente: a prova que interessa e a tela de revisao, e ela ja foi
            # conferida antes desta espera. Marcar GRAVE_FATAL aqui pausava o
            # lote e mandava o operador "marcar como emitida" uma nota que ele
            # deliberadamente nao emitiu — foi o que o levou a emitir de verdade
            # so para encerrar a validacao.
            veredito = (
                'sem divergencias' if validacao['aprovada']
                else f"{len(validacao['divergencias'])} divergencia(s)"
            )
            return False, None, (
                f'Validacao concluida ({veredito}). A nota {nota_id} ficou '
                'preenchida e nao emitida — use "Nao emiti" na lista para '
                'devolve-la a fila.')
        return False, batch_engine.GRAVE_FATAL, (
            f'O navegador foi fechado com a nota {nota_id} na tela de revisao. '
            'Se voce chegou a emitir, marque a linha como emitida na lista.')

    if desfecho == TIMEOUT:
        # Pausa em vez de pular: a nota preenchida continua no portal esperando,
        # e retomar recomeca por ela (o motor nao avanca o indice ao pausar).
        batch_engine.request_pause(NFSE_BATCH_LOCK, NFSE_BATCH_STATE)
        batch_engine.marcar_resultado_pendente(NFSE_BATCH_STATE, NFSE_BATCH_LOCK)
        return False, None, (
            f'Sem confirmacao da nota {nota_id} no tempo previsto. Lote pausado '
            'nesta nota — retome quando quiser continuar.')

    # CANCELADA: pausar/parar chegou durante a espera; o motor decide o status
    batch_engine.marcar_resultado_pendente(NFSE_BATCH_STATE, NFSE_BATCH_LOCK)
    return False, None, f'Espera da nota {nota_id} interrompida.'


# --- montagem da fila ------------------------------------------------------

def _emitivel(nota):
    # Regra unica, no `nfse_service`: a fila do lote e a emissao individual
    # precisam concordar sobre o que entra, senao o lote enfileira nota que o
    # preenchimento recusa e o operador ve a fila travar sem explicacao.
    return nfse_service.emitivel(nota)


def calcular_alvos(nota_id=None, lote_id=None, competencia=None):
    """Fila do lote, no formato que o `batch_engine` espera.

    `nota_id` monta a fila de uma nota so (modo individual). Sem ele, a fila
    tem de ser EXATAMENTE o que a pagina mostra: por competencia quando o
    operador filtrou um mes, pelo lote importado quando nao filtrou. Enfileirar
    o ultimo lote enquanto a tela mostra outro mes emitiria notas que o operador
    nao esta olhando.

    Os contadores `vencidas`/`a_vencer` existem so porque o payload de status e
    compartilhado com os lotes de certidao; aqui nao ha vencimento a apurar.
    """
    if nota_id is not None:
        nota = db.session.get(NotaNfse, nota_id)
        ids = [nota.id] if nota is not None and _emitivel(nota) else []
    else:
        consulta = NotaNfse.query
        consulta = (consulta.filter_by(competencia=competencia) if competencia
                    else consulta.filter_by(lote_id=lote_id))
        ids = [n.id for n in consulta.order_by(NotaNfse.id).all() if _emitivel(n)]

    return {
        'ids': ids,
        'total': len(ids),
        'scope': 'default',
        'vencidas': 0,
        'a_vencer': 0,
        'pendentes': 0,
    }


# --- worker ----------------------------------------------------------------

# Desfechos em que o trabalho acabou: o navegador nao tem mais razao de ficar
# aberto (e segurando a chave do certificado no registro). `paused` fica de
# fora — retomar depende da MESMA janela, ainda na tela de revisao.
STATUS_QUE_FECHAM_O_NAVEGADOR = ('stopped', 'completed', 'error')


def _encerrar_sessao(_ctx):
    """`on_teardown` do motor: fecha o navegador (quando cabe) e solta o lock.

    Fechar ANTES de liberar, e nao em `on_finish`, importa: o motor roda o
    teardown primeiro, entao soltar o lock ali deixaria uma nova emissao entrar
    e chamar `garantir()` enquanto o `quit()` da anterior ainda esta em curso.

    O lock e adquirido na thread da requisicao e liberado aqui, na do worker:
    `threading.Lock` nao tem dono, entao isso e valido — e e o unico jeito de a
    checagem "ja tem lote rodando" ser atomica com o inicio dele.
    """
    if NFSE_BATCH_STATE.get('status') in STATUS_QUE_FECHAM_O_NAVEGADOR:
        SESSAO.encerrar()
    SESSAO.liberar()


def worker(app):
    """Rede de seguranca em volta do motor.

    Qualquer excecao que escape daqui mata a thread e deixa o estado em
    `running` para sempre: nao ha quem o conserte, e todo inicio seguinte
    responde 409. Melhor terminar em `error`, que a interface sabe mostrar."""
    try:
        _rodar_lote(app)
    except Exception as exc:
        with NFSE_BATCH_LOCK:
            NFSE_BATCH_STATE['status'] = 'error'
            NFSE_BATCH_STATE['message'] = (
                'A emissao parou por um erro inesperado. Confira o log e comece '
                'de novo.')
        # `liberar` e idempotente: o teardown do motor pode ja ter rodado, mas
        # se a excecao veio antes dele o lock ficaria preso para sempre.
        SESSAO.liberar()
        log_event('nfse_lote_worker_morreu', level='ERROR', error=str(exc))


def _rodar_lote(app):
    batch_engine.run_batch_loop(
        app,
        lock=NFSE_BATCH_LOCK,
        state=NFSE_BATCH_STATE,
        emit_fn=_emitir_nota,
        nome_lote='NFSe',
        curto='NFSe',
        tag='NFSE-LOTE',
        event_prefix='nfse_batch',
        # sem create_driver: o navegador e da NfseSession, e o motor fecharia
        # a sessao compartilhada no finally
        create_driver=None,
        on_teardown=_encerrar_sessao,
    )


def status():
    """Payload de status com o que e proprio da NFSe."""
    with NFSE_BATCH_LOCK:
        dados = batch_engine.build_batch_status_payload(NFSE_BATCH_STATE)
        dados['nota_id'] = NFSE_BATCH_STATE.get('current_id')
    dados['modo'] = nfse_batch_opcoes()['modo']
    # `tem_driver`, nao `driver_vivo()`: este payload e consultado de 2 em 2
    # segundos e nao pode custar uma ida ao chromedriver (ver `tem_driver`).
    dados['sessao_ativa'] = SESSAO.tem_driver
    dados['validacao'] = validacao_em_curso()
    return dados
