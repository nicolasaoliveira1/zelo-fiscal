"""Os fluxos de emissao recusam PDF reprovado no gate (spec 08, DATA-03.3/03.4).

Cobre o nucleo compartilhado `pdf.descartar_pdf_invalido` e o comportamento dos
fluxos de `automation/emissao.py` quando a classificacao volta 'invalida':
arquivo descartado, validade NAO gravada e falha nao-grave (para o item voltar
pelo retry do lote).

Os testes dirigem as FUNCOES REAIS com o Selenium mockado, no padrao de
`_run_emit_trabalhista` em test_emissao_puras.py — um teste que reimplementasse
o ramo passaria mesmo com a producao revertida. Isso vale para os 4 fluxos,
inclusive o Estadual RS (`_run_emit_rs`), que exige mais mocks por causa do
login com certificado e do ALTCHA.

Unica excecao: o zeramento de `arquivo_salvo_msg` dentro de `_automatizar_fgts`
e verificado no fonte (`test_fgts_zera_arquivo_salvo_ao_reprovar`), porque essa
funcao exige o Selenium inteiro. O EFEITO que importa — validade nao concedida —
e asserido no codigo real, via `_emitir_fgts_certidao`.
"""
import inspect
import os
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from app import db
from app.automation import emissao, pdf
from app.models import Certidao, Municipio, StatusEspecial, TipoCertidao


def _arquivo(tmp_path, nome='cert.pdf'):
    arq = tmp_path / nome
    arq.write_bytes(b'%PDF-1.4\n' + b'x' * 2048)
    return str(arq)


class _FakeWait:
    """WebDriverWait falso: resolve na hora, sem polling real de Selenium."""

    def __init__(self, driver, timeout):
        pass

    def until(self, cond):
        return MagicMock()


# --- nucleo compartilhado: pdf.descartar_pdf_invalido ------------------------

def test_descartar_apaga_arquivo_e_limpa_caminho(app, ids, tmp_path):
    caminho = _arquivo(tmp_path)
    with app.app_context():
        cert = Certidao.query.filter_by(tipo=TipoCertidao.FGTS).first()
        cert.caminho_arquivo = caminho
        db.session.commit()

        msg = pdf.descartar_pdf_invalido(cert, caminho, tipo_label='FGTS')

        assert not os.path.exists(caminho)
        assert cert.caminho_arquivo is None
        assert 'não é uma certidão FGTS válida' in msg


def test_descartar_restaura_validade_anterior(app, ids, tmp_path):
    """A emissao falhou, entao a certidao volta ao que era — nao pode ficar com
    a validade nova (falsa) nem perder a antiga."""
    anterior = date.today() + timedelta(days=30)
    caminho = _arquivo(tmp_path)
    with app.app_context():
        cert = Certidao.query.filter_by(tipo=TipoCertidao.FGTS).first()
        cert.data_validade = date.today() + timedelta(days=180)  # a "nova", falsa
        db.session.commit()

        pdf.descartar_pdf_invalido(cert, caminho, validade_anterior=anterior)
        assert cert.data_validade == anterior


def test_descartar_nao_marca_pendente(app, ids, tmp_path):
    """Diferenca deliberada em relacao a certidao positiva: PDF reprovado e
    falha da emissao, nao um fato fiscal sobre a empresa."""
    with app.app_context():
        cert = Certidao.query.filter_by(tipo=TipoCertidao.FGTS).first()
        pdf.descartar_pdf_invalido(cert, _arquivo(tmp_path))
        assert cert.status_especial != StatusEspecial.PENDENTE


def test_descartar_com_arquivo_ja_removido_nao_levanta(app, ids, tmp_path):
    with app.app_context():
        cert = Certidao.query.filter_by(tipo=TipoCertidao.FGTS).first()
        assert pdf.descartar_pdf_invalido(cert, str(tmp_path / 'nao_existe.pdf'))


# --- Trabalhista: funcao real ------------------------------------------------

