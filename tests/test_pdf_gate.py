"""Gate de sanidade do PDF de certidao (spec 08, DATA-03).

O problema: o portal pode devolver uma pagina de erro no lugar da certidao, e
o arquivo era aceito e ganhava validade. O gate exige EVIDENCIA POSITIVA de que
o arquivo e uma certidao — mas so isso: redacao nao reconhecida (classificacao
'desconhecida') continua passando, senao quebraria Trabalhista e Municipal, que
dependem desse caminho hoje.

Logica quase toda pura; pdfplumber e mockado como em test_pdf_extra.py.
"""
import pytest

from app.automation import pdf

# Trechos reais do inicio de cada tipo de certidao que o sistema emite.
# Sao a fronteira do gate: se algum deixar de passar, uma emissao que funciona
# hoje passa a falhar em producao.
TEXTOS_CERTIDAO_REAL = {
    'federal': (
        'MINISTERIO DA FAZENDA Secretaria Especial da Receita Federal do Brasil '
        'CERTIDÃO NEGATIVA DE DÉBITOS RELATIVOS AOS TRIBUTOS FEDERAIS E À DÍVIDA '
        'ATIVA DA UNIÃO'
    ),
    'fgts': (
        'CAIXA ECONÔMICA FEDERAL Certificado de Regularidade do FGTS - CRF '
        'A Caixa Econômica Federal certifica que nesta data a empresa acima '
        'encontra-se em situação regular perante o FGTS.'
    ),
    'trabalhista': (
        'PODER JUDICIÁRIO JUSTIÇA DO TRABALHO CERTIDÃO NEGATIVA DE DÉBITOS '
        'TRABALHISTAS Nome: EMPRESA EXEMPLO LTDA'
    ),
    'estadual_rs': (
        'ESTADO DO RIO GRANDE DO SUL SECRETARIA DA FAZENDA CERTIDÃO DE SITUAÇÃO '
        'FISCAL Certificamos que o contribuinte acima identificado'
    ),
    'municipal': (
        'PREFEITURA MUNICIPAL CERTIDÃO NEGATIVA DE DÉBITOS MUNICIPAIS '
        'Certificamos para os devidos fins'
    ),
}

# O que o portal devolve quando algo deu errado — nenhum destes pode passar.
TEXTOS_PAGINA_DE_ERRO = {
    'erro_500': 'Erro 500 - Servico temporariamente indisponivel. Tente mais tarde.',
    'manutencao': 'Sistema em manutencao programada. Voltamos as 8h.',
    'sessao': 'Sua sessao expirou. Faca login novamente para continuar.',
    'captcha': 'Nao foi possivel validar o codigo de seguranca informado.',
}


class _FakePage:
    def __init__(self, texto):
        self._texto = texto

    def extract_text(self):
        return self._texto


class _FakePdf:
    def __init__(self, paginas):
        self.pages = paginas

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _pdf_com_texto(monkeypatch, texto):
    """Faz pdfplumber devolver o texto dado, seja qual for o caminho."""
    monkeypatch.setattr(pdf.pdfplumber, 'open', lambda _c: _FakePdf([_FakePage(texto)]))


def _arquivo_grande(tmp_path, nome='cert.pdf'):
    """Arquivo acima do tamanho minimo, para isolar o criterio de texto."""
    arq = tmp_path / nome
    arq.write_bytes(b'%PDF-1.4\n' + b'x' * (pdf.TAMANHO_MINIMO_PDF_BYTES + 10))
    return str(arq)


# --- tem_marcador_certidao: funcao pura (DATA-03.1) ---------------------------

@pytest.mark.parametrize('texto', [
    'CERTIDÃO NEGATIVA DE DÉBITOS',
    'certidao positiva com efeitos de negativa',
    'Certificado de Regularidade do FGTS',
    'Consulta Regularidade do Empregador',
    'Nada consta em nome do contribuinte',
    'Documento: CND emitida em 01/01/2026',
    'Numero do CRF: 2026010112345678',
])
def test_marcador_reconhece_vocabulario_de_certidao(texto):
    assert pdf.tem_marcador_certidao(texto) is True


@pytest.mark.parametrize('texto', list(TEXTOS_PAGINA_DE_ERRO.values()) + ['', None])
def test_marcador_recusa_pagina_de_erro(texto):
    assert pdf.tem_marcador_certidao(texto) is False


def test_marcador_ignora_acento_e_caixa():
    assert pdf.tem_marcador_certidao('certidão negativa') is True
    assert pdf.tem_marcador_certidao('CERTIDAO NEGATIVA') is True


def test_marcador_nao_casa_sigla_dentro_de_palavra():
    """CND/CRF sao siglas curtas: so valem como palavra inteira, senao
    qualquer texto com 'SEGUNDO' ou 'ESCRF' viraria certidao."""
    assert pdf.tem_marcador_certidao('SEGUNDO o relatorio anexo') is False


