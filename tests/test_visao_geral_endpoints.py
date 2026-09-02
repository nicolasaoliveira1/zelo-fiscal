"""A pagina inicial (`/`) e o novo endereco da carteira (`/certidoes`).

O que estes testes protegem: que abrir o sistema cai na Visao Geral e NAO na
tabela de certidoes, que um bloco com fonte quebrada nao derruba a pagina, e que
o papel decide quais blocos aparecem — mostrar um numero e negar o clique seria
pior que nao mostrar.
"""
from datetime import datetime

import pytest

from app.services import visao_geral

# A faixa de produção precisa existir em TODO render: o template a lê sempre.
# Default = agendador ligado e nada registrado desde a passagem — o dia mudo, que
# é justamente o caso que a feature existe para deixar de ser mudo.
_PRODUCAO_PADRAO = {
    'situacao': 'sem_registro',
    'inicio_local': datetime(2026, 8, 22, 3, 0),
    'proxima': datetime(2026, 8, 23, 3, 0),
    'lotes': 0, 'emitidas': 0, 'falhas': 0, 'em_andamento': 0, 'tipos': [],
    'semana': {'emitidas': 0, 'pct_agendador': None},
}


def _sem_fontes(monkeypatch, **kw):
    """Todas as fontes vazias, salvo o que o teste sobrescrever."""
    blocos = kw.get('blocos', {
        'certidoes': {'vencidas': 0, 'a_vencer': 0,
                      'pendentes': 0, 'validas': 0,
                      'sem_data': 0, 'total': 0,
                      'atencao': 0, 'vazio': True},
        'certificados': {'itens': [], 'inventariado': True,
                         'vazio': True, 'com_vencimento': 0,
                         'janela_dias': 30},
    })
    blocos.setdefault('producao', dict(_PRODUCAO_PADRAO))
    monkeypatch.setattr(visao_geral, 'montar', lambda usuario: blocos)


def test_raiz_abre_a_visao_geral_e_nao_a_tabela(app, ids, client, monkeypatch):
    _sem_fontes(monkeypatch)

    resposta = client.get('/')
    corpo = resposta.get_data(as_text=True)

    assert resposta.status_code == 200
    assert 'Visão geral' in corpo
    # a tabela de certidoes tem filtros por status; a Visao Geral nao
    assert 'id="tabelaCertidoes"' not in corpo


def test_carteira_responde_no_endereco_novo(app, ids, client):
    assert client.get('/certidoes').status_code == 200


def test_saudacao_segue_a_hora_do_servidor(app, ids, client, monkeypatch):
    from datetime import datetime

    from app.routes import _saudacao

    assert _saudacao(datetime(2026, 8, 20, 8, 0)) == 'Bom dia'
    assert _saudacao(datetime(2026, 8, 20, 13, 0)) == 'Boa tarde'
    assert _saudacao(datetime(2026, 8, 20, 21, 0)) == 'Boa noite'


def test_saudacao_formata_nome_e_dia_da_semana(app, ids, client):
    from datetime import date

    from app.routes import _data_extenso, _nome_exibicao

    assert _nome_exibicao(' nICOLAS ') == 'Nicolas'
    assert _data_extenso(date(2026, 8, 20)) == 'Quinta-feira, 20/08/2026'


def test_saudacao_renderiza_o_usuario_capitalizado(app, ids, client):
    from datetime import date

    from app.routes import _data_extenso

    corpo = client.get('/').get_data(as_text=True)

    assert f'Admin_test · {_data_extenso(date.today())}' in corpo


def test_dia_da_semana_sai_acentuado(app, ids, client):
    """`strftime('%A')` volta em ingles no Windows do escritorio, entao os nomes
    sao escritos a mao — e escritos a mao e onde o acento se perde."""
    from app.routes import _DIAS_SEMANA

    assert 'terça-feira' in _DIAS_SEMANA
    assert 'sábado' in _DIAS_SEMANA
    assert not any(dia in ('terca-feira', 'sabado') for dia in _DIAS_SEMANA)


def test_dia_calmo_diz_que_nada_trava(app, ids, client, monkeypatch):
    """A faixa muda de estado; ela nao some. A pagina precisa DIZER que esta
    tudo bem, e nao apenas deixar de dizer que esta mal."""
    _sem_fontes(monkeypatch)

    corpo = client.get('/').get_data(as_text=True)

    assert 'Nada trava trabalho hoje' in corpo


