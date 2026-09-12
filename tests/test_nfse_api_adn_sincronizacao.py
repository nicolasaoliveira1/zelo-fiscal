"""Sincronização incremental do ADN, somente com transporte sintético."""
import base64
import gzip
import json
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.models import (
    ConfiguracaoNfse,
    EventoEmitidaNfse,
    NotaEmitidaNfse,
    ObservacaoEmitidaNfse,
    SincronizacaoAdnNfse,
)
from app.services import nfse_api_adn as adn
from app.services import nfse_api_credencial
from app.services import nfse_api_transporte as transporte
from app.services import nfse_emitidas
from app.services.pem_temporario import Credencial


NS = 'http://www.sped.fazenda.gov.br/nfse'
DOCUMENTO_ESCRITORIO = '11111111111111'
DOCUMENTO_OUTRO_PRESTADOR = '44556677000186'
DOCUMENTO_TOMADOR = '39053344705'
CHAVE_A = '0' * 50
CHAVE_B = '1' * 50
CHAVE_C = '2' * 50


def _configurar(app, ids):
    with app.app_context():
        db.session.add(ConfiguracaoNfse(
            id=1,
            empresa_escritorio_id=ids['empresa'],
            api_ambiente='restrita',
            api_habilitada=True,
        ))
        db.session.commit()


def _compactar(xml):
    return base64.b64encode(gzip.compress(xml)).decode('ascii')


def _xml_nfse(chave=CHAVE_A, prestador=DOCUMENTO_ESCRITORIO,
              tomador=DOCUMENTO_OUTRO_PRESTADOR, emitente=None):
    emitente_xml = (
        f'<emit><CNPJ>{emitente}</CNPJ></emit>' if emitente else '')
    return f'''<NFSe xmlns="{NS}" versao="1.01">
  <infNFSe Id="NFS{chave}">
    <xLocEmi>Município Sintético/RS</xLocEmi>
    <cStat>100</cStat>
    <dhProc>2026-07-31T18:45:00-03:00</dhProc>
    {emitente_xml}
    <valores><vLiq>1280.40</vLiq></valores>
    <DPS><infDPS>
      <dCompet>2026-07-01</dCompet>
      <cLocEmi>0000000</cLocEmi>
      <prest><CNPJ>{prestador}</CNPJ><xNome>Prestador Sintético</xNome></prest>
      <toma><CNPJ>{tomador}</CNPJ><xNome>Tomador Sintético</xNome></toma>
    </infDPS></DPS>
  </infNFSe>
</NFSe>'''.encode('utf-8')


def _xml_evento(chave=CHAVE_A, tipo='e101101'):
    return f'''<evento xmlns="{NS}" versao="1.01">
  <infEvento Id="EVT{'0' * 59}">
    <nSeqEvento>1</nSeqEvento>
    <dhProc>2026-08-02T10:30:00-03:00</dhProc>
    <pedRegEvento versao="1.01"><infPedReg Id="PRE{'0' * 56}">
      <dhEvento>2026-08-02T10:29:00-03:00</dhEvento>
      <chNFSe>{chave}</chNFSe>
      <{tipo}> <xDesc>Evento sintético</xDesc> </{tipo}>
    </infPedReg></pedRegEvento>
  </infEvento>
</evento>'''.encode('utf-8')


def _item(nsu, tipo='NFSE', xml=None, chave=CHAVE_A):
    item = {
        'NSU': nsu,
        'ChaveAcesso': chave,
        'TipoDocumento': tipo,
        'TipoEvento': None,
        'ArquivoXml': _compactar(xml) if xml is not None else None,
        'DataHoraGeracao': '2026-08-02T10:30:00-03:00',
    }
    if tipo == 'EVENTO':
        item['TipoEvento'] = 'CANCELAMENTO'
    return item


def _corpo(status, itens=None, erros=None):
    return json.dumps({
        'StatusProcessamento': status,
        'LoteDFe': itens,
        'Alertas': [],
        'Erros': erros or [],
        'TipoAmbiente': 'HOMOLOGACAO',
        'VersaoAplicativo': 'sintetica',
        'DataHoraProcessamento': '2026-08-02T10:31:00-03:00',
    })


