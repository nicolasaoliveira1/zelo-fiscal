"""Rotas da emissao de NFSe dos honorarios (NFSE-01..17).

Registra no blueprint "main" compartilhado (AD-013). Rotas finas: toda a
logica vive em `app/services/nfse_*`; aqui so entra validacao de entrada,
autorizacao e montagem da resposta.
"""
from datetime import datetime

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
from app.utils import (
    TIPO_CNPJ,
    TIPO_CPF,
    detectar_tipo_documento,
    documento_valido,
    formatar_documento,
    json_error,
)

TAMANHO_MAXIMO_CSV = 5 * 1024 * 1024  # 5 MB: o extrato mensal tem ~7 KB

ORIGEM_AUTOMACAO = 'automacao'
ORIGEM_MANUAL = 'manual'


def _nota_para_json(nota):
    empresa = nota.empresa_id and db.session.get(Empresa, nota.empresa_id)
    return {
        'id': nota.id,
        'nome_csv': nota.nome_csv,
        'empresa': empresa.nome if empresa else None,
        'empresa_id': nota.empresa_id,
        'documento': nota.documento,
        'tipo_documento': nota.tipo_documento,
        'competencia': nota.competencia,
        'valor': f'{nota.valor_final:.2f}'.replace('.', ',') if nota.valor_final else None,
        'vencimento': nota.vencimento.strftime('%d/%m/%Y') if nota.vencimento else None,
        'status': nota.status,
        'origem_vinculo': nota.origem_vinculo,
        'score_match': nota.score_match,
        'divergencia_valor': nota.divergencia_valor,
        'duplicata_liberada': nota.duplicata_liberada,
        'erro': nota.erro,
        'origem_emissao': nota.origem_emissao,
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
    enviados = [a for a in request.files.getlist('arquivo')
                if a is not None and (a.filename or '').strip()]
    if not enviados:
        return json_error('Selecione ao menos um arquivo CSV exportado do banco.', 400)

    arquivos = []
    total = 0
    for arquivo in enviados:
        conteudo = arquivo.read()
        total += len(conteudo)
        if total > TAMANHO_MAXIMO_CSV:
            return json_error(
                'Arquivos grandes demais para serem extratos mensais de cobrancas.', 400)
        arquivos.append((arquivo.filename, conteudo))

    try:
        lote = nfse_import.importar(arquivos)
    except nfse_import.ArquivoInvalidoError as exc:
        return json_error(str(exc), 400)

    notas = NotaNfse.query.filter_by(lote_id=lote.id).order_by(NotaNfse.id).all()
    return {
        'status': 'ok',
        'lote_id': lote.id,
        'notas': [_nota_para_json(n) for n in notas],
        'resumo': _resumo(notas),
        'arquivos': len(arquivos),
        'ignoradas_duplicadas': getattr(lote, 'ignoradas_duplicadas', 0),
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
    documento = (dados.get('documento') or dados.get('cnpj') or '').strip()

    # Recusa entrada ambigua em vez de eleger um dos dois em silencio: escolher
    # sozinho ja vinculou nota ao tomador errado E memorizou o apelido errado
    # para os meses seguintes.
    if empresa_id and documento:
        return json_error(
            'Escolha uma empresa OU informe um CPF/CNPJ, nao os dois. '
            'Limpe o campo que nao quer usar.', 400)

    if empresa_id:
        empresa = db.session.get(Empresa, int(empresa_id))
        if empresa is None:
            return json_error('Empresa nao encontrada.', 404)
        _vincular(nota, empresa)
        _lembrar(nota.nome_csv_norm, empresa_id=empresa.id)
    elif documento:
        tipo = detectar_tipo_documento(documento)
        if not documento_valido(documento):
            rotulo = {TIPO_CPF: 'CPF', TIPO_CNPJ: 'CNPJ'}.get(tipo, 'CPF/CNPJ')
            return json_error(
                f'{rotulo} invalido: confira os digitos. Um digito trocado '
                'emite a nota no documento de outra pessoa.', 400)

        formatado = formatar_documento(documento)
        empresa = (Empresa.query.filter_by(cnpj=formatado).first()
                   if tipo == TIPO_CNPJ else None)
        if empresa is not None:
            _vincular(nota, empresa)
            _lembrar(nota.nome_csv_norm, empresa_id=empresa.id)
        else:
            # documento avulso: emite normalmente, sem cadastro. CPF e estado
            # final; CNPJ segue convidando a cadastrar nos proximos meses.
            nota.empresa_id = None
            nota.documento = formatado
            nota.tipo_documento = tipo
            nota.origem_vinculo = OrigemVinculoNfse.MANUAL
            nota.status = (StatusNotaNfse.PESSOA_FISICA if tipo == TIPO_CPF
                           else StatusNotaNfse.CADASTRO_PENDENTE)
            _lembrar(nota.nome_csv_norm, documento=formatado, tipo=tipo)
    else:
        return json_error('Informe uma empresa ou um CPF/CNPJ.', 400)

    db.session.commit()
    return {'status': 'ok', 'nota': _nota_para_json(nota)}


def _vincular(nota, empresa):
    nota.empresa_id = empresa.id
    nota.documento = empresa.cnpj
    nota.tipo_documento = detectar_tipo_documento(empresa.cnpj)
    nota.origem_vinculo = OrigemVinculoNfse.MANUAL
    nota.score_match = None
    if nota.status in (StatusNotaNfse.EMPRESA_PENDENTE,
                       StatusNotaNfse.CADASTRO_PENDENTE,
                       StatusNotaNfse.PESSOA_FISICA):
        nota.status = StatusNotaNfse.PRONTA


def _lembrar(nome_norm, empresa_id=None, documento=None, tipo=None):
    """Memoriza o que fazer com este nome do banco no proximo import.

    Guarda vinculo com Empresa OU documento avulso — para CPF e para CNPJ ainda
    nao cadastrado, que de outro modo seriam redigitados todo mes."""
    if not nome_norm:
        return
    apelido = ApelidoNfse.query.filter_by(nome_norm=nome_norm).first()
    if apelido is None:
        apelido = ApelidoNfse(nome_norm=nome_norm)
        db.session.add(apelido)
    apelido.empresa_id = empresa_id
    apelido.documento = documento
    apelido.tipo_documento = tipo


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


# --- nota emitida fora do sistema ------------------------------------------

@bp.route('/nfse/nota/<int:nota_id>/emitida-manual', methods=['POST'])
@requer_papel('operador')
def nfse_marcar_emitida_manual(nota_id):
    """Marca/desmarca uma nota que o operador emitiu na mao.

    Marcar conta na trava de duplicidade: se o mesmo tomador e a mesma
    competencia voltarem no CSV do mes seguinte, o sistema avisa. A origem fica
    registrada para distinguir do que passou pela automacao.
    """
    nota = db.session.get(NotaNfse, nota_id)
    if nota is None:
        return json_error('Nota nao encontrada.', 404)

    dados = request.get_json(silent=True) or {}
    marcar = dados.get('marcar', True)

    if marcar:
        if nota.origem_emissao == ORIGEM_AUTOMACAO:
            return json_error(
                'Esta nota foi emitida pela automacao; nao da para marcar como '
                'manual.', 409)
        nota.status = StatusNotaNfse.EMITIDA
        nota.origem_emissao = ORIGEM_MANUAL
        nota.emitida_em = datetime.now()
    else:
        if nota.origem_emissao != ORIGEM_MANUAL:
            return json_error(
                'So da para desmarcar nota que voce marcou como emitida na mao.',
                409)
        nota.status = _status_apos_desmarcar(nota)
        nota.origem_emissao = None
        nota.emitida_em = None

    db.session.commit()
    return {'status': 'ok', 'nota': _nota_para_json(nota)}


def _status_apos_desmarcar(nota):
    """Volta ao estado que a linha teria sem a marcacao manual."""
    if nota.empresa_id:
        return StatusNotaNfse.PRONTA
    if nota.tipo_documento == TIPO_CPF:
        return StatusNotaNfse.PESSOA_FISICA
    if nota.documento:
        return StatusNotaNfse.CADASTRO_PENDENTE
    return StatusNotaNfse.EMPRESA_PENDENTE


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
