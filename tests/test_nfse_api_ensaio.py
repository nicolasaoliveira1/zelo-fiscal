"""Preparação local do ensaio restrito com dados e certificado sintéticos."""
import json
from concurrent.futures import ThreadPoolExecutor
import threading
import xml.etree.ElementTree as ET

import pytest

from app import db
from app.models import (
    ConfiguracaoNfse,
    ContadorDpsNfse,
    Empresa,
    EnsaioDpsNfse,
    LoteNfse,
    NotaEmitidaNfse,
    NotaNfse,
    Usuario,
)
from app.services import nfse_api_dps, nfse_api_ensaio, nfse_api_sefin
from tests.test_nfse_api_dps import (
    CHAVE_SINTETICA,
    _credencial_sintetica,
    _xml_referencia,
    TOMADOR_SINTETICO,
)


NS = nfse_api_dps.NAMESPACE_NFSE


def _resultado_nao_encontrado(identificador):
    return nfse_api_sefin.ResultadoRestrito(
        'nao_encontrado', 404, identificador=identificador)


def _resultado_gerado(identificador, incluir_xml=True):
    return nfse_api_sefin.ResultadoRestrito(
        'gerado_teste',
        200,
        identificador=identificador,
        chave_acesso=CHAVE_SINTETICA,
        xml_nfse=_xml_referencia() if incluir_xml else None,
    )


def _resultado_rejeitado(identificador):
    return nfse_api_sefin.ResultadoRestrito(
        'rejeitado',
        400,
        identificador=identificador,
        codigo='E-SINT',
        motivo='Rejeição sintética',
    )


def _entrada(app, ids):
    with app.app_context():
        empresa = Empresa(
            nome='Escritório Sintético',
            cnpj='11.222.333/0001-81',
            estado='RS',
            cidade='Cidade Sintética',
        )
        lote = LoteNfse(total=1)
        nota = NotaNfse(
            lote=lote,
            status='emitida',
            documento='44.556.677/0001-86',
            competencia='09/2026',
            descricao_servico='SERVIÇO SINTÉTICO DE ENSAIO',
        )
        db.session.add_all([empresa, lote, nota])
        db.session.flush()
        config = ConfiguracaoNfse(
            id=1,
            empresa_escritorio_id=empresa.id,
            serie_dps_restrita='7',
        )
        espelho = NotaEmitidaNfse(
            chave=CHAVE_SINTETICA[3:],
            nota_id=nota.id,
            documento=TOMADOR_SINTETICO,
            situacao_fiscal='gerada',
        )
        db.session.add_all([config, espelho])
        db.session.commit()
        operador = db.session.query(Usuario).filter_by(papel='admin').first()
        return nota.id, operador.id


def _injetar_fontes(monkeypatch, xml=None):
    chamadas = []
    monkeypatch.setattr(
        nfse_api_ensaio,
        'obter_xml_referencia',
        lambda nota, espelho: (
            chamadas.append((nota.id, espelho.id)),
            _xml_referencia() if xml is None else xml,
        )[1],
    )
    monkeypatch.setattr(
        nfse_api_ensaio,
        'obter_material_assinatura',
        _credencial_sintetica,
    )
    monkeypatch.setattr(
        nfse_api_sefin,
        'enviar_restrita',
        lambda *_args, **_kwargs: pytest.fail(
            'preparar não pode chamar o POST restrito'),
    )
    return chamadas


