"""O formulário de nova empresa sobrevive a uma falha de validação.

Até aqui o caminho do erro era: POST, o servidor dá `flash` e REDIRECIONA — e o
redirect descartava tudo o que havia sido digitado. Trocar um dígito do CNPJ
custava reescrever o formulário inteiro.

O redirect fica (o PRG evita reenvio ao atualizar a página, e o caminho de
sucesso depende dele), mas a falha leva junto o que foi digitado. Vai pela
SESSÃO e não pela querystring: CNPJ em URL cai no log do servidor, no histórico
do navegador e no cabeçalho Referer.
"""


def _form(**extra):
    dados = {
        'nome': 'Empresa Teste do Formulario',
        'cnpj': '11.111.111/1111-11',      # 14 dígitos, dígito verificador inválido
        'estado': 'RS',
        'cidade': 'Tramandai',
        'origem': 'nova_empresa',
    }
    dados.update(extra)
    return dados


def test_falha_de_validacao_devolve_o_que_foi_digitado(app, ids, client):
    resposta = client.post('/empresa/adicionar', data=_form(), follow_redirects=True)
    corpo = resposta.get_data(as_text=True)

    assert resposta.status_code == 200
    # o que o operador digitou volta na tela, para ele corrigir só o que errou
    assert 'Empresa Teste do Formulario' in corpo
    assert '11.111.111/1111-11' in corpo
    assert 'value="RS" selected' in corpo or "value=\"RS\" selected" in corpo
    # e o campo culpado chega marcado, sem esperar um segundo envio
    assert 'is-invalid' in corpo


def test_o_formulario_preservado_vale_uma_visita_so(app, ids, client):
    """Segunda visita à página vem limpa.

    O valor é consumido com `pop`: deixá-lo na sessão faria a tela reaparecer
    preenchida dias depois, sem o operador entender por quê.
    """
    client.post('/empresa/adicionar', data=_form(), follow_redirects=True)

    segunda = client.get('/empresa/nova').get_data(as_text=True)

    assert 'Empresa Teste do Formulario' not in segunda


def test_cadastro_bem_sucedido_nao_deixa_resto_na_sessao(app, ids, client):
    """Sucesso limpa a tela: o próximo cadastro começa em branco."""
    ok = client.post('/empresa/adicionar', follow_redirects=True,
                     data=_form(cnpj='11.222.333/0001-81'))   # DV válido

    assert ok.status_code == 200
    depois = client.get('/empresa/nova').get_data(as_text=True)
    assert 'Empresa Teste do Formulario' not in depois
