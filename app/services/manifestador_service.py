r"""Orquestracao de UMA manifestacao (MANIF-12, MANIF-16..19).

Esta e a **costura** do design: todo o resto do sistema fala com a SEFAZ por
aqui. E o que torna barata a contingencia do portal — trocar webservice por
Selenium substitui a implementacao de `manifestar` e mais nada; modelo, import,
cofre, UI, lote e auditoria continuam iguais.

Duas responsabilidades: montar o XML do evento e conduzir uma manifestacao
do banco ate o desfecho gravado.
"""
import re
import xml.etree.ElementTree as ET
from datetime import datetime

from app.services.execution_logger import log_event
from app.services.manifestador_import import dv_valido
# Importados por NOME de modulo para o teste poder substituir o envio sem
# tocar a rede — e para a costura ficar visivel num lugar so.
from app.services.nfe_assinatura import AssinaturaError, assinar
from app.services.nfe_sefaz import enviar_evento

NS_NFE = 'http://www.portalfiscal.inf.br/nfe'

# Codigos e descricoes vieram do select do proprio portal da NF-e. Codigo e
# descricao andam juntos: divergir os dois e rejeicao na hora.
CIENCIA = '210210'
CONFIRMACAO = '210200'
DESCONHECIMENTO = '210220'
NAO_REALIZADA = '210240'

DESCRICOES = {
    CIENCIA: 'Ciencia da Operacao',
    CONFIRMACAO: 'Confirmacao da Operacao',
    DESCONHECIMENTO: 'Desconhecimento da Operacao',
    NAO_REALIZADA: 'Operacao nao Realizada',
}

# O unico dos quatro que carrega texto livre. Sem `xJust` a SEFAZ rejeita; nos
# outros tres, um `xJust` a mais e rejeicao de schema.
EXIGEM_JUSTIFICATIVA = (NAO_REALIZADA,)

# Manifestacao do destinatario e sempre Ambiente Nacional — nao e a UF da
# empresa.
CORGAO_AMBIENTE_NACIONAL = '91'
VERSAO_EVENTO = '1.00'

# Cabe UM evento de cada tipo por NF-e: reenviar o mesmo tipo devolve cStat 573
# (duplicidade), que o fluxo trata como desfecho de sucesso. Nao ha sequencia a
# incrementar.
SEQUENCIA = 1

TP_AMB = {'producao': '1', 'homologacao': '2'}


class EventoError(Exception):
    """Nao da para montar o evento com o que foi informado."""


def _so_digitos(valor):
    return re.sub(r'\D', '', str(valor or ''))


def montar_evento(chave, cnpj_destinatario, tipo_evento=CONFIRMACAO,
                  ambiente='producao', justificativa=None, quando=None):
    """`<evento>` pronto para assinar, com `<infEvento>` como primeiro filho.

    A ordem dos campos NAO e cosmetica: o XSD da NF-e usa `sequence`, entao um
    campo fora de lugar e rejeicao de schema, nao aviso.

    O `Id` tem tamanho fixo de 54 (`ID` + tpEvento(6) + chNFe(44) +
    nSeqEvento(2)) e e ele que a `Reference` da assinatura aponta — formato
    medido, nao suposto (`recon.md`)."""
    if tipo_evento not in DESCRICOES:
        raise EventoError(
            f'Tipo de evento {tipo_evento!r} nao existe. Os validos sao: '
            f'{", ".join(sorted(DESCRICOES))}.')

    chave = _so_digitos(chave)
    if not dv_valido(chave):
        raise EventoError(
            'A chave de acesso nao passou no digito verificador — nao vou '
            'mandar para a SEFAZ uma chave que ja sei estar errada.')

    cnpj = _so_digitos(cnpj_destinatario)
    if len(cnpj) != 14:
        raise EventoError(
            'O CNPJ do destinatario precisa ter 14 digitos — e ele que a SEFAZ '
            'confere contra o certificado que assina o evento.')

    if tipo_evento in EXIGEM_JUSTIFICATIVA and not (justificativa or '').strip():
        raise EventoError(
            f'{DESCRICOES[tipo_evento]} exige justificativa; sem ela a SEFAZ '
            f'rejeita o evento.')

    tp_amb = TP_AMB.get(ambiente)
    if tp_amb is None:
        raise EventoError(
            f'Ambiente {ambiente!r} nao existe. Use: {", ".join(sorted(TP_AMB))}.')

    quando = quando or datetime.now().astimezone()

    raiz = ET.Element(f'{{{NS_NFE}}}evento', {'versao': VERSAO_EVENTO})
    inf = ET.SubElement(raiz, f'{{{NS_NFE}}}infEvento', {
        'Id': f'ID{tipo_evento}{chave}{SEQUENCIA:02d}'})

    for tag, valor in (
        ('cOrgao', CORGAO_AMBIENTE_NACIONAL),
        ('tpAmb', tp_amb),
        ('CNPJ', cnpj),
        ('chNFe', chave),
        # `isoformat` ja entrega `2026-08-17T14:25:33-03:00`, que e exatamente o
        # que a SEFAZ exige. `timespec` corta os microssegundos, que o schema
        # nao aceita.
        ('dhEvento', quando.isoformat(timespec='seconds')),
        ('tpEvento', tipo_evento),
        ('nSeqEvento', str(SEQUENCIA)),
        ('verEvento', VERSAO_EVENTO),
    ):
        ET.SubElement(inf, f'{{{NS_NFE}}}{tag}').text = valor

    det = ET.SubElement(inf, f'{{{NS_NFE}}}detEvento', {'versao': VERSAO_EVENTO})
    ET.SubElement(det, f'{{{NS_NFE}}}descEvento').text = DESCRICOES[tipo_evento]
    if tipo_evento in EXIGEM_JUSTIFICATIVA:
        ET.SubElement(det, f'{{{NS_NFE}}}xJust').text = justificativa.strip()

    return raiz


