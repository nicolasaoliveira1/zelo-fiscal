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
                                          'pendentes': 0, 'vazio': True},
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


def test_menu_tem_visao_geral_e_certidoes(app, ids, client, monkeypatch):
    _sem_fontes(monkeypatch)

    corpo = client.get('/').get_data(as_text=True)

    assert '<span>Visão geral</span>' in corpo
    assert '<span>Certidões</span>' in corpo
    assert '<span>Dashboard</span>' not in corpo
