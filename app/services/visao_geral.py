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


# O que conta como "trava" — e o que NAO conta.
#
# Trava = o sistema (ou uma frente inteira) parou e so uma acao humana destrava:
# certificado VENCIDO (a manifestacao daquela empresa nao roda ate renovar),
# portal aberto no breaker (a emissao naquele alvo esta pausada) e grupo de
# notas esperando confirmacao (as notas do grupo ficam FORA da fila enquanto a
# proposta espera, ND-005).
#
# NAO travam: certidao vencida, nota a emitir, tarefa em falha. Isso e TRABALHO,
# e trabalho e o que a tela toda ja mostra. Se tudo virasse "trava", a faixa
# viraria um segundo resumo e perderia a unica funcao que tem — dizer, no dia
# calmo, que nao ha nada.
#
# Certificado VENCENDO tambem nao entra: e aviso, nao parede. Ele aparece no
# cartao dos certificados, com os dias restantes.

def itens_que_travam(blocos):
    """O que impede trabalho hoje, DERIVADO dos blocos ja montados.

    Nao consulta nada: recebe o que `montar` produziu. E o que faz a contagem da
    faixa ser verdadeira por construcao — ela e o `len()` desta lista, nunca um
    contador proprio que possa divergir do que esta listado logo abaixo.

    Bloco com `erro` nao contribui e nao quebra a derivacao: nao saber se ha
    trava e diferente de nao haver trava, e quem diz isso e o proprio bloco, na
    sua area da tela.
    """
    itens = []

    certificados = blocos.get('certificados') or {}
    if not certificados.get('erro'):
        for cert in certificados.get('itens') or []:
            if cert.get('causa') != 'vencido':
                continue
            itens.append({
                'tom': 'danger',
                'rotulo': 'vencido',
                'titulo': f"Certificado A1 de {cert.get('empresa_nome') or '?'}",
                'detalhe': 'A manifestacao dessa empresa nao roda ate renovar.',
                'destino': 'main.manifestador_painel',
            })

    fila = blocos.get('fila') or {}
    if not fila.get('erro'):
        for breaker in fila.get('breakers') or []:
            itens.append({
                'tom': 'danger',
                'rotulo': 'pausado',
                'titulo': f"{breaker.get('alvo')} pausado pelo circuit breaker",
                'detalhe': 'A emissao nesse portal esta parada. O bloqueio expira '
                           'sozinho; nada precisa ser religado.',
                'destino': 'main.diagnostico',
            })

    nfse = blocos.get('nfse') or {}
    if not nfse.get('erro') and nfse.get('grupos_pendentes'):
        total = nfse['grupos_pendentes']
        itens.append({
            'tom': 'pend',
            'rotulo': 'aguarda voce',
            'titulo': f"{total} grupo{'s' if total != 1 else ''} de notas "
                      f"esperando confirmacao",
            'detalhe': 'As notas do grupo ficam fora da fila ate voce decidir.',
            'destino': 'main.nfse_painel',
        })

    return itens
