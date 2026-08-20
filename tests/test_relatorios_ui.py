"""Estados de interface da página de Relatórios."""


def test_ultimos_lotes_vazios_dizem_o_fato_uma_vez(login_as):
    resposta = login_as('leitura').get('/relatorios')
    corpo = resposta.get_data(as_text=True)

    assert resposta.status_code == 200
    assert corpo.count('Nenhum lote foi emitido ainda.') == 1
    assert '>nunca<' not in corpo