def _resposta(status='DOCUMENTOS_LOCALIZADOS', itens=None):
    return transporte.Desfecho(
        'ok', 200, _corpo(status, itens), 'resposta sintética')


def _preparar(monkeypatch, app, ids, respostas, configurar=True):
    if configurar:
        _configurar(app, ids)
    monkeypatch.setattr(
        nfse_api_credencial,
        'diagnostico',
        lambda: nfse_api_credencial.Diagnostico(True, 'ok'),
    )
    monkeypatch.setattr(
        nfse_api_credencial,
        'credencial_do_escritorio',
        lambda: Credencial('certificado-sintetico.pfx', 'senha-sintetica'),
    )

    @contextmanager
    def sessao_falsa(_credencial):
        yield 'sessao-sintetica'

    chamadas = []
    respostas = iter(respostas)

    def chamar_falso(metodo, url, **kwargs):
        chamadas.append((metodo, url, kwargs))
        return next(respostas)

    monkeypatch.setattr(adn.transporte, 'sessao', sessao_falsa)
    monkeypatch.setattr(adn.transporte, 'chamar', chamar_falso)
    conciliacoes = []
    monkeypatch.setattr(
        nfse_emitidas, 'conciliar',
        lambda **_kwargs: conciliacoes.append(True) or 0,
    )
    return chamadas, conciliacoes


def test_primeira_sincronizacao_envia_zero_depois_o_cursor_e_lote_true(
        monkeypatch, app, ids):
    respostas = [
        _resposta(itens=[
            _item(2, xml=_xml_nfse(CHAVE_B), chave=CHAVE_B),
            _item(1, xml=_xml_nfse(CHAVE_A), chave=CHAVE_A),
        ]),
        _resposta('NENHUM_DOCUMENTO_LOCALIZADO', []),
    ]
    chamadas, conciliacoes = _preparar(monkeypatch, app, ids, respostas)

    with app.app_context():
        resultado = adn.sincronizar(execution_id='exec-sintetico')

        cursor = SincronizacaoAdnNfse.query.one()
        assert cursor.ultimo_nsu == 2
        assert ObservacaoEmitidaNfse.query.count() == 2
        assert resultado.nsu_inicial == 0
        assert resultado.nsu_final == 2
        assert resultado.lidos == 2
        assert resultado.gravados == 2
        assert resultado.ignorados == 0
        assert resultado.falha is None
        assert conciliacoes == [True]

    assert [(metodo, url.rsplit('/', 1)[-1], dados['params'])
            for metodo, url, dados in chamadas] == [
        ('GET', '0', {'lote': 'true'}),
        ('GET', '2', {'lote': 'true'}),
    ]


def test_documentos_fora_do_escopo_avancam_sem_gravar(monkeypatch, app, ids):
    respostas = [
        _resposta(itens=[
            _item(3, tipo='DPS'),
            _item(4, tipo='EVENTO', xml=_xml_evento(tipo='e999999')),
        ]),
        _resposta('NENHUM_DOCUMENTO_LOCALIZADO', []),
    ]
    _preparar(monkeypatch, app, ids, respostas)

    with app.app_context():
        resultado = adn.sincronizar()

        assert resultado.nsu_final == 4
        assert resultado.gravados == 0
        assert resultado.ignorados == 2
        assert ObservacaoEmitidaNfse.query.count() == 0
        assert EventoEmitidaNfse.query.count() == 0


def test_nfse_em_que_o_escritorio_e_tomador_e_ignorada(
        monkeypatch, app, ids):
    respostas = [
        _resposta(itens=[_item(
            5,
            xml=_xml_nfse(
                CHAVE_A,
                prestador=DOCUMENTO_OUTRO_PRESTADOR,
                tomador=DOCUMENTO_ESCRITORIO,
            ),
        )]),
        _resposta('NENHUM_DOCUMENTO_LOCALIZADO', []),
    ]
    _preparar(monkeypatch, app, ids, respostas)

    with app.app_context():
        resultado = adn.sincronizar()

        assert resultado.nsu_final == 5
        assert resultado.ignorados == 1
        assert ObservacaoEmitidaNfse.query.count() == 0
        assert NotaEmitidaNfse.query.count() == 0


