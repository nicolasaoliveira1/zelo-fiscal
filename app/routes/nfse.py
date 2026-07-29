"""Rotas da emissao de NFSe dos honorarios (NFSE-01..17).

Registra no blueprint "main" compartilhado (AD-013). Rotas finas: toda a
logica vive em `app/services/nfse_*`; aqui so entra validacao de entrada,
autorizacao e montagem da resposta.
"""
from flask import render_template, request

from app import db
from app.auth import requer_papel
from app.models import (
    ApelidoNfse,
    Empresa,
    LoteNfse,
    NotaNfse,
    OrigemVinculoNfse,
    StatusNotaNfse,
)
from app.routes import bp
from app.services import nfse_config, nfse_import, nfse_service
from app.services.nfse_session import SESSAO
from app.utils import json_error

TAMANHO_MAXIMO_CSV = 5 * 1024 * 1024  # 5 MB: o extrato mensal tem ~7 KB


def _so_digitos(valor):
    return ''.join(c for c in str(valor or '') if c.isdigit())


def cnpj_valido(valor):
    """Valida CNPJ por digitos verificadores.

    Existe porque o operador digita o CNPJ na mao para empresa nao cadastrada,
    e um digito trocado emite nota fiscal no CNPJ de outra pessoa."""
    numeros = _so_digitos(valor)
    if len(numeros) != 14 or numeros == numeros[0] * 14:
        return False
    for tamanho in (12, 13):
        pesos = list(range(tamanho - 7, 1, -1)) + list(range(9, 1, -1))
        soma = sum(int(d) * p for d, p in zip(numeros[:tamanho], pesos))
        resto = soma % 11
        digito = 0 if resto < 2 else 11 - resto
        if int(numeros[tamanho]) != digito:
            return False
    return True


def formatar_cnpj(valor):
    n = _so_digitos(valor)
    return f'{n[:2]}.{n[2:5]}.{n[5:8]}/{n[8:12]}-{n[12:]}'


def _nota_para_json(nota):
    empresa = nota.empresa_id and db.session.get(Empresa, nota.empresa_id)
    return {
        'id': nota.id,
        'nome_csv': nota.nome_csv,
        'empresa': empresa.nome if empresa else None,
        'empresa_id': nota.empresa_id,
        'cnpj': nota.cnpj,
        'competencia': nota.competencia,
        'valor': f'{nota.valor_final:.2f}'.replace('.', ',') if nota.valor_final else None,
        'vencimento': nota.vencimento.strftime('%d/%m/%Y') if nota.vencimento else None,
        'status': nota.status,
        'origem_vinculo': nota.origem_vinculo,
        'score_match': nota.score_match,
        'divergencia_valor': nota.divergencia_valor,
        'duplicata_liberada': nota.duplicata_liberada,
        'erro': nota.erro,
    }


def _resumo(notas):
    conta = {}
    for nota in notas:
        conta[nota.status] = conta.get(nota.status, 0) + 1
    return {
        'total': len(notas),
        'por_status': conta,
        'divergencias': sum(1 for n in notas if n.divergencia_valor),
    }


# --- pagina ----------------------------------------------------------------

@bp.route('/nfse')
@requer_papel('operador')
def nfse_painel():
    lote = LoteNfse.query.order_by(LoteNfse.id.desc()).first()
    notas = (NotaNfse.query.filter_by(lote_id=lote.id).order_by(NotaNfse.id).all()
             if lote else [])
    return render_template(
        'nfse.html',
        lote=lote,
        notas=[_nota_para_json(n) for n in notas],
        resumo=_resumo(notas),
        config=nfse_config.get_config_nfse(),
        empresas=[{'id': e.id, 'nome': e.nome, 'cnpj': e.cnpj}
                  for e in Empresa.query.order_by(Empresa.nome).all()],
    )


# --- importacao (NFSE-01..07) ----------------------------------------------

