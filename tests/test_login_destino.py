"""Entrar leva à Visão Geral, não à tabela de certidões.

A Visão Geral virou a página inicial em 2026-08-20 — ela responde "por onde eu
começo hoje" em vez de "o que existe". Os redirects de autenticação ficaram para
trás da decisão e continuaram mandando para `/certidoes`.

O `next` continua tendo prioridade: quem foi barrado a caminho de uma página
volta para ela, e não para o começo.
"""
from tests.conftest import USUARIOS_TESTE


def _entrar(client, papel='admin', **extra):
    usuario, senha = USUARIOS_TESTE[papel]
    dados = {'username': usuario, 'senha': senha}
    dados.update(extra)
    return client.post('/login', data=dados)


def test_entrar_leva_a_visao_geral(app, ids, client_anon):
    resposta = _entrar(client_anon)

    assert resposta.status_code == 302
    assert resposta.headers['Location'].rstrip('/') in ('', 'http://localhost')


def test_next_tem_prioridade_sobre_o_destino_padrao(app, ids, client_anon):
    """Quem foi barrado a caminho de uma página volta para ela."""
    resposta = _entrar(client_anon, next='/relatorios')

    assert resposta.status_code == 302
    assert resposta.headers['Location'].endswith('/relatorios')


def test_ja_autenticado_em_login_vai_para_a_visao_geral(app, ids, client):
    resposta = client.get('/login')

    assert resposta.status_code == 302
    assert resposta.headers['Location'].rstrip('/') in ('', 'http://localhost')


def test_next_para_fora_do_site_e_ignorado(app, ids, client_anon):
    """A guarda contra open-redirect continua valendo, e cai no destino padrão."""
    resposta = _entrar(client_anon, next='//evil.example')

    assert resposta.status_code == 302
    assert 'evil.example' not in resposta.headers['Location']