def test_emitente_nao_substitui_prestador_na_identificacao_da_nota(
        monkeypatch, app, ids):
    respostas = [
        _resposta(itens=[_item(
            5,
            xml=_xml_nfse(
                prestador=DOCUMENTO_OUTRO_PRESTADOR,
                emitente=DOCUMENTO_ESCRITORIO,
            ),
        )]),
        _resposta('NENHUM_DOCUMENTO_LOCALIZADO', []),
    ]
    _preparar(monkeypatch, app, ids, respostas)

    with app.app_context():
        resultado = adn.sincronizar()

        assert resultado.nsu_final == 5
        assert resultado.ignorados == 1
        assert ObservacaoEmitidaNfse.query.count() == 0


def test_evento_de_cancelamento_e_nota_sao_aplicados_em_ordem_de_nsu(
        monkeypatch, app, ids):
    respostas = [
        _resposta(itens=[
            _item(7, tipo='EVENTO', xml=_xml_evento(CHAVE_A)),
            _item(6, xml=_xml_nfse(CHAVE_A)),
        ]),
        _resposta('NENHUM_DOCUMENTO_LOCALIZADO', []),
    ]
    _preparar(monkeypatch, app, ids, respostas)

    with app.app_context():
        resultado = adn.sincronizar()

        assert resultado.gravados == 2
        assert EventoEmitidaNfse.query.count() == 1
        nota = NotaEmitidaNfse.query.one()
        assert nota.situacao_fiscal == 'cancelada'
        assert nota.origem_autoritativa == 'adn'


def test_lote_com_cinquenta_itens_e_aceito(monkeypatch, app, ids):
    itens = [_item(nsu, tipo='DPS') for nsu in range(1, 51)]
    respostas = [_resposta(itens=itens), _resposta('NENHUM_DOCUMENTO_LOCALIZADO', [])]
    _preparar(monkeypatch, app, ids, respostas)

    with app.app_context():
        resultado = adn.sincronizar(teto_chamadas=2)

        assert resultado.nsu_final == 50
        assert resultado.lidos == 50
        assert resultado.ignorados == 50
        assert SincronizacaoAdnNfse.query.one().ultimo_nsu == 50


def test_lote_maior_que_cinquenta_nao_avanca_cursor(monkeypatch, app, ids):
    _preparar(monkeypatch, app, ids, [_resposta(itens=[
        _item(nsu, tipo='DPS') for nsu in range(1, 52)
    ])])

    with app.app_context():
        resultado = adn.sincronizar()

        assert resultado.falha
        assert resultado.desfecho == 'falha_local'
        assert resultado.nsu_falha == 0
        assert resultado.nsu_final is None
        assert resultado.nsu_falha == 0
        assert SincronizacaoAdnNfse.query.one().ultimo_nsu is None


def test_documentos_localizados_sem_lote_sao_falha_local(
        monkeypatch, app, ids):
    _preparar(monkeypatch, app, ids, [_resposta('DOCUMENTOS_LOCALIZADOS', [])])

    with app.app_context():
        resultado = adn.sincronizar()

        assert resultado.desfecho == 'falha_local'
        assert resultado.nsu_falha == 0
        assert SincronizacaoAdnNfse.query.one().ultimo_nsu is None


def test_falha_de_banco_na_preparacao_e_classificada_local(
        monkeypatch, app, ids):
    _configurar(app, ids)
    monkeypatch.setattr(
        adn,
        '_documento_consulta',
        lambda: (_ for _ in ()).throw(SQLAlchemyError('banco-sintetico')),
    )
    chamadas_breaker = []
    monkeypatch.setattr(
        adn.circuit_breaker,
        'registrar_falha',
        lambda *args, **kwargs: chamadas_breaker.append((args, kwargs)),
    )

    with app.app_context():
        resultado = adn.sincronizar(execution_id='exec-banco-preparo')

        assert resultado.desfecho == 'falha_local'
        assert resultado.nsu_falha == 0
        assert resultado.falha
        assert chamadas_breaker == []


