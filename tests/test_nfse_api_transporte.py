from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest
import requests

from app import db
from app.models import ConfiguracaoNfse
from app.services import nfse_api_transporte as transporte
from app.services.pem_temporario import PemTemporarioError


class RespostaFalsa:
    def __init__(self, status_code, corpo='corpo-sintetico'):
        self.status_code = status_code
        self.text = corpo


class SessaoFalsa:
    def __init__(self, respostas=None, excecao=None):
        self.respostas = iter(respostas or [])
        self.excecao = excecao
        self.chamadas = []

    def request(self, metodo, url, **kwargs):
        self.chamadas.append((metodo, url, kwargs))
        if self.excecao:
            raise self.excecao
        return next(self.respostas)


def test_ambiente_atual_ler_configuracao_e_usar_restrita_como_default(app, ids):
    with app.app_context():
        assert transporte.ambiente_atual() == 'restrita'
        db.session.add(ConfiguracaoNfse(id=1, api_ambiente='producao'))
        db.session.commit()
        assert transporte.ambiente_atual() == 'producao'


@pytest.mark.parametrize('ambiente', transporte.AMBIENTES)
def test_url_de_separa_ambientes_e_servicos_oficiais(ambiente):
    assert transporte.url_de('parametros_convenio', ambiente).endswith(
        '/parametros_municipais')
    assert transporte.url_de('adn_dfe', ambiente).endswith('/DFe')
    assert transporte.url_de('nfse', ambiente).endswith('/nfse')
    assert transporte.url_de('nfse_chave', ambiente).endswith('/nfse')
    assert transporte.url_de('dps', ambiente).endswith('/dps')
    assert transporte.url_de('eventos', ambiente).endswith('/nfse')

    if ambiente == 'producao':
        assert 'producaorestrita' not in transporte.url_de('adn_dfe', ambiente)
        assert transporte.url_de('adn_dfe', ambiente) == (
            'https://adn.nfse.gov.br/contribuintes/DFe')
    else:
        assert transporte.url_de('adn_dfe', ambiente) == (
            'https://adn.producaorestrita.nfse.gov.br/contribuintes/DFe')


def test_url_de_recusa_ambiente_e_servico_desconhecidos():
    with pytest.raises(transporte.NfseApiTransporteError):
        transporte.url_de('nfse', 'homologacao')
    with pytest.raises(transporte.NfseApiTransporteError):
        transporte.url_de('inexistente', 'restrita')


def test_sessao_configura_mtls_timeout_e_fecha_a_sessao(monkeypatch):
    caminho_pem = 'pem-sintetico.pem'
    sessoes = []

    @contextmanager
    def pem_falso(credencial):
        assert credencial == 'credencial-sintetica'
        yield caminho_pem

    class SessaoDeRequestsFalsa:
        def __init__(self):
            self.headers = {}
            self.cert = None
            self.request_timeout = None
            self.fechada = False
            sessoes.append(self)

        def close(self):
            self.fechada = True

    monkeypatch.setattr(transporte, 'pem_temporario', pem_falso)
    monkeypatch.setattr(transporte.requests, 'Session', SessaoDeRequestsFalsa)

    with transporte.sessao('credencial-sintetica', timeout=17) as conexao:
        assert conexao.cert == caminho_pem
        assert conexao.headers['User-Agent'] == transporte.USER_AGENT
        assert conexao.request_timeout == 17
        assert conexao.fechada is False
    assert sessoes[0].fechada is True


def test_sessao_traduz_falha_ao_preparar_certificado(monkeypatch):
    @contextmanager
    def pem_falho(_credencial):
        raise PemTemporarioError('falha sintetica')
        yield

    monkeypatch.setattr(transporte, 'pem_temporario', pem_falho)
    with pytest.raises(transporte.NfseApiTransporteError):
        with transporte.sessao('credencial-sintetica'):
            pass


