"""
Mede o painel "Adicionar chaves" com a lista de pastas cheia, em várias alturas
de tela, e salva um screenshot por combinação em tools/ui-harness/shots/.

O que ele prova: o painel é `position: fixed` ancorado no rodapé e cresce para
CIMA. Com 26 pastas e sem teto de altura, o topo do painel — e com ele o botão
de importar — saía da viewport e ficava inalcançável. A medida que importa não é
estética: é `botaoImportarVisivel` e `painelCabe`.

Uso:
    python tools/ui-harness/manifestador/shot_entrada_pastas.py

Não faz parte do app. Requer: pip install playwright && playwright install chromium.
"""
from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

AQUI = Path(__file__).resolve().parent
HARNESS_URL = (AQUI / "entrada-pastas.html").as_uri()
SAIDA = AQUI.parent / "shots"

# (largura, altura). O caso do print é 1900x920; as telas baixas são o aperto
# real — notebook de 768px de altura com a barra do navegador comendo mais.
TELAS = [(1900, 920), (1440, 900), (1366, 768), (1280, 700), (1024, 620), (820, 900)]

# Quantidade de pastas: uma sozinha, o mês normal e o exagero.
QUANTIDADES = [1, 26]


def main() -> None:
    SAIDA.mkdir(parents=True, exist_ok=True)
    print(f"Harness: {HARNESS_URL}\nSaída:   {SAIDA}\n")
    cabecalho = (f"{'tela':>10} | {'pastas':>6} | {'topo':>5} | {'altura':>6} | "
                 f"{'cabe':>5} | {'importar':>8} | {'fechar':>6} | {'rola':>5}")
    print(cabecalho)
    print("-" * len(cabecalho))

    falhas = []
    with sync_playwright() as p:
        navegador = p.chromium.launch()
        for largura, altura in TELAS:
            for quantas in QUANTIDADES:
                pagina = navegador.new_page(viewport={"width": largura, "height": altura})
                pagina.goto(HARNESS_URL, wait_until="networkidle")
                pagina.evaluate("document.fonts.ready")
                pagina.evaluate(f"window.pintar({quantas})")
                m = pagina.evaluate("window.medirPainel()")

                print(f"{largura}x{altura:<4} | {quantas:>6} | {m['topoDoPainel']:>5} | "
                      f"{m['alturaDoPainel']:>6} | {str(m['painelCabe']):>5} | "
                      f"{str(m['botaoImportarVisivel']):>8} | "
                      f"{str(m['botaoFecharVisivel']):>6} | {str(m['listaRola']):>5}")

                for campo in ("painelCabe", "botaoImportarVisivel", "botaoFecharVisivel"):
                    if not m[campo]:
                        falhas.append(f"{largura}x{altura} / {quantas} pastas: {campo}")
                if m["paginaRolaNaHorizontal"]:
                    falhas.append(f"{largura}x{altura} / {quantas} pastas: rolagem horizontal")

                pagina.screenshot(path=str(SAIDA / f"manif_pastas_{largura}x{altura}_{quantas:02d}.png"))
                pagina.close()
        navegador.close()

    print()
    if falhas:
        print("FALHOU:")
        for f in falhas:
            print(f"  - {f}")
        raise SystemExit(1)
    print("OK — painel, botão de importar e botão de fechar dentro da tela em todos os casos.")


if __name__ == "__main__":
    main()
