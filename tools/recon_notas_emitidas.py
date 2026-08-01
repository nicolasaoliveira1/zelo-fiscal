# -*- coding: utf-8 -*-
"""Recon assistida da tela de Notas Emitidas do Emissor Nacional.

Alvo: https://www.nfse.gov.br/EmissorNacional/Notas/Emitidas

Mesma regra da recon T0 (`tools/recon_nfse.py`): o portal exige certificado
digital, e navegar autenticado como o contador num portal fiscal federal e acao
com identidade juridica — quem navega e o OPERADOR. O script so observa. Ele
nao preenche filtro, nao pagina e nao clica em nada alem do link de acesso via
certificado.

Por que nao reusar o `recon_nfse.py` direto: o inventario dele cataloga
controles de formulario e botoes, que era o que as telas do assistente DPS
exigiam. Aqui o que interessa e a TABELA — cabecalhos, celulas, links por linha
— e a PAGINACAO, que aquele inventario nao enxerga. As partes comuns (policy do
certificado, Chrome, formatacao) sao importadas de la em vez de copiadas.

Uso:
    python tools/recon_notas_emitidas.py

Encerrar: feche a janela do Chrome (ou Ctrl+C). A policy sai do registro no
encerramento, inclusive em caso de erro.
"""
import json
import os
import sys
import time
from datetime import datetime

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, 'tools'))

from selenium.common.exceptions import WebDriverException  # noqa: E402
from selenium.webdriver.common.by import By                # noqa: E402

# Nucleo comum da recon T0: policy do certificado, Chrome e o destilado de
# controles. Import, nao copia — a manipulacao de registro do Windows nao pode
# existir em duas versoes.
from recon_nfse import (                                   # noqa: E402
    JS_INVENTARIO,
    URL_LOGIN,
    abrir_chrome,
    aplicar_policy,
    formatar,
    remover_policy,
)

SAIDA = os.path.join(RAIZ, 'logs', 'recon_notas_emitidas',
                     datetime.now().strftime('%Y%m%d_%H%M%S'))

URL_ALVO = 'https://www.nfse.gov.br/EmissorNacional/Notas/Emitidas'

# Quantas linhas da tabela detalhar celula a celula. O suficiente para deduzir o
# tipo de cada coluna sem despejar a listagem inteira (que e dado fiscal real).
LINHAS_DETALHADAS = 3

JS_TABELAS = r"""
function texto(el){ return (el ? (el.innerText || el.textContent || '') : '').replace(/\s+/g,' ').trim(); }
function seletor(el){
  if (!el) return '';
  if (el.id) return '#' + el.id;
  var c = (el.getAttribute('class')||'').trim().split(/\s+/).filter(Boolean).slice(0,3).join('.');
  return el.tagName.toLowerCase() + (c ? '.' + c : '');
}
var out = { url: location.href, titulo: document.title, tabelas: [], paginacao: [], resumo: [] };

document.querySelectorAll('table').forEach(function(t, i){
  var cabecalhos = Array.prototype.map.call(
    t.querySelectorAll('thead th, thead td'), function(th){ return texto(th); });
  var linhas = Array.prototype.slice.call(t.querySelectorAll('tbody tr'));
  var amostra = linhas.slice(0, LIMITE).map(function(tr){
    return {
      atributos: Array.prototype.slice.call(tr.attributes).map(function(a){
        return a.name + '=' + a.value.slice(0, 80); }),
      celulas: Array.prototype.map.call(tr.children, function(td){
        var link = td.querySelector('a[href]');
        var botoes = Array.prototype.map.call(
          td.querySelectorAll('button,a[href]'), function(b){
            return { texto: texto(b).slice(0,40), href: (b.getAttribute('href')||'').slice(0,160),
                     cls: (b.getAttribute('class')||'').slice(0,80),
                     onclick: (b.getAttribute('onclick')||'').slice(0,120) }; });
        return { texto: texto(td).slice(0, 90), classe: td.getAttribute('class') || '',
                 href: link ? link.getAttribute('href') : '', acoes: botoes };
      })
    };
  });
  out.tabelas.push({ indice: i, seletor: seletor(t), classe: t.getAttribute('class')||'',
                     cabecalhos: cabecalhos, total_linhas: linhas.length, amostra: amostra });
});

// Paginacao: procura pelos padroes usuais e devolve a estrutura crua, porque e
// dela que sai a forma de avancar de pagina na automacao.
document.querySelectorAll(
  '.pagination, [class*="pagina"], [class*="paging"], nav[aria-label*="ági"], nav[aria-label*="agin"]'
).forEach(function(p){
  out.paginacao.push({
    seletor: seletor(p), classe: p.getAttribute('class')||'', html: p.outerHTML.slice(0, 2500),
    itens: Array.prototype.map.call(p.querySelectorAll('a,button,span,li'), function(el){
      return { tag: el.tagName.toLowerCase(), texto: texto(el).slice(0,24),
               href: (el.getAttribute('href')||'').slice(0,160),
               cls: (el.getAttribute('class')||'').slice(0,80),
               disabled: el.hasAttribute('disabled') || /disabled/.test(el.getAttribute('class')||''),
               ativo: /active|current|selecionad/.test(el.getAttribute('class')||'') };
    })
  });
});

// Qualquer texto que pareca contagem/total: "1 a 20 de 137", "Total: R$ ...".
document.querySelectorAll('*').forEach(function(el){
  if (el.children.length) return;
  var t = texto(el);
  if (!t || t.length > 120) return;
  if (/(\d+\s*(a|-|até)\s*\d+\s*de\s*\d+)|total|registros?|resultados?|R\$/i.test(t)) {
    out.resumo.push({ seletor: seletor(el.parentElement), texto: t });
  }
});
out.resumo = out.resumo.slice(0, 40);

return JSON.stringify(out);
""".replace('LIMITE', str(LINHAS_DETALHADAS))