# --- a costura: manifestar UMA chave (MANIF-16..19) -------------------------

STATUS_MANIFESTAVEIS = ('pendente', 'rejeitada', 'indefinida')

# Teto de reenvios com a MESMA rejeicao. A SEFAZ bloqueia o CNPJ por 1 hora a
# partir de 20 (NT 2018.002); paramos MUITO antes porque insistir 20 vezes na
# mesma recusa nunca foi util — se a rejeicao nao mudou, o proximo envio tambem
# nao vai mudar. O teto protege sobretudo o botao "reprocessar", que antes nao
# tinha limite nenhum.
TETO_REENVIOS = 3


class Resultado:
    """Desfecho de uma manifestacao, do ponto de vista de quem chamou."""

    def __init__(self, sucesso, mensagem, resposta=None):
        self.sucesso = sucesso
        self.mensagem = mensagem
        self.cstat = getattr(resposta, 'cstat', None)
        self.xmotivo = getattr(resposta, 'xmotivo', None)
        self.protocolo = getattr(resposta, 'protocolo', None)
        self.ja_existia = bool(getattr(resposta, 'duplicidade', False))
        self.indefinido = bool(getattr(resposta, 'indefinido', False))
        self.consumo_indevido = bool(getattr(resposta, 'consumo_indevido', False))

    def __repr__(self):
        return (f'<Resultado sucesso={self.sucesso} cstat={self.cstat} '
                f'indefinido={self.indefinido}>')


def manifestavel(chave_linha):
    """A chave pode ser manifestada agora? **Regra unica** do fluxo.

    Consumida pela rota individual E pela fila do lote. Duas copias
    divergiriam, e a divergencia apareceria como fila que enfileira o que o
    servico recusa — travando sem explicacao.

    Chave que ja bateu no `TETO_REENVIOS` sai da fila: insistir na mesma recusa
    caminha para o bloqueio do CNPJ e nunca produziu desfecho diferente."""
    if chave_linha is None:
        return False
    if chave_linha.status not in STATUS_MANIFESTAVEIS:
        return False
    return (chave_linha.tentativas or 0) < TETO_REENVIOS


def _gravar_desfecho(linha, resposta):
    """Traduz a resposta da SEFAZ em estado persistido. Devolve (sucesso, msg).

    A ordem dos ramos importa: `indefinido` e testado ANTES de qualquer
    conclusao sobre `cStat`, porque nele nao ha cStat nenhum para interpretar —
    e concluir alguma coisa ali seria justamente o chute que o desenho evita."""
    from app import db
    from app.models import StatusManifestacao

    cstat_anterior = linha.cstat
    linha.cstat = resposta.cstat
    linha.xmotivo = (resposta.xmotivo or resposta.erro or '')[:255] or None

    if resposta.consumo_indevido:
        # O evento NAO foi avaliado: a SEFAZ recusou a requisicao. A chave volta
        # a fila intacta — mas quem chamou tem de parar o lote (ver
        # `manifestador_lote`), porque insistir prolonga o bloqueio.
        linha.status = StatusManifestacao.PENDENTE
        db.session.commit()
        return False, (
            'A SEFAZ bloqueou este CNPJ por consumo indevido (656). O bloqueio '
            'dura 1 hora e INSISTIR REINICIA a contagem. A chave continua na '
            'fila; retome depois.')

    if resposta.indefinido:
        linha.status = StatusManifestacao.INDEFINIDA
        mensagem = (f'Enviei a manifestacao da chave {linha.chave} e nao recebi '
                    f'a resposta. Confira no portal da NF-e se ela saiu antes de '
                    f'tentar de novo.')
    elif resposta.registrado or resposta.duplicidade:
        linha.status = StatusManifestacao.MANIFESTADA
        linha.tentativas = 0
        linha.ja_existia = resposta.duplicidade
        linha.protocolo = resposta.protocolo
        linha.manifestado_em = datetime.now()
        mensagem = ('Ja estava manifestada na SEFAZ.' if resposta.duplicidade
                    else f'Manifestada. Protocolo {resposta.protocolo}.')
    elif resposta.cstat:
        linha.status = StatusManifestacao.REJEITADA
        # Conta reenvios com a MESMA rejeicao; rejeicao diferente zera, porque o
        # problema passou a ser outro e a contagem velha nao diz nada sobre ele.
        linha.tentativas = (linha.tentativas + 1
                            if resposta.cstat == cstat_anterior else 1)
        mensagem = f'Recusada pela SEFAZ ({resposta.cstat}): {resposta.xmotivo}'
    else:
        # nao houve resposta e nao houve envio: o pedido nao chegou a sair
        linha.status = StatusManifestacao.PENDENTE
        mensagem = f'Nao consegui falar com a SEFAZ: {resposta.erro}'

    db.session.commit()
    sucesso = linha.status == StatusManifestacao.MANIFESTADA
    return sucesso, mensagem


