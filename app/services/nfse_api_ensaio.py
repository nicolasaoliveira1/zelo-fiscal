"""Domínio da preparação local e da numeração do ensaio restrito da NFS-e.

Preparar é uma transição local e explícita: lê a referência oficial, reserva
um identificador, prova o leiaute e a assinatura e só então libera a tentativa
para a ação remota posterior. Este módulo não chama a SEFIN durante essa
transição.
"""

import json
import re
from datetime import datetime

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError, OperationalError

from app import db
from app.models import (
    ConfiguracaoNfse,
    ContadorDpsNfse,
    EnsaioDpsNfse,
    Empresa,
    NotaEmitidaNfse,
    NotaNfse,
    StatusNotaNfse,
    Usuario,
)
from app.services import nfe_assinatura, nfse_api_credencial
from app.services.execution_logger import log_event


AMBIENTE_RESTRITA = 'restrita'
NUMERO_INICIAL = 1
TENTATIVAS_RESERVA = 10

ESTADO_RESERVADO = 'reservado'
ESTADO_PREPARADO = 'preparado'
ESTADO_ENVIANDO = 'enviando'
ESTADO_GERADO_TESTE = 'gerado_teste'
ESTADO_REJEITADO = 'rejeitado'
ESTADO_INDEFINIDA = 'indefinida'
ESTADO_FALHA_PREPARACAO = 'falha_preparacao'


class NfseApiEnsaioError(RuntimeError):
    """Erro controlado do fluxo de ensaio."""


class AmbienteDpsInvalidoError(NfseApiEnsaioError):
    """O contador solicitado não pertence ao ambiente permitido no P2."""


class PrestadorDpsInvalidoError(NfseApiEnsaioError):
    """O identificador do prestador não é representável no P2."""


class SerieDpsInvalidaError(NfseApiEnsaioError):
    """A série não atende ao formato ou à faixa reservada do P2."""


class ReservaDpsConflitoError(NfseApiEnsaioError):
    """O contador não pôde ser reservado após as tentativas seguras."""


class NotaDpsNaoEncontradaError(NfseApiEnsaioError):
    """A nota selecionada não existe no banco operacional."""


class OperadorDpsNaoEncontradoError(NfseApiEnsaioError):
    """O operador informado não existe no banco operacional."""


class ConfiguracaoDpsAusenteError(NfseApiEnsaioError):
    """A configuração única da NFS-e não foi criada."""


class EspelhoNfseAusenteError(NfseApiEnsaioError):
    """A nota não tem a evidência oficial necessária para o replay."""


class EspelhoNfseAmbiguoError(NfseApiEnsaioError):
    """A nota está ligada a mais de um espelho oficial."""


class EspelhoNfseInvalidoError(NfseApiEnsaioError):
    """A chave do espelho não confere com o XML histórico recuperado."""


class XmlReferenciaIndisponivelError(NfseApiEnsaioError):
    """A fonte read-only do XML histórico não está disponível."""


class PrestadorDpsDivergenteError(NfseApiEnsaioError):
    """O prestador da referência não é o escritório configurado."""


class MaterialAssinaturaIndisponivelError(NfseApiEnsaioError):
    """A chave e o certificado do escritório não puderam ser carregados."""


class PreparacaoDpsInvalidaError(NfseApiEnsaioError):
    """Uma prova local impediu a liberação da tentativa."""


class ComparacaoDpsBloqueadaError(NfseApiEnsaioError):
    """A comparação encontrou diferença fiscal que impede o envio."""


def validar_serie(serie):
    """Valida e devolve a série textual sem normalização silenciosa."""
    if not isinstance(serie, str) or re.fullmatch(r'[0-9]{1,5}', serie) is None:
        raise SerieDpsInvalidaError(
            'A série restrita deve conter de 1 a 5 algarismos.')
    if 80000 <= int(serie) <= 89999:
        raise SerieDpsInvalidaError(
            'A faixa de séries restrita de 80000 a 89999 é reservada.')
    return serie


def _validar_prestador(prestador):
    if not isinstance(prestador, str) or re.fullmatch(r'[0-9]{14}', prestador) is None:
        raise PrestadorDpsInvalidoError(
            'O prestador do ensaio deve ter 14 algarismos.')


def _validar_chave(ambiente, prestador, serie):
    if ambiente != AMBIENTE_RESTRITA:
        raise AmbienteDpsInvalidoError(
            'O contador do P2 aceita somente o ambiente restrito.')
    _validar_prestador(prestador)
    validar_serie(serie)