def test_preparar_persiste_provas_locais_sem_post_e_preserva_a_nota(
        app, ids, monkeypatch):
    nota_id, operador_id = _entrada(app, ids)
    chamadas = _injetar_fontes(monkeypatch)

    with app.app_context():
        ensaio = nfse_api_ensaio.preparar(
            nota_id, operador_id=operador_id)
        recarregado = db.session.get(EnsaioDpsNfse, ensaio.id)
        nota = db.session.get(NotaNfse, nota_id)
        dps = ET.fromstring(recarregado.xml_dps_assinada)
        inf_dps = dps.find(f'{{{NS}}}infDPS')
        comparacao = json.loads(recarregado.comparacao_json)

        assert recarregado.estado == nfse_api_ensaio.ESTADO_PREPARADO
        assert recarregado.ambiente == nfse_api_ensaio.AMBIENTE_RESTRITA
        assert recarregado.numero == 1
        assert recarregado.identificador_dps.startswith('DPS4310330')
        assert recarregado.xml_referencia.startswith('<NFSe')
        assert nfse_api_dps.verificar(dps) is True
        assert recarregado.identificador_dps == nfse_api_dps.identificador(dps)
        assert inf_dps.find(f'{{{NS}}}tpAmb').text == '2'
        assert inf_dps.find(f'{{{NS}}}dCompet').text == '2026-06-17'
        assert comparacao['pode_enviar'] is True
        assert comparacao['bloqueadoras'] == []
        assert nota.status == 'emitida'
        assert db.session.query(ContadorDpsNfse).one().proximo_numero == 2
        assert len(chamadas) == 1


def test_falha_na_validacao_local_consume_numero_e_persiste_falha(
        app, ids, monkeypatch):
    nota_id, operador_id = _entrada(app, ids)
    _injetar_fontes(monkeypatch)
    monkeypatch.setattr(
        nfse_api_dps,
        'validar',
        lambda _dps: [nfse_api_dps.ProblemaDps(
            '/DPS/infDPS/serv', 'campo sintético inválido')],
    )

    with app.app_context():
        ensaio = nfse_api_ensaio.preparar(
            nota_id, operador_id=operador_id)
        recarregado = db.session.get(EnsaioDpsNfse, ensaio.id)

        assert recarregado.estado == nfse_api_ensaio.ESTADO_FALHA_PREPARACAO
        assert '/DPS/infDPS/serv' in recarregado.ultima_falha
        assert recarregado.xml_dps_assinada is None
        assert db.session.query(ContadorDpsNfse).one().proximo_numero == 2
        assert db.session.query(NotaNfse).filter_by(
            id=nota_id).one().status == 'emitida'


def test_divergencia_fiscal_fica_persistida_e_bloqueia_a_preparacao(
        app, ids, monkeypatch):
    nota_id, operador_id = _entrada(app, ids)
    _injetar_fontes(monkeypatch)
    montar_original = nfse_api_dps.montar

    def montar_divergente(*args, **kwargs):
        dps = montar_original(*args, **kwargs)
        dps.find(f'.//{{{NS}}}cTribNac').text = '171902'
        return dps

    monkeypatch.setattr(nfse_api_dps, 'montar', montar_divergente)

    with app.app_context():
        ensaio = nfse_api_ensaio.preparar(
            nota_id, operador_id=operador_id)
        recarregado = db.session.get(EnsaioDpsNfse, ensaio.id)
        comparacao = json.loads(recarregado.comparacao_json)

        assert recarregado.estado == nfse_api_ensaio.ESTADO_FALHA_PREPARACAO
        assert comparacao['pode_enviar'] is False
        assert any(
            item['caminho'].endswith('/cTribNac')
            for item in comparacao['bloqueadoras'])
        assert recarregado.xml_dps_assinada is not None


def test_falha_na_assinatura_consume_numero_e_nao_libera_envio(
        app, ids, monkeypatch):
    nota_id, operador_id = _entrada(app, ids)
    _injetar_fontes(monkeypatch)

    def assinatura_falha(*_args, **_kwargs):
        raise nfse_api_dps.AssinaturaDpsInvalidaError(
            'assinatura sintética recusada')

    monkeypatch.setattr(nfse_api_dps, 'assinar', assinatura_falha)

    with app.app_context():
        ensaio = nfse_api_ensaio.preparar(
            nota_id, operador_id=operador_id)

        assert ensaio.estado == nfse_api_ensaio.ESTADO_FALHA_PREPARACAO
        assert ensaio.xml_dps_assinada is None
        assert db.session.query(ContadorDpsNfse).one().proximo_numero == 2


