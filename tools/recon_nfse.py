# -*- coding: utf-8 -*-
"""Recon assistida do Emissor Nacional (nfse.gov.br) — T0 da feature nfse-honorarios.

Abre o portal com auto-selecao do certificado, e a partir dai apenas OBSERVA: a cada
mudanca de pagina salva HTML + screenshot + um inventario destilado dos controles de
formulario (id/name/classe/label/opcoes). Quem navega e o operador.

O script NUNCA clica em nada alem do link de acesso via certificado. Ele nao preenche
nem emite nota.

Uso:
    python tools/recon_nfse.py

Para encerrar: feche a janela do Chrome (ou Ctrl+C no terminal). A policy de certificado
e removida do registro no encerramento, inclusive em caso de erro.

Standalone por desenho: nao importa o app, para rodar mesmo com a aplicacao quebrada.
"""
import json
import os
import re
import sys
import time
from datetime import datetime

try:
    import winreg
except ImportError:
    winreg = None

from dotenv import load_dotenv
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(RAIZ, '.env'))

URL_LOGIN = 'https://www.nfse.gov.br/EmissorNacional/Login?ReturnUrl=%2fEmissorNacional'
PATTERN_CERT = 'https://certificado.nfse.gov.br'
CHAVE_POLICY = r"Software\Policies\Google\Chrome\AutoSelectCertificateForUrls"

SAIDA = os.path.join(RAIZ, 'logs', 'recon_nfse',
                     datetime.now().strftime('%Y%m%d_%H%M%S'))


def _env(nome, default=''):
    return (os.environ.get(nome, default) or '').strip()


def aplicar_policy():
    """Grava a policy de auto-selecao para o dominio da NFSe. Indice proprio, para
    nao sobrescrever o do RS. Retorna o indice usado (ou None se nao aplicou)."""
    if os.name != 'nt' or winreg is None:
        print('[!] Sem registro do Windows: o dialogo de certificado vai aparecer.')
        return None

    indice = _env('NFSE_CERT_AUTOSELECT_POLICY_INDEX', '2') or '2'
    indice_rs = _env('RS_CERT_AUTOSELECT_POLICY_INDEX', '1') or '1'
    if indice == indice_rs:
        print(f'[X] Indice {indice} e o mesmo do RS. Isso sobrescreveria a policy do RS.')
        sys.exit(1)

    issuer = _env('NFSE_CERT_AUTOSELECT_ISSUER_CN') or _env('RS_CERT_AUTOSELECT_ISSUER_CN')
    subject = _env('NFSE_CERT_AUTOSELECT_SUBJECT_CN') or _env('RS_CERT_AUTOSELECT_SUBJECT_CN')

    filtro = {}
    if issuer:
        filtro['ISSUER'] = {'CN': issuer}
    if subject:
        filtro['SUBJECT'] = {'CN': subject}
    if not filtro:
        print('[!] Sem ISSUER/SUBJECT CN no .env: o dialogo de certificado vai aparecer.')
        return None

    pattern = _env('NFSE_CERT_AUTOSELECT_PATTERN', PATTERN_CERT) or PATTERN_CERT
    valor = json.dumps({'pattern': pattern, 'filter': filtro},
                       ensure_ascii=False, separators=(',', ':'))
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, CHAVE_POLICY) as chave:
        winreg.SetValueEx(chave, indice, 0, winreg.REG_SZ, valor)
    print(f'[+] Policy aplicada no indice {indice} para {pattern}')
    print(f'    subject CN: {subject or "(qualquer)"}')
    print(f'    issuer  CN: {issuer or "(qualquer)"}')
    return indice


def remover_policy(indice):
    if not indice or os.name != 'nt' or winreg is None:
        return
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, CHAVE_POLICY) as chave:
            winreg.DeleteValue(chave, indice)
        print(f'[+] Policy do indice {indice} removida.')
    except FileNotFoundError:
        pass
    except OSError as exc:
        print(f'[!] Falha removendo policy: {exc}')


