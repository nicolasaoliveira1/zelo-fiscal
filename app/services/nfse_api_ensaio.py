"""Domínio da preparação local e da numeração do ensaio restrito da NFS-e.

Preparar é uma transição local e explícita: lê a referência oficial, reserva
um identificador, prova o leiaute e a assinatura e só então libera a tentativa
para a ação remota posterior. Este módulo não chama a SEFIN durante essa
transição.
"""

import json
import re
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET

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
from app.services.nfse_api_xml import NAMESPACE_NFSE


AMBIENTE_RESTRITA = 'restrita'
NUMERO_INICIAL = 1
TENTATIVAS_RESERVA = 10
JANELA_ENVIO_PRESO = timedelta(minutes=5)

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


class EnsaioDpsNaoEncontradoError(NfseApiEnsaioError):
    """A tentativa selecionada não existe no banco operacional."""


class TransicaoDpsInvalidaError(NfseApiEnsaioError):
    """A tentativa não está no estado exigido pela ação."""


class EnsaioDpsEmAndamentoError(NfseApiEnsaioError):
    """Outra ação já assumiu o envio desta tentativa."""


class EnvioDpsCredencialError(NfseApiEnsaioError):
    """A credencial impediu o envio sem criar um desfecho fiscal."""


class RespostaRestritaInvalidaError(NfseApiEnsaioError):
    """A resposta não permite persistir um desfecho com segurança."""


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


_CHAVE_NFSE = re.compile(r'NFS[0-9]{50}')


def _carregar_ensaio(ensaio_id, operador_id):
    operador = db.session.get(Usuario, operador_id)
    if operador is None:
        raise OperadorDpsNaoEncontradoError(
            'O operador informado não existe mais no banco.')
    ensaio = db.session.get(EnsaioDpsNfse, ensaio_id)
    if ensaio is None:
        raise EnsaioDpsNaoEncontradoError(
            'A tentativa de ensaio não existe mais no banco.')
    return ensaio


def _exigir_estado(ensaio, estado):
    if ensaio.estado != estado:
        raise TransicaoDpsInvalidaError(
            f'A tentativa está em {ensaio.estado!r}; a ação exige '
            f'{estado!r}.')


def _envio_preso(ensaio, agora=None):
    if ensaio.estado != ESTADO_ENVIANDO:
        return False
    if ensaio.atualizado_em is None:
        return False
    instante = agora or datetime.now()
    return instante - ensaio.atualizado_em >= JANELA_ENVIO_PRESO


def _exigir_reconsulta(ensaio):
    if ensaio.estado == ESTADO_INDEFINIDA:
        return
    if ensaio.estado == ESTADO_ENVIANDO:
        if _envio_preso(ensaio):
            return
        raise EnsaioDpsEmAndamentoError(
            'O envio ainda está em andamento; aguarde a janela de segurança '
            'antes de reconsultar.')
    raise TransicaoDpsInvalidaError(
        f'A tentativa está em {ensaio.estado!r}; a reconsulta exige '
        f'{ESTADO_INDEFINIDA!r} ou um envio preso.')


def _comparacao_liberada(ensaio):
    try:
        comparacao = json.loads(ensaio.comparacao_json or '')
    except (TypeError, ValueError) as exc:
        raise TransicaoDpsInvalidaError(
            'A tentativa não possui uma comparação local válida.') from exc
    if (not isinstance(comparacao, dict)
            or comparacao.get('pode_enviar') is not True
            or not isinstance(comparacao.get('bloqueadoras'), list)
            or comparacao['bloqueadoras']):
        raise TransicaoDpsInvalidaError(
            'A comparação local contém divergência bloqueadora.')


def _dps_assinada_do_ensaio(ensaio, dps_api):
    xml = ensaio.xml_dps_assinada
    if not isinstance(xml, str) or not xml.strip():
        raise TransicaoDpsInvalidaError(
            'A tentativa não possui a DPS assinada para envio.')
    try:
        dps = ET.fromstring(xml)
    except (ET.ParseError, TypeError, ValueError) as exc:
        raise TransicaoDpsInvalidaError(
            'A DPS persistida da tentativa está ilegível.') from exc
    problemas = dps_api.validar(dps)
    if problemas:
        raise TransicaoDpsInvalidaError(
            'A DPS persistida da tentativa não passa no XSD restrito.')
    try:
        identificador = dps_api.identificador(dps)
    except dps_api.NfseApiDpsError as exc:
        raise TransicaoDpsInvalidaError(
            'A DPS persistida da tentativa não possui identificador válido.') from exc
    if identificador != ensaio.identificador_dps:
        raise TransicaoDpsInvalidaError(
            'O identificador persistido não corresponde à DPS assinada.')
    if not dps_api.verificar(dps):
        raise TransicaoDpsInvalidaError(
            'A assinatura persistida da tentativa não pôde ser verificada.')
    return dps