# --- avaliar_arquivo_certidao: reprovacoes (DATA-03.1) ------------------------

def test_reprova_arquivo_inexistente(tmp_path):
    ok, motivo = pdf.avaliar_arquivo_certidao(str(tmp_path / 'nao_existe.pdf'))
    assert ok is False
    assert 'ausente' in motivo


def test_reprova_caminho_vazio():
    ok, motivo = pdf.avaliar_arquivo_certidao('')
    assert ok is False
    assert 'ausente' in motivo


def test_reprova_arquivo_abaixo_do_tamanho_minimo(tmp_path):
    arq = tmp_path / 'truncado.pdf'
    arq.write_bytes(b'%PDF-1.4 truncado')
    ok, motivo = pdf.avaliar_arquivo_certidao(str(arq))
    assert ok is False
    assert 'pequeno' in motivo


def test_reprova_pdf_sem_texto_extraivel(tmp_path, monkeypatch):
    _pdf_com_texto(monkeypatch, '')
    ok, motivo = pdf.avaliar_arquivo_certidao(_arquivo_grande(tmp_path))
    assert ok is False
    assert 'texto' in motivo


@pytest.mark.parametrize('chave,texto', sorted(TEXTOS_PAGINA_DE_ERRO.items()))
def test_reprova_texto_sem_marcador_de_certidao(chave, texto, tmp_path, monkeypatch):
    _pdf_com_texto(monkeypatch, texto)
    ok, motivo = pdf.avaliar_arquivo_certidao(_arquivo_grande(tmp_path))
    assert ok is False, f'pagina de erro {chave} passou no gate'
    assert 'certid' in motivo.lower()


# --- avaliar_arquivo_certidao: aprovacoes (DATA-03.5) ------------------------

@pytest.mark.parametrize('tipo,texto', sorted(TEXTOS_CERTIDAO_REAL.items()))
def test_aprova_certidao_real_de_cada_tipo(tipo, texto, tmp_path, monkeypatch):
    _pdf_com_texto(monkeypatch, texto)
    ok, motivo = pdf.avaliar_arquivo_certidao(_arquivo_grande(tmp_path))
    assert ok is True, f'certidao {tipo} reprovada pelo gate: {motivo}'
    assert motivo is None


def test_aprova_certidao_com_redacao_nao_reconhecida(tmp_path, monkeypatch):
    """DATA-03.5: o gate nao e a classificacao. Texto que o classificador chama
    de 'desconhecida' passa, desde que tenha marcador de certidao — e o que
    mantem Trabalhista e Municipal funcionando."""
    texto = 'CERTIDÃO de regularidade emitida pelo orgao competente'
    _pdf_com_texto(monkeypatch, texto)
    assert pdf.classificar_texto(texto) == 'desconhecida'
    ok, _motivo = pdf.avaliar_arquivo_certidao(_arquivo_grande(tmp_path))
    assert ok is True


# --- observabilidade e robustez (DATA-03.6) ----------------------------------

def test_reprovacao_registra_log_com_motivo(tmp_path, monkeypatch):
    eventos = []
    monkeypatch.setattr(pdf, 'log_event',
                        lambda evento, **campos: eventos.append((evento, campos)))
    _pdf_com_texto(monkeypatch, TEXTOS_PAGINA_DE_ERRO['erro_500'])
    pdf.avaliar_arquivo_certidao(_arquivo_grande(tmp_path), origem_log='FGTS')

    assert eventos, 'reprovacao nao registrou log'
    evento, campos = eventos[-1]
    assert evento == 'pdf_gate_reprovado'
    assert campos['origem'] == 'FGTS'
    assert campos['motivo']
    assert campos['level'] == 'WARNING'


def test_aprovacao_nao_registra_reprovacao(tmp_path, monkeypatch):
    eventos = []
    monkeypatch.setattr(pdf, 'log_event',
                        lambda evento, **campos: eventos.append(evento))
    _pdf_com_texto(monkeypatch, TEXTOS_CERTIDAO_REAL['federal'])
    pdf.avaliar_arquivo_certidao(_arquivo_grande(tmp_path))
    assert 'pdf_gate_reprovado' not in eventos


def test_pdf_corrompido_reprova_sem_levantar(tmp_path, monkeypatch):
    """O gate roda no meio da emissao: nunca pode levantar."""
    def _boom(_c):
        raise OSError('arquivo corrompido')
    monkeypatch.setattr(pdf.pdfplumber, 'open', _boom)
    ok, motivo = pdf.avaliar_arquivo_certidao(_arquivo_grande(tmp_path))
    assert ok is False
    assert motivo
