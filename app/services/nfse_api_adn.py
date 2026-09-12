"""Provas de acesso aos serviços nacionais de NFS-e.

A verificação é uma sondagem controlada: uma chamada à SEFIN para o convênio
do município e uma consulta pontual ao ADN. Ela não percorre histórico e não
confunde a autorização de um serviço com a do outro.
"""

from contextlib import ExitStack
from dataclasses import dataclass

from app import db
from app.models import ConfiguracaoNfse
from app.services import nfse_api_credencial
from app.services import nfse_api_transporte as transporte
from app.services.execution_logger import log_event


@dataclass(frozen=True)
class Acessos:
    """Desfechos independentes da SEFIN e do ADN."""

    sefin: transporte.Desfecho
    adn: transporte.Desfecho


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
