"""Provas de acesso aos serviços nacionais de NFS-e.

A verificação é uma sondagem controlada: uma chamada à SEFIN para o convênio
do município e uma consulta pontual ao ADN. Ela não percorre histórico e não
confunde a autorização de um serviço com a do outro.
"""

import json
from contextlib import ExitStack
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app import db
from app.models import ConfiguracaoNfse, Empresa, SincronizacaoAdnNfse
from app.services import circuit_breaker
from app.services import nfse_api_credencial
from app.services import nfse_api_transporte as transporte
from app.services import nfse_api_xml as xml_api
from app.services import nfse_emitidas
from app.services.execution_logger import log_event


@dataclass(frozen=True)
class Acessos:
    """Desfechos independentes da SEFIN e do ADN."""

    sefin: transporte.Desfecho
    adn: transporte.Desfecho


TETO_CHAMADAS_PADRAO = 100
LIMITE_ITENS_LOTE = 50
LEASE_MINUTOS = 15
STATUS_DOCUMENTOS = 'DOCUMENTOS_LOCALIZADOS'
STATUS_SEM_DOCUMENTOS = 'NENHUM_DOCUMENTO_LOCALIZADO'
STATUS_REJEICAO = 'REJEICAO'


class NfseApiAdnError(RuntimeError):
    """Falha controlada ao sincronizar a distribuição do ADN."""


class RejeicaoAdnError(NfseApiAdnError):
    """O ADN respondeu, mas rejeitou o processamento do pedido."""

    def __init__(self, mensagem, codigo=None):
        super().__init__(mensagem)
        self.codigo = codigo


class FalhaTransporteAdnError(NfseApiAdnError):
    """Falha segura do transporte associada ao NSU consultado."""

    def __init__(self, situacao, http=None, nsu=None):
        self.codigo = f'transporte_{situacao}'[:80]
        self.http = http
        super().__init__(
            f'Transporte {situacao}'
            f'{f" (HTTP {http})" if http is not None else ""}'
            f' no NSU {nsu}.')


class SincronizacaoEmCursoError(NfseApiAdnError):
    """Outra execução ainda possui o lease do cursor."""


@dataclass(frozen=True)
class Resultado:
    """Resumo seguro de uma sincronização, sem corpo bruto da API."""

    nsu_inicial: int
    nsu_final: int | None
    lidos: int = 0
    gravados: int = 0
    ignorados: int = 0
    falha: str | None = None
    desfecho: str = 'concluida'
    nsu_falha: int | None = None


def verificar_acesso():
    """Sonda convênio e ADN sem iniciar a distribuição histórica."""
    diagnostico = nfse_api_credencial.diagnostico()
    if not diagnostico.disponivel:
        desfechos = Acessos(
            _desfecho_credencial(diagnostico),
            _desfecho_credencial(diagnostico),
        )
        _registrar('sefin', desfechos.sefin)
        _registrar('adn', desfechos.adn)
        return desfechos

    credencial = nfse_api_credencial.credencial_do_escritorio()
    if credencial is None:
        desfechos = Acessos(
            _desfecho_credencial(),
            _desfecho_credencial(),
        )
        _registrar('sefin', desfechos.sefin)
        _registrar('adn', desfechos.adn)
        return desfechos

    config = db.session.get(ConfiguracaoNfse, 1)
    ambiente = transporte.ambiente_atual()
    codigo_municipio = str(
        getattr(config, 'municipio_servico_codigo', '') or '').strip()
    url_sefin = (
        f'{transporte.url_de("parametros_convenio", ambiente)}/'
        f'{codigo_municipio}/convenio')
    url_adn = f'{transporte.url_de("adn_dfe", ambiente)}/0'

    try:
        with ExitStack() as pilha:
            sessao = pilha.enter_context(transporte.sessao(credencial))
            sefin = transporte.chamar('GET', url_sefin, sessao=sessao)
            adn = transporte.chamar(
                'GET', url_adn, sessao=sessao,
                params={'lote': 'false'})
    except transporte.NfseApiTransporteError:
        sefin = adn = _desfecho_credencial()

    desfechos = Acessos(sefin, adn)
    _registrar('sefin', desfechos.sefin)
    _registrar('adn', desfechos.adn)
    return desfechos


