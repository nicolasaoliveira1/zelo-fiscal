r"""Orquestracao de UMA manifestacao (MANIF-12, MANIF-16..19).

Esta e a **costura** do design: todo o resto do sistema fala com a SEFAZ por
aqui. E o que torna barata a contingencia do portal — trocar webservice por
Selenium substitui a implementacao de `manifestar` e mais nada; modelo, import,
cofre, UI, lote e auditoria continuam iguais.

Neste passo (T9) mora so a montagem do XML do evento.
"""
import re
import xml.etree.ElementTree as ET
from datetime import datetime

from app.services.manifestador_import import dv_valido

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