@pytest.mark.parametrize(
    ('desfecho', 'http'),
    [('rejeitado', 400), ('rejeitado', 404)],
)
def test_http_erro_nao_avanca_nem_alimenta_breaker(
        monkeypatch, app, ids, desfecho, http):
    _configurar(app, ids)
    monkeypatch.setattr(
        nfse_api_credencial,
        'diagnostico',
        lambda: nfse_api_credencial.Diagnostico(True, 'ok'),
    )
    monkeypatch.setattr(
        nfse_api_credencial,
        'credencial_do_escritorio',
        lambda: Credencial('certificado-sintetico.pfx', 'senha-sintetica'),
    )

    @contextmanager
    def sessao_falsa(_credencial):
        yield 'sessao-sintetica'

    chamadas_breaker = []
    monkeypatch.setattr(adn.transporte, 'sessao', sessao_falsa)
    monkeypatch.setattr(
        adn.transporte,
        'chamar',
        lambda *_args, **_kwargs: transporte.Desfecho(
            desfecho, http, 'erro-sintetico', 'rejeitado'),
    )
    monkeypatch.setattr(
        adn.circuit_breaker,
        'registrar_falha',
        lambda *args, **kwargs: chamadas_breaker.append((args, kwargs)),
    )
    monkeypatch.setattr(nfse_emitidas, 'conciliar', lambda **_kwargs: None)

    with app.app_context():
        resultado = adn.sincronizar()

        assert resultado.falha
        assert resultado.desfecho == desfecho
        assert resultado.nsu_falha == 0
        assert resultado.nsu_final is None
        assert SincronizacaoAdnNfse.query.one().ultimo_nsu is None
        assert chamadas_breaker == []


def test_rejeicao_do_adn_nao_avanca_cursor(monkeypatch, app, ids):
    _configurar(app, ids)
    monkeypatch.setattr(
        nfse_api_credencial,
        'diagnostico',
        lambda: nfse_api_credencial.Diagnostico(True, 'ok'),
    )
    monkeypatch.setattr(
        nfse_api_credencial,
        'credencial_do_escritorio',
        lambda: Credencial('certificado-sintetico.pfx', 'senha-sintetica'),
    )

    @contextmanager
    def sessao_falsa(_credencial):
        yield 'sessao-sintetica'

    monkeypatch.setattr(adn.transporte, 'sessao', sessao_falsa)
    monkeypatch.setattr(
        adn.transporte,
        'chamar',
        lambda *_args, **_kwargs: transporte.Desfecho(
            'ok', 200, _corpo('REJEICAO', [], [{'Codigo': 'COD-SINTETICO'}]),
            'rejeitado'),
    )
    monkeypatch.setattr(nfse_emitidas, 'conciliar', lambda **_kwargs: None)

    with app.app_context():
        resultado = adn.sincronizar()

        assert resultado.falha
        assert resultado.desfecho == 'rejeitado'
        assert resultado.nsu_falha == 0
        assert SincronizacaoAdnNfse.query.one().ultimo_nsu is None


def test_falha_de_xml_preserva_nsu_anterior_e_nao_alimenta_breaker(
        monkeypatch, app, ids):
    respostas = [_resposta(itens=[
        _item(1, xml=_xml_nfse(CHAVE_A)),
        _item(2, xml='xml inválido'.encode('utf-8')),
    ])]
    _preparar(monkeypatch, app, ids, respostas)
    chamadas_breaker = []
    monkeypatch.setattr(
        adn.circuit_breaker,
        'registrar_falha',
        lambda *args, **kwargs: chamadas_breaker.append((args, kwargs)),
    )

    with app.app_context():
        resultado = adn.sincronizar()

        assert resultado.nsu_final == 1
        assert resultado.nsu_falha == 2
        assert resultado.lidos == 2
        assert ObservacaoEmitidaNfse.query.count() == 1
        assert chamadas_breaker == []


