"""Orquestracao da emissao de NFSe (NFSE-13/14/16/18).

Espelha o papel do `emissao_service` das certidoes: casa a sessao de navegador,
os steps do assistente e a persistencia do status. As rotas so delegam aqui.

Regra que atravessa o modulo (ND-005): **nos estagios P1 e P2 a automacao nao
emite**. Ela preenche ate a tela de revisao e para; quem clica em emitir e o
operador. O artefato e um documento fiscal — errar nao e rollback, e
cancelamento de nota.
"""
from datetime import date

from app import db
from app.automation import nfse as automacao
from app.automation.capture import capturar_contexto_falha
from app.models import NotaNfse, StatusNotaNfse
from app.services import nfse_config
from app.services.execution_logger import log_event
from app.services.nfse_session import SESSAO

# Status a partir dos quais faz sentido preencher uma nota.
STATUS_EMITIVEIS = (
    StatusNotaNfse.PRONTA,
    StatusNotaNfse.CADASTRO_PENDENTE,   # CNPJ digitado na mao pelo operador
    StatusNotaNfse.FALHA,               # nova tentativa apos erro
    StatusNotaNfse.PULADA,
)

MOTIVO_POR_STATUS = {
    StatusNotaNfse.EMPRESA_PENDENTE:
        'Esta linha ainda nao tem empresa vinculada. Escolha a empresa ou '
        'informe o CNPJ antes de emitir.',
    StatusNotaNfse.INVALIDA:
        'Esta linha veio incompleta do extrato do banco e nao pode ser emitida.',
    StatusNotaNfse.DUPLICATA:
        'Ja existe nota emitida para esta empresa nesta competencia. Libere a '
        'duplicata se quiser emitir mesmo assim.',
    StatusNotaNfse.EMITIDA:
        'Esta nota ja foi emitida.',
    StatusNotaNfse.AGUARDANDO_CONFIRMACAO:
        'Esta nota ja esta preenchida no portal, aguardando sua confirmacao.',
}


class NotaNaoEmitivelError(RuntimeError):
    """A nota nao esta em estado de ser preenchida. Mensagem pronta para a UI."""


def _pode_emitir(nota):
    if nota.status == StatusNotaNfse.DUPLICATA and nota.duplicata_liberada:
        return
    if nota.status in STATUS_EMITIVEIS:
        return
    raise NotaNaoEmitivelError(
        MOTIVO_POR_STATUS.get(nota.status, 'Esta linha nao pode ser emitida.'))


def preparar_sessao():
    """Garante o login e devolve a aliquota lida, para o operador conferir.

    Nao libera emissao nenhuma: a liberacao e a confirmacao explicita do
    operador (NFSE-12). Aliquota ilegivel devolve None, e quem chama pede
    confirmacao manual em vez de seguir calado.
    """
    SESSAO.garantir()
    aliquota = SESSAO.ler_aliquota()
    log_event('nfse_sessao_preparada', aliquota=aliquota)
    return {
        'aliquota': aliquota,
        'aliquota_confirmada': SESSAO.aliquota_confirmada,
    }


