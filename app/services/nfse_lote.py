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
from datetime import datetime

from app import db
from app.automation import nfse as automacao
from app.automation.batch_state import (
    NFSE_BATCH_LOCK,
    NFSE_BATCH_STATE,
    nfse_batch_opcoes,
)
from app.models import NotaNfse, StatusNotaNfse
from app.services import batch_engine, nfse_config, nfse_service
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
            ignorar_aliquota=opcoes['ignorar_aliquota'])
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
        return False, None, resultado.get('message')

    return _esperar_e_registrar(nota_id, opcoes, execution_id)


def _emitir_sozinho(nota, execution_id):
    """Confere a tela de revisao e, so entao, emite (NFSE-24).

    A conferencia e a unica coisa entre a automacao e uma nota fiscal errada,
    entao ela falha para o lado seguro: divergencia OU campo ilegivel param o
    lote naquela nota, sem emitir. Parar em vez de pular e proposital — a tela
    de revisao fica na frente do operador, que decide ali mesmo. Seguir para a
    proxima abriria outra DPS e deixaria esta como rascunho orfao no portal.
    """
    driver = SESSAO.driver
    config = nfse_config.get_config_nfse()
    descricao = nfse_config.renderizar_descricao(config, nota.competencia)

    divergencias = automacao.conferir_revisao(
        driver, nota.documento, nota.valor_final, descricao)
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
        return _emitir_sozinho(nota, execution_id)

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
    if nota.status == StatusNotaNfse.DUPLICATA:
        return bool(nota.duplicata_liberada)
    return nota.status in nfse_service.STATUS_EMITIVEIS


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
    return dados
