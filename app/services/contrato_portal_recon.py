"""Recon passivo agendado dos contratos dos portais (RAC-07).

O recon é ANTECIPATÓRIO: observa a estrutura na madrugada para o drift aparecer
antes da primeira emissão do dia. Ele **não substitui o preflight** de cada
execução, porque o portal pode mudar depois da janela agendada.

Três limites de propósito:

- **Passivo.** Só entra na tela observável do alvo: não preenche, não resolve
  captcha e não submete (AC-08.5). Por isso roda mesmo com a renovação
  automática desligada — não emite documento nem gasta crédito (AC-07.3).
- **Não alimenta o circuit breaker.** Drift observado às 2h abriria o breaker
  sobre uma observação que ninguém está usando, pausando o lote da manhã antes
  de qualquer falha real. O preflight da execução abre o breaker quando a
  emissão de fato esbarrar na mudança.
- **Falha de infraestrutura é `desconhecido`**, nunca drift e nunca
  autoativação: portal fora do ar, timeout e driver que não abriu não dizem nada
  sobre a estrutura.

Um alvo que falhe não derruba os demais nem o scheduler (AC-07.5).
"""
from app.services import contrato_portal, contrato_portal_preflight
from app.services.contrato_portal_registry import (
    AdaptadorRecon,
    RegistroAdaptadores,
)
from app.services.contrato_portal_protocol import AdaptadorPortal
from app.services.contrato_portal_drift import COMPATIVEL as DRIFT_COMPATIVEL
from app.services.contrato_portal_drift import REVISAO as DRIFT_REVISAO
from app.services.contrato_portal_drift import (
    BaselineNaoObservavelError,
    comparar,
    montar_baseline_observada,
)
from app.services.execution_logger import log_event

# Resultados de uma passada de recon e, para os três primeiros, também o estado
# do contrato que o painel exibe.
COMPATIVEL = 'compativel'
AUTOAJUSTADO = 'autoajustado'
BLOQUEADO = 'bloqueado'
DESCONHECIDO = 'desconhecido'
ADIADO = 'adiado'
SEM_CONTRATO = 'sem_contrato'


def _registro_padrao() -> RegistroAdaptadores:
    """Registry do piloto: só o Trabalhista declara recon passivo seguro.

    Os municípios continuam no dry-run diário que já existe; duas navegações
    diárias equivalentes contra o mesmo portal não se justificam.
    """
    from app.automation import trabalhista
    from app.automation.batch_state import TRABALHISTA_BATCH_LOCK
    from app.services import circuit_breaker

    return RegistroAdaptadores((AdaptadorRecon(
            fluxo=trabalhista.FLUXO_CONTRATO,
            alvo=trabalhista.ALVO_CONTRATO,
            nome='Trabalhista (CNDT/TST)',
            chave_health=circuit_breaker.ALVO_TRABALHISTA,
            observar=trabalhista.observar_passivo,
            lock=TRABALHISTA_BATCH_LOCK,
            recon_passivo_seguro=True,
            definicao=trabalhista.definicao_baseline,
        ),))


def adaptadores_padrao() -> list[AdaptadorPortal]:
    return list(_registro_padrao().todos())


class AlvoOcupadoError(RuntimeError):
    """Uma emissão está em curso no mesmo alvo; observar agora atrapalharia."""


class NadaParaRevisarError(RuntimeError):
    """A observação de agora não sustenta a revisão pedida."""


def adaptador_por_alvo(fluxo, alvo):
    return _registro_padrao().por_alvo(fluxo, alvo)


def _fechar(driver):
    if driver is None:
        return
    try:
        driver.quit()
    except Exception:
        pass


def _observar_alvo(adaptador, ativo, criar_driver, execution_id):
    """Abre o driver, observa uma vez e devolve o resultado da comparação."""
    driver = None
    try:
        driver = criar_driver()
        snapshot = contrato_portal_preflight.executar(
            fluxo=adaptador.fluxo,
            alvo=adaptador.alvo,
            observar=lambda contrato: adaptador.observar(driver, contrato),
            alvo_breaker=None,
            execution_id=execution_id,
            contrato_ativo=ativo,
        )
    except contrato_portal_preflight.ContratoPortalBloqueadoError:
        return BLOQUEADO
    except Exception as exc:
        log_event(
            'contrato_portal_recon_falhou', level='ERROR',
            fluxo=adaptador.fluxo, alvo=adaptador.alvo,
            error=str(exc), execution_id=execution_id)
        return DESCONHECIDO
    finally:
        _fechar(driver)
    return AUTOAJUSTADO if snapshot.versao != ativo.versao else COMPATIVEL