@pytest.mark.parametrize(
    ('status', 'situacao'),
    [
        (200, 'ok'),
        (201, 'ok'),
        (401, 'negado'),
        (403, 'negado'),
        (400, 'rejeitado'),
        (404, 'rejeitado'),
        (500, 'indisponivel'),
    ],
)
def test_chamar_classifica_respostas_http(status, situacao):
    sessao = SessaoFalsa([RespostaFalsa(status)])
    desfecho = transporte.chamar('get', 'https://api-sintetica.test', sessao=sessao)
    assert desfecho.situacao == situacao
    assert desfecho.http == status
    assert desfecho.corpo == 'corpo-sintetico'
    assert sessao.chamadas[0][0:2] == ('GET', 'https://api-sintetica.test')


@pytest.mark.parametrize(
    'excecao',
    [
        requests.exceptions.Timeout(),
        requests.exceptions.ConnectionError(),
        requests.exceptions.RequestException(),
    ],
)
def test_chamar_nunca_vaza_falha_de_rede(excecao):
    sessao = SessaoFalsa(excecao=excecao)
    desfecho = transporte.chamar('post', 'https://api-sintetica.test', sessao=sessao)
    assert desfecho.situacao == 'indisponivel'
    assert desfecho.http is None


def test_chamar_classifica_falha_de_certificado():
    sessao = SessaoFalsa(excecao=requests.exceptions.SSLError())
    desfecho = transporte.chamar('get', 'https://api-sintetica.test', sessao=sessao)
    assert desfecho.situacao == 'credencial'
    assert desfecho.http is None


def test_chamar_reexecuta_429_e_503_com_mesma_requisicao(monkeypatch):
    sessao = SessaoFalsa([
        RespostaFalsa(429),
        RespostaFalsa(503),
        RespostaFalsa(200, 'sucesso-sintetico'),
    ])
    intervalos = []
    monkeypatch.setattr(transporte.time, 'sleep', intervalos.append)

    desfecho = transporte.chamar(
        'post', 'https://api-sintetica.test', sessao=sessao,
        tentativas=3, backoff_base=0.25, json={'id': 'documento-sintetico'})

    assert desfecho == transporte.Desfecho('ok', 200, 'sucesso-sintetico',
                                           'Chamada concluída.')
    assert len(sessao.chamadas) == 3
    assert {(metodo, url) for metodo, url, _ in sessao.chamadas} == {
        ('POST', 'https://api-sintetica.test')}
    assert [chamada[2]['json'] for chamada in sessao.chamadas] == [
        {'id': 'documento-sintetico'}] * 3
    assert intervalos == [0.25, 0.5]


def test_chamar_retorna_indisponivel_ao_esgotar_retry(monkeypatch):
    sessao = SessaoFalsa([RespostaFalsa(503), RespostaFalsa(503)])
    intervalos = []
    monkeypatch.setattr(transporte.time, 'sleep', intervalos.append)

    desfecho = transporte.chamar(
        'get', 'https://api-sintetica.test', sessao=sessao,
        tentativas=2, backoff_base=0)

    assert desfecho.situacao == 'indisponivel'
    assert desfecho.http == 503
    assert len(sessao.chamadas) == 2
    assert intervalos == [0]


def test_chamar_serializa_requisicoes_concorrentes():
    estado = {'ativas': 0, 'maximo': 0}
    trava_estado = threading.Lock()

    class SessaoSerializada:
        def request(self, _metodo, _url, **_kwargs):
            with trava_estado:
                estado['ativas'] += 1
                estado['maximo'] = max(estado['maximo'], estado['ativas'])
            time.sleep(0.005)
            with trava_estado:
                estado['ativas'] -= 1
            return RespostaFalsa(200)

    with ThreadPoolExecutor(max_workers=8) as executor:
        resultados = list(executor.map(
            lambda _indice: transporte.chamar(
                'get', 'https://api-sintetica.test',
                sessao=SessaoSerializada()),
            range(8)))

    assert estado['maximo'] == 1
    assert {resultado.situacao for resultado in resultados} == {'ok'}