def test_preparacoes_repetidas_criam_nova_tentativa_sem_mutar_a_anterior(
        app, ids, monkeypatch):
    nota_id, operador_id = _entrada(app, ids)
    _injetar_fontes(monkeypatch)

    with app.app_context():
        primeira = nfse_api_ensaio.preparar(
            nota_id, operador_id=operador_id)
        xml_primeira = primeira.xml_dps_assinada
        segunda = nfse_api_ensaio.preparar(
            nota_id, operador_id=operador_id)
        tentativas = EnsaioDpsNfse.query.order_by(EnsaioDpsNfse.numero).all()

        assert len(tentativas) == 2
        assert (primeira.numero, segunda.numero) == (1, 2)
        assert primeira.identificador_dps != segunda.identificador_dps
        assert primeira.estado == nfse_api_ensaio.ESTADO_PREPARADO
        assert primeira.xml_dps_assinada == xml_primeira
        assert db.session.query(ContadorDpsNfse).one().proximo_numero == 3


def test_xml_ausente_falha_antes_da_reserva(app, ids, monkeypatch):
    nota_id, operador_id = _entrada(app, ids)
    monkeypatch.setattr(
        nfse_api_ensaio,
        'obter_xml_referencia',
        lambda _nota, _espelho: None,
    )

    with app.app_context():
        with pytest.raises(nfse_api_dps.XmlReferenciaAusenteError):
            nfse_api_ensaio.preparar(nota_id, operador_id=operador_id)

        assert db.session.query(ContadorDpsNfse).count() == 0
        assert db.session.query(EnsaioDpsNfse).count() == 0


def _preparado(app, ids, monkeypatch):
    nota_id, operador_id = _entrada(app, ids)
    _injetar_fontes(monkeypatch)
    with app.app_context():
        ensaio = nfse_api_ensaio.preparar(
            nota_id, operador_id=operador_id)
        return ensaio.id, operador_id


def test_enviar_recupera_id_preexistente_sem_post(app, ids, monkeypatch):
    ensaio_id, operador_id = _preparado(app, ids, monkeypatch)
    chamadas = []

    def consultar(identificador):
        chamadas.append(('consultar', identificador))
        return _resultado_gerado(identificador, incluir_xml=False)

    monkeypatch.setattr(nfse_api_sefin, 'consultar_dps_restrita', consultar)
    monkeypatch.setattr(
        nfse_api_sefin,
        'enviar_restrita',
        lambda *_args, **_kwargs: pytest.fail(
            'ID preexistente não pode gerar um segundo POST'),
    )

    with app.app_context():
        ensaio = nfse_api_ensaio.enviar(
            ensaio_id, operador_id=operador_id)
        nota = db.session.get(NotaNfse, ensaio.nota_nfse_id)

        assert ensaio.estado == nfse_api_ensaio.ESTADO_GERADO_TESTE
        assert ensaio.chave_nfse_teste == CHAVE_SINTETICA
        assert ensaio.xml_nfse_teste is None
        assert len(chamadas) == 1
        assert nota.status == 'emitida'


def test_timeout_com_consulta_encontrada_fecha_gerada_com_um_post(
        app, ids, monkeypatch):
    ensaio_id, operador_id = _preparado(app, ids, monkeypatch)
    consultas = []
    postagens = []

    def consultar(identificador):
        consultas.append(identificador)
        if len(consultas) == 1:
            return _resultado_nao_encontrado(identificador)
        return _resultado_gerado(identificador)

    def enviar( dps):
        postagens.append(nfse_api_dps.identificador(dps))
        return nfse_api_sefin.ResultadoRestrito(
            'indisponivel', 503,
            identificador=postagens[-1],
            motivo='indisponibilidade sintética',
        )

    monkeypatch.setattr(nfse_api_sefin, 'consultar_dps_restrita', consultar)
    monkeypatch.setattr(nfse_api_sefin, 'enviar_restrita', enviar)

    with app.app_context():
        ensaio = nfse_api_ensaio.enviar(
            ensaio_id, operador_id=operador_id)

        assert ensaio.estado == nfse_api_ensaio.ESTADO_GERADO_TESTE
        assert ensaio.xml_nfse_teste.startswith('<NFSe')
        assert len(postagens) == 1
        assert len(consultas) == 2