def test_certificado_vencido_aparece_na_faixa(app, ids, client, monkeypatch):
    from datetime import datetime

    _sem_fontes(monkeypatch, blocos={
        'certidoes': {'vencidas': 0, 'a_vencer': 0, 'pendentes': 0, 'vazio': True},
        'certificados': {'inventariado': True, 'vazio': False,
                         'com_vencimento': 12, 'janela_dias': 30, 'itens': [
            {'empresa_nome': 'ACME', 'causa': 'vencido', 'dias_restantes': -3,
             'not_after': datetime(2026, 8, 17, 9, 0)}]},
    })

    corpo = client.get('/').get_data(as_text=True)

    assert 'trava trabalho hoje' in corpo
    assert 'ACME' in corpo


def test_bloco_com_erro_nao_derruba_a_pagina(app, ids, client, monkeypatch):
    """OVER-07: a fonte quebrada vira um aviso NAQUELE cartao, e os outros
    blocos continuam renderizando com o conteudo que tinham."""
    _sem_fontes(monkeypatch, blocos={
        'certidoes': {'vencidas': 4, 'a_vencer': 0, 'pendentes': 0, 'vazio': False},
        'certificados': {'erro': True, 'nome': 'certificados'},
    })

    resposta = client.get('/')
    corpo = resposta.get_data(as_text=True)

    assert resposta.status_code == 200
    assert 'Não consegui ler este bloco agora' in corpo
    assert 'vg-mosaico is-a1-curto' in corpo
    assert '>4<' in corpo or '4' in corpo


def test_cofre_nunca_inventariado_nao_diz_zero_vencendo(app, ids, client,
                                                        monkeypatch):
    _sem_fontes(monkeypatch, blocos={
        'certidoes': {'vencidas': 0, 'a_vencer': 0, 'pendentes': 0, 'vazio': True},
        'certificados': {'itens': [], 'inventariado': False, 'vazio': False,
                         'com_vencimento': 0, 'janela_dias': 30},
    })

    corpo = client.get('/').get_data(as_text=True)

    assert 'ainda não foi inventariado' in corpo
    assert 'Nenhum vencendo' not in corpo


def test_menu_usa_um_nome_so_para_a_pagina_inicial(app, ids, client, monkeypatch):
    """O item do menu e o titulo da pagina dizem a MESMA coisa.

    Havia dois nomes para a mesma tela: o menu dizia "Início" e o <title>,
    o design language e a propria rota (visao_geral_painel) diziam "Visão
    geral". "Início" nomeia onde a tela FICA; "Visão geral" nomeia o que
    ela E — e e o nome que o resto do projeto ja usava.
    """
    _sem_fontes(monkeypatch)

    corpo = client.get('/').get_data(as_text=True)

    assert '<span class="sidebar-rotulo">Visão geral</span>' in corpo
    assert '<span class="sidebar-rotulo">Certidões</span>' in corpo
    assert 'Dashboard' not in corpo
    assert 'Início' not in corpo


# --- o numero em destaque tem escala, e o quarto estado existe --------------

def _com_carteira(monkeypatch, **contagem):
    base = {'vencidas': 0, 'a_vencer': 0, 'pendentes': 0, 'validas': 0,
            'sem_data': 0}
    base.update(contagem)
    atencao = base['vencidas'] + base['a_vencer'] + base['pendentes'] + base['sem_data']
    bloco = {**base, 'total': sum(base.values()), 'atencao': atencao,
             'vazio': not atencao}
    _sem_fontes(monkeypatch, blocos={
        'certidoes': bloco,
        'certificados': {'itens': [], 'inventariado': True, 'vazio': True,
                         'com_vencimento': 0, 'janela_dias': 30},
    })
    return bloco


def test_cartao_de_certidoes_mostra_o_denominador(app, ids, client, monkeypatch):
    """89 de 1240 e uma frase; 89 sozinho nao diz se e crise ou terca-feira."""
    _com_carteira(monkeypatch, vencidas=12, a_vencer=45, pendentes=25,
                  sem_data=7, validas=1151)

    corpo = client.get('/').get_data(as_text=True)

    assert 'de 1240 pedem atenção' in corpo
    assert '>89<' in corpo  # a soma dos quatro baldes de atencao


def test_o_quarto_estado_aparece_na_legenda(app, ids, client, monkeypatch):
    _com_carteira(monkeypatch, vencidas=1, sem_data=7, validas=10)

    corpo = client.get('/').get_data(as_text=True)

    assert 'sem data' in corpo
    assert 'is-muted' in corpo