def _validar_campo_resposta(valor, limite):
    if valor is None:
        return None
    if not isinstance(valor, str) or len(valor) > limite:
        raise RespostaRestritaInvalidaError(
            'A resposta da SEFIN trouxe um campo maior que o armazenamento permitido.')
    return valor


def _xml_nfse_texto(xml_nfse):
    if isinstance(xml_nfse, bytearray):
        xml_nfse = bytes(xml_nfse)
    if isinstance(xml_nfse, bytes):
        try:
            xml_nfse = xml_nfse.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise RespostaRestritaInvalidaError(
                'O XML da NFS-e de teste não está em uma codificação suportada.') from exc
    if not isinstance(xml_nfse, str) or not xml_nfse.strip():
        raise RespostaRestritaInvalidaError(
            'A resposta da SEFIN não trouxe um XML de NFS-e de teste válido.')
    return xml_nfse


def _validar_xml_nfse(xml_nfse, chave):
    try:
        raiz = ET.fromstring(xml_nfse)
    except (ET.ParseError, TypeError, ValueError) as exc:
        raise RespostaRestritaInvalidaError(
            'O XML da NFS-e de teste está malformado.') from exc
    inf_nfse = raiz.find(f'{{{NAMESPACE_NFSE}}}infNFSe')
    if (raiz.tag != f'{{{NAMESPACE_NFSE}}}NFSe'
            or inf_nfse is None
            or inf_nfse.get('Id') != chave):
        raise RespostaRestritaInvalidaError(
            'O XML da NFS-e de teste não corresponde à chave retornada.')


def _registrar_desfecho(ensaio, resultado):
    log_event(
        'nfse_ensaio_desfecho',
        ensaio_id=ensaio.id,
        estado=ensaio.estado,
        situacao=resultado.situacao,
        http=resultado.http,
    )


def _persistir_resultado(ensaio, resultado):
    if resultado.identificador not in (None, ensaio.identificador_dps):
        raise RespostaRestritaInvalidaError(
            'A resposta da SEFIN pertence a outro identificador de DPS.')

    if resultado.situacao == ESTADO_GERADO_TESTE:
        chave = resultado.chave_acesso
        if not isinstance(chave, str) or _CHAVE_NFSE.fullmatch(chave) is None:
            raise RespostaRestritaInvalidaError(
                'A resposta gerada não trouxe uma chave de acesso válida.')
        if resultado.xml_nfse is not None:
            xml_nfse = _xml_nfse_texto(resultado.xml_nfse)
            _validar_xml_nfse(xml_nfse, chave)
            ensaio.xml_nfse_teste = xml_nfse
        ensaio.chave_nfse_teste = chave
        ensaio.codigo_rejeicao = None
        ensaio.motivo_rejeicao = None
        ensaio.ultima_falha = None
        ensaio.estado = ESTADO_GERADO_TESTE
    elif resultado.situacao == ESTADO_REJEITADO:
        codigo = _validar_campo_resposta(resultado.codigo, 40)
        motivo = _validar_campo_resposta(resultado.motivo, 1000)
        if codigo is None and motivo is None:
            raise RespostaRestritaInvalidaError(
                'A rejeição da SEFIN não trouxe código nem motivo.')
        ensaio.codigo_rejeicao = codigo
        ensaio.motivo_rejeicao = motivo
        ensaio.ultima_falha = None
        ensaio.estado = ESTADO_REJEITADO
    else:
        raise RespostaRestritaInvalidaError(
            'A resposta da SEFIN não representa um desfecho persistível.')

    ensaio.atualizado_em = datetime.now()
    db.session.commit()
    _registrar_desfecho(ensaio, resultado)
    return ensaio


def _persistir_indefinida(ensaio, mensagem):
    ensaio.estado = ESTADO_INDEFINIDA
    ensaio.ultima_falha = mensagem[:1000]
    ensaio.atualizado_em = datetime.now()
    db.session.commit()
    log_event(
        'nfse_ensaio_desfecho_indefinido',
        ensaio_id=ensaio.id,
        estado=ensaio.estado,
    )
    return ensaio


def _restaurar_preparado(ensaio, mensagem):
    ensaio.estado = ESTADO_PREPARADO
    ensaio.ultima_falha = mensagem[:1000]
    ensaio.atualizado_em = datetime.now()
    db.session.commit()
    return ensaio


def _consultar(ensaio, sefin):
    try:
        return sefin.consultar_dps_restrita(ensaio.identificador_dps)
    except Exception as exc:
        log_event(
            'nfse_ensaio_consulta_falhou',
            level='WARNING',
            ensaio_id=ensaio.id,
            tipo=type(exc).__name__,
        )
        return None


