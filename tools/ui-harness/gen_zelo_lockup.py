"""Gera o lockup horizontal da marca Zelo (marca + wordmark + fio + tagline).

Por que imagem, e nao HTML no README: o GitHub remove `style` do markdown, poe
borda visivel em celula de tabela (`.markdown-body table td {border: 1px solid}`)
e nao ha como estreitar um container sem CSS. Float resolve as duas colunas mas
gruda o bloco a esquerda e faz o fio do `<h1>` atravessar por tras da marca,
porque o heading e um bloco de largura total. O lockup rasterizado e a unica
forma de ter marca a esquerda, wordmark e tagline a direita, tudo centralizado.

Texto vai rasterizado tambem por necessidade: `<text>` em SVG depende da fonte
existir na maquina de quem abre, e o IBM Plex Serif nao esta instalado na
maioria delas, entao o wordmark cairia para uma serif qualquer do sistema.

A marca vem de gen_zelo_mark.marca(), nao de uma copia: lockup e favicon tem que
sair da mesma geometria.
"""
import importlib.util
from pathlib import Path

from playwright.sync_api import sync_playwright

AQUI = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("gen_zelo_mark", AQUI / "gen_zelo_mark.py")
_gm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gm)

MARCA_KW = _gm.VARIANTES["f-carimbo"]["kw"]

# Tokens do :root de app/static/css/style.css, por tema.
TEMAS = {
    "light": {"tinta": "#16181C", "fio": "#C9CED6", "suave": "#4A5158"},
    "dark": {"tinta": "#F2F4F7", "fio": "#3D444D", "suave": "#B4BCC6"},
}

FONTES = ("https://fonts.googleapis.com/css2?family=IBM+Plex+Serif:wght@600"
          "&family=IBM+Plex+Sans:wght@400&display=swap")

GABARITO = """<!doctype html>
<meta charset="utf-8">
<link href="{fontes}" rel="stylesheet">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: transparent; }}
  #lockup {{ display: inline-flex; align-items: center; gap: 24px; padding: 6px; }}
  .marca {{ width: 104px; height: 104px; flex: none; }}
  .marca svg {{ width: 100%; height: 100%; display: block; }}
  .texto {{ display: flex; flex-direction: column; }}
  .wordmark {{
    font-family: 'IBM Plex Serif', Georgia, serif; font-weight: 600;
    font-size: 62px; line-height: 1; letter-spacing: -0.005em; color: {tinta};
  }}
  .fio {{ height: 1px; background: {fio}; margin: 13px 0 11px; }}
  .tagline {{
    font-family: 'IBM Plex Sans', system-ui, sans-serif; font-weight: 400;
    font-size: 19px; line-height: 1.2; color: {suave};
  }}
</style>
<div id="lockup">
  <div class="marca">{svg}</div>
  <div class="texto">
    <div class="wordmark">Zelo</div>
    <div class="fio"></div>
    <div class="tagline">Regularidade sob controle.</div>
  </div>
</div>
"""


def main():
    saida = Path(__file__).resolve().parents[2] / "app" / "static" / "images"
    tmp = AQUI / "shots" / "_lockup.html"
    tmp.parent.mkdir(exist_ok=True)
    svg = _gm.marca(**MARCA_KW)

    with sync_playwright() as pw:
        navegador = pw.chromium.launch()
        for tema, cores in TEMAS.items():
            tmp.write_text(GABARITO.format(fontes=FONTES, svg=svg, **cores), encoding="utf-8")
            pagina = navegador.new_page(viewport={"width": 900, "height": 300},
                                        device_scale_factor=2)
            pagina.goto(tmp.as_uri())
            pagina.wait_for_timeout(1500)          # aguarda o webfont pintar
            destino = saida / "zelo-lockup-{}.png".format(tema)
            pagina.locator("#lockup").screenshot(path=str(destino), omit_background=True)
            pagina.close()
            print("  {}  {:.1f} KB".format(destino.name, destino.stat().st_size / 1024))
        navegador.close()
    tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