def test_cada_chip_leva_ao_seu_recorte_na_tela_de_certidoes(app, ids, client,
                                                            monkeypatch):
    _com_carteira(monkeypatch, vencidas=1, a_vencer=1, pendentes=1, sem_data=1)

    corpo = client.get('/').get_data(as_text=True)

    for filtro in ('vencidas', 'a_vencer', 'pendentes', 'nao_definida'):
        assert f'/certidoes?status={filtro}' in corpo


def test_carteira_sem_nenhuma_certidao_nao_diz_de_zero(app, ids, client,
                                                       monkeypatch):
    """Carteira vazia e carteira em dia nao sao a mesma frase — o mesmo erro do
    "0 vencendo" num cofre que nunca foi inventariado."""
    _com_carteira(monkeypatch)

    corpo = client.get('/').get_data(as_text=True)

    assert 'Nenhuma certidão cadastrada' in corpo
    assert 'de 0 pedem atenção' not in corpo


def test_carteira_em_dia_nao_diz_que_esta_vazia(app, ids, client, monkeypatch):
    _com_carteira(monkeypatch, validas=1240)

    corpo = client.get('/').get_data(as_text=True)

    assert 'Nenhuma certidão pede atenção hoje' in corpo
    assert 'Nenhuma certidão cadastrada' not in corpo


# --- o cartao de certificados diz DE QUANTOS nenhum vence -------------------

def _cofre(monkeypatch, **kw):
    bloco = {'itens': [], 'inventariado': True, 'vazio': True,
             'com_vencimento': 0, 'janela_dias': 30}
    bloco.update(kw)
    _sem_fontes(monkeypatch, blocos={
        'certidoes': {'vencidas': 0, 'a_vencer': 0, 'pendentes': 0,
                      'validas': 0, 'sem_data': 0, 'total': 0, 'atencao': 0,
                      'vazio': True},
        'certificados': bloco,
    })


def test_cofre_em_dia_diz_de_quantos_nenhum_vence(app, ids, client, monkeypatch):
    _cofre(monkeypatch, com_vencimento=24, janela_dias=30)

    corpo = client.get('/').get_data(as_text=True)

    assert 'Nenhum dos 24 certificados vence' in corpo
    assert '30 dias' in corpo


def test_um_certificado_so_nao_vira_nenhum_dos_1(app, ids, client, monkeypatch):
    _cofre(monkeypatch, com_vencimento=1)

    corpo = client.get('/').get_data(as_text=True)

    assert 'Nenhum dos 1' not in corpo
    assert 'O único certificado com vencimento conhecido' in corpo


def test_cofre_sem_nenhum_vencimento_conhecido_nao_diz_nenhum_dos_zero(
        app, ids, client, monkeypatch):
    _cofre(monkeypatch, com_vencimento=0)

    corpo = client.get('/').get_data(as_text=True)

    assert 'Nenhum certificado tem vencimento conhecido' in corpo
    assert 'Nenhum dos 0' not in corpo


def test_cofre_nao_inventariado_nao_ganha_denominador(app, ids, client,
                                                      monkeypatch):
    """Nao inventariado vem ANTES do denominador: nao se sabe quantos existem,
    entao nao ha de quantos nenhum vence."""
    _cofre(monkeypatch, inventariado=False, vazio=False)

    corpo = client.get('/').get_data(as_text=True)

    assert 'cofre ainda não foi inventariado' in corpo
    assert 'vence nos próximos' not in corpo


# --- a faixa de producao: o contrapeso no rodape ----------------------------

def _com_producao(monkeypatch, **kw):
    bloco = dict(_PRODUCAO_PADRAO)
    bloco.update(kw)
    _sem_fontes(monkeypatch, blocos={
        'certidoes': {'vencidas': 0, 'a_vencer': 0, 'pendentes': 0,
                      'validas': 0, 'sem_data': 0, 'total': 0, 'atencao': 0,
                      'vazio': True},
        'certificados': {'itens': [], 'inventariado': True, 'vazio': True,
                         'com_vencimento': 0, 'janela_dias': 30},
        'producao': bloco,
    })


def test_faixa_mostra_o_resultado_da_passagem(app, ids, client, monkeypatch):
    _com_producao(monkeypatch, situacao='ok', lotes=2, emitidas=38, falhas=3,
                  tipos=['FGTS', 'Municipal'])

    corpo = client.get('/').get_data(as_text=True)

    assert 'Últimas emissões automáticas' in corpo
    assert 'Últimas emissões automáticas de certidões' not in corpo
    assert '>38</strong> certidões emitidas' in corpo
    assert '>3</strong>' in corpo and 'falharam' in corpo
    assert 'FGTS, Municipal' in corpo
    assert 'Passagem de' not in corpo
    assert 'Roda de novo' not in corpo


