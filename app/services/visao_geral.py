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


# O que conta como "trava" — e o que NÃO conta.
#
# Trava = o sistema (ou uma frente inteira) parou e só uma ação humana destrava:
# certificado VENCIDO (a manifestação daquela empresa não roda até renovar),
# portal aberto no breaker (a emissão naquele alvo está pausada) e grupo de
# notas esperando confirmação (as notas do grupo ficam FORA da fila enquanto a
# proposta espera, ND-005).
#
# NÃO travam: certidão vencida, nota a emitir, tarefa em falha. Isso é TRABALHO,
# e trabalho é o que a tela toda já mostra. Se tudo virasse "trava", a faixa
# viraria um segundo resumo e perderia a única função que tem — dizer, no dia
# calmo, que não há nada.
#
# Certificado VENCENDO também não entra: é aviso, não parede. Ele aparece no
# cartão dos certificados, com os dias restantes.
#
# A SAÍDA É AGRUPADA POR TIPO, e isso não é economia de pixel: dez certificados
# vencidos são UM problema com dez casos, não dez problemas. A ação é a mesma
# (renovar), o destino é o mesmo (o Manifestador) e a decisão do operador é uma
# só. Uma linha por caso fazia a faixa crescer sem limite — com 30 casos ela
# tomava a tela inteira e empurrava para fora justamente os cartões que dizem o
# que fazer.

# Quantos nomes a faixa cita antes de resumir o resto em "+N". Oito cabe em duas
# linhas mesmo em tela estreita; o resto o operador vê na tela de destino, que é
# onde ele vai agir de qualquer forma.
_NOMES_NA_FAIXA = 8


def _grupo(tipo, tom, rotulo, titulo, detalhe, destino, nomes):
    """Uma linha da faixa: um TIPO de problema, com quantos casos e quais."""
    nomes = [n for n in nomes if n]
    return {
        'tipo': tipo,
        'tom': tom,
        'rotulo': rotulo,
        'quantidade': len(nomes),
        'titulo': titulo,
        'detalhe': detalhe,
        'destino': destino,
        'nomes': nomes[:_NOMES_NA_FAIXA],
        'restantes': max(0, len(nomes) - _NOMES_NA_FAIXA),
    }


def itens_que_travam(blocos):
    """O que impede trabalho hoje, DERIVADO dos blocos já montados.

    Não consulta nada: recebe o que `montar` produziu. É o que faz a contagem da
    faixa ser verdadeira por construção — o total é a soma das quantidades dos
    grupos, e cada grupo mostra a sua, então o número do cabeçalho sempre bate
    com o que está listado logo abaixo.

    Bloco com `erro` não contribui e não quebra a derivação: não saber se há
    trava é diferente de não haver trava, e quem diz isso é o próprio bloco, na
    sua área da tela.
    """
    grupos = []

    certificados = blocos.get('certificados') or {}
    if not certificados.get('erro'):
        vencidos = [c.get('empresa_nome') for c in certificados.get('itens') or []
                    if c.get('causa') == 'vencido']
        if vencidos:
            um = len(vencidos) == 1
            grupos.append(_grupo(
                'certificado_vencido', 'danger', 'vencido' if um else 'vencidos',
                'Certificado A1 vencido' if um else 'Certificados A1 vencidos',
                'A manifestação dessa empresa não roda até renovar.' if um else
                'A manifestação dessas empresas não roda até renovar.',
                'main.manifestador_painel', vencidos))

    fila = blocos.get('fila') or {}
    if not fila.get('erro'):
        alvos = [b.get('alvo') for b in fila.get('breakers') or []]
        if alvos:
            um = len(alvos) == 1
            grupos.append(_grupo(
                'portal_pausado', 'danger', 'pausado' if um else 'pausados',
                'Portal pausado pelo circuit breaker' if um else
                'Portais pausados pelo circuit breaker',
                'A emissão está parada nele. O bloqueio expira sozinho; nada '
                'precisa ser religado.' if um else
                'A emissão está parada neles. O bloqueio expira sozinho; nada '
                'precisa ser religado.',
                'main.diagnostico', alvos))

    nfse = blocos.get('nfse') or {}
    if not nfse.get('erro') and nfse.get('grupos_pendentes'):
        total = nfse['grupos_pendentes']
        um = total == 1
        grupo = _grupo(
            'grupo_nfse', 'pend', 'aguarda você',
            'Grupo de notas esperando confirmação' if um else
            'Grupos de notas esperando confirmação',
            'As notas do grupo ficam fora da fila até você decidir.' if um else
            'As notas desses grupos ficam fora da fila até você decidir.',
            'main.nfse_painel', [])
        # aqui não há nomes a citar: a proposta é identificada na tela da NFSe,
        # e o que importa na faixa é que existem N decisões penduradas
        grupo['quantidade'] = total
        grupos.append(grupo)

    return grupos


def total_de_travas(grupos):
    """Quantos CASOS travam trabalho — a soma das quantidades dos grupos.

    O cabeçalho da faixa mostra este número e cada grupo mostra o seu, então os
    dois nunca podem discordar do que está na tela.
    """
    return sum(grupo.get('quantidade') or 0 for grupo in grupos or [])