@bp.route('/nfse/importar', methods=['POST'])
@requer_papel('operador')
def nfse_importar():
    arquivo = request.files.get('arquivo')
    if arquivo is None or not (arquivo.filename or '').strip():
        return json_error('Selecione o arquivo CSV exportado do banco.', 400)

    conteudo = arquivo.read()
    if len(conteudo) > TAMANHO_MAXIMO_CSV:
        return json_error(
            'Arquivo grande demais para ser o extrato mensal de cobrancas.', 400)

    try:
        lote = nfse_import.importar(conteudo, nome_arquivo=arquivo.filename)
    except nfse_import.ArquivoInvalidoError as exc:
        return json_error(str(exc), 400)

    notas = NotaNfse.query.filter_by(lote_id=lote.id).order_by(NotaNfse.id).all()
    return {
        'status': 'ok',
        'lote_id': lote.id,
        'notas': [_nota_para_json(n) for n in notas],
        'resumo': _resumo(notas),
    }


# --- resolucao manual da empresa (NFSE-03, NFSE-22) ------------------------

@bp.route('/nfse/nota/<int:nota_id>/resolver', methods=['POST'])
@requer_papel('operador')
def nfse_resolver_empresa(nota_id):
    """Vincula a nota a uma empresa escolhida, ou a um CNPJ digitado.

    Ao vincular por empresa, salva o apelido: o mesmo nome do banco resolve
    sozinho no mes seguinte."""
    nota = db.session.get(NotaNfse, nota_id)
    if nota is None:
        return json_error('Nota nao encontrada.', 404)
    if nota.status == StatusNotaNfse.EMITIDA:
        return json_error('Esta nota ja foi emitida.', 409)

    dados = request.get_json(silent=True) or {}
    empresa_id = dados.get('empresa_id')
    cnpj = (dados.get('cnpj') or '').strip()

    if empresa_id:
        empresa = db.session.get(Empresa, int(empresa_id))
        if empresa is None:
            return json_error('Empresa nao encontrada.', 404)
        _vincular(nota, empresa)
        _salvar_apelido(nota.nome_csv_norm, empresa.id)
    elif cnpj:
        if not cnpj_valido(cnpj):
            return json_error(
                'CNPJ invalido: confira os digitos. Um digito trocado emite a '
                'nota no CNPJ de outra empresa.', 400)
        formatado = formatar_cnpj(cnpj)
        empresa = Empresa.query.filter_by(cnpj=formatado).first()
        if empresa is not None:
            _vincular(nota, empresa)
            _salvar_apelido(nota.nome_csv_norm, empresa.id)
        else:
            nota.cnpj = formatado
            nota.empresa_id = None
            nota.origem_vinculo = OrigemVinculoNfse.MANUAL
            nota.status = StatusNotaNfse.CADASTRO_PENDENTE
    else:
        return json_error('Informe uma empresa ou um CNPJ.', 400)

    db.session.commit()
    return {'status': 'ok', 'nota': _nota_para_json(nota)}


def _vincular(nota, empresa):
    nota.empresa_id = empresa.id
    nota.cnpj = empresa.cnpj
    nota.origem_vinculo = OrigemVinculoNfse.MANUAL
    nota.score_match = None
    if nota.status in (StatusNotaNfse.EMPRESA_PENDENTE, StatusNotaNfse.CADASTRO_PENDENTE):
        nota.status = StatusNotaNfse.PRONTA


def _salvar_apelido(nome_norm, empresa_id):
    if not nome_norm:
        return
    existente = ApelidoNfse.query.filter_by(nome_norm=nome_norm).first()
    if existente is None:
        db.session.add(ApelidoNfse(nome_norm=nome_norm, empresa_id=empresa_id))
    else:
        existente.empresa_id = empresa_id


# --- liberar duplicata (ND-004) --------------------------------------------

