"""Consulta de CNPJ na Receita: BrasilAPI com fallback ReceitaWS (spec 08).

Camada de REDE, sem Flask e sem banco. As duas fontes devolvem formatos
diferentes e a diferenca morre aqui: tudo vira `DadosReceitaDTO` antes de subir.
E o mesmo padrao do `LinhaExtrato` da NFSe — normaliza na borda para que nada
abaixo ramifique por fonte.

Tres armadilhas descobertas consultando as APIs de verdade (nao presumidas):

1. A BrasilAPI devolve **403** para cliente sem `User-Agent`. Nao e rate limit,
   e bloqueio de anonimo — sem o header, toda consulta falharia.
2. A ReceitaWS sinaliza erro com `{"status": "ERROR"}` no CORPO, com **HTTP
   200**. Checar so o codigo HTTP daria por boa uma resposta de erro.
3. As datas divergem: a BrasilAPI manda ISO (`2005-11-03`), a ReceitaWS manda
   BR (`03/11/2005`). O CEP idem (`20031170` x `20.031-170`).

Nunca levanta: em qualquer falha devolve (None, motivo), espelhando o
best-effort do `email_sender` (AD-011). Cadastro nunca pode cair por causa de
API externa.
"""
from dataclasses import dataclass, fields
from datetime import datetime

import requests

from app.services.execution_logger import log_event
from app.utils import so_digitos

URL_BRASILAPI = 'https://brasilapi.com.br/api/cnpj/v1/{cnpj}'
URL_RECEITAWS = 'https://receitaws.com.br/v1/cnpj/{cnpj}'

# Sem User-Agent a BrasilAPI responde 403 (verificado ao vivo).
USER_AGENT = 'Zelo-Certidoes/1.0 (+escritorio-contabil)'
TIMEOUT_S = 8

# Motivos de falha — o chamador precisa distinguir "esse CNPJ nao existe" de
# "nao consegui perguntar", porque a mensagem ao operador e outra.
ERRO_NAO_ENCONTRADO = 'nao_encontrado'
ERRO_INDISPONIVEL = 'indisponivel'
ERRO_CNPJ_INVALIDO = 'cnpj_invalido'


@dataclass
class DadosReceitaDTO:
    """Resposta normalizada, independente da fonte. Espelha `DadosReceita`."""
    cnpj: str = ''
    situacao: str = None
    situacao_data: object = None
    situacao_motivo: str = None
    razao_social: str = None
    nome_fantasia: str = None
    data_inicio_atividade: object = None
    porte: str = None
    natureza_juridica: str = None
    matriz_filial: str = None
    logradouro: str = None
    numero: str = None
    complemento: str = None
    bairro: str = None
    cep: str = None
    municipio_ibge: str = None
    municipio: str = None
    uf: str = None
    cnae_fiscal: str = None
    cnae_descricao: str = None
    opcao_simples: bool = None
    opcao_simples_data: object = None
    fonte: str = None

    def como_dict(self):
        return {campo.name: getattr(self, campo.name) for campo in fields(self)}


def _texto(valor):
    """Normaliza para str com conteudo, ou None. String vazia da Receita ('' em
    motivo_situacao/complemento) vira None para nao poluir a tela."""
    if valor is None:
        return None
    texto = str(valor).strip()
    return texto or None


def _data(valor):
    """Aceita ISO ('2005-11-03') e BR ('03/11/2005') — as duas fontes divergem."""
    texto = _texto(valor)
    if not texto:
        return None
    texto = texto[:10]
    for formato in ('%Y-%m-%d', '%d/%m/%Y'):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    return None


def _cep(valor):
    """A BrasilAPI manda '20031170', a ReceitaWS '20.031-170'."""
    digitos = so_digitos(valor)
    return digitos or None


def _normalizar_brasilapi(payload):
    # O tipo do logradouro vem separado ('AVENIDA' + 'REPUBLICA DO CHILE'),
    # diferente da ReceitaWS, que ja manda junto.
    partes = [_texto(payload.get('descricao_tipo_de_logradouro')),
              _texto(payload.get('logradouro'))]
    logradouro = ' '.join(p for p in partes if p) or None

    cnae = payload.get('cnae_fiscal')
    return DadosReceitaDTO(
        cnpj=so_digitos(payload.get('cnpj')),
        situacao=_texto(payload.get('descricao_situacao_cadastral')),
        situacao_data=_data(payload.get('data_situacao_cadastral')),
        situacao_motivo=_texto(payload.get('descricao_motivo_situacao_cadastral')),
        razao_social=_texto(payload.get('razao_social')),
        nome_fantasia=_texto(payload.get('nome_fantasia')),
        data_inicio_atividade=_data(payload.get('data_inicio_atividade')),
        porte=_texto(payload.get('porte')),
        natureza_juridica=_texto(payload.get('natureza_juridica')),
        matriz_filial=_texto(payload.get('descricao_identificador_matriz_filial')),
        logradouro=logradouro,
        numero=_texto(payload.get('numero')),
        complemento=_texto(payload.get('complemento')),
        bairro=_texto(payload.get('bairro')),
        cep=_cep(payload.get('cep')),
        municipio_ibge=_texto(payload.get('codigo_municipio_ibge')),
        municipio=_texto(payload.get('municipio')),
        uf=_texto(payload.get('uf')),
        cnae_fiscal=_texto(cnae),
        cnae_descricao=_texto(payload.get('cnae_fiscal_descricao')),
        opcao_simples=payload.get('opcao_pelo_simples'),
        opcao_simples_data=_data(payload.get('data_opcao_pelo_simples')),
        fonte='brasilapi',
    )