def manifestar(chave_id, tipo_evento=CONFIRMACAO, justificativa=None,
               ambiente=None, execution_id=None):
    """Manifesta UMA chave e grava o desfecho. Nunca levanta.

    E a unica porta entre o sistema e a SEFAZ (a costura do design). Trocar o
    webservice pelo portal — a contingencia da spec — substituiria o miolo desta
    funcao e nada mais."""
    from app import db
    from app.models import ChaveManifestacao, StatusManifestacao
    from app.services import auditoria, manifestador_cofre
    from app.services.nfe_sefaz import Credencial, SefazError, ambiente_atual

    linha = db.session.get(ChaveManifestacao, chave_id)
    if linha is None:
        return Resultado(False, f'Chave {chave_id} nao existe mais.')

    if not manifestavel(linha):
        return Resultado(
            False, f'A chave {linha.chave} esta como "{linha.status}" e nao '
                   f'entra na fila. Libere-a antes de manifestar de novo.')

    empresa = linha.empresa
    credencial_bruta = manifestador_cofre.credencial(empresa)
    if credencial_bruta is None:
        # Recusa ANTES de qualquer rede: certificado ausente ou vencido e
        # estado de negocio, e o pre-voo existe justamente para isso nao
        # aparecer no meio de um lote.
        estado = getattr(empresa.certificado, 'estado', 'sem_arquivo')
        return Resultado(
            False, f'{empresa.nome} esta com o certificado "{estado}". '
                   f'Resolva no pre-voo do cofre antes de manifestar.')

    caminho, senha = credencial_bruta
    ambiente = ambiente or ambiente_atual()

    # Monta e assina. Falha aqui e ANTES do envio: nada saiu, a chave continua
    # na fila e repetir e seguro.
    try:
        info = manifestador_cofre.carregar_pfx(caminho, senha.encode() or None)
        if info is None or info.chave_privada is None:
            raise SefazError(
                f'Nao consegui abrir o certificado de {empresa.nome}. Confira a '
                f'senha no cofre.')
        evento = montar_evento(
            chave=linha.chave, cnpj_destinatario=empresa.cnpj,
            tipo_evento=tipo_evento, ambiente=ambiente,
            justificativa=justificativa)
        assinar(evento, evento.find(f'{{{NS_NFE}}}infEvento'),
                info.chave_privada, info.certificado)
    except (EventoError, SefazError, AssinaturaError) as exc:
        log_event('manifestador_preparo_falhou', level='ERROR',
                  chave=linha.chave, error=str(exc), execution_id=execution_id)
        return Resultado(False, str(exc))

    linha.status = StatusManifestacao.ENVIANDO
    linha.tipo_evento = tipo_evento
    db.session.commit()

    try:
        resposta = enviar_evento(
            evento, credencial=Credencial(caminho=caminho, senha=senha),
            ambiente=ambiente)
    except Exception as exc:
        # `enviar_evento` nao deveria levantar por rede; se levantou, e defeito
        # nosso — e nao da para saber se o evento saiu. INDEFINIDA em vez de
        # PENDENTE porque um falso "pendente" reenviaria um evento protocolado,
        # e o preco de um falso "indefinida" e uma conferencia manual.
        linha.status = StatusManifestacao.INDEFINIDA
        linha.xmotivo = str(exc)[:255]
        db.session.commit()
        log_event('manifestador_envio_estourou', level='ERROR',
                  chave=linha.chave, error=str(exc), execution_id=execution_id)
        return Resultado(
            False, f'Erro inesperado ao enviar a chave {linha.chave}. Confira '
                   f'no portal se a manifestacao saiu.')

    sucesso, mensagem = _gravar_desfecho(linha, resposta)

    auditoria.registrar(
        'manifestacao', alvo_tipo='chave_manifestacao', alvo_id=linha.id,
        resultado='ok' if sucesso else 'erro',
        detalhe=(f'chave={linha.chave} evento={tipo_evento} '
                 f'cStat={resposta.cstat} prot={resposta.protocolo} '
                 f'empresa={empresa.nome}'))
    log_event('manifestador_desfecho', chave=linha.chave, cstat=resposta.cstat,
              status=linha.status, execution_id=execution_id)

    return Resultado(sucesso, mensagem, resposta)