def _desfecho_credencial(diagnostico=None):
    mensagem = 'A credencial do escritório não está disponível para a API nacional.'
    if diagnostico is not None and diagnostico.causa == 'vencido':
        data = diagnostico.not_after
        if data is not None:
            mensagem = (
                'O certificado do escritório está vencido em '
                f'{data:%d/%m/%Y}.')
    return transporte.Desfecho('credencial', None, '', mensagem)


def _registrar(servico, desfecho):
    log_event(
        'nfse_api_acesso', servico=servico,
        situacao=desfecho.situacao, http=desfecho.http)


# --- sincronização incremental por NSU ------------------------------------

def _agora():
    """Relógio isolado para testar expiração e retomada do lease."""
    return datetime.now()


def _digitos(valor):
    return ''.join(caractere for caractere in str(valor or '')
                   if caractere.isdigit())


def _documento_consulta():
    """Resolve o CNPJ do próprio escritório, sem aceitar valor de tela."""
    config = db.session.get(ConfiguracaoNfse, 1)
    if config is None or not config.empresa_escritorio_id:
        return None
    empresa = db.session.get(Empresa, config.empresa_escritorio_id)
    if empresa is None:
        return None
    documento = _digitos(empresa.cnpj)
    return documento if len(documento) == 14 else None


def _nome_dono(execution_id):
    base = str(execution_id or '').strip() or uuid4().hex
    return f'{base[:31]}-{uuid4().hex[:8]}'


def _lease_vigente(cursor, agora, dono):
    return (cursor.dono_execucao not in (None, dono)
            and cursor.lease_ate is not None
            and cursor.lease_ate > agora)


def _adquirir_lease(ambiente, documento, dono):
    """Adquire o cursor ou recusa uma execução ainda viva."""
    agora = _agora()
    cursor = SincronizacaoAdnNfse.query.filter_by(
        ambiente=ambiente,
        documento_consulta=documento,
    ).with_for_update().first()
    if cursor is None:
        cursor = SincronizacaoAdnNfse(
            ambiente=ambiente,
            documento_consulta=documento,
            atualizado_em=agora,
        )
        db.session.add(cursor)
        try:
            db.session.flush()
        except IntegrityError:
            # Duas primeiras execuções podem tentar criar a mesma chave. A
            # restrição única é a arbitragem do banco; a segunda relê a linha
            # antes de decidir se o lease está ocupado.
            db.session.rollback()
            cursor = SincronizacaoAdnNfse.query.filter_by(
                ambiente=ambiente,
                documento_consulta=documento,
            ).with_for_update().first()
            if cursor is None:
                raise

    if _lease_vigente(cursor, agora, dono):
        db.session.rollback()
        raise SincronizacaoEmCursoError(
            'Já existe uma sincronização do ADN em andamento.')

    cursor.dono_execucao = dono
    cursor.lease_ate = agora + timedelta(minutes=LEASE_MINUTOS)
    cursor.atualizado_em = agora
    db.session.commit()
    return cursor


def _renovar_lease(cursor_id, dono):
    agora = _agora()
    resultado = db.session.execute(
        update(SincronizacaoAdnNfse)
        .where(
            SincronizacaoAdnNfse.id == cursor_id,
            SincronizacaoAdnNfse.dono_execucao == dono,
            SincronizacaoAdnNfse.lease_ate > agora,
        )
        .values(
            lease_ate=agora + timedelta(minutes=LEASE_MINUTOS),
            atualizado_em=agora,
        )
        .execution_options(synchronize_session=False)
    )
    if resultado.rowcount != 1:
        raise SincronizacaoEmCursoError(
            'O lease da sincronização do ADN não está mais ativo.')
    db.session.commit()
    return db.session.get(SincronizacaoAdnNfse, cursor_id)


def _liberar_lease(cursor_id, dono, execution_id=None):
    try:
        db.session.rollback()
        agora = _agora()
        db.session.execute(
            update(SincronizacaoAdnNfse)
            .where(
                SincronizacaoAdnNfse.id == cursor_id,
                SincronizacaoAdnNfse.dono_execucao == dono,
            )
            .values(
                dono_execucao=None,
                lease_ate=None,
                atualizado_em=agora,
            )
            .execution_options(synchronize_session=False)
        )
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        log_event(
            'nfse_api_adn_lease_falha',
            level='WARNING',
            erro='SQLAlchemyError',
            execution_id=execution_id,
        )