def test_faixa_sem_registro_nao_diz_zero_emitidas(app, ids, client, monkeypatch):
    """Nao se sabe se o PC ficou desligado ou se nao havia o que renovar. Zero
    afirmaria a segunda hipotese sem nenhuma evidencia dela."""
    _com_producao(monkeypatch, situacao='sem_registro')

    corpo = client.get('/').get_data(as_text=True)

    assert 'Nenhuma execução automática registrada recentemente' in corpo
    # nenhuma contagem da passagem — nem "0 emitidas", nem "0 falharam"
    assert '</strong> certidões emitidas\n' not in corpo
    assert 'falharam' not in corpo


def test_agendador_desligado_aponta_para_configuracoes(app, ids, client,
                                                       monkeypatch):
    """Desligado e "produziu 0" nao sao o mesmo fato — e o primeiro tem conserto
    a um clique, entao a faixa diz onde."""
    _com_producao(monkeypatch, situacao='desligado', proxima=None)

    corpo = client.get('/').get_data(as_text=True)

    assert 'Renovação automática desligada' in corpo
    assert '/configuracoes' in corpo
    assert 'Nenhum lote registrado desde' not in corpo


def test_lote_ainda_rodando_nao_vira_desfecho(app, ids, client, monkeypatch):
    _com_producao(monkeypatch, situacao='ok', lotes=2, emitidas=8, falhas=0,
                  em_andamento=1, tipos=['FGTS'])

    corpo = client.get('/').get_data(as_text=True)

    assert 'lote ainda em andamento' in corpo


def test_faixa_leva_para_produtividade(app, ids, client, monkeypatch):
    _com_producao(monkeypatch, situacao='ok', lotes=1, emitidas=5, falhas=0,
                  tipos=['FGTS'])

    corpo = client.get('/').get_data(as_text=True)

    assert '/produtividade' in corpo


def test_linha_de_sete_dias_mostra_total_de_certidoes(app, ids, client,
                                                      monkeypatch):
    _com_producao(monkeypatch, semana={'emitidas': 214, 'pct_agendador': 88})

    corpo = client.get('/').get_data(as_text=True)

    assert '>214</strong>' in corpo
    assert '214</strong>\n                    certidões emitidas' in corpo
    assert 'sem ninguém clicar' not in corpo


def test_semana_sem_emissao_mostra_estado_vazio(app, ids, client, monkeypatch):
    _com_producao(monkeypatch, semana={'emitidas': 0, 'pct_agendador': None})

    corpo = client.get('/').get_data(as_text=True)

    assert '7 dias: nenhuma emissão de certidão registrada' in corpo
    assert '0% sem ninguém clicar' not in corpo


def test_visualizador_tambem_ve_a_faixa(app, ids, login_as, monkeypatch):
    """O destino e /produtividade, que e `leitura` — a regra e o papel da tela
    de destino (OVER-09), e negar aqui esconderia um numero que ele pode abrir."""
    _com_producao(monkeypatch, situacao='ok', lotes=1, emitidas=9, falhas=0,
                  tipos=['FGTS'])

    resposta = login_as('leitura').get('/')

    assert resposta.status_code == 200
    assert 'Últimas emissões automáticas' in resposta.get_data(as_text=True)


def test_falha_da_faixa_nao_derruba_o_mosaico(app, ids, client, monkeypatch):
    _sem_fontes(monkeypatch, blocos={
        'certidoes': {'vencidas': 0, 'a_vencer': 0, 'pendentes': 0,
                      'validas': 0, 'sem_data': 0, 'total': 0, 'atencao': 0,
                      'vazio': True},
        'certificados': {'itens': [], 'inventariado': True, 'vazio': True,
                         'com_vencimento': 0, 'janela_dias': 30},
        'producao': {'erro': True, 'nome': 'producao'},
    })

    resposta = client.get('/')
    corpo = resposta.get_data(as_text=True)

    assert resposta.status_code == 200
    assert 'Não consegui ler a produção agora' in corpo
    assert 'Certidões' in corpo  # o mosaico continua de pé


