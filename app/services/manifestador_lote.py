r"""Lote de manifestacao nos tres modos (MANIF-14, MANIF-15, MANIF-18).

Os tres modos que o operador escolhe na tela sao o **mesmo laco**, mudando duas
coisas so:

- **individual**: a fila tem uma chave, escolhida na linha da tabela;
- **empresa**: a fila tem as pendentes de uma empresa, e o certificado e um so
  do inicio ao fim;
- **carteira**: a fila tem as pendentes de todas, **agrupadas por empresa** —
  agrupar nao e estetica, e o que faz cada certificado ser usado num bloco
  contiguo em vez de intercalado.

Duplicar o laco por modo faria uma correcao de desfecho valer para um e nao para
os outros.

Como o `nfse_lote` (ND-010), consome `batch_engine.run_batch_loop` **direto** e
nao pela factory `_register_batch_routes`: aquela e moldada para certidao e
exigiria forjar um `certidao_id`. O `create_driver` fica `None` porque aqui nao
ha navegador nenhum — a canalizacao e HTTP com certificado de cliente.
"""
from app import db
from app.automation.batch_state import (
    MANIF_BATCH_LOCK,
    MANIF_BATCH_STATE,
    manif_batch_opcoes,
)
from app.models import ChaveManifestacao, Empresa, EstadoCertificado
from app.services import batch_engine, circuit_breaker
from app.services.execution_logger import log_event
from app.services.manifestador_service import CONFIRMACAO, manifestar, manifestavel

MODO_INDIVIDUAL = 'individual'
MODO_EMPRESA = 'empresa'
MODO_CARTEIRA = 'carteira'
MODOS = (MODO_INDIVIDUAL, MODO_EMPRESA, MODO_CARTEIRA)

# Alvo EXPLICITO, vindo do fluxo — nunca inferido do payload de log (AD-026).
ALVO_BREAKER = circuit_breaker.ALVO_SEFAZ_AN


# --- montagem da fila -------------------------------------------------------

def calcular_alvos(modo=MODO_EMPRESA, chave_id=None, empresa_id=None,
                   competencia=None):
    """Fila do lote no formato que o `batch_engine` espera.

    A fila tem de ser EXATAMENTE o que a tela mostra: filtrar aqui por um
    criterio e la por outro manifestaria notas que o operador nao esta olhando.

    Os contadores `vencidas`/`a_vencer` existem so porque o payload de status e
    compartilhado com os lotes de certidao; aqui nao ha vencimento a apurar."""
    consulta = ChaveManifestacao.query

    if modo == MODO_INDIVIDUAL:
        linha = db.session.get(ChaveManifestacao, chave_id)
        ids = [linha.id] if manifestavel(linha) else []
    else:
        if modo == MODO_EMPRESA and empresa_id is not None:
            consulta = consulta.filter_by(empresa_id=empresa_id)
        if competencia:
            consulta = consulta.filter_by(competencia=competencia)
        # Ordenar por empresa antes do id e o que agrupa: cada empresa vira um
        # bloco contiguo, e o certificado troca uma vez por bloco em vez de a
        # cada nota.
        linhas = consulta.order_by(ChaveManifestacao.empresa_id,
                                   ChaveManifestacao.id).all()
        ids = [linha.id for linha in linhas if manifestavel(linha)]

    return {
        'ids': ids,
        'total': len(ids),
        'scope': modo,
        'vencidas': 0,
        'a_vencer': 0,
        'pendentes': 0,
    }


def grupos_sem_certificado(ids):
    """{nome da empresa: motivo} para os grupos que serao pulados.

    Nomear e o ponto: "2 empresas puladas" manda o operador caçar quais, e com
    uma parte da carteira sem certificado utilizavel isso e o caso comum, nao a
    excecao."""
    if not ids:
        return {}

    empresas = (
        db.session.query(Empresa)
        .join(ChaveManifestacao, ChaveManifestacao.empresa_id == Empresa.id)
        .filter(ChaveManifestacao.id.in_(ids))
        .distinct()
        .all()
    )

    pulados = {}
    for empresa in empresas:
        certificado = empresa.certificado
        estado = getattr(certificado, 'estado', None) or EstadoCertificado.SEM_ARQUIVO
        if estado != EstadoCertificado.PRONTO:
            pulados[empresa.nome] = estado
    return pulados