def reservar_numero(ambiente, prestador, serie, *, agora=None):
    """Reserva atomicamente o próximo número da série restrita.

    A atualização condicional protege o contador mesmo quando o dialeto não
    implementa `SELECT ... FOR UPDATE` (como SQLite). Se a primeira execução
    precisar criar a linha, a unicidade do banco arbitra a corrida de criação.
    """
    _validar_chave(ambiente, prestador, serie)
    instante = agora or datetime.now()

    for _ in range(TENTATIVAS_RESERVA):
        contador = ContadorDpsNfse.query.filter_by(
            ambiente=ambiente,
            prestador=prestador,
            serie=serie,
        ).with_for_update().one_or_none()

        if contador is None:
            db.session.add(ContadorDpsNfse(
                ambiente=ambiente,
                prestador=prestador,
                serie=serie,
                proximo_numero=NUMERO_INICIAL + 1,
                atualizado_em=instante,
            ))
            try:
                db.session.commit()
            except (IntegrityError, OperationalError):
                db.session.rollback()
                continue
            return NUMERO_INICIAL

        numero = int(contador.proximo_numero)
        resultado = db.session.execute(
            update(ContadorDpsNfse)
            .where(
                ContadorDpsNfse.id == contador.id,
                ContadorDpsNfse.proximo_numero == numero,
            )
            .values(
                proximo_numero=numero + 1,
                atualizado_em=instante,
            )
        )
        if resultado.rowcount != 1:
            db.session.rollback()
            continue
        try:
            db.session.commit()
        except OperationalError:
            db.session.rollback()
            continue
        return numero

    db.session.rollback()
    raise ReservaDpsConflitoError(
        'Não foi possível reservar o número do ensaio com segurança.')


def obter_xml_referencia(nota, espelho):
    """Obtém o XML oficial por uma fonte read-only ligada à aplicação.

    O P1 persiste no espelho os fatos necessários à conferência, mas não o XML
    inteiro. A fonte que recupera esse documento depende do conector autorizado
    pela instalação e é injetada nesta fronteira; a preparação é o único ponto
    que pode chamá-la. Até que o conector seja ligado, falha de forma explícita
    em vez de inventar um XML a partir das colunas resumidas.
    """
    del nota, espelho
    raise XmlReferenciaIndisponivelError(
        'A fonte read-only do XML histórico ainda não está disponível.')


def obter_material_assinatura():
    """Carrega a chave e o certificado do escritório no momento da preparação.

    A credencial continua vindo do cofre. O material só vive na memória desta
    chamada e é entregue ao núcleo compartilhado de assinatura; não é persistido
    nem incluído em mensagens ou logs.
    """
    credencial = nfse_api_credencial.credencial_do_escritorio()
    if credencial is None:
        raise MaterialAssinaturaIndisponivelError(
            'Não há certificado pronto do escritório para assinar a DPS.')

    from app.services import manifestador_cofre

    info = manifestador_cofre.carregar_pfx(
        credencial.caminho,
        (credencial.senha or '').encode() or None,
    )
    if info is None or info.chave_privada is None or info.certificado is None:
        raise MaterialAssinaturaIndisponivelError(
            'O certificado do escritório não pôde ser carregado para a assinatura.')
    return info.chave_privada, info.certificado


def _digitos_empresa(empresa):
    bruto = str(getattr(empresa, 'cnpj', '') or '').strip()
    if not bruto or re.fullmatch(r'[0-9 .()/\-]+', bruto) is None:
        raise PrestadorDpsInvalidoError(
            'O CNPJ do escritório não tem formato representável no ensaio.')
    cnpj = ''.join(caractere for caractere in bruto if caractere.isdigit())
    if re.fullmatch(r'[0-9]{14}', cnpj) is None:
        raise PrestadorDpsInvalidoError(
            'O CNPJ do escritório precisa ter 14 algarismos.')
    return cnpj


def _entrada_preparacao(nota_id, operador_id):
    nota = db.session.get(NotaNfse, nota_id)
    if nota is None:
        raise NotaDpsNaoEncontradaError(
            'A nota selecionada não existe mais no banco.')

    operador = db.session.get(Usuario, operador_id)
    if operador is None:
        raise OperadorDpsNaoEncontradoError(
            'O operador informado não existe mais no banco.')

    if nota.status != StatusNotaNfse.EMITIDA:
        raise NotaDpsNaoEncontradaError(
            'O ensaio restrito aceita somente nota histórica emitida.')

    espelhos = NotaEmitidaNfse.query.filter_by(nota_id=nota.id).all()
    if not espelhos:
        raise EspelhoNfseAusenteError(
            'A nota não está vinculada ao espelho oficial de NFS-e.')
    if len(espelhos) != 1:
        raise EspelhoNfseAmbiguoError(
            'A nota está vinculada a mais de um espelho oficial de NFS-e.')

    config = db.session.get(ConfiguracaoNfse, 1)
    if config is None:
        raise ConfiguracaoDpsAusenteError(
            'A configuração da NFS-e não foi criada.')
    serie = validar_serie(config.serie_dps_restrita)

    empresa = None
    if config.empresa_escritorio_id:
        empresa = db.session.get(Empresa, config.empresa_escritorio_id)
    if empresa is None:
        raise PrestadorDpsInvalidoError(
            'A empresa do escritório não está configurada para o ensaio.')
    prestador = _digitos_empresa(empresa)
    return nota, operador, espelhos[0], config, prestador, serie