def _corpo_json(desfecho):
    try:
        corpo = json.loads(desfecho.corpo or '')
    except (TypeError, ValueError) as exc:
        raise NfseApiAdnError(
            'A resposta do ADN não contém JSON válido.') from exc
    if not isinstance(corpo, dict):
        raise NfseApiAdnError('A resposta do ADN não contém um objeto JSON.')
    return corpo


def _codigo_erro(corpo):
    for nome in ('Erros', 'Alertas'):
        mensagens = corpo.get(nome) or []
        if not isinstance(mensagens, list):
            continue
        for mensagem in mensagens:
            if isinstance(mensagem, dict) and mensagem.get('Codigo'):
                return str(mensagem['Codigo'])[:80]
    return None


def _itens_da_resposta(desfecho):
    corpo = _corpo_json(desfecho)
    status = str(corpo.get('StatusProcessamento') or '').strip().upper()
    if status == STATUS_SEM_DOCUMENTOS:
        return status, []
    if status == STATUS_REJEICAO:
        codigo = _codigo_erro(corpo)
        detalhe = f' ({codigo})' if codigo else ''
        raise RejeicaoAdnError(
            f'O ADN rejeitou a distribuição{detalhe}.', codigo=codigo)
    if status != STATUS_DOCUMENTOS:
        raise NfseApiAdnError(
            'O ADN devolveu um status de processamento desconhecido.')

    itens = corpo.get('LoteDFe')
    if itens is None:
        itens = []
    if not isinstance(itens, list):
        raise NfseApiAdnError('O lote do ADN não é uma lista.')
    if not itens:
        raise NfseApiAdnError(
            'O ADN informou documentos localizados, mas devolveu lote vazio.')
    if len(itens) > LIMITE_ITENS_LOTE:
        raise NfseApiAdnError(
            f'O lote do ADN excede {LIMITE_ITENS_LOTE} documentos.')
    return status, itens


def _nsu_do_item(item):
    if not isinstance(item, dict):
        raise NfseApiAdnError('Um item do lote do ADN não é um objeto.')
    bruto = item.get('NSU')
    if isinstance(bruto, bool) or bruto is None:
        raise NfseApiAdnError('Um item do lote do ADN não informa NSU.')
    if isinstance(bruto, str) and not bruto.strip().isdigit():
        raise NfseApiAdnError('O NSU do lote do ADN não é numérico.')
    try:
        nsu = int(bruto)
    except (TypeError, ValueError) as exc:
        raise NfseApiAdnError('O NSU do lote do ADN não é numérico.') from exc
    if nsu < 0:
        raise NfseApiAdnError('O NSU do lote do ADN não pode ser negativo.')
    return nsu


def _ordenar_itens(itens):
    normalizados = [(_nsu_do_item(item), item) for item in itens]
    return sorted(normalizados, key=lambda par: par[0])


def _registrar_ignorado(nsu, tipo, motivo, execution_id):
    log_event(
        'nfse_api_adn_ignorado',
        nsu=nsu,
        tipo=tipo or 'desconhecido',
        motivo=motivo,
        execution_id=execution_id,
    )


def _confirmar_nsu(cursor_id, dono, nsu):
    """Atualiza o checkpoint só se o worker ainda possuir o lease."""
    agora = _agora()
    resultado = db.session.execute(
        update(SincronizacaoAdnNfse)
        .where(
            SincronizacaoAdnNfse.id == cursor_id,
            SincronizacaoAdnNfse.dono_execucao == dono,
            SincronizacaoAdnNfse.lease_ate > agora,
        )
        .values(
            ultimo_nsu=nsu,
            ultima_falha=None,
            atualizado_em=agora,
        )
        .execution_options(synchronize_session=False)
    )
    if resultado.rowcount != 1:
        raise SincronizacaoEmCursoError(
            'O lease da sincronização do ADN expirou antes do checkpoint.')


