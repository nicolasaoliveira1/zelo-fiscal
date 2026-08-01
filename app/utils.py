"""Pequenos utilitarios compartilhados entre modulos do app."""
import os
import re
from datetime import datetime, timezone

from flask import current_app, jsonify


def utcnow_naive():
    """Datetime atual em UTC sem tzinfo (naive).

    Substitui datetime.utcnow() (deprecado a partir do Python 3.12)
    preservando a convencao de UTC-naive usada nas colunas e comparacoes
    de tempo do projeto.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on', 'sim'}


def get_config_value(name, default=None):
    """Le um valor de config preferindo o app context; cai para os.environ
    quando chamado fora de um contexto Flask."""
    try:
        return current_app.config.get(name, default)
    except RuntimeError:
        return os.environ.get(name, default)


def normalizar_cidade(valor):
    """Chave canonica de cidade: remove acentos, maiusculiza e descarta tudo que
    nao for letra/digito (hifen, espaco, ponto); '' se vazio.

    Fonte unica compartilhada pelo filtro do dashboard, pela exportacao da
    carteira e pela busca de municipio da automacao. Descartar separadores faz
    'Xangri-La', 'Xangri La' e 'Xangrila' caírem na MESMA chave — antes o hifen
    gerava chaves distintas e o cadastro precisava de linhas-espelho por grafia
    (COV-05). Import de `remover_acentos` e lazy para manter `utils` como modulo
    base sem acoplar seu tempo de import ao de `file_manager`.
    """
    texto = (valor or '').strip()
    if not texto:
        return ''
    from app.file_manager import remover_acentos
    return re.sub(r'[^0-9A-Z]', '', remover_acentos(texto).upper())


def json_error(message=None, code=400, exc=None, **extra):
    """Envelope JSON de erro padrao das rotas assincronas.

    Fonte unica compartilhada pelas rotas (blueprint `main`) e pela camada de
    servico (ex.: `emissao_service`). Imports de `app.errors`/`correlation` sao
    lazy para manter `utils` como modulo base sem acoplar seu tempo de import.
    """
    from app.errors import descrever_erro, mensagem_usuario
    from app.services.correlation import CorrelationContext

    info = descrever_erro(exc) if exc is not None else None
    texto = message or (mensagem_usuario(exc) if exc is not None else 'Erro inesperado.')
    payload = {
        'status': 'error',
        'message': texto,
        'mensagem': texto,
        'codigo': code,
        'request_id': CorrelationContext.get_request_id(),
    }
    if info is not None:
        payload.setdefault('error_type', info.tipo.value)
        payload.setdefault('acao', info.acao)
    payload.update(extra)
    return jsonify(payload), code


def mtime_para_datetime_local(caminho):
    """mtime do arquivo em `caminho` como datetime local (naive), ou None.

    Retorna None quando o caminho e vazio/None, o arquivo nao existe, ou ha
    erro de I/O. Usa a mesma convencao de hora local do carimbo ao vivo
    (`datetime.now`) para o backfill de Certidao.atualizado_em ficar coerente.
    """
    if not caminho:
        return None
    try:
        return datetime.fromtimestamp(os.path.getmtime(caminho))
    except OSError:
        return None


# --- documentos do tomador (CPF/CNPJ) --------------------------------------
# Vive aqui, e nao nas rotas da NFSe, porque a resolucao do import, a rota de
# vinculo manual e a automacao precisam da MESMA regra. Duas implementacoes
# divergindo emitiriam nota fiscal no documento errado.

TIPO_CPF = 'cpf'
TIPO_CNPJ = 'cnpj'


def so_digitos(valor):
    return ''.join(c for c in str(valor or '') if c.isdigit())


def detectar_tipo_documento(valor):
    """'cpf' (11 digitos), 'cnpj' (14) ou None. Detecta pelo tamanho."""
    n = so_digitos(valor)
    if len(n) == 11:
        return TIPO_CPF
    if len(n) == 14:
        return TIPO_CNPJ
    return None


def _digitos_verificadores_ok(numeros, tamanhos, pesos_iniciais):
    for tamanho, peso_inicial in zip(tamanhos, pesos_iniciais):
        soma = sum(int(d) * (peso_inicial - i) for i, d in enumerate(numeros[:tamanho]))
        resto = (soma * 10) % 11 % 10
        if int(numeros[tamanho]) != resto:
            return False
    return True


def cpf_valido(valor):
    n = so_digitos(valor)
    if len(n) != 11 or n == n[0] * 11:
        return False
    return _digitos_verificadores_ok(n, (9, 10), (10, 11))


def cnpj_valido(valor):
    n = so_digitos(valor)
    if len(n) != 14 or n == n[0] * 14:
        return False
    for tamanho in (12, 13):
        pesos = list(range(tamanho - 7, 1, -1)) + list(range(9, 1, -1))
        soma = sum(int(d) * p for d, p in zip(n[:tamanho], pesos))
        resto = soma % 11
        digito = 0 if resto < 2 else 11 - resto
        if int(n[tamanho]) != digito:
            return False
    return True


def documento_valido(valor):
    tipo = detectar_tipo_documento(valor)
    if tipo == TIPO_CPF:
        return cpf_valido(valor)
    if tipo == TIPO_CNPJ:
        return cnpj_valido(valor)
    return False


def formatar_documento(valor):
    """Mascara conforme o tipo. Devolve o valor cru se nao for CPF nem CNPJ."""
    n = so_digitos(valor)
    tipo = detectar_tipo_documento(n)
    if tipo == TIPO_CPF:
        return f'{n[:3]}.{n[3:6]}.{n[6:9]}-{n[9:]}'
    if tipo == TIPO_CNPJ:
        return f'{n[:2]}.{n[2:5]}.{n[5:8]}/{n[8:12]}-{n[12:]}'
    return str(valor or '').strip()
