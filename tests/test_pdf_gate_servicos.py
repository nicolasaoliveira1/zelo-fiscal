"""Os servicos recusam PDF reprovado no gate (spec 08, DATA-03.2/03.3/03.4).

Dois consumidores, ambos no codigo real:

- `certidao_service.finalizar_federal` — usado pelo monitor de download E pelo
  upload manual da Federal. O upload e justamente onde PDF errado chega, porque
  quem escolhe o arquivo e uma pessoa.
- `emissao_service._baixar_classificar_pdf` / `_montar_resposta_baixar` — a
  emissao individual ("baixar"), os 3 caminhos de classificacao por tipo.
"""
from datetime import date, timedelta
from unittest.mock import patch

from app import db
from app.automation import pdf
from app.models import Certidao, StatusEspecial, TipoCertidao
from app.services import certidao_service, emissao_service


def _arquivo(tmp_path, nome='cert.pdf'):
    arq = tmp_path / nome
    arq.write_bytes(b'%PDF-1.4\n' + b'x' * 2048)
    return str(arq)


# --- finalizar_federal (monitor + upload manual) -----------------------------

def test_federal_invalida_nao_grava_validade(app, ids, tmp_path):
    """DATA-03.3: sem validade e SEM o fallback de 180 dias — o fallback e
    justamente o que transformaria uma pagina de erro em certidao valida."""
    caminho = _arquivo(tmp_path, 'federal.pdf')
    with app.app_context(), \
            patch.object(pdf, 'classificar_status', return_value='invalida'):
        cert = Certidao.query.filter_by(tipo=TipoCertidao.FEDERAL).first()
        res = certidao_service.finalizar_federal(cert, caminho)

        assert res['ok'] is False
        assert res['grave'] is False
        assert 'não é uma certidão Federal válida' in res['message']
        assert cert.data_validade is None
        assert cert.caminho_arquivo is None


def test_federal_invalida_preserva_validade_anterior(app, ids, tmp_path):
    caminho = _arquivo(tmp_path, 'federal2.pdf')
    anterior = date.today() + timedelta(days=60)
    with app.app_context(), \
            patch.object(pdf, 'classificar_status', return_value='invalida'):
        cert = Certidao.query.filter_by(tipo=TipoCertidao.FEDERAL).first()
        cert.data_validade = anterior
        db.session.commit()

        certidao_service.finalizar_federal(cert, caminho)
        assert cert.data_validade == anterior


def test_federal_invalida_nao_marca_pendente(app, ids, tmp_path):
    """PDF ruim nao e o mesmo que certidao positiva: nao vira PENDENTE."""
    caminho = _arquivo(tmp_path, 'federal3.pdf')
    with app.app_context(), \
            patch.object(pdf, 'classificar_status', return_value='invalida'):
        cert = Certidao.query.filter_by(tipo=TipoCertidao.FEDERAL).first()
        certidao_service.finalizar_federal(cert, caminho)
        assert cert.status_especial != StatusEspecial.PENDENTE


def test_federal_negativa_continua_recebendo_validade(app, ids, tmp_path):
    """Regressao: o fallback de 180 dias (COV-01b) segue valendo para certidao
    real que nao traz 'Valida ate'."""
    caminho = _arquivo(tmp_path, 'federal4.pdf')
    with app.app_context(), \
            patch.object(pdf, 'classificar_status', return_value='negativa'), \
            patch.object(pdf, 'extrair_validade_federal', return_value=None):
        cert = Certidao.query.filter_by(tipo=TipoCertidao.FEDERAL).first()
        res = certidao_service.finalizar_federal(cert, caminho)

        assert res['ok'] is True
        assert res['data_validade'] == date.today() + timedelta(days=180)