@bp.route('/nfse/nota/<int:nota_id>/liberar-duplicata', methods=['POST'])
@requer_papel('operador')
def nfse_liberar_duplicata(nota_id):
    nota = db.session.get(NotaNfse, nota_id)
    if nota is None:
        return json_error('Nota nao encontrada.', 404)
    if nota.status != StatusNotaNfse.DUPLICATA:
        return json_error('Esta linha nao esta marcada como duplicata.', 400)

    nota.duplicata_liberada = True
    db.session.commit()
    return {'status': 'ok', 'nota': _nota_para_json(nota)}


# --- configuracao (NFSE-08/09) ---------------------------------------------

@bp.route('/nfse/configuracao', methods=['POST'])
@requer_papel('operador')
def nfse_salvar_configuracao():
    dados = request.get_json(silent=True) or request.form.to_dict()
    try:
        config = nfse_config.salvar(dados)
    except nfse_config.ConfiguracaoInvalidaError as exc:
        return json_error(exc.mensagem, 400, campo=exc.campo)
    return {
        'status': 'ok',
        'config': {campo: getattr(config, campo)
                   for campo in nfse_config.CAMPOS_OBRIGATORIOS},
    }


# --- sessao do navegador (NFSE-11/12/15) -----------------------------------

@bp.route('/nfse/sessao/preparar', methods=['POST'])
@requer_papel('operador')
def nfse_preparar_sessao():
    """Abre o navegador, loga com certificado e le a aliquota do Simples.

    Nao libera emissao: quem libera e a confirmacao explicita do operador."""
    if not SESSAO.adquirir():
        return json_error(
            'Ja existe uma sessao da NFSe em andamento nesta maquina.', 409)
    try:
        return {'status': 'ok', **nfse_service.preparar_sessao()}
    except Exception as exc:
        SESSAO.encerrar()
        return json_error(exc=exc, code=500)
    finally:
        SESSAO.liberar()


@bp.route('/nfse/sessao/confirmar-aliquota', methods=['POST'])
@requer_papel('operador')
def nfse_confirmar_aliquota():
    dados = request.get_json(silent=True) or {}
    SESSAO.confirmar_aliquota(dados.get('aliquota'))
    return {'status': 'ok', 'aliquota': SESSAO.aliquota,
            'aliquota_confirmada': SESSAO.aliquota_confirmada}


@bp.route('/nfse/sessao/encerrar', methods=['POST'])
@requer_papel('operador')
def nfse_encerrar_sessao():
    """Idempotente: encerrar sem sessao aberta e sucesso, nao erro."""
    SESSAO.encerrar()
    return {'status': 'ok'}


@bp.route('/nfse/sessao/status')
@requer_papel('operador')
def nfse_status_sessao():
    return {
        'status': 'ok',
        'ativa': SESSAO.driver_vivo(),
        'ocupada': SESSAO.ocupada,
        'aliquota': SESSAO.aliquota,
        'aliquota_confirmada': SESSAO.aliquota_confirmada,
    }


# --- preenchimento de uma nota (NFSE-13/14) --------------------------------

@bp.route('/nfse/nota/<int:nota_id>/preencher', methods=['POST'])
@requer_papel('operador')
def nfse_preencher_nota(nota_id):
    """Preenche a nota no portal ate a tela de revisao e para.

    A emissao em si continua sendo um clique do operador no navegador."""
    if not SESSAO.adquirir():
        return json_error(
            'Ja existe uma emissao da NFSe em andamento. Aguarde terminar.', 409)
    try:
        resultado = nfse_service.preencher_nota(nota_id)
    except nfse_service.NotaNaoEmitivelError as exc:
        return json_error(str(exc), 409)
    except Exception as exc:
        return json_error(exc=exc, code=500)
    finally:
        SESSAO.liberar()

    if resultado.get('status') == 'error':
        return json_error(resultado.get('message'), 500, nota_id=nota_id)

    nota = db.session.get(NotaNfse, nota_id)
    resultado['nota'] = _nota_para_json(nota)
    return resultado
