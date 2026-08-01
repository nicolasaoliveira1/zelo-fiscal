"""Raspagem da tela de Notas Emitidas do Emissor Nacional (NFSE-28).

Todos os seletores vieram da recon assistida de 31/07/2026
(`logs/recon_notas_emitidas/20260731_192451/`, ver `recon.md`) — nenhum
suposto, como manda a disciplina desta feature.

**A automação não preenche campo nem clica em paginação.** Filtro e página são
querystring (`?datainicio=&datafim=&pg=`), então navegar direto na URL some com
uma classe inteira de falha: máscara de campo de data, datepicker que não fecha,
botão de página que só existe depois de um render assíncrono. Os campos
`#datainicio`/`#datafim` e o `#btnPesquisar` existem e estão registrados na
recon, mas são o caminho B.

Nada aqui é destrutivo: a tela tem botões de cancelar e substituir NFS-e, e este
módulo só lê. Ele nunca os toca.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

BASE = 'https://www.nfse.gov.br/EmissorNacional'
URL_EMITIDAS = BASE + '/Notas/Emitidas'

# 15 na amostra real (ultima pagina veio com 5; 5x15+5 = 80 = "Total de 80
# registros"). Nao e usado para paginar — a paginacao segue o link "Ultima" —,
# serve so para estimar quantas paginas esperar no log.
POR_PAGINA = 15

# A chave de acesso da NFS-e tem 50 digitos e sai do href do "Visualizar". O
# `data-chave` da <tr> e outra coisa: um token opaco que o portal usa nos botoes
# de cancelar/substituir, e nao identifica a nota para nos.
RE_CHAVE = re.compile(r'/Notas/Visualizar/Index/(\d{20,60})')
RE_TOTAL = re.compile(r'Total de\s+([\d.]+)\s+registros', re.I)
RE_PG = re.compile(r'[?&]pg=(\d+)')


@dataclass
class LinhaEmitida:
    """Uma linha da listagem, como o portal a mostra."""
    chave: str = ''
    data_geracao: date | None = None
    documento: str = ''
    nome_tomador: str = ''
    competencia: str = ''
    municipio: str = ''
    valor: Decimal | None = None
    situacao: str = ''


def montar_url(inicio, fim, pagina=1):
    """URL da listagem filtrada. O portal quer as datas em dd/mm/aaaa.

    `busca` vai vazio de proposito: e o campo de pesquisa por tomador, e a
    consulta e do periodo inteiro."""
    parametros = {
        'busca': '',
        'datainicio': inicio.strftime('%d/%m/%Y'),
        'datafim': fim.strftime('%d/%m/%Y'),
    }
    if pagina and pagina > 1:
        parametros = {'pg': str(pagina), **parametros}
    return f'{URL_EMITIDAS}?{urlencode(parametros)}'


def _para_data(texto):
    try:
        return datetime.strptime((texto or '').strip(), '%d/%m/%Y').date()
    except (TypeError, ValueError):
        return None


def _para_decimal(texto):
    """'1.459,00' -> Decimal('1459.00')."""
    bruto = (texto or '').strip()
    if not bruto:
        return None
    bruto = bruto.replace('.', '').replace(',', '.')
    try:
        return Decimal(bruto)
    except (InvalidOperation, ValueError):
        return None


# JavaScript de leitura da pagina. Roda de uma vez e devolve tudo: e uma ida ao
# navegador por pagina, em vez de uma por celula (~100 por pagina via Selenium).
JS_LISTAGEM = r"""
function txt(el){ return (el ? (el.innerText || el.textContent || '') : '').replace(/\s+/g,' ').trim(); }
function cel(tr, classe){ var td = tr.querySelector('td.' + classe); return txt(td); }

var out = { linhas: [], total: null, ultima_pagina: null, pagina_atual: null };

document.querySelectorAll('table tbody tr').forEach(function(tr){
  var visualizar = tr.querySelector('td.td-opcoes a[href*="/Notas/Visualizar/Index/"]');
  // Coluna do tomador: o documento vem num <span> e o nome e o resto do texto
  // da celula. Separar assim, e nao por posicao de caractere, porque o nome
  // pode conter tracos.
  //
  // A classe do span depende do TIPO: `.cnpj` para pessoa juridica, `.cpf` para
  // fisica. Procurar so `.cnpj` deixava o documento vazio em toda nota de
  // pessoa fisica — e sem documento a nota nunca casava com a linha do extrato,
  // aparecendo nos DOIS lados da conferencia ao mesmo tempo (ND-028).
  var td_tomador = tr.querySelector('td.td-texto-grande');
  var span_doc = td_tomador ? td_tomador.querySelector('.cnpj, .cpf') : null;
  var doc = span_doc ? txt(span_doc) : '';
  var nome = td_tomador ? txt(td_tomador) : '';
  if (doc && nome.indexOf(doc) === 0) { nome = nome.slice(doc.length).replace(/^\s*-\s*/, ''); }

  out.linhas.push({
    href_visualizar: visualizar ? visualizar.getAttribute('href') : '',
    situacao: tr.getAttribute('data-situacao') || '',
    valor: tr.getAttribute('data-valor') || cel(tr, 'td-valor'),
    data_geracao: cel(tr, 'td-data'),
    competencia: cel(tr, 'td-competencia'),
    municipio: cel(tr, 'td-center'),
    documento: doc,
    nome_tomador: nome
  });
});