def test_upload_manual_federal_recusa_pdf_que_nao_e_certidao(app, client, ids, tmp_path):
    """DATA-03.2 ponta a ponta: o operador sobe um PDF errado pela rota de
    registro manual e o sistema recusa em vez de gravar validade."""
    import io as _io

    with app.app_context():
        cert_id = Certidao.query.filter_by(tipo=TipoCertidao.FEDERAL).first().id

    conteudo = b'%PDF-1.4\n' + b'Erro 500 - servico indisponivel ' * 60
    with patch.object(pdf, 'classificar_status', return_value='invalida'):
        resp = client.post(
            f'/certidao/federal/registrar/{cert_id}',
            data={'arquivo': (_io.BytesIO(conteudo), 'erro.pdf')},
            content_type='multipart/form-data')

    assert resp.status_code >= 400
    with app.app_context():
        cert = db.session.get(Certidao, cert_id)
        assert cert.data_validade is None


# --- emissao_service (emissao individual "baixar") ---------------------------

def _cfg(tipo_chave, estado='RS'):
    return {
        'tipo_certidao_chave': tipo_chave,
        'estado_emp': estado,
        'regra_municipio': None,
        'config_municipal': None,
        'usar_config_municipal': False,
    }


def test_baixar_gate_reprova_antes_de_classificar_por_tipo(app, ids, tmp_path):
    """DATA-03.2: o gate roda primeiro; nenhuma classificacao por tipo acontece
    quando o arquivo nao e certidao."""
    caminho = _arquivo(tmp_path, 'baixar.pdf')
    with app.app_context(), \
            patch.object(emissao_service.pdf, 'classificar_status',
                         return_value='invalida'), \
            patch.object(emissao_service.pdf, 'classificar_e_tratar_positivo') as classificar:
        cert = Certidao.query.filter_by(tipo=TipoCertidao.ESTADUAL).first()
        classif = emissao_service._baixar_classificar_pdf(cert, _cfg('ESTADUAL'), caminho)

        assert classif['pdf_invalida_msg']
        assert 'não é uma certidão' in classif['pdf_invalida_msg']
        classificar.assert_not_called()
        assert classif['rs_estadual_classificacao'] is None


def test_baixar_gate_vale_para_municipal_sem_classificar_pdf_status(app, ids, tmp_path):
    """DATA-03.2: o municipal so classificava quando o config pedia; o gate nao
    depende disso."""
    caminho = _arquivo(tmp_path, 'muni.pdf')
    with app.app_context(), \
            patch.object(emissao_service.pdf, 'classificar_status',
                         return_value='invalida'):
        cert = Certidao.query.filter_by(tipo=TipoCertidao.MUNICIPAL).first()
        classif = emissao_service._baixar_classificar_pdf(
            cert, _cfg('MUNICIPAL'), caminho)
        assert classif['pdf_invalida_msg']


def test_baixar_gate_aprovado_segue_classificando(app, ids, tmp_path):
    """Regressao: arquivo bom continua sendo classificado normalmente."""
    caminho = _arquivo(tmp_path, 'ok.pdf')
    with app.app_context(), \
            patch.object(emissao_service.pdf, 'avaliar_arquivo_certidao',
                         return_value=(True, None)), \
            patch.object(emissao_service.pdf, 'classificar_e_tratar_positivo',
                         return_value=('negativa', None)):
        cert = Certidao.query.filter_by(tipo=TipoCertidao.ESTADUAL).first()
        classif = emissao_service._baixar_classificar_pdf(cert, _cfg('ESTADUAL'), caminho)

        assert classif['pdf_invalida_msg'] is None
        assert classif['rs_estadual_classificacao'] == 'negativa'


def _cfg_resposta():
    cfg = _cfg('ESTADUAL')
    cfg['nome_certidao_arquivo'] = 'Estadual'
    return cfg