def _recon_de_um(adaptador, criar_driver, execution_id):
    lock = adaptador.lock
    if lock is not None and not lock.acquire(blocking=False):
        # Emissão em andamento no mesmo alvo: adiar é o comportamento correto,
        # não erro. A observação de amanhã cobre o mesmo terreno.
        log_event(
            'contrato_portal_recon_adiado', fluxo=adaptador.fluxo,
            alvo=adaptador.alvo, execution_id=execution_id)
        return ADIADO
    try:
        ativo = contrato_portal_preflight.buscar_ativo(
            adaptador.fluxo, adaptador.alvo, obrigatorio=False)
        if ativo is None:
            log_event(
                'contrato_portal_recon_sem_contrato', fluxo=adaptador.fluxo,
                alvo=adaptador.alvo, execution_id=execution_id)
            return SEM_CONTRATO
        return _observar_alvo(adaptador, ativo, criar_driver, execution_id)
    finally:
        if lock is not None:
            lock.release()


def executar(adaptadores, criar_driver, *, execution_id=None) -> dict:
    """Roda o recon de cada adaptador seguro; devolve {alvo: resultado}."""
    resultados = {}
    for adaptador in adaptadores:
        if not adaptador.recon_passivo_seguro:
            continue
        try:
            resultados[adaptador.alvo] = _recon_de_um(
                adaptador, criar_driver, execution_id)
        except Exception as exc:
            # Blindagem final: um alvo não pode derrubar os demais (AC-07.5).
            log_event(
                'contrato_portal_recon_falhou', level='ERROR',
                fluxo=adaptador.fluxo, alvo=adaptador.alvo,
                error=str(exc), execution_id=execution_id)
            resultados[adaptador.alvo] = DESCONHECIDO
    return resultados


def _estado_do_alvo(adaptador):
    """Estado do contrato ativo do alvo, lido do banco (sem rede)."""
    from app.models import ContratoPortal, IncidenteContratoPortal

    ativo = (ContratoPortal.query
             .filter_by(fluxo=adaptador.fluxo, alvo=adaptador.alvo,
                        estado='ativa')
             .one_or_none())
    if ativo is None:
        return {'estado': DESCONHECIDO, 'versao': None,
                'mensagem': 'sem contrato ativo'}

    aberto = (IncidenteContratoPortal.query
              .filter_by(contrato_base_id=ativo.id, estado='aberto')
              .first())
    if aberto is not None:
        return {'estado': BLOQUEADO, 'versao': ativo.versao,
                'mensagem': 'mudança estrutural aguardando revisão'}
    # Versão ativa nascida do sistema é a que a autoativação promoveu; a de
    # origem humana é a baseline ou a candidata aceita na revisão.
    if ativo.origem == 'sistema':
        return {'estado': AUTOAJUSTADO, 'versao': ativo.versao,
                'mensagem': 'seletor autoajustado na última observação'}
    return {'estado': COMPATIVEL, 'versao': ativo.versao, 'mensagem': None}


def estado_por_alvo() -> dict:
    """{chave_health: estado do contrato} para o painel compor com o ping.

    Nunca levanta: um drift de schema não pode derrubar a tela que existe para
    diagnosticar problemas.
    """
    estados = {}
    try:
        adaptadores = adaptadores_padrao()
    except Exception:
        return estados
    for adaptador in adaptadores:
        try:
            estados[adaptador.chave_health] = _estado_do_alvo(adaptador)
        except Exception:
            estados[adaptador.chave_health] = {
                'estado': DESCONHECIDO, 'versao': None, 'mensagem': None}
    return estados