def _confirmar_sem_documentos(cursor_id, dono):
    agora = _agora()
    resultado = db.session.execute(
        update(SincronizacaoAdnNfse)
        .where(
            SincronizacaoAdnNfse.id == cursor_id,
            SincronizacaoAdnNfse.dono_execucao == dono,
            SincronizacaoAdnNfse.lease_ate > agora,
        )
        .values(ultima_falha=None, atualizado_em=agora)
        .execution_options(synchronize_session=False)
    )
    if resultado.rowcount != 1:
        raise SincronizacaoEmCursoError(
            'O lease da sincronização do ADN expirou antes do encerramento.')


def _confirmar_lease(cursor_id, dono):
    agora = _agora()
    resultado = db.session.execute(
        update(SincronizacaoAdnNfse)
        .where(
            SincronizacaoAdnNfse.id == cursor_id,
            SincronizacaoAdnNfse.dono_execucao == dono,
            SincronizacaoAdnNfse.lease_ate > agora,
        )
        .values(atualizado_em=agora)
        .execution_options(synchronize_session=False)
    )
    if resultado.rowcount != 1:
        raise SincronizacaoEmCursoError(
            'O lease da sincronização do ADN expirou antes da conciliação.')


def _lease_do_dono_vigente(cursor_id, dono):
    cursor = db.session.get(SincronizacaoAdnNfse, cursor_id)
    return (cursor is not None and cursor.dono_execucao == dono
            and cursor.lease_ate is not None
            and cursor.lease_ate > _agora())


def _processar_item(item, documento_consulta, nsu, execution_id):
    tipo = str(item.get('TipoDocumento') or '').strip().upper()
    if tipo not in ('NFSE', 'EVENTO'):
        _registrar_ignorado(nsu, tipo, 'tipo_documento_fora_do_escopo', execution_id)
        return False, True

    arquivo = item.get('ArquivoXml')
    if not arquivo:
        raise NfseApiAdnError(
            f'O documento do NSU {nsu} não contém ArquivoXml.')
    xml = xml_api.descomprimir(arquivo)
    classificacao = xml_api.classificar(xml)

    if tipo == 'NFSE':
        if classificacao != 'nfse':
            raise NfseApiAdnError(
                f'O XML do NSU {nsu} não é uma NFS-e.')
        linha = xml_api.ler_nfse(xml)
        prestador = xml_api.documento_prestador(xml)
        if not prestador:
            _registrar_ignorado(
                nsu, tipo, 'prestador_nao_identificado', execution_id)
            return False, True
        if _digitos(prestador) != documento_consulta:
            _registrar_ignorado(
                nsu, tipo, 'escritorio_e_tomador', execution_id)
            return False, True
        nfse_emitidas._gravar_observacoes(
            [linha], 'adn', execution_id=execution_id)
        nfse_emitidas.projetar_espelho([linha.chave])
        return True, False

    if classificacao != 'evento':
        raise NfseApiAdnError(
            f'O XML do NSU {nsu} não é um evento de NFS-e.')
    evento = xml_api.ler_evento(xml)
    if not evento.tratado:
        _registrar_ignorado(nsu, tipo, 'evento_nao_tratado', execution_id)
        return False, True
    nfse_emitidas.registrar_eventos(
        [replace(evento, nsu=nsu)], execution_id=execution_id)
    return True, False


def _marcar_falha_cursor(cursor_id, dono, nsu, exc, execution_id=None):
    motivo = getattr(exc, 'codigo', None) or type(exc).__name__
    atualizado = False
    try:
        db.session.rollback()
        agora = _agora()
        resultado = db.session.execute(
            update(SincronizacaoAdnNfse)
            .where(
                SincronizacaoAdnNfse.id == cursor_id,
                SincronizacaoAdnNfse.dono_execucao == dono,
                SincronizacaoAdnNfse.lease_ate > agora,
            )
            .values(
                ultima_falha=f'NSU {nsu}: {motivo}'[:500],
                atualizado_em=agora,
            )
            .execution_options(synchronize_session=False)
        )
        db.session.commit()
        atualizado = resultado.rowcount == 1
    except SQLAlchemyError:
        db.session.rollback()
    log_event(
        'nfse_api_adn_falha_nsu',
        nsu=nsu,
        erro=motivo,
        atualizado=atualizado,
        http=getattr(exc, 'http', None),
        execution_id=execution_id,
    )