def abrir_chrome():
    opts = Options()
    opts.add_argument('--start-maximized')
    opts.add_argument('--no-first-run')
    opts.add_argument('--no-default-browser-check')
    opts.add_argument('--incognito')
    return webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()),
                            options=opts)


JS_INVENTARIO = r"""
function texto(el){ return (el ? (el.innerText || el.textContent || '') : '').replace(/\s+/g,' ').trim().slice(0,120); }
function rotulo(el){
  if (el.id) { var l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) return texto(l); }
  var p = el.closest('label'); if (p) return texto(p);
  var g = el.closest('.form-group,.form-check,.mb-3,.col,.field,div');
  if (g) { var lb = g.querySelector('label,legend'); if (lb) return texto(lb); }
  return '';
}
function visivel(el){ var r = el.getBoundingClientRect(); return !!(r.width || r.height); }
function base(el){
  return { tag: el.tagName.toLowerCase(), type: (el.getAttribute('type')||''),
           id: el.id||'', name: el.getAttribute('name')||'',
           cls: (el.getAttribute('class')||'').slice(0,120),
           placeholder: el.getAttribute('placeholder')||'',
           aria: el.getAttribute('aria-label')||'',
           label: rotulo(el), visivel: visivel(el) };
}
var out = { url: location.href, titulo: document.title, controles: [], acoes: [], titulos: [] };
document.querySelectorAll('h1,h2,h3,legend,.wizard-step,.step,[class*="etapa"],[class*="passo"]').forEach(function(h){
  var t = texto(h); if (t) out.titulos.push(t);
});
document.querySelectorAll('input,select,textarea').forEach(function(el){
  var o = base(el);
  if (o.type === 'radio' || o.type === 'checkbox') { o.value = el.value; o.checked = el.checked; }
  else if (el.tagName.toLowerCase() === 'select') {
    o.opcoes = Array.prototype.slice.call(el.options).map(function(op){
      return { value: op.value, texto: texto(op), selecionada: op.selected }; });
  } else { o.valor_atual = (el.value||'').slice(0,60); }
  out.controles.push(o);
});
document.querySelectorAll('button,a[href],input[type=submit],[role=button]').forEach(function(el){
  var t = texto(el); if (!t && !el.id) return;
  out.acoes.push({ tag: el.tagName.toLowerCase(), texto: t, id: el.id||'',
                   cls: (el.getAttribute('class')||'').slice(0,120),
                   href: (el.getAttribute('href')||'').slice(0,140),
                   visivel: visivel(el) });
});
return JSON.stringify(out);
"""


def _slug(url):
    s = re.sub(r'^https?://[^/]+/', '', url)
    s = re.sub(r'[^A-Za-z0-9]+', '_', s).strip('_')
    return (s or 'raiz')[:60]


def formatar(inv):
    L = []
    L.append('URL:    ' + inv.get('url', ''))
    L.append('TITULO: ' + inv.get('titulo', ''))
    if inv.get('titulos'):
        L.append('CABECALHOS: ' + ' | '.join(dict.fromkeys(inv['titulos']))[:400])
    L.append('')
    L.append('--- CONTROLES DE FORMULARIO ---')
    for c in inv.get('controles', []):
        if not c.get('visivel'):
            continue
        cab = '%-8s %-10s id=%-28s name=%-24s' % (
            c.get('tag', ''), c.get('type', ''), c.get('id', '') or '-', c.get('name', '') or '-')
        L.append(cab)
        rot = c.get('label') or c.get('aria') or c.get('placeholder')
        if rot:
            L.append('         label: ' + rot)
        if c.get('cls'):
            L.append('         class: ' + c['cls'])
        if c.get('type') in ('radio', 'checkbox'):
            L.append('         value=%s checked=%s' % (c.get('value'), c.get('checked')))
        if c.get('valor_atual'):
            L.append('         valor: ' + c['valor_atual'])
        for op in c.get('opcoes', []) or []:
            marca = ' <== SELECIONADA' if op.get('selecionada') else ''
            L.append('         opcao: [%s] %s%s' % (op.get('value'), op.get('texto'), marca))
        L.append('')
    L.append('--- BOTOES / LINKS ---')
    for a in inv.get('acoes', []):
        if not a.get('visivel'):
            continue
        L.append('%-7s %-38s id=%-26s %s' % (
            a.get('tag', ''), (a.get('texto') or '')[:38], a.get('id') or '-',
            (a.get('href') or '')[:60]))
    return '\n'.join(L)