def test_baixar_resposta_de_pdf_invalido_e_erro(app, ids):
    """DATA-03.4: a resposta da emissao individual e erro acionavel, nao um
    'success_file_saved' com arquivo ruim."""
    with app.app_context():
        cert = Certidao.query.filter_by(tipo=TipoCertidao.ESTADUAL).first()
        resultado = emissao_service._resultado_baixar_vazio()
        resultado['pdf_invalida_msg'] = 'O arquivo baixado não é uma certidão válida.'
        resultado['arquivo_salvo_msg'] = 'salvo em /rede/x.pdf'

        resposta = emissao_service._montar_resposta_baixar(
            cert, _cfg_resposta(), resultado)
        corpo = resposta[0] if isinstance(resposta, tuple) else resposta
        dados = corpo.get_json()
    assert dados['status'] == 'error'
    assert 'não é uma certidão' in dados['message']


def test_baixar_pdf_invalido_tem_prioridade_sobre_arquivo_salvo(app, ids):
    """O ramo de sucesso ('arquivo_salvo_msg') vem depois no fluxo: se o gate
    reprovou, ele nao pode vencer."""
    with app.app_context():
        cert = Certidao.query.filter_by(tipo=TipoCertidao.ESTADUAL).first()
        resultado = emissao_service._resultado_baixar_vazio()
        resultado['pdf_invalida_msg'] = 'reprovado'
        resultado['arquivo_salvo_msg'] = 'salvo'
        resultado['data_encontrada'] = date.today() + timedelta(days=90)

        resposta = emissao_service._montar_resposta_baixar(
            cert, _cfg_resposta(), resultado)
        corpo = resposta[0] if isinstance(resposta, tuple) else resposta
        assert corpo.get_json()['status'] == 'error'


# --- correcoes do code-review ------------------------------------------------

def test_fgts_individual_propaga_mensagem_do_gate(app, ids):
    """CR4: o FGTS individual reprova o PDF dentro de `_automatizar_fgts`, que so
    escreve no `contexto`. Sem propagar para o `resultado`, o operador recebia
    'window_closed_no_file' em vez da mensagem acionavel do gate (DATA-03.4)."""
    import inspect
    fonte = inspect.getsource(emissao_service._executar_automacao_baixar)
    assert "contexto.get('pdf_classificacao') == 'invalida'" in fonte
    assert 'pdf_invalida_msg' in fonte


def test_resposta_do_fgts_individual_reprovado_e_acionavel(app, ids):
    """O efeito observavel do CR4: a resposta diz o que houve, em vez de um erro
    generico de janela fechada."""
    with app.app_context():
        cert = Certidao.query.filter_by(tipo=TipoCertidao.FGTS).first()
        resultado = emissao_service._resultado_baixar_vazio()
        resultado['pdf_invalida_msg'] = ('O arquivo baixado não é uma certidão '
                                         'FGTS válida.')
        resultado['window_closed'] = True     # o que mascarava a causa antes

        cfg = _cfg('FGTS')
        cfg['nome_certidao_arquivo'] = 'FGTS'
        resposta = emissao_service._montar_resposta_baixar(cert, cfg, resultado)
        corpo = resposta[0] if isinstance(resposta, tuple) else resposta
        dados = corpo.get_json()

    assert dados['status'] == 'error'
    assert 'não é uma certidão' in dados['message']


def test_emissao_individual_le_o_pdf_uma_vez_so(app, ids, tmp_path, monkeypatch):
    """CR7: o PDF vive no drive de rede. Antes, o gate e a classificacao por tipo
    abriam e extraiam o arquivo duas vezes por emissao."""
    caminho = _arquivo(tmp_path, 'uma_vez.pdf')
    leituras = []

    class _FakePage:
        def extract_text(self):
            return 'CERTIDÃO NEGATIVA DE DÉBITOS'

    class _FakePdf:
        pages = [_FakePage()]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _abrir(c):
        leituras.append(c)
        return _FakePdf()

    monkeypatch.setattr(pdf.pdfplumber, 'open', _abrir)

    with app.app_context():
        cert = Certidao.query.filter_by(tipo=TipoCertidao.ESTADUAL).first()
        emissao_service._baixar_classificar_pdf(cert, _cfg('ESTADUAL'), caminho)

    assert len(leituras) == 1, f'PDF lido {len(leituras)} vezes'