def test_cartao_a1_cheio_diz_quantos_de_quantos(app, ids, client, monkeypatch):
    """A lista rola POR DENTRO (max-height 15rem): sem a contagem no cabecalho,
    18 dos 23 ficam atras de uma barra que pode nem aparecer. Conteudo escondido
    sem sinal de que existe e o unico problema real de rolagem nesta pagina."""
    from datetime import datetime

    itens = [{'empresa_nome': f'EMPRESA {i}', 'causa': 'vencendo',
              'dias_restantes': i, 'not_after': datetime(2026, 9, 1)}
             for i in range(23)]
    _cofre(monkeypatch, itens=itens, vazio=False, com_vencimento=47)

    corpo = client.get('/').get_data(as_text=True)

    assert 'vg-card-h-extra' in corpo
    assert '23 de 47' in corpo
    assert 'vg-mosaico is-a1-curto' not in corpo


def test_cartao_a1_vazio_nao_ganha_contagem_no_cabecalho(app, ids, client,
                                                          monkeypatch):
    """Sem item nao ha lista para rolar, e "0 de 47" no cabecalho seria ruido
    contradizendo a frase de estado vazio logo abaixo."""
    _cofre(monkeypatch, com_vencimento=47)

    corpo = client.get('/').get_data(as_text=True)

    assert '0 de 47' not in corpo
    assert 'Nenhum dos 47 certificados vence' in corpo
    assert 'vg-mosaico is-a1-curto' in corpo


@pytest.mark.parametrize('quantidade', [1, 2, 3])
def test_cartao_a1_curto_nao_reserva_linhas_vazias(app, ids, client, monkeypatch,
                                                   quantidade):
    from datetime import datetime

    itens = [{'empresa_nome': f'TESTE {i}', 'causa': 'vencendo',
              'dias_restantes': i, 'not_after': datetime(2026, 9, 1)}
             for i in range(quantidade)]
    _cofre(monkeypatch, itens=itens, vazio=False,
           com_vencimento=quantidade)

    corpo = client.get('/').get_data(as_text=True)

    assert 'vg-mosaico is-a1-curto' in corpo


def test_noite_sem_falha_nao_poe_o_zero_em_destaque(app, ids, client, monkeypatch):
    """Zero não é notícia (design language): numa noite limpa, "0 falharam" não
    pode competir com o número que importa."""
    _com_producao(monkeypatch, situacao='ok', lotes=3, emitidas=38, falhas=0,
                  tipos=['FGTS'])

    corpo = client.get('/').get_data(as_text=True)

    assert 'vg-producao-parte" data-zero="1"' in corpo
    # o comentario Jinja com `-#}` ja comeu este espaco uma vez, colando
    # "emitidas· 0" — a regressao e invisivel no HTML e obvia na tela
    assert 'emitidas<span' not in corpo


def test_noite_com_falha_mantem_o_numero_em_destaque(app, ids, client, monkeypatch):
    _com_producao(monkeypatch, situacao='ok', lotes=3, emitidas=38, falhas=3,
                  tipos=['FGTS'])

    corpo = client.get('/').get_data(as_text=True)

    assert 'vg-producao-parte" data-zero="0"' in corpo


def test_dia_calmo_nao_afirma_certificados_em_dia_sem_inventario(
        app, ids, client, monkeypatch):
    """O pior lugar para um chute é dentro de uma boa notícia: sem inventário
    não há como afirmar que os certificados estão em dia."""
    _cofre(monkeypatch, inventariado=False, vazio=False)

    corpo = client.get('/').get_data(as_text=True)

    assert 'Nada trava trabalho hoje' in corpo
    assert 'Certificados em dia' not in corpo
    assert 'Dos certificados eu' in corpo


def test_dia_calmo_com_cofre_inventariado_mantem_a_frase_inteira(
        app, ids, client, monkeypatch):
    _cofre(monkeypatch, inventariado=True, com_vencimento=47)

    corpo = client.get('/').get_data(as_text=True)

    assert 'Certificados em dia, portais respondendo' in corpo


def test_nenhum_comentario_jinja_vaza_para_a_pagina(app, ids, client, monkeypatch):
    """Comentario com `#}` DENTRO do texto fecha o bloco cedo e despeja o resto
    na tela — foi o que aconteceu no cartao de Passagem. O HTML renderizado nao
    pode conter delimitador de comentario nenhum, em nenhum cenario."""
    for cenario in ({'situacao': 'ok', 'lotes': 2, 'emitidas': 38, 'falhas': 0,
                     'tipos': ['FGTS']},
                    {'situacao': 'sem_registro'},
                    {'situacao': 'desligado', 'proxima': None}):
        _com_producao(monkeypatch, **cenario)

        corpo = client.get('/').get_data(as_text=True)

        assert '#}' not in corpo, cenario['situacao']
        assert '{#' not in corpo, cenario['situacao']