def preencher_nota(nota_id, hoje=None, execution_id=None):
    """Preenche uma nota no portal ate a tela de revisao e PARA (NFSE-14).

    Nunca clica no botao de emitir. Ao final a nota fica
    `aguardando_confirmacao` e o operador confere e emite no navegador.
    """
    nota = db.session.get(NotaNfse, nota_id)
    if nota is None:
        raise NotaNaoEmitivelError('Nota nao encontrada.')

    _pode_emitir(nota)

    if not SESSAO.aliquota_confirmada:
        raise NotaNaoEmitivelError(
            'Confira a aliquota do Simples Nacional antes de emitir: ela muda '
            'mes a mes e sai na nota.')

    config = nfse_config.get_config_nfse()
    descricao = nfse_config.renderizar_descricao(config, nota.competencia)
    hoje = hoje or date.today()

    nota.status = StatusNotaNfse.PREENCHENDO
    nota.erro = None
    db.session.commit()

    log_event('nfse_preenchimento_inicio', nota_id=nota.id,
              competencia=nota.competencia, execution_id=execution_id)

    driver = SESSAO.garantir()
    try:
        automacao.abrir_nova_dps(driver)
        automacao.preencher_etapa_pessoas(driver, nota, config, hoje)
        automacao.preencher_etapa_servico(driver, nota, config, descricao)
        automacao.preencher_etapa_tributacao(driver, nota, config)

        if not automacao.esperar_revisao(driver):
            raise automacao.InteracaoPortalError(
                'O portal nao chegou a tela de revisao apos as tres etapas.')
    except Exception as exc:
        return _registrar_falha(nota, driver, exc, execution_id)

    nota.status = StatusNotaNfse.AGUARDANDO_CONFIRMACAO
    db.session.commit()
    log_event('nfse_preenchimento_ok', nota_id=nota.id, execution_id=execution_id)

    return {
        'status': 'aguardando_confirmacao',
        'nota_id': nota.id,
        'competencia': nota.competencia,
        'documento': nota.documento,
        'valor': automacao.formatar_valor(nota.valor_final),
        'descricao': descricao,
        'message': 'Nota preenchida no portal. Confira os dados e emita no navegador.',
    }


# Erros que a propria automacao levanta ja tem texto escrito para o operador.
_ERROS_COM_MENSAGEM_PRONTA = (
    automacao.InteracaoPortalError,
    automacao.LoginNfseError,
)

# Traducao das falhas do Selenium que este fluxo realmente produz. Nao usa o
# `errors.mensagem_usuario` compartilhado: a taxonomia dele foi calibrada para
# os fluxos de certidao e classifica "element not interactable" como "portal
# indisponivel" — amigavel, porem falso, e mandaria o operador conferir a coisa
# errada. Aqui e melhor ser curto e correto.
_FALHAS_SELENIUM = {
    'ElementNotInteractableException':
        'O campo existe mas nao aceitou interacao — a tela pode nao ter '
        'terminado de carregar ou ter um overlay aberto.',
    'ElementClickInterceptedException':
        'Algo ficou por cima do elemento (aviso ou calendario aberto) e '
        'bloqueou o clique.',
    'NoSuchElementException':
        'Um campo esperado nao existe nesta tela. O portal pode ter mudado o '
        'formulario.',
    'StaleElementReferenceException':
        'A tela mudou no meio do preenchimento.',
    'TimeoutException':
        'O portal demorou demais para responder.',
    'InvalidSessionIdException':
        'A sessao do navegador foi encerrada.',
    'NoSuchWindowException':
        'A janela do navegador foi fechada durante o preenchimento.',
}


def mensagem_da_falha(exc):
    """Frase curta e correta para mostrar na linha da nota.

    Excecao do Selenium traz stacktrace e endereco de memoria — util no log,
    ilegivel na tabela. O texto cru continua inteiro no log e na captura,
    alcancavel pelo request_id.
    """
    if isinstance(exc, _ERROS_COM_MENSAGEM_PRONTA):
        return str(getattr(exc, 'mensagem', exc)).strip()

    conhecida = _FALHAS_SELENIUM.get(type(exc).__name__)
    if conhecida:
        return conhecida

    primeira = (str(exc).strip().splitlines() or [''])[0].strip()
    return primeira[:200] or 'Falha na automacao.'


def _registrar_falha(nota, driver, exc, execution_id):
    """Marca SO esta nota como falha; as demais do lote ficam intactas."""
    try:
        capturar_contexto_falha(driver, contexto=f'nfse_nota_{nota.id}',
                                execution_id=execution_id)
    except Exception:
        pass

    mensagem = mensagem_da_falha(exc)
    nota.status = StatusNotaNfse.FALHA
    nota.erro = mensagem[:300]
    db.session.commit()

    # o texto cru (com stacktrace) fica so no log, alcancavel pelo request_id
    log_event('nfse_preenchimento_erro', level='ERROR', nota_id=nota.id,
              error=str(exc), execution_id=execution_id)
    return {
        'status': 'error',
        'nota_id': nota.id,
        'message': mensagem,
    }
