"""Cliente restrito testado somente com transporte sintético."""
from contextlib import contextmanager
import base64
from datetime import datetime, timezone
import gzip
import json

import pytest

from app.services import nfse_api_dps, nfse_api_sefin, nfse_api_transporte
from tests.test_nfse_api_dps import (
    _config, _credencial_sintetica, _xml_referencia, _nota,
)


ID_DPS = 'DPS4310330' + '1' + '11222333000181' + '00007' + '12'.zfill(15)
CHAVE_SINTETICA = 'NFS' + '0' * 50


class SessaoSintetica:
    pass


def _dps_assinada(tp_ambiente='2'):
    referencia = nfse_api_dps.ler_referencia(_nota(), _xml_referencia())
    dps = nfse_api_dps.montar(
        referencia, _config(), serie='7', numero=12,
        agora=datetime(2026, 9, 12, 14, 35, 20, tzinfo=timezone.utc))
    dps.find(f'.//{{{nfse_api_dps.NAMESPACE_NFSE}}}tpAmb').text = tp_ambiente
    chave, certificado = _credencial_sintetica()
    return nfse_api_dps.assinar(dps, chave, certificado)


def _resposta_sucesso(incluir_xml=True, **valores):
    dados = {
        'tipoAmbiente': 2,
        'versaoAplicativo': 'SEFIN sintética',
        'dataHoraProcessamento': '2026-09-12T14:35:21-03:00',
        'idDps': ID_DPS,
        'chaveAcesso': CHAVE_SINTETICA,
    }
    if incluir_xml:
        dados['nfseXmlGZipB64'] = base64.b64encode(
            gzip.compress(_xml_referencia(), mtime=0)).decode('ascii')
    dados.update(valores)
    return json.dumps(dados, ensure_ascii=False)


def _preparar_transporte(monkeypatch, desfecho):
    chamadas = []

    @contextmanager
    def sessao_falsa(credencial):
        assert credencial == 'credencial-sintetica'
        yield SessaoSintetica()

    def chamar_falso(metodo, url, **kwargs):
        chamadas.append((metodo, url, kwargs))
        return desfecho

    monkeypatch.setattr(
        nfse_api_sefin.nfse_api_credencial,
        'credencial_do_escritorio',
        lambda: 'credencial-sintetica')
    monkeypatch.setattr(
        nfse_api_sefin.nfse_api_transporte, 'sessao', sessao_falsa)
    monkeypatch.setattr(
        nfse_api_sefin.nfse_api_transporte, 'chamar', chamar_falso)
    return chamadas


def test_enviar_restrita_monta_envelope_exato_e_parseia_gerada(monkeypatch):
    chamadas = _preparar_transporte(monkeypatch, nfse_api_transporte.Desfecho(
        'ok', 201, _resposta_sucesso(), 'sucesso sintético'))

    resultado = nfse_api_sefin.enviar_restrita(_dps_assinada())

    assert resultado.situacao == 'gerado_teste'
    assert resultado.identificador == ID_DPS
    assert resultado.chave_acesso == CHAVE_SINTETICA
    assert resultado.xml_nfse.startswith(b'<NFSe')
    assert len(chamadas) == 1
    metodo, url, kwargs = chamadas[0]
    assert metodo == 'POST'
    assert url == 'https://sefin.producaorestrita.nfse.gov.br/SefinNacional/nfse'
    assert kwargs['tentativas'] == 1
    corpo = kwargs['json']
    assert set(corpo) == {'dpsXmlGZipB64'}
    xml_enviado = gzip.decompress(base64.b64decode(corpo['dpsXmlGZipB64']))
    assert b'<DPS' in xml_enviado
    assert ID_DPS.encode() in xml_enviado


def test_enviar_restrita_recusa_tpamb_um_antes_do_http(monkeypatch):
    chamadas = _preparar_transporte(monkeypatch, nfse_api_transporte.Desfecho(
        'ok', 201, _resposta_sucesso(), 'sucesso sintético'))

    with pytest.raises(nfse_api_sefin.NfseApiSefinError):
        nfse_api_sefin.enviar_restrita(_dps_assinada(tp_ambiente='1'))

    assert chamadas == []


def test_destino_de_producao_e_recusado_antes_da_sessao(monkeypatch):
    chamadas = _preparar_transporte(monkeypatch, nfse_api_transporte.Desfecho(
        'ok', 201, _resposta_sucesso(), 'sucesso sintético'))
    monkeypatch.setattr(
        nfse_api_sefin.nfse_api_transporte, 'url_de',
        lambda *_args: 'https://sefin.nfse.gov.br/SefinNacional/nfse')

    with pytest.raises(nfse_api_sefin.NfseApiSefinError):
        nfse_api_sefin.enviar_restrita(_dps_assinada())

    assert chamadas == []