def _slug(url):
    import re
    s = re.sub(r'^https?://[^/]+/', '', url)
    s = re.sub(r'[^A-Za-z0-9]+', '_', s).strip('_')
    return (s or 'raiz')[:60]


def formatar_tabelas(dados):
    L = ['', '=' * 72, 'TABELAS', '=' * 72]
    if not dados.get('tabelas'):
        L.append('(nenhuma <table> nesta tela)')
    for t in dados.get('tabelas', []):
        L.append('')
        L.append('tabela[%d]  seletor: %s' % (t['indice'], t['seletor']))
        L.append('  classe: %s' % (t['classe'] or '-'))
        L.append('  linhas no tbody: %d' % t['total_linhas'])
        L.append('  COLUNAS (%d):' % len(t['cabecalhos']))
        for i, cab in enumerate(t['cabecalhos']):
            L.append('    [%d] %s' % (i, cab))
        for n, linha in enumerate(t.get('amostra', [])):
            L.append('  --- linha de exemplo %d ---' % n)
            if linha['atributos']:
                L.append('      atributos da <tr>: %s' % ', '.join(linha['atributos']))
            for i, cel in enumerate(linha['celulas']):
                cab = t['cabecalhos'][i] if i < len(t['cabecalhos']) else '?'
                L.append('      [%d] %-22s = %s' % (i, cab[:22], cel['texto']))
                if cel['href']:
                    L.append('           href: %s' % cel['href'])
                for acao in cel.get('acoes', []):
                    if acao['texto'] or acao['href'] or acao['onclick']:
                        L.append('           acao: %-18s href=%s %s' % (
                            acao['texto'], acao['href'][:70],
                            ('onclick=' + acao['onclick']) if acao['onclick'] else ''))

    L += ['', '=' * 72, 'PAGINACAO', '=' * 72]
    if not dados.get('paginacao'):
        L.append('(nenhum bloco de paginacao reconhecido)')
    for p in dados.get('paginacao', []):
        L.append('')
        L.append('seletor: %s   classe: %s' % (p['seletor'], p['classe'] or '-'))
        for item in p['itens']:
            marcas = []
            if item['ativo']:
                marcas.append('ATIVO')
            if item['disabled']:
                marcas.append('DESABILITADO')
            L.append('  %-6s %-14s href=%-46s %s %s' % (
                item['tag'], item['texto'], item['href'][:46],
                item['cls'][:30], ' '.join(marcas)))
        L.append('  HTML CRU:')
        L.append('  ' + p['html'].replace('\n', ' ')[:2000])

    L += ['', '=' * 72, 'CONTAGENS / TOTAIS NA TELA', '=' * 72]
    for r in dados.get('resumo', []):
        L.append('  %-34s %s' % (r['seletor'][:34], r['texto']))
    return '\n'.join(L)