var desc = document.querySelector('.paginacao .descricao');
out.total = desc ? txt(desc) : '';

var ultima = document.querySelector('.pagination a[data-original-title="Última"]');
out.ultima_pagina = ultima ? (ultima.getAttribute('href') || '') : '';
var ativa = document.querySelector('.pagination li.active a');
out.pagina_atual = ativa ? txt(ativa) : '';

return JSON.stringify(out);
"""


def interpretar_pagina(dados):
    """Converte o retorno cru do JS em `(linhas, total, ultima_pagina)`.

    Separada da ida ao navegador para ser testavel sem Selenium: e aqui que mora
    a interpretacao, e e ela que precisa de teste."""
    linhas = []
    for cru in dados.get('linhas', []):
        achado = RE_CHAVE.search(cru.get('href_visualizar') or '')
        if not achado:
            # sem chave nao ha identidade: a linha nao pode ser gravada nem
            # deduplicada, e engoli-la calada esconderia nota do total
            continue
        linhas.append(LinhaEmitida(
            chave=achado.group(1),
            data_geracao=_para_data(cru.get('data_geracao')),
            documento=(cru.get('documento') or '').strip(),
            nome_tomador=(cru.get('nome_tomador') or '').strip()[:140],
            competencia=(cru.get('competencia') or '').strip(),
            municipio=(cru.get('municipio') or '').strip()[:60],
            valor=_para_decimal(cru.get('valor')),
            situacao=(cru.get('situacao') or '').strip()[:30],
        ))

    total = None
    achado = RE_TOTAL.search(dados.get('total') or '')
    if achado:
        total = int(achado.group(1).replace('.', ''))

    # "Ultima" some (href='javascript:') quando ja se esta nela; nesse caso a
    # pagina corrente E a ultima
    ultima = 1
    achado = RE_PG.search(dados.get('ultima_pagina') or '')
    if achado:
        ultima = int(achado.group(1))
    else:
        try:
            ultima = max(1, int((dados.get('pagina_atual') or '1').strip()))
        except ValueError:
            ultima = 1

    return linhas, total, ultima


def ler_pagina(driver):
    """Executa o JS de leitura na pagina aberta e interpreta o resultado."""
    import json
    return interpretar_pagina(json.loads(driver.execute_script(JS_LISTAGEM)))


class TotalDivergenteError(RuntimeError):
    """A raspagem terminou com contagem diferente da que o portal anuncia.

    Recusar e mais seguro que devolver: o numero vai virar o total emitido do
    mes, e um total a menos passa despercebido justamente por parecer
    plausivel."""


def listar_periodo(driver, inicio, fim, abrir=None, log=None):
    """Percorre a listagem do periodo e devolve todas as linhas.

    `abrir` (default `driver.get`) existe para o teste trocar a navegacao. Nao
    ha clique em paginacao: descobre-se a ultima pagina pelo link "Ultima" e
    visita-se `pg=1..N` por URL.

    Confere a contagem contra o "Total de N registros" da propria tela e
    levanta `TotalDivergenteError` se nao bater.
    """
    abrir = abrir or driver.get
    registrar = log or (lambda *a, **k: None)

    abrir(montar_url(inicio, fim, 1))
    linhas, total, ultima = ler_pagina(driver)
    registrar('nfse_emitidas_pagina', pagina=1, de=ultima, linhas=len(linhas))

    for pagina in range(2, ultima + 1):
        abrir(montar_url(inicio, fim, pagina))
        novas, _, _ = ler_pagina(driver)
        linhas.extend(novas)
        registrar('nfse_emitidas_pagina', pagina=pagina, de=ultima, linhas=len(novas))

    # Deduplica por chave: paginas visitadas em sequencia podem repetir uma
    # linha se uma nota for emitida durante a varredura e empurrar as demais.
    unicas = {}
    for linha in linhas:
        unicas[linha.chave] = linha
    resultado = list(unicas.values())

    if total is not None and len(resultado) != total:
        raise TotalDivergenteError(
            f'O portal anuncia {total} nota(s) no período e a leitura terminou '
            f'com {len(resultado)}. Nada foi gravado — refaça a consulta.')
    return resultado
