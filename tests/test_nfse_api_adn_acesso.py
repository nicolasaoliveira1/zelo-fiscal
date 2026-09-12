from contextlib import contextmanager

import pytest

from app import db
from app.models import ConfiguracaoNfse
from app.services import nfse_api_adn as adn
from app.services import nfse_api_credencial
from app.services import nfse_api_transporte as transporte
from app.services.pem_temporario import Credencial


def _configurar(app, ids, codigo='1234567'):
    with app.app_context():
        db.session.add(ConfiguracaoNfse(
            id=1,
            empresa_escritorio_id=ids['empresa'],
            municipio_servico_codigo=codigo,
        ))
        db.session.commit()


def _preparar_sondagem(monkeypatch, app, ids):
    _configurar(app, ids)
    monkeypatch.setattr(
        nfse_api_credencial, 'diagnostico',
        lambda: nfse_api_credencial.Diagnostico(True, 'ok'))
    monkeypatch.setattr(
        nfse_api_credencial, 'credencial_do_escritorio',
        lambda: Credencial('certificado-sintetico.pfx', 'senha-sintetica'))

    @contextmanager
    def sessao_falsa(_credencial):
        yield 'sessao-sintetica'

    monkeypatch.setattr(adn.transporte, 'sessao', sessao_falsa)


def test_verificar_acesso_mantem_sefin_e_adn_independentes(
        monkeypatch, app, ids):
    _preparar_sondagem(monkeypatch, app, ids)
    respostas = iter([
        transporte.Desfecho('ok', 200, 'convenio-sintetico', 'ok'),
        transporte.Desfecho('negado', 403, 'adn-negado', 'negado'),
    ])
    chamadas = []
    monkeypatch.setattr(
        adn.transporte, 'chamar',
        lambda metodo, url, **kwargs: chamadas.append((metodo, url, kwargs))
        or next(respostas))

    with app.app_context():
        acessos = adn.verificar_acesso()

    assert acessos.sefin.situacao == 'ok'
    assert acessos.sefin.http == 200
    assert acessos.adn.situacao == 'negado'
    assert acessos.adn.http == 403
    assert len(chamadas) == 2


@pytest.mark.parametrize(
    ('situacao_sefin', 'situacao_adn'),
    [
        ('indisponivel', 'ok'),
        ('rejeitado', 'indisponivel'),
        ('credencial', 'rejeitado'),
    ],
)
def test_verificar_acesso_preserva_desfechos_de_cada_sondagem(
        monkeypatch, app, ids, situacao_sefin, situacao_adn):
    _preparar_sondagem(monkeypatch, app, ids)
    respostas = iter([
        transporte.Desfecho(situacao_sefin, None, '', situacao_sefin),
        transporte.Desfecho(situacao_adn, None, '', situacao_adn),
    ])
    monkeypatch.setattr(
        adn.transporte, 'chamar', lambda *args, **kwargs: next(respostas))

    with app.app_context():
        acessos = adn.verificar_acesso()

    assert acessos.sefin.situacao == situacao_sefin
    assert acessos.adn.situacao == situacao_adn


def test_verificar_acesso_fixa_lote_false_e_municipio_da_configuracao(
        monkeypatch, app, ids):
    _preparar_sondagem(monkeypatch, app, ids)
    chamadas = []

    def chamar_falso(metodo, url, **kwargs):
        chamadas.append((metodo, url, kwargs))
        return transporte.Desfecho('ok', 200, '', 'ok')

    monkeypatch.setattr(adn.transporte, 'chamar', chamar_falso)
    with app.app_context():
        adn.verificar_acesso()

    assert chamadas[0][0] == 'GET'
    assert chamadas[0][1].endswith('/parametros_municipais/1234567/convenio')
    assert chamadas[1][0] == 'GET'
    assert chamadas[1][1].endswith('/DFe/0')
    assert chamadas[1][2]['params'] == {'lote': 'false'}
    assert chamadas[1][2]['params']['lote'] != 'true'


def test_verificar_acesso_sem_credencial_nao_toca_o_transporte(
        monkeypatch, app, ids):
    with app.app_context():
        # Configuração ausente representa o estado suportado sem empresa.
        pass

    monkeypatch.setattr(
        nfse_api_credencial, 'diagnostico',
        lambda: nfse_api_credencial.Diagnostico(False, 'sem_empresa'))
    monkeypatch.setattr(
        nfse_api_credencial, 'credencial_do_escritorio',
        lambda: pytest.fail('não deve resolver credencial ausente'))
    monkeypatch.setattr(
        adn.transporte, 'sessao',
        lambda *_args, **_kwargs: pytest.fail('não deve abrir sessão'))
    monkeypatch.setattr(
        adn.transporte, 'chamar',
        lambda *_args, **_kwargs: pytest.fail('não deve chamar a API'))

    with app.app_context():
        acessos = adn.verificar_acesso()

    assert acessos.sefin.situacao == 'credencial'
    assert acessos.adn.situacao == 'credencial'
    assert acessos.sefin.http is None
    assert acessos.adn.http is None


def test_verificar_acesso_registra_situacao_e_http_sem_credencial(
        monkeypatch, app, ids):
    _preparar_sondagem(monkeypatch, app, ids)
    respostas = iter([
        transporte.Desfecho('ok', 204, '', 'ok'),
        transporte.Desfecho('rejeitado', 400, 'erro', 'rejeitado'),
    ])
    monkeypatch.setattr(
        adn.transporte, 'chamar', lambda *args, **kwargs: next(respostas))
    eventos = []
    monkeypatch.setattr(
        adn, 'log_event',
        lambda evento, **campos: eventos.append((evento, campos)))

    with app.app_context():
        adn.verificar_acesso()

    assert eventos == [
        ('nfse_api_acesso', {
            'servico': 'sefin', 'situacao': 'ok', 'http': 204}),
        ('nfse_api_acesso', {
            'servico': 'adn', 'situacao': 'rejeitado', 'http': 400}),
    ]
    assert all('senha' not in campos and 'certificado' not in campos
               for _evento, campos in eventos)