def test_falha_de_banco_nao_alimenta_breaker(monkeypatch, app, ids):
    respostas = [_resposta(itens=[_item(1, xml=_xml_nfse(CHAVE_A))])]
    _preparar(monkeypatch, app, ids, respostas)
    monkeypatch.setattr(
        nfse_emitidas,
        '_gravar_observacoes',
        lambda *args, **kwargs: (_ for _ in ()).throw(
            SQLAlchemyError('banco-sintetico')),
    )
    chamadas_breaker = []
    monkeypatch.setattr(
        adn.circuit_breaker,
        'registrar_falha',
        lambda *args, **kwargs: chamadas_breaker.append((args, kwargs)),
    )

    with app.app_context():
        resultado = adn.sincronizar()

        assert resultado.falha
        assert resultado.nsu_final is None
        assert SincronizacaoAdnNfse.query.one().ultimo_nsu is None
        assert chamadas_breaker == []


def test_falha_de_rede_alimenta_breaker(monkeypatch, app, ids):
    _configurar(app, ids)
    monkeypatch.setattr(
        nfse_api_credencial,
        'diagnostico',
        lambda: nfse_api_credencial.Diagnostico(True, 'ok'),
    )
    monkeypatch.setattr(
        nfse_api_credencial,
        'credencial_do_escritorio',
        lambda: Credencial('certificado-sintetico.pfx', 'senha-sintetica'),
    )

    @contextmanager
    def sessao_falsa(_credencial):
        yield 'sessao-sintetica'

    chamadas_breaker = []
    monkeypatch.setattr(adn.transporte, 'sessao', sessao_falsa)
    monkeypatch.setattr(
        adn.transporte,
        'chamar',
        lambda *_args, **_kwargs: transporte.Desfecho(
            'indisponivel', None, '', 'indisponível'),
    )
    monkeypatch.setattr(
        adn.circuit_breaker,
        'registrar_falha',
        lambda *args, **kwargs: chamadas_breaker.append((args, kwargs)),
    )
    monkeypatch.setattr(nfse_emitidas, 'conciliar', lambda **_kwargs: None)

    with app.app_context():
        resultado = adn.sincronizar(execution_id='exec-rede-sintetico')

        assert resultado.falha
        assert resultado.nsu_falha == 0
        assert chamadas_breaker
        assert chamadas_breaker[0][0][0] == adn.circuit_breaker.ALVO_NFSE_NACIONAL
        assert chamadas_breaker[0][1]['execution_id'] == 'exec-rede-sintetico'


def test_lease_vigente_recusa_sem_chamar_a_api(monkeypatch, app, ids):
    _configurar(app, ids)
    agora = datetime(2026, 8, 2, 12, 0)
    with app.app_context():
        db.session.add(SincronizacaoAdnNfse(
            ambiente='restrita', documento_consulta=DOCUMENTO_ESCRITORIO,
            dono_execucao='outro-execucao',
            lease_ate=agora + timedelta(minutes=5),
        ))
        db.session.commit()
    monkeypatch.setattr(adn, '_agora', lambda: agora)
    monkeypatch.setattr(
        adn.nfse_api_credencial,
        'diagnostico',
        lambda: nfse_api_credencial.Diagnostico(True, 'ok'),
    )
    monkeypatch.setattr(
        adn.nfse_api_credencial,
        'credencial_do_escritorio',
        lambda: Credencial('certificado-sintetico.pfx', 'senha-sintetica'),
    )
    monkeypatch.setattr(
        adn.transporte,
        'sessao',
        lambda *_args, **_kwargs: pytest.fail('lease ocupado não abre sessão'),
    )

    with app.app_context(), pytest.raises(adn.SincronizacaoEmCursoError):
        adn.sincronizar()