def _normalizar_receitaws(payload):
    atividade = (payload.get('atividade_principal') or [{}])[0]
    simples = payload.get('simples') or {}
    return DadosReceitaDTO(
        cnpj=so_digitos(payload.get('cnpj')),
        situacao=_texto(payload.get('situacao')),
        situacao_data=_data(payload.get('data_situacao')),
        situacao_motivo=_texto(payload.get('motivo_situacao')),
        razao_social=_texto(payload.get('nome')),
        nome_fantasia=_texto(payload.get('fantasia')),
        data_inicio_atividade=_data(payload.get('abertura')),
        porte=_texto(payload.get('porte')),
        natureza_juridica=_texto(payload.get('natureza_juridica')),
        matriz_filial=_texto(payload.get('tipo')),
        logradouro=_texto(payload.get('logradouro')),
        numero=_texto(payload.get('numero')),
        complemento=_texto(payload.get('complemento')),
        bairro=_texto(payload.get('bairro')),
        cep=_cep(payload.get('cep')),
        # A ReceitaWS nao devolve codigo IBGE do municipio.
        municipio_ibge=None,
        municipio=_texto(payload.get('municipio')),
        uf=_texto(payload.get('uf')),
        cnae_fiscal=_texto(atividade.get('code')),
        cnae_descricao=_texto(atividade.get('text')),
        opcao_simples=simples.get('optante'),
        opcao_simples_data=_data(simples.get('data_opcao')),
        fonte='receitaws',
    )


def _buscar(url, cnpj, fonte):
    """(payload, erro). Nunca levanta."""
    try:
        resp = requests.get(url.format(cnpj=cnpj),
                            headers={'User-Agent': USER_AGENT},
                            timeout=TIMEOUT_S)
    except Exception as exc:
        log_event('receita_consulta_falhou', level='WARNING', fonte=fonte,
                  error=str(exc))
        return None, ERRO_INDISPONIVEL

    if resp.status_code == 404:
        return None, ERRO_NAO_ENCONTRADO
    if resp.status_code != 200:
        log_event('receita_consulta_http_erro', level='WARNING', fonte=fonte,
                  status=resp.status_code)
        return None, ERRO_INDISPONIVEL

    try:
        payload = resp.json()
    except Exception as exc:
        log_event('receita_consulta_json_invalido', level='WARNING', fonte=fonte,
                  error=str(exc))
        return None, ERRO_INDISPONIVEL

    if not isinstance(payload, dict):
        return None, ERRO_INDISPONIVEL

    # A ReceitaWS erra com HTTP 200 e `status: ERROR` no corpo — checar so o
    # codigo HTTP daria a resposta de erro por boa.
    if str(payload.get('status') or '').upper() == 'ERROR':
        mensagem = str(payload.get('message') or '').lower()
        if 'nao encontrado' in mensagem or 'não encontrado' in mensagem:
            return None, ERRO_NAO_ENCONTRADO
        log_event('receita_consulta_status_erro', level='WARNING', fonte=fonte,
                  mensagem=payload.get('message'))
        return None, ERRO_INDISPONIVEL

    return payload, None


def consultar(cnpj):
    """Consulta o CNPJ na Receita. Retorna `(DadosReceitaDTO, None)` em sucesso
    ou `(None, motivo)` em falha.

    Tenta a BrasilAPI e cai para a ReceitaWS se ela estiver indisponivel. CNPJ
    inexistente NAO tenta a segunda fonte: as duas leem o mesmo cadastro, entao
    reperguntar so gastaria cota da fonte com rate limit mais apertado.
    """
    limpo = so_digitos(cnpj)
    if len(limpo) != 14:
        return None, ERRO_CNPJ_INVALIDO

    payload, erro = _buscar(URL_BRASILAPI, limpo, 'brasilapi')
    if payload is not None:
        return _normalizar_brasilapi(payload), None
    if erro == ERRO_NAO_ENCONTRADO:
        return None, ERRO_NAO_ENCONTRADO

    payload, erro_fallback = _buscar(URL_RECEITAWS, limpo, 'receitaws')
    if payload is not None:
        log_event('receita_consulta_fallback', fonte='receitaws')
        return _normalizar_receitaws(payload), None

    return None, erro_fallback