def _texto_xml(xml_real):
    if isinstance(xml_real, bytearray):
        xml_real = bytes(xml_real)
    if isinstance(xml_real, bytes):
        try:
            return xml_real.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise XmlReferenciaIndisponivelError(
                'O XML oficial não está em uma codificação textual suportada.') from exc
    if isinstance(xml_real, str) and xml_real.strip():
        return xml_real
    raise XmlReferenciaIndisponivelError(
        'A fonte read-only não devolveu um XML histórico.')


def _falha_legivel(exc, dps_api):
    if isinstance(exc, NfseApiEnsaioError):
        mensagem = str(exc).strip()
    elif isinstance(exc, dps_api.NfseApiDpsError):
        mensagem = str(exc).strip()
    else:
        mensagem = (
            'Uma prova local da DPS falhou. Revise a configuração e o material '
            'de assinatura antes de tentar novamente.')
    return (mensagem or 'A preparação local falhou.')[:1000]


def _marcar_falha(ensaio, exc, dps_api):
    ensaio.estado = ESTADO_FALHA_PREPARACAO
    ensaio.ultima_falha = _falha_legivel(exc, dps_api)
    ensaio.atualizado_em = datetime.now()
    db.session.commit()
    log_event(
        'nfse_ensaio_preparacao_falhou',
        nota_id=ensaio.nota_nfse_id,
        ensaio_id=ensaio.id,
        tipo=type(exc).__name__,
    )
    return ensaio


def _validar_dps(dps, dps_api):
    problemas = dps_api.validar(dps)
    if not problemas:
        return
    primeiro = problemas[0]
    raise PreparacaoDpsInvalidaError(
        f'A DPS não passou na validação local em {primeiro.caminho}: '
        f'{primeiro.mensagem}')


def preparar(nota_id, *, operador_id):
    """Prepara e persiste uma tentativa, sem fazer escrita fiscal remota.

    A obtenção da referência ocorre antes da reserva para que XML ausente ou
    ilegível não consuma numeração. Depois da reserva, a tentativa é gravada em
    ``reservado`` antes das provas restantes; assim uma falha local deixa o
    número e o motivo rastreáveis, sem alterar a nota histórica.
    """
    from app.services import nfse_api_dps as dps_api

    nota, operador, espelho, config, prestador, serie = _entrada_preparacao(
        nota_id, operador_id)

    try:
        xml_real = obter_xml_referencia(nota, espelho)
    except NfseApiEnsaioError:
        raise
    except Exception as exc:
        raise XmlReferenciaIndisponivelError(
            'A fonte read-only do XML histórico não pôde ser consultada.') from exc

    referencia = dps_api.ler_referencia(nota, xml_real)
    if referencia.prestador != prestador:
        raise PrestadorDpsDivergenteError(
            'O prestador do XML histórico não é o escritório configurado.')
    chave_espelho = str(getattr(espelho, 'chave', '') or '').strip()
    if re.fullmatch(r'[0-9]{50}', chave_espelho) is None:
        raise EspelhoNfseInvalidoError(
            'O espelho oficial não possui uma chave de acesso representável.')
    if referencia.chave_nfse != f'NFS{chave_espelho}':
        raise EspelhoNfseInvalidoError(
            'A chave do XML histórico não corresponde ao espelho oficial.')
    xml_referencia = _texto_xml(xml_real)

    numero = reservar_numero(AMBIENTE_RESTRITA, prestador, serie)
    identificador_dps = dps_api.identificador_de_referencia(
        referencia, serie=serie, numero=numero)
    ensaio = EnsaioDpsNfse(
        nota_nfse_id=nota.id,
        operador_id=operador.id,
        ambiente=AMBIENTE_RESTRITA,
        prestador=prestador,
        serie=serie,
        numero=numero,
        identificador_dps=identificador_dps,
        estado=ESTADO_RESERVADO,
        xml_referencia=xml_referencia,
    )
    db.session.add(ensaio)
    db.session.commit()

    try:
        dps = dps_api.montar(
            referencia,
            config,
            serie=serie,
            numero=numero,
        )
        _validar_dps(dps, dps_api)

        chave_privada, certificado = obter_material_assinatura()
        dps_assinada = dps_api.assinar(
            dps, chave_privada, certificado)
        if not dps_api.verificar(dps_assinada):
            raise PreparacaoDpsInvalidaError(
                'A assinatura produzida não passou na verificação local.')
        ensaio.xml_dps_assinada = nfe_assinatura.serializar_documento(
            dps_assinada)

        comparacao = dps_api.comparar_com_real(dps_assinada, xml_real)
        ensaio.comparacao_json = json.dumps(
            comparacao.serializar(), ensure_ascii=False, sort_keys=True)
        if not comparacao.pode_enviar:
            raise ComparacaoDpsBloqueadaError(
                'A comparação encontrou divergências fiscais bloqueadoras.')

        ensaio.estado = ESTADO_PREPARADO
        ensaio.ultima_falha = None
        ensaio.atualizado_em = datetime.now()
        db.session.commit()
    except Exception as exc:
        return _marcar_falha(ensaio, exc, dps_api)

    log_event(
        'nfse_ensaio_preparado',
        nota_id=ensaio.nota_nfse_id,
        ensaio_id=ensaio.id,
        estado=ensaio.estado,
    )
    return ensaio