# --- emissao de um item -----------------------------------------------------

def _alimentar_breaker(resultado):
    """So falha DO SERVICO conta para o breaker.

    Rejeicao da SEFAZ (`cStat` presente) significa que ela respondeu — ela esta
    no ar, e a nota e que tem problema. Contar isso como portal fora pararia o
    lote inteiro por causa de uma nota invalida.

    A EXCECAO e o consumo indevido (656): ali a SEFAZ respondeu, mas para dizer
    que o nosso acesso esta bloqueado. Isso e exatamente o que o breaker existe
    para representar, e nao tem nada a ver com a nota."""
    if resultado.sucesso:
        circuit_breaker.registrar_sucesso(ALVO_BREAKER)
    elif resultado.consumo_indevido or not resultado.cstat:
        circuit_breaker.registrar_falha(ALVO_BREAKER, mensagem=resultado.mensagem)


def _manifestar_item(chave_id, _driver, execution_id):
    """`emit_fn` do motor. Assinatura ditada pelo `batch_engine`.

    Devolve `grave=None` sempre: rejeicao de uma nota nao pode derrubar o lote —
    com 200 chaves, abortar na primeira invalida jogaria fora as outras 199."""
    opcoes = manif_batch_opcoes()
    resultado = manifestar(
        chave_id,
        tipo_evento=opcoes.get('tipo_evento') or CONFIRMACAO,
        execution_id=execution_id)

    _alimentar_breaker(resultado)

    if resultado.consumo_indevido:
        # PARA o lote, nao passa para a proxima chave. A SEFAZ bloqueou o CNPJ
        # por 1 hora e continuar enviando REINICIA o cronometro — com 200 chaves
        # na fila seriam 200 requisicoes prolongando o bloqueio, e 50 bloqueios
        # consecutivos viram bloqueio PERMANENTE (NT 2018.002). Pausa e
        # retomavel: a fila fica intacta para depois.
        batch_engine.request_pause(MANIF_BATCH_LOCK, MANIF_BATCH_STATE)
        log_event('manifestador_consumo_indevido', level='ERROR',
                  chave_id=chave_id, execution_id=execution_id)
        return False, None, resultado.mensagem

    return resultado.sucesso, None, resultado.mensagem


# --- worker -----------------------------------------------------------------

def worker(app):
    """Rede de seguranca em volta do motor.

    Excecao que escapa daqui mata a thread e deixa o estado `running` para
    sempre: todo inicio seguinte responderia 409 e ninguem conseguiria destravar.
    Melhor terminar em `error`, que a tela sabe mostrar. (Mesma licao que o
    `nfse_lote.worker` documenta.)"""
    try:
        _rodar_lote(app)
    except Exception as exc:
        with MANIF_BATCH_LOCK:
            MANIF_BATCH_STATE['status'] = 'error'
            MANIF_BATCH_STATE['message'] = (
                'A manifestacao parou por um erro inesperado. Confira o log e '
                'comece de novo.')
        log_event('manifestador_worker_morreu', level='ERROR', error=str(exc))


def _rodar_lote(app):
    batch_engine.run_batch_loop(
        app,
        lock=MANIF_BATCH_LOCK,
        state=MANIF_BATCH_STATE,
        emit_fn=_manifestar_item,
        nome_lote='Manifestacao',
        curto='Manifesto',
        tag='MANIF-LOTE',
        event_prefix='manif_batch',
        # sem navegador: a canalizacao e HTTP com certificado de cliente
        create_driver=None,
        alvo_lote=ALVO_BREAKER,
    )


def status():
    """Payload de status com o que e proprio da manifestacao."""
    with MANIF_BATCH_LOCK:
        dados = batch_engine.build_batch_status_payload(MANIF_BATCH_STATE)
        dados['chave_id'] = MANIF_BATCH_STATE.get('current_id')
    opcoes = manif_batch_opcoes()
    dados['modo'] = opcoes['modo']
    dados['tipo_evento'] = opcoes['tipo_evento']
    return dados