def _run_emit_trabalhista(app, cert_id, classificacao):
    """Executa `_emitir_trabalhista_certidao` de verdade, com a I/O mockada."""
    with app.app_context(), \
            patch.object(emissao, 'WebDriverWait', _FakeWait), \
            patch.object(emissao, '_configurar_download_automatico_chrome'), \
            patch.object(emissao, '_snapshot_downloads_pdf', return_value=set()), \
            patch.object(emissao, '_pick_changed_download_pdf',
                         return_value='/tmp/cndt.pdf'), \
            patch.object(emissao, '_wait_file_stable', return_value=True), \
            patch.object(emissao.file_manager, 'mover_e_renomear',
                         return_value=(True, '/rede/cndt_final.pdf')), \
            patch.object(emissao.pdf, 'classificar_e_tratar_positivo',
                         return_value=classificacao), \
            patch.object(emissao.trabalhista, 'resolver_captcha_e_submeter',
                         return_value=(True, None)), \
            patch.object(emissao.capture, 'capturar_contexto_falha', MagicMock()):
        resultado = emissao._emitir_trabalhista_certidao(cert_id, driver=MagicMock())
        cert = db.session.get(Certidao, cert_id)
        estado = (cert.data_validade, cert.caminho_arquivo, cert.status_especial)
    return resultado, estado


def test_trabalhista_invalida_falha_sem_gravar_validade(app, ids):
    """DATA-03.3/03.4 na funcao real: nem validade, nem caminho, e grave=False
    para o item voltar pelo retry."""
    validade_antes = date.today() + timedelta(days=15)
    with app.app_context():
        cert = db.session.get(Certidao, ids['trabalhista'])
        cert.data_validade = validade_antes
        db.session.commit()

    (ok, grave, msg), (validade, caminho, status) = _run_emit_trabalhista(
        app, ids['trabalhista'], ('invalida', None))

    assert ok is False
    assert grave is False
    assert 'não é uma certidão Trabalhista válida' in msg
    assert validade == validade_antes
    assert caminho is None
    assert status != StatusEspecial.PENDENTE


def test_trabalhista_negativa_continua_concedendo_validade(app, ids):
    """Regressao: o gate nao pode atrapalhar a emissao que funciona."""
    (ok, grave, _msg), (validade, caminho, _status) = _run_emit_trabalhista(
        app, ids['trabalhista'], ('negativa', None))

    assert (ok, grave) == (True, False)
    assert validade == date.today() + timedelta(days=180)
    assert caminho == '/rede/cndt_final.pdf'


# --- FGTS: funcao real -------------------------------------------------------

def _run_emit_fgts(app, cert_id, contexto_extra):
    """Executa `_emitir_fgts_certidao` real, com `_automatizar_fgts` no lugar da
    navegacao — ele so preenche o contexto, que e o contrato entre os dois."""
    def _fake_automatizar(contexto, *_a, **_k):
        contexto.update(contexto_extra)

    with app.app_context(), \
            patch.object(emissao, 'WebDriverWait', _FakeWait), \
            patch.object(emissao, '_automatizar_fgts', _fake_automatizar), \
            patch.object(emissao, '_fgts_fechar_abas_extras'), \
            patch.object(emissao.capture, 'capturar_contexto_falha', MagicMock()):
        resultado = emissao._emitir_fgts_certidao(cert_id, driver=MagicMock())
        cert = db.session.get(Certidao, cert_id)
        estado = (cert.data_validade, cert.status_especial)
    return resultado, estado


def test_fgts_invalida_falha_sem_gravar_validade(app, ids):
    """DATA-03.3/03.4 na funcao real do FGTS."""
    (ok, grave, msg), (validade, status) = _run_emit_fgts(
        app, ids['fgts'],
        {'pdf_classificacao': 'invalida', 'pdf_msg': 'O arquivo baixado não é '
         'uma certidão FGTS válida (possível página de erro do portal).'})

    assert ok is False
    assert grave is False
    assert 'não é uma certidão FGTS válida' in msg
    assert validade is None
    assert status != StatusEspecial.PENDENTE


