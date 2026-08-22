"""A pagina inicial (`/`) e o novo endereco da carteira (`/certidoes`).

O que estes testes protegem: que abrir o sistema cai na Visao Geral e NAO na
tabela de certidoes, que um bloco com fonte quebrada nao derruba a pagina, e que
o papel decide quais blocos aparecem — mostrar um numero e negar o clique seria
pior que nao mostrar.
"""
from app.services import visao_geral


def _sem_fontes(monkeypatch, **kw):
    """Todas as fontes vazias, salvo o que o teste sobrescrever."""
    monkeypatch.setattr(visao_geral, 'montar',
                        lambda usuario: kw.get('blocos', {
                            'certidoes': {'vencidas': 0, 'a_vencer': 0,
                                          'pendentes': 0, 'validas': 0,
                                          'sem_data': 0, 'total': 0,
                                          'atencao': 0, 'vazio': True},
                            'certificados': {'itens': [], 'inventariado': True,
                                             'vazio': True},
                        }))


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
        'certificados': {'inventariado': True, 'vazio': False, 'itens': [
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
    assert '>4<' in corpo or '4' in corpo


def test_cofre_nunca_inventariado_nao_diz_zero_vencendo(app, ids, client,
                                                        monkeypatch):
    _sem_fontes(monkeypatch, blocos={
        'certidoes': {'vencidas': 0, 'a_vencer': 0, 'pendentes': 0, 'vazio': True},
        'certificados': {'itens': [], 'inventariado': False, 'vazio': False},
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
        'certificados': {'itens': [], 'inventariado': True, 'vazio': True},
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