def test_lease_expirado_pode_ser_retomado(monkeypatch, app, ids):
    _configurar(app, ids)
    agora = datetime(2026, 8, 2, 12, 0)
    with app.app_context():
        db.session.add(SincronizacaoAdnNfse(
            ambiente='restrita', documento_consulta=DOCUMENTO_ESCRITORIO,
            dono_execucao='processo-caído',
            lease_ate=agora - timedelta(seconds=1),
        ))
        db.session.commit()
    monkeypatch.setattr(adn, '_agora', lambda: agora)
    _preparar(
        monkeypatch, app, ids,
        [_resposta('NENHUM_DOCUMENTO_LOCALIZADO', [])], configurar=False)

    with app.app_context():
        resultado = adn.sincronizar()
        cursor = SincronizacaoAdnNfse.query.one()

        assert resultado.falha is None
        assert cursor.dono_execucao is None
        assert cursor.lease_ate is None


def test_nsu_igual_ou_menor_ao_cursor_e_ignorado_sem_retroceder(
        monkeypatch, app, ids):
    _configurar(app, ids)
    with app.app_context():
        db.session.add(SincronizacaoAdnNfse(
            ambiente='restrita', documento_consulta=DOCUMENTO_ESCRITORIO,
            ultimo_nsu=5,
        ))
        db.session.commit()
    _preparar(
        monkeypatch, app, ids,
        [
            _resposta(itens=[
                _item(4, tipo='DPS'),
                _item(5, tipo='DPS'),
                _item(6, tipo='DPS'),
            ]),
            _resposta('NENHUM_DOCUMENTO_LOCALIZADO', []),
        ],
        configurar=False,
    )

    with app.app_context():
        resultado = adn.sincronizar()

        assert resultado.nsu_inicial == 5
        assert resultado.nsu_final == 6
        assert resultado.ignorados == 3
        assert SincronizacaoAdnNfse.query.one().ultimo_nsu == 6


def test_log_registra_execucao_faixa_contagens_e_desfecho(
        monkeypatch, app, ids):
    respostas = [
        _resposta(itens=[_item(1, tipo='DPS')]),
        _resposta('NENHUM_DOCUMENTO_LOCALIZADO', []),
    ]
    _preparar(monkeypatch, app, ids, respostas)
    eventos = []
    monkeypatch.setattr(
        adn, 'log_event',
        lambda evento, **campos: eventos.append((evento, campos)),
    )

    with app.app_context():
        adn.sincronizar(execution_id='exec-log-sintetico')

    finais = [campos for evento, campos in eventos
              if evento == 'nfse_api_adn_fim']
    assert finais == [{
        'nsu_inicial': 0,
        'nsu_final': 1,
        'lidos': 1,
        'gravados': 0,
        'ignorados': 1,
        'desfecho': 'sem_documentos',
        'nsu_falha': None,
        'execution_id': 'exec-log-sintetico',
    }]

    ignorados = [campos for evento, campos in eventos
                 if evento == 'nfse_api_adn_ignorado']
    assert ignorados == [{
        'nsu': 1,
        'tipo': 'DPS',
        'motivo': 'tipo_documento_fora_do_escopo',
        'execution_id': 'exec-log-sintetico',
    }]


def test_lease_perdido_nao_sobrescreve_checkpoint(
        monkeypatch, app, ids):
    _chamadas, conciliacoes = _preparar(
        monkeypatch, app, ids, [_resposta(itens=[_item(1, tipo='DPS')])])

    def trocar_dono(*_args, **_kwargs):
        cursor = SincronizacaoAdnNfse.query.one()
        cursor.dono_execucao = 'novo-worker-sintetico'
        cursor.lease_ate = datetime.now() + timedelta(minutes=5)
        db.session.commit()
        return False, True

    monkeypatch.setattr(adn, '_processar_item', trocar_dono)

    with app.app_context():
        resultado = adn.sincronizar(execution_id='exec-lease-sintetico')
        cursor = SincronizacaoAdnNfse.query.one()

        assert resultado.nsu_final is None
        assert resultado.nsu_falha == 1
        assert cursor.ultimo_nsu is None
        assert cursor.dono_execucao == 'novo-worker-sintetico'
        assert conciliacoes == []


def test_dono_do_lease_mantem_sufixo_unico_com_id_longo():
    primeiro = adn._nome_dono('e' * 40)
    segundo = adn._nome_dono('e' * 40)

    assert len(primeiro) == 40
    assert len(segundo) == 40
    assert primeiro != segundo