def capturar(driver, seq):
    try:
        inv = json.loads(driver.execute_script(JS_INVENTARIO))
        tab = json.loads(driver.execute_script(JS_TABELAS))
    except Exception as exc:
        print('    [!] captura falhou: %s' % exc)
        return

    nome = '%03d_%s' % (seq, _slug(tab.get('url', '')))
    os.makedirs(SAIDA, exist_ok=True)

    with open(os.path.join(SAIDA, nome + '.txt'), 'w', encoding='utf-8') as f:
        f.write(formatar(inv))
        f.write(formatar_tabelas(tab))
    with open(os.path.join(SAIDA, nome + '.html'), 'w', encoding='utf-8') as f:
        f.write(driver.page_source)
    with open(os.path.join(SAIDA, nome + '.json'), 'w', encoding='utf-8') as f:
        json.dump({'controles': inv, 'tabelas': tab}, f, ensure_ascii=False, indent=1)
    try:
        driver.save_screenshot(os.path.join(SAIDA, nome + '.png'))
    except Exception:
        pass

    tabelas = tab.get('tabelas', [])
    linhas = sum(t['total_linhas'] for t in tabelas)
    print('  [%03d] %s' % (seq, tab.get('url', '')[:86]))
    print('        %d tabela(s), %d linha(s), %d bloco(s) de paginacao -> %s.txt'
          % (len(tabelas), linhas, len(tab.get('paginacao', [])), nome))


def assinatura(driver):
    """Estado atual: URL + valores dos campos + primeira celula de cada linha.

    A primeira celula entra porque, na paginacao, a URL as vezes nao muda (POST
    ou JS) e os campos tambem nao — o unico sinal de que a pagina virou e o
    conteudo da tabela. Sem isso, so a primeira pagina seria capturada."""
    return driver.execute_script(
        "var s=[];document.querySelectorAll('input,select,textarea').forEach("
        "function(e){var r=e.getBoundingClientRect();if(r.width||r.height)"
        "s.push((e.id||e.name||'?')+'='+(e.value||''));});"
        "var l=[];document.querySelectorAll('tbody tr').forEach(function(tr){"
        "l.push((tr.textContent||'').replace(/\\s+/g,' ').trim().slice(0,40));});"
        "return location.href+'##'+s.sort().join('|')+'##'+l.join('~');")


ROTEIRO = """
========================================================================
 A PARTIR DAQUI O SCRIPT SO OBSERVA. NAVEGUE VOCE MESMO.
========================================================================
 Roteiro sugerido (cada estado novo e capturado sozinho):

  1. Va em Notas > Emitidas
     ({alvo})
  2. CAPTURE A TELA SEM FILTRO primeiro (so espere 3s parado)
  3. Preencha a data inicial e a data final de um mes fechado
     (ex.: 01/07/2026 a 31/07/2026) e clique em filtrar
       -> se o portal RECUSAR por passar de 30 dias, OTIMO: deixe o erro
          na tela uns segundos, e a mensagem exata fica registrada
  4. Refaca com um periodo aceito (ex.: 01/07/2026 a 30/07/2026)
  5. Passe por 2 ou 3 paginas usando os botoes de paginacao
  6. Va ate a ULTIMA pagina (para o botao "proxima" aparecer desabilitado)
  7. Se houver botao de EXPORTAR / baixar (CSV, Excel, PDF), passe o mouse
     e deixe a tela parada — se existir, pode ser que nem precisemos raspar
  8. Abra UMA nota da lista (clique no numero/detalhe) e volte

 Feche o Chrome quando terminar.
========================================================================
""".format(alvo=URL_ALVO)


def main():
    indice = None
    driver = None
    try:
        indice = aplicar_policy()
        driver = abrir_chrome()

        print('\n[+] Abrindo %s' % URL_LOGIN)
        driver.get(URL_LOGIN)
        time.sleep(2)

        print('[+] Clicando em "Acesso via certificado digital"...')
        try:
            driver.find_element(By.CSS_SELECTOR, 'a.img-certificado').click()
        except Exception as exc:
            print('[!] Nao achei o link do certificado (%s). Faca o login a mao.' % exc)

        print(ROTEIRO)

        seq = 0
        anterior = None
        estavel = 0
        while True:
            try:
                atual = assinatura(driver)
            except WebDriverException:
                print('\n[+] Navegador fechado. Encerrando.')
                break
            if atual != anterior:
                estavel = 0
                anterior = atual
            else:
                estavel += 1
                if estavel == 1:
                    capturar(driver, seq)
                    seq += 1
            time.sleep(1.5)

        print('\n[+] %d telas capturadas em:\n    %s' % (seq, SAIDA))
        print('\n[+] Me mande o caminho acima (ou o conteudo dos .txt).')
    except KeyboardInterrupt:
        print('\n[+] Interrompido pelo operador.')
    finally:
        remover_policy(indice)
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == '__main__':
    main()