def _resultado_falha(nsu_inicial, mensagem, desfecho, execution_id=None,
                     nsu_falha=None):
    resultado = Resultado(
        nsu_inicial=nsu_inicial,
        nsu_final=None,
        falha=mensagem,
        desfecho=desfecho,
        nsu_falha=nsu_falha,
    )
    log_event(
        'nfse_api_adn_fim',
        nsu_inicial=resultado.nsu_inicial,
        nsu_final=resultado.nsu_final,
        lidos=resultado.lidos,
        gravados=resultado.gravados,
        ignorados=resultado.ignorados,
        desfecho=resultado.desfecho,
        nsu_falha=resultado.nsu_falha,
        execution_id=execution_id,
    )
    return resultado


def sincronizar(*, execution_id=None, teto_chamadas=TETO_CHAMADAS_PADRAO):
    """Percorre o ADN por NSU com checkpoint transacional e lease recuperável."""
    if isinstance(teto_chamadas, bool) or int(teto_chamadas) < 1:
        raise ValueError('O teto de chamadas precisa ser positivo.')
    teto_chamadas = int(teto_chamadas)
    execution_id = str(execution_id or uuid4())[:40]
    log_event(
        'nfse_api_adn_inicio',
        teto_chamadas=teto_chamadas,
        execution_id=execution_id,
    )

    try:
        documento = _documento_consulta()
        if documento is None:
            return _resultado_falha(
                0,
                'Não há empresa do escritório configurada para a API nacional.',
                'credencial',
                execution_id,
            )
        ambiente = transporte.ambiente_atual()

        diagnostico = nfse_api_credencial.diagnostico()
        if not diagnostico.disponivel:
            return _resultado_falha(
                0,
                f'Credencial indisponível: {diagnostico.causa}.',
                'credencial',
                execution_id,
            )
        credencial = nfse_api_credencial.credencial_do_escritorio()
        if credencial is None:
            return _resultado_falha(
                0,
                'A credencial do escritório não está disponível.',
                'credencial',
                execution_id,
            )

        dono = _nome_dono(execution_id)
        cursor = _adquirir_lease(ambiente, documento, dono)
    except SincronizacaoEmCursoError:
        log_event(
            'nfse_api_adn_fim',
            nsu_inicial=0,
            nsu_final=None,
            lidos=0,
            gravados=0,
            ignorados=0,
            desfecho='em_curso',
            nsu_falha=None,
            execution_id=execution_id,
        )
        raise
    except SQLAlchemyError as exc:
        db.session.rollback()
        return _resultado_falha(
            0,
            f'Falha local no banco: {type(exc).__name__}.',
            'falha_local',
            execution_id,
            nsu_falha=0,
        )
    cursor_id = cursor.id
    nsu_inicial = cursor.ultimo_nsu or 0
    nsu_confirmado = cursor.ultimo_nsu
    lidos = gravados = ignorados = 0
    nsu_falha = None
    nsu_corrente = nsu_inicial
    falha = None
    desfecho = 'teto'

    try:
        url_base = transporte.url_de('adn_dfe', ambiente)
        with ExitStack() as pilha:
            sessao = pilha.enter_context(transporte.sessao(credencial))
            for _numero_chamada in range(teto_chamadas):
                cursor = _renovar_lease(cursor_id, dono)
                nsu_atual = cursor.ultimo_nsu or 0
                nsu_corrente = nsu_atual
                resposta = transporte.chamar(
                    'GET', f'{url_base}/{nsu_atual}', sessao=sessao,
                    params={'lote': 'true'})
                if resposta.situacao != 'ok':
                    falha = resposta.mensagem or (
                        'A consulta do ADN não foi concluída.')
                    desfecho = resposta.situacao
                    nsu_falha = nsu_atual
                    _marcar_falha_cursor(
                        cursor_id, dono, nsu_falha,
                        FalhaTransporteAdnError(
                            resposta.situacao, resposta.http, nsu_atual),
                        execution_id,
                    )
                    if resposta.situacao == 'indisponivel':
                        circuit_breaker.registrar_falha(
                            circuit_breaker.ALVO_NFSE_NACIONAL,
                            mensagem='API nacional indisponível',
                            execution_id=execution_id,
                        )
                    break
                circuit_breaker.registrar_sucesso(
                    circuit_breaker.ALVO_NFSE_NACIONAL,
                    execution_id=execution_id)
                try:
                    status, itens = _itens_da_resposta(resposta)
                    ordenados = _ordenar_itens(itens)
                except Exception as exc:
                    db.session.rollback()
                    falha = f'Falha na resposta do ADN: {type(exc).__name__}.'
                    desfecho = (
                        'rejeitado'
                        if isinstance(exc, RejeicaoAdnError)
                        else 'falha_local')
                    nsu_falha = nsu_atual
                    _marcar_falha_cursor(
                        cursor_id, dono, nsu_falha, exc, execution_id)
                    break

                if status == STATUS_SEM_DOCUMENTOS:
                    _confirmar_sem_documentos(cursor_id, dono)
                    db.session.commit()
                    desfecho = 'sem_documentos'
                    break

                interrompida = False
                for nsu, item in ordenados:
                    lidos += 1
                    if nsu <= (cursor.ultimo_nsu or 0):
                        ignorados += 1
                        _registrar_ignorado(
                            nsu,
                            item.get('TipoDocumento'),
                            'nsu_ja_confirmado',
                            execution_id,
                        )
                        continue
                    try:
                        foi_gravado, foi_ignorado = _processar_item(
                            item, documento, nsu, execution_id)
                        _confirmar_nsu(cursor_id, dono, nsu)
                        db.session.commit()
                        nsu_confirmado = nsu
                        gravados += int(foi_gravado)
                        ignorados += int(foi_ignorado)
                    except Exception as exc:
                        db.session.rollback()
                        falha = (
                            f'Falha local no NSU {nsu}: '
                            f'{type(exc).__name__}.')
                        desfecho = 'falha_local'
                        nsu_falha = nsu
                        _marcar_falha_cursor(
                            cursor_id, dono, nsu, exc, execution_id)
                        interrompida = True
                        break
                if interrompida:
                    break
    except transporte.NfseApiTransporteError as exc:
        db.session.rollback()
        falha = f'Falha de credencial no transporte: {type(exc).__name__}.'
        desfecho = 'credencial'
        nsu_falha = nsu_corrente
        _marcar_falha_cursor(
            cursor_id, dono, nsu_falha, exc, execution_id)
    except SQLAlchemyError as exc:
        db.session.rollback()
        falha = f'Falha local no banco: {type(exc).__name__}.'
        desfecho = 'falha_local'
        nsu_falha = nsu_falha if nsu_falha is not None else nsu_corrente
        _marcar_falha_cursor(
            cursor_id, dono, nsu_falha, exc, execution_id)
    except Exception as exc:
        db.session.rollback()
        falha = f'Falha local na sincronização: {type(exc).__name__}.'
        desfecho = 'falha_local'
        nsu_falha = nsu_falha if nsu_falha is not None else nsu_corrente
        _marcar_falha_cursor(
            cursor_id, dono, nsu_falha, exc, execution_id)
    finally:
        try:
            if _lease_do_dono_vigente(cursor_id, dono):
                nfse_emitidas.conciliar(
                    persistir=False, execution_id=execution_id)
                _confirmar_lease(cursor_id, dono)
                db.session.commit()
        except Exception as exc:
            db.session.rollback()
            if falha is None:
                falha = f'Falha local na conciliação: {type(exc).__name__}.'
                desfecho = 'falha_local'
        _liberar_lease(cursor_id, dono, execution_id)

    resultado = Resultado(
        nsu_inicial=nsu_inicial,
        nsu_final=nsu_confirmado,
        lidos=lidos,
        gravados=gravados,
        ignorados=ignorados,
        falha=falha,
        desfecho=desfecho,
        nsu_falha=nsu_falha,
    )
    log_event(
        'nfse_api_adn_fim',
        nsu_inicial=resultado.nsu_inicial,
        nsu_final=resultado.nsu_final,
        lidos=resultado.lidos,
        gravados=resultado.gravados,
        ignorados=resultado.ignorados,
        desfecho=resultado.desfecho,
        nsu_falha=resultado.nsu_falha,
        execution_id=execution_id,
    )
    return resultado
