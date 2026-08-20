"""Composicao dos blocos da pagina inicial Visao Geral.

A pagina nao e fonte de dados: cada bloco reaproveita a pergunta que ja e
respondida no respectivo pilar. Assim, ela nao faz rede nem cria uma segunda
regra para os mesmos numeros.
"""
from app.models import PapelUsuario
from app.services import (
    circuit_breaker,
    fila_emissao,
    manifestador_cofre,
    nfse_import,
    snapshot_service,
)
from app.services.execution_logger import log_event


def _bloco(nome, fn):
    """Executa uma fonte sem deixar sua falha derrubar a pagina inicial."""
    try:
        return fn()
    except Exception as exc:
        log_event('visao_geral_bloco_falhou', level='ERROR', bloco=nome,
                  error=str(exc))
        return {'erro': True, 'nome': nome}


def _certidoes():
    contagem = snapshot_service.contagem_carteira()
    return {
        **contagem,
        'vazio': not any(contagem.values()),
    }


def _certificados():
    estados = manifestador_cofre.estado_da_carteira()
    itens = manifestador_cofre.certificados_a_vencer()
    inventariado = bool(estados)
    return {
        'itens': itens,
        'inventariado': inventariado,
        'vazio': inventariado and not itens,
    }


def _nfse():
    contagem = nfse_import.contagem_fila()
    return {
        **contagem,
        'vazio': not any(contagem.values()),
    }


def _fila():
    grupos = fila_emissao.agrupar_falhas()
    breakers = circuit_breaker.abertos()
    total_falhas = sum(grupo['total'] for grupo in grupos)
    return {
        'falhas': total_falhas,
        'motivo': grupos[0]['titulo'] if grupos else None,
        'grupos': grupos,
        'breakers': breakers,
        'vazio': not (total_falhas or breakers),
    }


def montar(usuario):
    """Monta os blocos que o papel do usuario pode acessar."""
    blocos = {
        'certidoes': _bloco('certidoes', _certidoes),
        'certificados': _bloco('certificados', _certificados),
    }
    if getattr(usuario, 'papel', None) in (
        PapelUsuario.OPERADOR,
        PapelUsuario.ADMIN,
    ):
        blocos['nfse'] = _bloco('nfse', _nfse)
        blocos['fila'] = _bloco('fila', _fila)
    return blocos