@pytest.mark.parametrize(
    ('desfecho', 'situacao'),
    [
        (nfse_api_transporte.Desfecho(
            'rejeitado', 400,
            json.dumps({'erros': [{'codigo': 'E-SINT',
                                   'descricao': 'Rejeição sintética'}]}),
            'rejeitado sintético'), 'rejeitado'),
        (nfse_api_transporte.Desfecho('negado', 403, '{}', 'negado sintético'),
         'credencial'),
        (nfse_api_transporte.Desfecho(
            'indisponivel', 503, '', 'indisponível sintético'), 'indisponivel'),
    ],
)
def test_enviar_restrita_nomeia_rejeicao_credencial_e_indisponibilidade(
        monkeypatch, desfecho, situacao):
    _preparar_transporte(monkeypatch, desfecho)

    resultado = nfse_api_sefin.enviar_restrita(_dps_assinada())

    assert resultado.situacao == situacao
    if situacao == 'rejeitado':
        assert resultado.codigo == 'E-SINT'


def test_enviar_restrita_recusa_resposta_201_malformada(monkeypatch):
    _preparar_transporte(monkeypatch, nfse_api_transporte.Desfecho(
        'ok', 201, '{"tipoAmbiente": 2}', 'malformada sintética'))

    resultado = nfse_api_sefin.enviar_restrita(_dps_assinada())

    assert resultado.situacao == 'resposta_invalida'


def test_enviar_restrita_nao_trata_http_200_como_gerada(monkeypatch):
    _preparar_transporte(monkeypatch, nfse_api_transporte.Desfecho(
        'ok', 200, _resposta_sucesso(), 'status inesperado sintético'))

    resultado = nfse_api_sefin.enviar_restrita(_dps_assinada())

    assert resultado.situacao == 'resposta_invalida'


@pytest.mark.parametrize(
    ('status', 'corpo', 'situacao'),
    [
        (200, _resposta_sucesso(incluir_xml=False), 'gerado_teste'),
        (404, '{}', 'nao_encontrado'),
        (500, '', None),
    ],
)
def test_consultar_restrita_distingue_existente_ausente_e_inconclusiva(
        monkeypatch, status, corpo, situacao):
    chamadas = _preparar_transporte(monkeypatch, nfse_api_transporte.Desfecho(
        'ok' if status == 200 else 'rejeitado' if status == 404 else 'indisponivel',
        status, corpo, 'consulta sintética'))

    resultado = nfse_api_sefin.consultar_dps_restrita(ID_DPS)

    if situacao is None:
        assert resultado is None
    else:
        assert resultado.situacao == situacao
    assert chamadas[0][0] == 'GET'
    assert chamadas[0][1].endswith('/dps/' + ID_DPS)


def test_consultar_restrita_recusa_id_invalido_sem_http(monkeypatch):
    chamadas = _preparar_transporte(monkeypatch, nfse_api_transporte.Desfecho(
        'ok', 200, _resposta_sucesso(incluir_xml=False), 'sintético'))

    with pytest.raises(nfse_api_sefin.NfseApiSefinError):
        nfse_api_sefin.consultar_dps_restrita('DPS-invalido')

    assert chamadas == []


def test_consultar_restrita_parseia_o_erro_singular_do_openapi(monkeypatch):
    corpo = json.dumps({
        'erro': {'codigo': 'E-SINT', 'descricao': 'Consulta sintética rejeitada'},
    })
    _preparar_transporte(monkeypatch, nfse_api_transporte.Desfecho(
        'rejeitado', 400, corpo, 'rejeição sintética'))

    resultado = nfse_api_sefin.consultar_dps_restrita(ID_DPS)

    assert resultado.situacao == 'rejeitado'
    assert resultado.codigo == 'E-SINT'


def test_operacao_sem_credencial_nao_abre_sessao_nem_faz_http(monkeypatch):
    chamadas = []
    monkeypatch.setattr(
        nfse_api_sefin.nfse_api_credencial,
        'credencial_do_escritorio', lambda: None)
    monkeypatch.setattr(
        nfse_api_sefin.nfse_api_transporte, 'chamar',
        lambda *args, **kwargs: chamadas.append((args, kwargs)))

    resultado = nfse_api_sefin.consultar_dps_restrita(ID_DPS)

    assert resultado.situacao == 'credencial'
    assert chamadas == []
