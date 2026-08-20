"""Leitura da listagem de Notas Emitidas: URL, parse e paginacao.

A extracao do DOM (o `JS_LISTAGEM`) foi validada contra o **HTML real** do
portal capturado na recon — 3 paginas, 35 linhas, todos os campos preenchidos,
`Total de 80 registros` e ultima pagina 6 lidos corretamente (ver `recon.md`).
Aqui testa-se a camada pura acima dela, `interpretar_pagina()`, que e onde mora
a interpretacao: extracao da chave, conversao de valores e decisao de quantas
paginas visitar.

Os dados usados sao ficticios: a captura real traz nomes e CNPJs de clientes.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.automation import nfse_emitidas as ne

CHAVE = '43103302294645405000120000000000058026079889748735'


def _linha(**campos):
    padrao = {
        'href_visualizar': f'/EmissorNacional/Notas/Visualizar/Index/{CHAVE}',
        'situacao': 'P100_GERADA',
        'valor': '400,00',
        'data_geracao': '31/07/2026',
        'competencia': '07/2026',
        'municipio': 'Imbé/RS',
        'documento': '11.111.111/0001-11',
        'nome_tomador': 'ALFA COMERCIO LTDA',
    }
    padrao.update(campos)
    return padrao


def _pagina(linhas=None, total='Total de 80 registros',
            ultima='/EmissorNacional/Notas/Emitidas?pg=6&busca=', atual='1'):
    return {'linhas': linhas if linhas is not None else [_linha()],
            'total': total, 'ultima_pagina': ultima, 'pagina_atual': atual}


# --- URL do filtro ---------------------------------------------------------

def test_url_do_filtro_bate_com_a_do_portal():
    """Formato copiado da barra de enderecos durante a recon."""
    url = ne.montar_url(date(2026, 7, 1), date(2026, 7, 31))
    assert url == ('https://www.nfse.gov.br/EmissorNacional/Notas/Emitidas'
                   '?busca=&datainicio=01%2F07%2F2026&datafim=31%2F07%2F2026')


def test_url_da_segunda_pagina_inclui_pg():
    url = ne.montar_url(date(2026, 7, 1), date(2026, 7, 31), 2)
    assert 'pg=2' in url
    assert 'datainicio=01%2F07%2F2026' in url


def test_primeira_pagina_nao_manda_pg():
    assert 'pg=' not in ne.montar_url(date(2026, 7, 1), date(2026, 7, 31), 1)


# --- parse da linha --------------------------------------------------------

def test_le_todos_os_campos_da_linha():
    linhas, total, ultima = ne.interpretar_pagina(_pagina())

    assert len(linhas) == 1
    lida = linhas[0]
    assert lida.chave == CHAVE
    assert lida.data_geracao == date(2026, 7, 31)
    assert lida.documento == '11.111.111/0001-11'
    assert lida.nome_tomador == 'ALFA COMERCIO LTDA'
    assert lida.competencia == '07/2026'
    assert lida.municipio == 'Imbé/RS'
    assert lida.valor == Decimal('400.00')
    assert lida.situacao == 'P100_GERADA'
    assert total == 80
    assert ultima == 6


def test_pessoa_fisica_tem_o_cpf_separado_do_nome():
    """O portal marca CPF com `<span class="cpf">` e CNPJ com `.cnpj`.

    Procurar so `.cnpj` deixava o documento vazio em toda nota de pessoa
    fisica; sem documento a nota nunca casava com a linha do extrato e o mesmo
    tomador aparecia nos DOIS lados da conferencia (ND-028). O formato batido
    aqui e o mesmo que `utils.formatar_documento` produz."""
    linhas, _, _ = ne.interpretar_pagina(_pagina([_linha(
        documento='390.533.447-05', nome_tomador='MARIO ALVES')]))
    assert linhas[0].documento == '390.533.447-05'
    assert linhas[0].nome_tomador == 'MARIO ALVES'


def test_valor_com_separador_de_milhar():
    linhas, _, _ = ne.interpretar_pagina(_pagina([_linha(valor='1.459,00')]))
    assert linhas[0].valor == Decimal('1459.00')


def test_linha_sem_chave_e_descartada():
    """Sem chave nao ha identidade: nao da para gravar nem deduplicar."""
    linhas, _, _ = ne.interpretar_pagina(_pagina([_linha(href_visualizar='')]))
    assert linhas == []


def test_situacao_vem_como_codigo_e_nao_como_rotulo():
    """O rotulo e o title de uma imagem e muda com tema/idioma; o codigo nao."""
    linhas, _, _ = ne.interpretar_pagina(_pagina([_linha(situacao='P200_QUALQUER')]))
    assert linhas[0].situacao == 'P200_QUALQUER'


# --- paginacao -------------------------------------------------------------

def test_ultima_pagina_sai_do_link_ultima():
    _, _, ultima = ne.interpretar_pagina(
        _pagina(ultima='/EmissorNacional/Notas/Emitidas?pg=17&busca='))
    assert ultima == 17


def test_na_ultima_pagina_o_link_some_e_vale_a_pagina_atual():
    """Na ultima pagina o portal desabilita "Ultima" (href='javascript:')."""
    _, _, ultima = ne.interpretar_pagina(_pagina(ultima='javascript:', atual='6'))
    assert ultima == 6


def test_pagina_unica():
    _, _, ultima = ne.interpretar_pagina(
        _pagina(total='Total de 3 registros', ultima='', atual='1'))
    assert ultima == 1


def test_total_com_separador_de_milhar():
    _, total, _ = ne.interpretar_pagina(_pagina(total='Total de 1.234 registros'))
    assert total == 1234


def test_sem_texto_de_total_nao_inventa_numero():
    _, total, _ = ne.interpretar_pagina(_pagina(total=''))
    assert total is None


# --- varredura completa ----------------------------------------------------

class DriverFalso:
    """Devolve uma pagina por URL visitada, sem navegador."""

    def __init__(self, paginas):
        self.paginas = paginas
        self.visitadas = []
        self.atual = None

    def get(self, url):
        self.visitadas.append(url)
        indice = 1
        achado = ne.RE_PG.search(url)
        if achado:
            indice = int(achado.group(1))
        self.atual = self.paginas[indice - 1]

    def execute_script(self, _js):
        import json
        return json.dumps(self.atual)


def _pagina_de(n, quantas, ultima_pg):
    linhas = [_linha(href_visualizar=f'/EmissorNacional/Notas/Visualizar/Index/{n}{i:049d}')
              for i in range(quantas)]
    return _pagina(linhas,
                   total=f'Total de {quantas * (ultima_pg - 1) + 5} registros',
                   ultima=('javascript:' if n == ultima_pg
                           else f'?pg={ultima_pg}&busca='),
                   atual=str(n))


def test_percorre_todas_as_paginas_por_url_sem_clicar():
    paginas = [_pagina_de(n, 15, 3) for n in (1, 2)] + [_pagina_de(3, 5, 3)]
    # total anunciado tem de bater com 15+15+5
    for p in paginas:
        p['total'] = 'Total de 35 registros'
    driver = DriverFalso(paginas)

    linhas = ne.listar_periodo(driver, date(2026, 7, 1), date(2026, 7, 31))

    assert len(linhas) == 35
    assert len(driver.visitadas) == 3
    assert 'pg=' not in driver.visitadas[0]
    assert 'pg=2' in driver.visitadas[1]
    assert 'pg=3' in driver.visitadas[2]


def test_contagem_diferente_da_anunciada_recusa_o_resultado():
    """Melhor recusar que devolver um total fiscal a menos — ele passaria
    despercebido justamente por parecer plausivel."""
    pagina = _pagina([_linha()], total='Total de 80 registros', ultima='javascript:')
    driver = DriverFalso([pagina])

    with pytest.raises(ne.TotalDivergenteError) as erro:
        ne.listar_periodo(driver, date(2026, 7, 1), date(2026, 7, 31))
    assert '80' in str(erro.value)


def test_linha_repetida_entre_paginas_conta_uma_vez():
    """Nota emitida durante a varredura empurra as demais e repete uma linha."""
    p1 = _pagina([_linha(href_visualizar=f'/Notas/Visualizar/Index/{"1" * 50}'),
                  _linha(href_visualizar=f'/Notas/Visualizar/Index/{"2" * 50}')],
                 total='Total de 3 registros', ultima='?pg=2&busca=', atual='1')
    p2 = _pagina([_linha(href_visualizar=f'/Notas/Visualizar/Index/{"2" * 50}'),
                  _linha(href_visualizar=f'/Notas/Visualizar/Index/{"3" * 50}')],
                 total='Total de 3 registros', ultima='javascript:', atual='2')

    linhas = ne.listar_periodo(DriverFalso([p1, p2]), date(2026, 7, 1), date(2026, 7, 31))
    assert len({linha.chave for linha in linhas}) == 3