def test_fgts_sucesso_continua_concedendo_validade(app, ids):
    """Regressao: com arquivo salvo e sem reprovacao, a validade e concedida.

    O FGTS raspa a data da propria pagina (`validade_dias_padrao` e None em
    VALIDADES_CERTIDOES), entao o contexto carrega a data encontrada."""
    encontrada = date.today() + timedelta(days=45)
    (ok, grave, _msg), (validade, _status) = _run_emit_fgts(
        app, ids['fgts'],
        {'arquivo_salvo_msg': 'salvo', 'data_encontrada': encontrada})

    assert (ok, grave) == (True, False)
    assert validade == encontrada


def test_fgts_zera_arquivo_salvo_ao_reprovar():
    """No FGTS a validade so e concedida se `arquivo_salvo_msg` sobreviver, e o
    ramo 'invalida' precisa zera-lo. Verificado no fonte real de
    `_automatizar_fgts`, que exige Selenium completo para rodar."""
    fonte = inspect.getsource(emissao._automatizar_fgts)
    assert "'positiva', 'erro', 'invalida'" in fonte
    assert 'descartar_pdf_invalido' in fonte


# --- Municipal: funcao real --------------------------------------------------

def _run_emit_municipal(app, cert_id, classificacao, caminho_final):
    """Executa `_emitir_municipal_certidao_lote` real para Tramandai (fluxo
    nao-IMBE), com a navegacao e o download mockados."""
    with app.app_context(), \
            patch.object(emissao, 'WebDriverWait', _FakeWait), \
            patch.object(emissao, '_configurar_download_automatico_chrome'), \
            patch.object(emissao, '_carregar_config_municipio', return_value={}), \
            patch.object(emissao.steps, 'executar_municipio', return_value={}), \
            patch.object(emissao, '_snapshot_downloads_pdf', return_value={}), \
            patch.object(emissao, '_pick_changed_download_pdf',
                         return_value='/tmp/muni.pdf'), \
            patch.object(emissao.file_manager, 'mover_e_renomear',
                         return_value=(True, caminho_final)), \
            patch.object(emissao.pdf, 'classificar_status',
                         return_value=classificacao), \
            patch.object(emissao.capture, 'capturar_contexto_falha', MagicMock()):
        resultado = emissao._emitir_municipal_certidao_lote(cert_id, driver=MagicMock())
        cert = db.session.get(Certidao, cert_id)
        estado = (cert.data_validade, cert.caminho_arquivo)
    return resultado, estado


def _semear_municipio_tramandai(app):
    with app.app_context():
        # validade_dias preenchido: sem ele _calcular_validade_municipal devolve
        # None e o teste de regressao nao teria validade nenhuma para observar.
        db.session.add(Municipio(nome='Tramandaí', url_certidao='https://exemplo.test',
                                 cnpj_field_id='cnpj', by='id', automacao_ativa=True,
                                 validade_dias=90))
        db.session.commit()


def test_municipal_invalida_falha_sem_gravar_validade(app, ids, tmp_path):
    """DATA-03.3: no municipal a validade e gravada ANTES da classificacao,
    entao reprovar precisa desfaze-la — nao so deixar de gravar."""
    _semear_municipio_tramandai(app)
    caminho = _arquivo(tmp_path, 'muni.pdf')
    validade_antes = date.today() + timedelta(days=10)

    with app.app_context():
        cert = db.session.get(Certidao, ids['municipal'])
        cert.data_validade = validade_antes
        db.session.commit()

    (ok, grave, msg), (validade, caminho_db) = _run_emit_municipal(
        app, ids['municipal'], 'invalida', caminho)

    assert ok is False
    assert grave is False
    assert 'não é uma certidão Municipal válida' in msg
    assert validade == validade_antes      # a validade nova NAO ficou
    assert caminho_db is None
    assert not os.path.exists(caminho)


