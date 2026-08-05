"""Leitura e classificação de PDFs de certidões.

Extraído de routes.py (C1). São funções puras de I/O + parsing, sem
dependência de Selenium nem do estado de lote.
"""
import os
import re
from datetime import datetime

import pdfplumber

from app import db, file_manager
from app.models import StatusEspecial
from app.services.execution_logger import log_event


def extrair_validade_federal(caminho_pdf):
    if not caminho_pdf:
        return None

    try:
        with pdfplumber.open(caminho_pdf) as pdf:
            texto = "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception as exc:
        log_event('federal_pdf_read_error', level='WARNING', error=str(exc))
        return None

    match = re.search(r"Válida\s+até\s+(\d{2}/\d{2}/\d{4})", texto, re.IGNORECASE)
    if not match:
        return None

    try:
        return datetime.strptime(match.group(1), "%d/%m/%Y").date()
    except ValueError:
        return None


def extrair_texto(caminho_pdf, origem_log='PDF'):
    if not caminho_pdf:
        return ''

    try:
        with pdfplumber.open(caminho_pdf) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception as exc:
        log_event('pdf_read_error', level='WARNING', origem=origem_log, error=str(exc))
        return ''


def _normalizar(texto):
    texto = file_manager.remover_acentos(texto or '')
    texto = re.sub(r'\s+', ' ', texto)
    return texto.upper().strip()


def classificar_texto(texto):
    """Classifica o conteúdo textual de uma certidão (lógica pura, testável).

    Retorna 'efeito_negativa' | 'positiva' | 'negativa' | 'desconhecida'.
    """
    texto = _normalizar(texto)
    if not texto:
        return 'desconhecida'

    if re.search(r'CERTIDAO\s+POSITIVA\s+COM\s+EFEITOS?\s+DE\s+NEGATIVA', texto):
        return 'efeito_negativa'

    if re.search(r'CERTIDAO\s+POSITIVA\b', texto):
        return 'positiva'

    if re.search(r'CERTIDAO\s+NEGATIVA\b', texto):
        return 'negativa'

    return 'desconhecida'


# Abaixo disso o arquivo esta truncado ou e um stub de erro — nenhuma certidao
# real do sistema chega perto. Sinal fraco de proposito: o criterio forte e o
# marcador de texto abaixo.
TAMANHO_MINIMO_PDF_BYTES = 1024

# Vocabulario AMPLO de certidao (spec 08, DATA-03.1). Amplo porque o gate so
# reprova por EVIDENCIA NEGATIVA: certidao com redacao que `classificar_texto`
# nao reconhece ('desconhecida') tem de continuar passando — Trabalhista e
# Municipal dependem desse caminho. Siglas curtas exigem palavra inteira, senao
# 'SEGUNDO' viraria CND.
_MARCADORES_CERTIDAO = re.compile(
    r'CERTIDAO'
    r'|CERTIFICADO\s+DE\s+REGULARIDADE'
    r'|CONSULTA\s+REGULARIDADE'
    r'|REGULARIDADE\s+DO\s+EMPREGADOR'
    r'|NADA\s+CONSTA'
    r'|\bCND\b'
    r'|\bCNDT\b'
    r'|\bCRF\b'
)


def tem_marcador_certidao(texto):
    """True se o texto tem alguma marca de que e uma certidao (logica pura)."""
    return bool(_MARCADORES_CERTIDAO.search(_normalizar(texto)))


def _motivo_arquivo_invalido(caminho_pdf):
    """Motivo da reprovacao pelas checagens de arquivo, ou None. Nao le o PDF."""
    if not caminho_pdf or not os.path.exists(caminho_pdf):
        return 'arquivo ausente'
    try:
        tamanho = os.path.getsize(caminho_pdf)
    except OSError as exc:
        return f'arquivo ilegivel ({exc})'
    if tamanho < TAMANHO_MINIMO_PDF_BYTES:
        return f'arquivo pequeno demais para uma certidao ({tamanho} bytes)'
    return None


def _motivo_texto_invalido(texto):
    """Motivo da reprovacao pelo conteudo, ou None."""
    if not (texto or '').strip():
        return 'sem texto extraivel (possivel pagina de erro ou PDF vazio)'
    if not tem_marcador_certidao(texto):
        return 'o texto nao parece de uma certidao'
    return None


def _reprovar(motivo, origem_log):
    log_event('pdf_gate_reprovado', level='WARNING', origem=origem_log, motivo=motivo)
    return False, motivo


def avaliar_arquivo_certidao(caminho_pdf, origem_log='PDF'):
    """Responde "isto e um PDF de certidao?" — (ok, motivo). Nunca levanta.

    Nucleo compartilhado do gate (spec 08, DATA-03): reprova apenas por
    evidencia negativa — arquivo ausente/truncado, sem texto extraivel, ou sem
    nenhum marcador de certidao. NAO reprova por classificacao 'desconhecida'.
    """
    motivo = _motivo_arquivo_invalido(caminho_pdf)
    if motivo:
        return _reprovar(motivo, origem_log)

    motivo = _motivo_texto_invalido(extrair_texto(caminho_pdf, origem_log=origem_log))
    if motivo:
        return _reprovar(motivo, origem_log)
    return True, None


def classificar_status(caminho_pdf, origem_log='PDF'):
    return classificar_texto(extrair_texto(caminho_pdf, origem_log=origem_log))


def classificar_e_tratar_positivo(certidao, caminho_pdf, origem_log='PDF', tipo_label=None):
    classificacao = classificar_status(caminho_pdf, origem_log=origem_log)
    if classificacao != 'positiva':
        return classificacao, None

    erro_remocao = None
    try:
        if caminho_pdf and os.path.exists(caminho_pdf):
            os.remove(caminho_pdf)
    except Exception as exc_remove:
        erro_remocao = str(exc_remove)

    tipo_label_final = (tipo_label or (certidao.tipo.value if certidao else '') or 'CERTIDAO').strip()
    try:
        if certidao:
            certidao.caminho_arquivo = None
            certidao.status_especial = StatusEspecial.PENDENTE
            certidao.data_validade = None
            db.session.commit()
    except Exception:
        db.session.rollback()
        msg = (
            f'Certidão {tipo_label_final} POSITIVA detectada, '
            'mas houve erro ao marcar como PENDENTE no banco.'
        )
        return 'erro', msg

    msg = f'Certidão {tipo_label_final} detectada como POSITIVA. Arquivo removido e certidão marcada como PENDENTE.'
    if erro_remocao:
        msg += f' Não foi possível remover o arquivo automaticamente: {erro_remocao}'
    return 'positiva', msg


def classificar_estadual_rs(caminho_pdf):
    return classificar_status(caminho_pdf, origem_log='ESTADUAL-RS')