def capturar(driver, seq):
    """Salva HTML + screenshot + inventario da pagina atual. Retorna a assinatura."""
    try:
        bruto = driver.execute_script(JS_INVENTARIO)
        inv = json.loads(bruto)
    except Exception as exc:
        print(f'    [!] inventario falhou: {exc}')
        return None

    nome = '%03d_%s' % (seq, _slug(inv.get('url', '')))
    os.makedirs(SAIDA, exist_ok=True)

    with open(os.path.join(SAIDA, nome + '.txt'), 'w', encoding='utf-8') as f:
        f.write(formatar(inv))
    with open(os.path.join(SAIDA, nome + '.html'), 'w', encoding='utf-8') as f:
        f.write(driver.page_source)
    with open(os.path.join(SAIDA, nome + '.json'), 'w', encoding='utf-8') as f:
        json.dump(inv, f, ensure_ascii=False, indent=1)
    try:
        driver.save_screenshot(os.path.join(SAIDA, nome + '.png'))
    except Exception:
        pass

    visiveis = [c for c in inv.get('controles', []) if c.get('visivel')]
    print(f'  [{seq:03d}] {inv.get("url","")[:88]}')
    print(f'        {len(visiveis)} controles visiveis -> {nome}.txt')
    return nome


def assinatura(driver):
    """Identifica o 'estado' atual: URL + controles visiveis + seus VALORES.

    Incluir o valor e proposital: sem isso, preencher um campo ou escolher opcao
    de select nao muda a assinatura e o estado preenchido nunca e capturado — foi
    a lacuna da primeira recon. Com o valor, a captura sai ~3s depois que o
    operador para de digitar.

    Propaga a excecao quando o navegador e fechado (quem chama usa isso para sair
    do loop); antes o except engolia e o script girava para sempre.
    """
    return driver.execute_script(
        "var s=[];document.querySelectorAll('input,select,textarea').forEach("
        "function(e){var r=e.getBoundingClientRect();if(r.width||r.height)"
        "s.push((e.id||e.name||e.getAttribute('type')||'?')+'='+(e.value||''));});"
        "return location.href+'##'+s.sort().join('|');")


def main():
    indice = None
    driver = None
    try:
        indice = aplicar_policy()
        driver = abrir_chrome()

        print(f'\n[+] Abrindo {URL_LOGIN}')
        driver.get(URL_LOGIN)
        time.sleep(2)

        seq = 0
        capturar(driver, seq); seq += 1

        print('[+] Clicando em "Acesso via certificado digital"...')
        driver.find_element(By.CSS_SELECTOR, 'a.img-certificado').click()

        print('\n' + '=' * 72)
        print(' A PARTIR DAQUI O SCRIPT SO OBSERVA. NAVEGUE VOCE MESMO.')
        print(' Cada tela nova e capturada automaticamente.')
        print(' Percorra: dashboard -> Perfil/Configuracao (aliquota) -> DPS/Pessoas')
        print(' -> as 3 etapas do assistente -> tela de REVISAO.')
        print(' Feche o Chrome quando terminar.')
        print('=' * 72 + '\n')

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
                if estavel == 1:          # captura 1 ciclo apos estabilizar
                    capturar(driver, seq); seq += 1
            time.sleep(1.5)

        print(f'\n[+] {seq} telas capturadas em:\n    {SAIDA}')
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