def test_timeout_inconclusivo_vira_indefinido_e_reconsultar_nao_faz_post(
        app, ids, monkeypatch):
    ensaio_id, operador_id = _preparado(app, ids, monkeypatch)
    consultas = []
    postagens = []

    def consultar(identificador):
        consultas.append(identificador)
        return (_resultado_nao_encontrado(identificador)
                if len(consultas) == 1 else None)

    def enviar(_dps):
        postagens.append(True)
        return nfse_api_sefin.ResultadoRestrito('indisponivel', 503)

    monkeypatch.setattr(nfse_api_sefin, 'consultar_dps_restrita', consultar)
    monkeypatch.setattr(nfse_api_sefin, 'enviar_restrita', enviar)

    with app.app_context():
        ensaio = nfse_api_ensaio.enviar(
            ensaio_id, operador_id=operador_id)
        assert ensaio.estado == nfse_api_ensaio.ESTADO_INDEFINIDA

        reconsultado = nfse_api_ensaio.reconsultar(
            ensaio_id, operador_id=operador_id)
        assert reconsultado.estado == nfse_api_ensaio.ESTADO_INDEFINIDA
        assert len(postagens) == 1
        assert len(consultas) == 3
        with pytest.raises(nfse_api_ensaio.TransicaoDpsInvalidaError):
            nfse_api_ensaio.enviar(ensaio_id, operador_id=operador_id)


def test_rejeicao_do_post_fica_persistida_sem_mudar_nota(app, ids, monkeypatch):
    ensaio_id, operador_id = _preparado(app, ids, monkeypatch)
    monkeypatch.setattr(
        nfse_api_sefin,
        'consultar_dps_restrita',
        lambda identificador: _resultado_nao_encontrado(identificador),
    )
    postagens = []

    def enviar(dps):
        identificador = nfse_api_dps.identificador(dps)
        postagens.append(identificador)
        return _resultado_rejeitado(identificador)

    monkeypatch.setattr(nfse_api_sefin, 'enviar_restrita', enviar)

    with app.app_context():
        ensaio = nfse_api_ensaio.enviar(
            ensaio_id, operador_id=operador_id)
        nota = db.session.get(NotaNfse, ensaio.nota_nfse_id)

        assert ensaio.estado == nfse_api_ensaio.ESTADO_REJEITADO
        assert ensaio.codigo_rejeicao == 'E-SINT'
        assert ensaio.motivo_rejeicao == 'Rejeição sintética'
        assert len(postagens) == 1
        assert nota.status == 'emitida'


def test_transicao_condicional_impede_dois_posts_concorrentes(
        app, ids, monkeypatch):
    ensaio_id, operador_id = _preparado(app, ids, monkeypatch)
    barreira = threading.Barrier(2)
    postagens = []

    def consultar(identificador):
        barreira.wait(timeout=5)
        return _resultado_nao_encontrado(identificador)

    def enviar(dps):
        postagens.append(nfse_api_dps.identificador(dps))
        return _resultado_rejeitado(postagens[-1])

    monkeypatch.setattr(nfse_api_sefin, 'consultar_dps_restrita', consultar)
    monkeypatch.setattr(nfse_api_sefin, 'enviar_restrita', enviar)

    def trabalhador():
        with app.app_context():
            try:
                return nfse_api_ensaio.enviar(
                    ensaio_id, operador_id=operador_id)
            except nfse_api_ensaio.NfseApiEnsaioError as exc:
                return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        resultados = list(executor.map(lambda _indice: trabalhador(), (1, 2)))

    with app.app_context():
        ensaio = db.session.get(EnsaioDpsNfse, ensaio_id)
        assert len(postagens) == 1
        assert ensaio.estado == nfse_api_ensaio.ESTADO_REJEITADO
        assert any(
            isinstance(resultado, nfse_api_ensaio.EnsaioDpsEmAndamentoError)
            or getattr(resultado, 'estado', None) == nfse_api_ensaio.ESTADO_REJEITADO
            for resultado in resultados)
