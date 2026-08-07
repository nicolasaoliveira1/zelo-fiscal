"""Operacoes de dominio sobre Certidao reutilizadas pelas rotas.

Centraliza o par mutacao + commit/rollback que estava duplicado nos
endpoints de atualizacao de validade e de marcacao como pendente.
"""
from app import db
from app.automation import pdf
from app.automation.emissao import calcular_validade_padrao
from app.errors import mensagem_usuario
from app.models import StatusEspecial


def aplicar_validade(certidao, nova_data):
    """Define a validade e limpa o status especial.

    Retorna (ok, erro): ok=True em sucesso; em falha de commit, faz rollback
    e devolve (False, mensagem).
    """
    certidao.data_validade = nova_data
    certidao.status_especial = None
    try:
        db.session.commit()
        return True, None
    except Exception as exc:
        db.session.rollback()
        return False, str(exc)


def marcar_pendente(certidao):
    """Marca a certidao como pendente e limpa a validade.

    Retorna (ok, erro), com a mesma semantica de aplicar_validade.
    """
    certidao.status_especial = StatusEspecial.PENDENTE
    certidao.data_validade = None
    try:
        db.session.commit()
        return True, None
    except Exception as exc:
        db.session.rollback()
        return False, str(exc)


def finalizar_federal(certidao, caminho_pdf):
    """Finaliza uma certidao FEDERAL registrada manualmente (COV-01b).

    Nucleo compartilhado pelo monitor de download ("Abrir Site") e pelo upload
    manual: dado o PDF ja no disco, classifica (reuso) e decide validade/pendencia.
    - positiva -> PENDENTE (o helper remove o arquivo e commita), sem validade;
    - negativa/efeito/desconhecida -> data do PDF ("Valida ate") ou fallback hoje+180;
    - falha de classificacao/DB ou excecao -> rollback + erro acionavel.

    Retorna dict serializavel: {'ok': bool, 'pendente': bool, 'data_validade': date|None,
    'message': str|None, 'grave': bool}. Nunca deixa a certidao inconsistente.
    """
    try:
        classificacao, msg_pos = pdf.classificar_e_tratar_positivo(
            certidao, caminho_pdf, origem_log='FEDERAL', tipo_label='Federal')

        if classificacao == 'erro':
            return {'ok': False, 'grave': True, 'message': msg_pos}

        # DATA-03.3: o arquivo nao e uma certidao (pagina de erro do portal, ou
        # PDF errado no upload manual). Sem validade e sem fallback de 180 dias.
        if classificacao == 'invalida':
            mensagem = pdf.descartar_pdf_invalido(
                certidao, caminho_pdf, validade_anterior=certidao.data_validade,
                tipo_label='Federal')
            return {'ok': False, 'grave': False, 'message': mensagem}

        if classificacao == 'positiva':
            # classificar_e_tratar_positivo ja marcou PENDENTE, removeu o arquivo e commitou.
            return {'ok': True, 'pendente': True, 'data_validade': None, 'message': msg_pos}

        # negativa / efeito_negativa / desconhecida: data do PDF ou fallback 180d.
        validade = pdf.extrair_validade_federal(caminho_pdf) or calcular_validade_padrao(certidao)
        certidao.data_validade = validade
        certidao.status_especial = None
        db.session.commit()
        return {'ok': True, 'pendente': False, 'data_validade': validade,
                'classificacao': classificacao, 'message': None}
    except Exception as exc:
        db.session.rollback()
        return {'ok': False, 'grave': True,
                'message': mensagem_usuario(exc, contexto='registro Federal')}