# --- ações sob demanda da central de Diagnóstico ---------------------------
#
# Tudo aqui abre navegador contra o portal real, sempre passivo: nenhuma dessas
# funções preenche documento, resolve captcha ou submete. E todas passam pelo
# lock do lote do alvo — observar durante uma emissão atrapalharia a emissão.

def _observar_com_lock(adaptador, contrato, criar_driver):
    lock = adaptador.lock
    if lock is not None and not lock.acquire(blocking=False):
        raise AlvoOcupadoError(
            'Há uma emissão em curso neste portal. Tente novamente depois.')
    driver = None
    try:
        driver = criar_driver()
        return adaptador.observar(driver, contrato)
    finally:
        _fechar(driver)
        if lock is not None:
            lock.release()


def recon_sob_demanda(adaptador, criar_driver, *, execution_id=None) -> str:
    """Antecipa o recon agendado a pedido do admin. Mesmo caminho, mesma
    política: autoativa só o inequívoco e registra incidente no resto."""
    ativo = contrato_portal_preflight.buscar_ativo(
        adaptador.fluxo, adaptador.alvo, obrigatorio=False)
    if ativo is None:
        return SEM_CONTRATO

    lock = adaptador.lock
    if lock is not None and not lock.acquire(blocking=False):
        raise AlvoOcupadoError(
            'Há uma emissão em curso neste portal. Tente novamente depois.')
    try:
        return _observar_alvo(adaptador, ativo, criar_driver, execution_id)
    finally:
        if lock is not None:
            lock.release()


def aceitar_incidente(incidente, adaptador, criar_driver, *, usuario_id):
    """Observa de novo e promove a estrutura observada, se ela ainda se sustenta.

    Uma observação só, duas comparações: a primeira diz o que mudou em relação
    à base (e produz os remapeamentos da candidata), a segunda revalida a
    candidata contra a MESMA observação — é o controle otimista que impede
    aprovar uma tela que já mudou de novo (AC-06.4).
    """
    base = incidente.contrato_base
    inventario = _observar_com_lock(adaptador, base, criar_driver)
    resultado = comparar(contrato_portal_preflight.comparavel(base), inventario)
    if resultado.classificacao != DRIFT_REVISAO:
        raise NadaParaRevisarError(
            'A observação de agora não reproduz a mudança registrada; '
            'rode o recon antes de aprovar.')

    candidata = contrato_portal.criar_candidata_revisao(
        base.id,
        ajustes=resultado.remapeamentos,
        resultado=resultado,
        fingerprint_base=base.fingerprint,
        usuario_id=usuario_id,
    )
    revalidacao = comparar(contrato_portal_preflight.comparavel(candidata), inventario)
    if revalidacao.classificacao != DRIFT_COMPATIVEL:
        contrato_portal.rejeitar_candidata(candidata.id, usuario_id=usuario_id)
        raise NadaParaRevisarError(
            'A estrutura observada não fica compatível com o ajuste proposto.')
    return contrato_portal.aceitar_candidata(
        candidata.id,
        fingerprint_base=base.fingerprint,
        revalidacao=revalidacao,
        usuario_id=usuario_id,
    )


def criar_baseline_observada(adaptador, criar_driver, *, usuario_id):
    """Ativa a primeira versão a partir do que a tela realmente mostra.

    A declaração do código continua mandando em identidade e política; a
    observação só preenche os fatos que ela não tem como saber. Se a tela não
    sustenta a declaração, nada é criado — ver `montar_baseline_observada`.
    """
    if adaptador.definicao is None:
        raise BaselineNaoObservavelError(
            'Este portal não declara baseline revisável.')

    declaracao = adaptador.definicao()
    inventario = _observar_com_lock(adaptador, declaracao, criar_driver)
    observada = montar_baseline_observada(declaracao, inventario)
    contrato = contrato_portal.criar_baseline(
        fluxo=adaptador.fluxo,
        alvo=adaptador.alvo,
        definicao=observada,
        usuario_id=usuario_id,
    )
    log_event(
        'contrato_portal_baseline_observada', fluxo=adaptador.fluxo,
        alvo=adaptador.alvo, versao=contrato.versao,
        elementos=len(observada.elementos))
    return contrato