def test_municipal_gate_vale_sem_classificar_pdf_status(app, ids, tmp_path):
    """DATA-03.2: antes, o PDF municipal so era lido quando o config pedia
    `classificar_pdf_status`. Aqui o config e {} e o gate vale mesmo assim."""
    _semear_municipio_tramandai(app)
    caminho = _arquivo(tmp_path, 'muni2.pdf')

    (ok, _grave, _msg), (validade, _caminho) = _run_emit_municipal(
        app, ids['municipal'], 'invalida', caminho)

    assert ok is False
    assert validade is None


def test_municipal_negativa_continua_concedendo_validade(app, ids, tmp_path):
    """Regressao: municipio que emite bem segue gravando a validade."""
    _semear_municipio_tramandai(app)

    (ok, grave, _msg), (validade, caminho_db) = _run_emit_municipal(
        app, ids['municipal'], 'negativa', '/rede/muni_final.pdf')

    assert (ok, grave) == (True, False)
    assert validade == date.today() + timedelta(days=90)
    assert caminho_db == '/rede/muni_final.pdf'


# --- Estadual RS: funcao real ------------------------------------------------

def _run_emit_rs(app, cert_id, classificacao, caminho_final):
    """Executa `_emitir_estadual_rs_certidao` real. O RS tem login por
    certificado + ALTCHA antes do download, entao a superfície de mock e maior —
    mas o que se testa continua sendo a decisao pos-download, no codigo real."""
    with app.app_context(), \
            patch.object(emissao, 'WebDriverWait', _FakeWait), \
            patch.object(emissao, '_rs_garantir_pagina_solicitacao', return_value=True), \
            patch.object(emissao, '_rs_sessao_expirada', return_value=False), \
            patch.object(emissao, '_rs_preencher_cnpj_com_confirmacao', return_value=True), \
            patch.object(emissao, '_resolver_altcha_rs_com_2captcha',
                         return_value={'status': 'ok', 'solved': True}), \
            patch.object(emissao, '_clicar_enviar_estadual_rs',
                         return_value={'clicked': True, 'method': 'js'}), \
            patch.object(emissao, '_rs_fechar_abas_processamento', return_value=False), \
            patch.object(emissao, '_snapshot_downloads_pdf', return_value={}), \
            patch.object(emissao, '_pick_changed_download_pdf',
                         return_value='/tmp/rs.pdf'), \
            patch.object(emissao, '_wait_file_stable', return_value=True), \
            patch.object(emissao.file_manager, 'verificar_novo_arquivo',
                         return_value='/tmp/rs.pdf'), \
            patch.object(emissao.file_manager, 'mover_e_renomear',
                         return_value=(True, caminho_final)), \
            patch.object(emissao.pdf, 'classificar_estadual_rs',
                         return_value=classificacao), \
            patch.object(emissao.capture, 'capturar_contexto_falha', MagicMock()):
        resultado = emissao._emitir_estadual_rs_certidao(cert_id, driver=MagicMock())
        cert = db.session.get(Certidao, cert_id)
        estado = (cert.data_validade, cert.caminho_arquivo)
    return resultado, estado


def test_rs_invalida_falha_sem_gravar_validade(app, ids, tmp_path):
    """DATA-03.3/03.4 na funcao real do Estadual RS."""
    caminho = _arquivo(tmp_path, 'rs.pdf')
    validade_antes = date.today() + timedelta(days=25)
    with app.app_context():
        cert = db.session.get(Certidao, ids['rs'])
        cert.data_validade = validade_antes
        db.session.commit()

    (ok, grave, msg), (validade, caminho_db) = _run_emit_rs(
        app, ids['rs'], 'invalida', caminho)

    assert ok is False
    assert grave is False
    assert 'não é uma certidão Estadual RS válida' in msg
    assert validade == validade_antes
    assert caminho_db is None
    assert not os.path.exists(caminho)


def test_rs_negativa_continua_concedendo_validade(app, ids):
    """Regressao: o gate nao pode atrapalhar a emissao RS que funciona."""
    (ok, grave, _msg), (validade, caminho_db) = _run_emit_rs(
        app, ids['rs'], 'negativa', '/rede/rs_final.pdf')

    assert (ok, grave) == (True, False)
    assert validade is not None
    assert caminho_db == '/rede/rs_final.pdf'
