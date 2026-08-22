"""Composicao dos blocos da pagina inicial Visao Geral.

A pagina nao e fonte de dados: cada bloco reaproveita a pergunta que ja e
respondida no respectivo pilar. Assim, ela nao faz rede nem cria uma segunda
regra para os mesmos numeros.
"""
from app.models import PapelUsuario
from app.services import (
    agendador,
    circuit_breaker,
    export_service,
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


# Os quatro baldes que sao TRABALHO. `validas` fica de fora porque nao pede
# nada; `sem_data` fica DENTRO porque certidao sem validade e desconhecida, e
# desconhecido pede conferencia (mesma leitura do chip `nao_definida` na tela de
# Certidoes).
_BALDES_DE_ATENCAO = ('vencidas', 'a_vencer', 'pendentes', 'sem_data')


def _certidoes():
    contagem = snapshot_service.contagem_carteira()
    atencao = sum(contagem[balde] for balde in _BALDES_DE_ATENCAO)
    return {
        **contagem,
        'total': sum(contagem.values()),
        'atencao': atencao,
        # `vazio` fala de ATENCAO, nao da existencia de certidoes. Um `any()`
        # sobre o dict inteiro ficaria verdadeiro em qualquer carteira saudavel
        # — desde que a contagem passou a trazer `validas` — e o estado vazio do
        # cartao sumiria em silencio, sem nenhum teste ficar vermelho.
        'vazio': not atencao,
    }


def _certificados():
    estados = manifestador_cofre.estado_da_carteira()
    # uma consulta so devolve a lista E a populacao de onde ela sai: sem o
    # denominador, "nenhum vencendo" e uma ausencia; com ele vira afirmacao.
    resumo = manifestador_cofre.resumo_de_vencimento()
    inventariado = bool(estados)
    return {
        'itens': resumo['itens'],
        'com_vencimento': resumo['com_vencimento'],
        'janela_dias': resumo['janela_dias'],
        'inventariado': inventariado,
        'vazio': inventariado and not resumo['itens'],
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


# Janela do acumulado da faixa. Fixa aqui, e nao em parametro de rota: o
# `coletar_produtividade` carrega os lotes do periodo em Python, o que e
# irrelevante em 7 dias e deixa de ser em 90.
_DIAS_DA_SEMANA = 7


def _semana():
    """Emitidas nos ultimos 7 dias e quanto disso saiu sem ninguem clicar.

    Reusa `coletar_produtividade`, que ja e a fonte da tela de Produtividade:
    "emitidas" tem UMA definicao no projeto, e o recorte por origem e o mesmo
    `por_origem` do AD-019.
    """
    dados = export_service.coletar_produtividade(_DIAS_DA_SEMANA)
    total = dados['total_emissoes']
    do_agendador = dados['por_origem']['agendador']['emissoes']
    return {
        'emitidas': total,
        # `None`, e nao 0%: sem nenhuma emissao na semana nao ha fracao que
        # signifique alguma coisa, e "0% automatico" leria como falha.
        'pct_agendador': round(100 * do_agendador / total) if total else None,
    }


def _producao():
    """O que o agendador fez na ultima passagem, e quando roda de novo.

    `situacao` e campo EXPLICITO, nao inferido de zeros, porque tres fatos
    diferentes produzem os mesmos zeros e so um deles e boa noticia:

    - `desligado`: a renovacao automatica esta off. Nao ha passagem a contar.
    - `sem_registro`: esta ligada e nenhum lote consta desde a ultima passagem.
      Nao da para saber se o PC ficou desligado ou se nao havia o que renovar —
      e desconhecido nao vira zero (AD-026).
    - `ok`: rodou, e os numeros sao dela.

    O acumulado de 7 dias sai nos tres casos: producao passada e fato, mesmo com
    o agendador desligado hoje.
    """
    proxima = agendador.proxima_execucao()
    semana = _semana()
    if proxima is None:
        return {'situacao': 'desligado', 'proxima': None, 'semana': semana}

    inicio_local, corte = agendador.janela_ultima_passagem()
    passagem = export_service.coletar_producao_agendador(corte)
    return {
        **passagem,
        'situacao': 'ok' if passagem['lotes'] else 'sem_registro',
        'inicio_local': inicio_local,
        'proxima': proxima,
        'semana': semana,
    }


def montar(usuario):
    """Monta os blocos que o papel do usuario pode acessar."""
    blocos = {
        'certidoes': _bloco('certidoes', _certidoes),
        'certificados': _bloco('certificados', _certificados),
        # fora do gate de papel: o destino da faixa e /produtividade, que e
        # `leitura` (AD-012) — a regra e o papel da tela de destino (OVER-09).
        'producao': _bloco('producao', _producao),
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