def _resolver_apos_envio(ensaio, sefin):
    resultado = _consultar(ensaio, sefin)
    if resultado is not None and resultado.situacao in {
            ESTADO_GERADO_TESTE, ESTADO_REJEITADO}:
        try:
            return _persistir_resultado(ensaio, resultado)
        except RespostaRestritaInvalidaError:
            pass
    return _persistir_indefinida(
        ensaio,
        'O desfecho do ensaio não pôde ser confirmado; faça somente uma reconsulta.',
    )


def _assumir_envio(ensaio):
    agora = datetime.now()
    resultado = db.session.execute(
        update(EnsaioDpsNfse)
        .where(
            EnsaioDpsNfse.id == ensaio.id,
            EnsaioDpsNfse.estado == ESTADO_PREPARADO,
        )
        .values(
            estado=ESTADO_ENVIANDO,
            ultima_falha=None,
            atualizado_em=agora,
        )
        .execution_options(synchronize_session=False)
    )
    if resultado.rowcount == 1:
        ensaio.estado = ESTADO_ENVIANDO
        ensaio.ultima_falha = None
        ensaio.atualizado_em = agora
        db.session.commit()
        return ensaio

    db.session.rollback()
    atual = db.session.get(EnsaioDpsNfse, ensaio.id)
    if atual is None:
        raise EnsaioDpsNaoEncontradoError(
            'A tentativa de ensaio não existe mais no banco.')
    if atual.estado == ESTADO_ENVIANDO:
        raise EnsaioDpsEmAndamentoError(
            'Outra ação já assumiu o envio desta tentativa.')
    if atual.estado in {
            ESTADO_GERADO_TESTE, ESTADO_REJEITADO, ESTADO_INDEFINIDA}:
        return atual
    raise TransicaoDpsInvalidaError(
        f'A tentativa mudou para o estado {atual.estado!r} antes do envio.')


def enviar(ensaio_id, *, operador_id):
    """Envia uma tentativa preparada, sem repetir POST após incerteza."""
    from app.services import nfse_api_dps as dps_api
    from app.services import nfse_api_sefin as sefin

    ensaio = _carregar_ensaio(ensaio_id, operador_id)
    _exigir_estado(ensaio, ESTADO_PREPARADO)
    _comparacao_liberada(ensaio)
    dps = _dps_assinada_do_ensaio(ensaio, dps_api)

    preexistente = _consultar(ensaio, sefin)
    if preexistente is None or preexistente.situacao == 'indisponivel':
        return _persistir_indefinida(
            ensaio,
            'Não foi possível consultar o identificador antes do POST; o ensaio '
            'ficou indefinido e deve ser reconsultado.',
        )
    if preexistente.situacao in {ESTADO_GERADO_TESTE, ESTADO_REJEITADO}:
        return _persistir_resultado(ensaio, preexistente)
    if preexistente.situacao == 'credencial':
        raise EnvioDpsCredencialError(
            'A credencial não autorizou a consulta anterior ao POST.')
    if preexistente.situacao != 'nao_encontrado':
        raise RespostaRestritaInvalidaError(
            'A consulta anterior ao POST não trouxe um desfecho utilizável.')

    ensaio = _assumir_envio(ensaio)
    if ensaio.estado != ESTADO_ENVIANDO:
        return ensaio

    try:
        resultado = sefin.enviar_restrita(dps)
    except Exception:
        return _resolver_apos_envio(ensaio, sefin)

    if resultado is None:
        return _resolver_apos_envio(ensaio, sefin)
    if resultado.situacao in {ESTADO_GERADO_TESTE, ESTADO_REJEITADO}:
        try:
            return _persistir_resultado(ensaio, resultado)
        except RespostaRestritaInvalidaError:
            return _resolver_apos_envio(ensaio, sefin)
    if resultado.situacao == 'credencial':
        return _restaurar_preparado(
            ensaio,
            'A credencial não autorizou o envio; nenhuma nova tentativa foi feita.',
        )
    return _resolver_apos_envio(ensaio, sefin)


def reconsultar(ensaio_id, *, operador_id):
    """Consulta uma tentativa indefinida ou um envio preso sem POST."""
    from app.services import nfse_api_sefin as sefin

    ensaio = _carregar_ensaio(ensaio_id, operador_id)
    _exigir_reconsulta(ensaio)
    resultado = _consultar(ensaio, sefin)
    if resultado is not None and resultado.situacao in {
            ESTADO_GERADO_TESTE, ESTADO_REJEITADO}:
        try:
            return _persistir_resultado(ensaio, resultado)
        except RespostaRestritaInvalidaError:
            pass
    return _persistir_indefinida(
        ensaio,
        'O desfecho do ensaio continua desconhecido; tente reconsultar mais tarde.',
    )
