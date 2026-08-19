"""Gera a marca Zelo em SVG e o preview de conferencia.

v2 -- revisado sob o metodo do Allan Peters ("Logos That Last"):

* O Z e o glifo REAL do IBM Plex Serif SemiBold, extraido com fontTools e
  embutido aqui como path. A v1 vetorizava o raster que o gerador de imagem
  produziu -- uma imitacao de Z serifado, 15% larga demais (ratio 0.906
  contra os 0.784 do glifo de verdade) e com serifas que eram entalhes
  retangulares, sem bracket. Agora a marca e o wordmark da navbar sao a
  mesma letra.
* A largura da barra e derivada da espessura do braco horizontal do proprio
  Z (~9.3% da altura), nao escolhida no olho. E o que faz a barra ler como
  parte do mesmo sistema em vez de retangulo avulso.
* Ha um teste sem a barra: ela e a camada de status, e em contexto de uma
  cor so nao existe status para mostrar.

Nao ha dependencia de fonte em runtime -- o path esta baked no modulo.
Cores: tokens do :root de app/static/css/style.css.
"""
from pathlib import Path

INK = "#16181C"    # --zelo-ink (light)
PAPER = "#FFFFFF"  # --zelo-paper (light)
OK = "#2E7D52"     # --zelo-ok (light) -- a unica cor

# IBM Plex Serif SemiBold, glifo "Z" cru, unitsPerEm=1000. Fica como referencia
# de comparacao -- a marca usa o redesenho abaixo.
Z_PATH_PLEX = "M50 64 428 633H166V517H63V698H587V634L209 65H494V197H597V0H50Z"

# --- Z redesenhado para tamanho de marca -------------------------------------
# O Plex Serif e um serif de influencia slab: serifas em lajota, sem bracket, e
# contraste braco/diagonal de 0.46. Isso le como "documento" a 15px numa tabela
# e como "mecanico" a 500px numa marca. O redesenho mantem o esqueleto do Plex
# (mesma largura de caixa, mesmos apoios) e mexe em duas coisas:
#
#   1. contraste -- bracos mais finos e diagonal mais grossa (0.46 -> ~0.32),
#      que e a faixa do serif gravado, a "gravidade notarial" do brief;
#   2. bracket -- as quatro juncoes internas ganham curva de transicao no lugar
#      do angulo reto. Sao so as concavas: as pontas externas seguem vivas,
#      senao a letra amolece e perde a autoridade.
#
# Continua sendo o Z do Plex, desenhado para o tamanho de marca -- e a diferenca
# entre trocar de letra e ajustar a letra.
CAP = 698
X_ESQ, X_DIR = 50, 597      # extremos da caixa (o braco superior recua 13u)
BRACO_TOPO = 47             # era 64
BRACO_BASE = 51             # era 65; base um fio mais pesada, como em serif classico
DIAGONAL = 178              # largura horizontal; era 159
SERIFA_L = 94               # largura das serifas; era 103
SERIFA_TOPO_P = 108         # quanto a serifa esquerda desce sob o braco
SERIFA_BASE_P = 124         # quanto a serifa direita sobe sobre o braco
BRACKET = 28                # raio da transicao nas juncoes internas


def _andar(de, para, dist):
    """Ponto a `dist` de `de`, na direcao de `para`."""
    dx, dy = para[0] - de[0], para[1] - de[1]
    n = (dx * dx + dy * dy) ** 0.5
    return (de[0] + dx / n * dist, de[1] + dy / n * dist)


def _contorno():
    """Pontos do contorno (y para cima) e quais juncoes levam bracket."""
    sub = CAP - BRACO_TOPO                       # face inferior do braco superior
    pts = [
        (X_ESQ, BRACO_BASE),                     # pe da diagonal, borda esquerda
        (X_DIR - 13 - DIAGONAL, sub),            # topo da diagonal, borda esquerda
        (X_ESQ + 13 + SERIFA_L, sub),            # face do braco ate a serifa
        (X_ESQ + 13 + SERIFA_L, sub - SERIFA_TOPO_P),
        (X_ESQ + 13, sub - SERIFA_TOPO_P),       # base da serifa esquerda
        (X_ESQ + 13, CAP),
        (X_DIR - 13, CAP),                       # topo do braco superior
        (X_DIR - 13, sub),
        (X_ESQ + DIAGONAL, BRACO_BASE),          # pe da diagonal, borda direita
        (X_DIR - SERIFA_L, BRACO_BASE),          # face do braco inferior
        (X_DIR - SERIFA_L, BRACO_BASE + SERIFA_BASE_P),
        (X_DIR, BRACO_BASE + SERIFA_BASE_P),     # topo da serifa direita
        (X_DIR, 0),
        (X_ESQ, 0),
    ]
    return pts, {1, 2, 8, 9}    # as quatro juncoes concavas


def _montar_path():
    pts, brackets = _contorno()
    n = len(pts)
    partes = []
    for i, p in enumerate(pts):
        ant, prox = pts[(i - 1) % n], pts[(i + 1) % n]
        if i in brackets:
            a = _andar(p, ant, BRACKET)
            b = _andar(p, prox, BRACKET)
            partes.append("L{:.1f} {:.1f}".format(*a))
            partes.append("Q{:.1f} {:.1f} {:.1f} {:.1f}".format(p[0], p[1], b[0], b[1]))
        else:
            partes.append("L{:.1f} {:.1f}".format(*p))
    return "M" + partes[0][1:] + "".join(partes[1:]) + "Z"


Z_PATH = _montar_path()
Z_BOX = (X_ESQ, 0, X_DIR, CAP)
Z_RATIO = (X_DIR - X_ESQ) / CAP
Z_HASTE = BRACO_BASE / CAP


def z_svg(x, y, altura, cor):
    """Coloca o glifo com o topo-esquerdo da caixa em (x, y)."""
    x0, y0, _, y1 = Z_BOX
    s = altura / (y1 - y0)
    tx, ty = x - s * x0, y + s * y1
    return ('<g transform="translate({:.2f},{:.2f}) scale({:.5f},-{:.5f})">'
            '<path d="{}" fill="{}"/></g>').format(tx, ty, s, s, Z_PATH, cor)


def marca(lado=512, z_frac=0.52, barra_frac=0.70, barra_larg=None,
          vao_mult=2.0, raio_frac=0.22, com_barra=True, invertida=False,
          mono=False, regua=False):
    """barra_larg em fracao da altura do Z (default: a espessura da haste).

    vao_mult   vao entre barra e Z, em multiplos da largura da barra
    invertida  marca grafite sobre papel
    mono       uma cor so (a barra perde o verde)
    regua      status como fio horizontal sob o Z, no lugar da espinha lateral
    """
    fundo, letra = (PAPER, INK) if invertida else (INK, PAPER)
    barra_cor = letra if mono else OK
    b_larg_f = Z_HASTE if barra_larg is None else barra_larg

    z_alt = lado * z_frac
    z_larg = z_alt * Z_RATIO
    b_larg = z_alt * b_larg_f
    b_alt = z_alt * barra_frac
    vao = b_larg * vao_mult

    if regua:
        # O fio corre sob o Z, na largura da letra. Sem elemento vertical ao
        # lado, nao existe leitura "IZ" -- nem em uma cor so.
        bloco_alt = z_alt + vao + b_larg
        z_y = (lado - bloco_alt) / 2
        zx = (lado - z_larg) / 2
        barra = ""
        if com_barra:
            barra = ('<rect x="{:.2f}" y="{:.2f}" width="{:.2f}" height="{:.2f}" '
                     'rx="{:.2f}" fill="{}"/>').format(zx, z_y + z_alt + vao, z_larg,
                                                       b_larg, b_larg / 2, barra_cor)
    else:
        grupo = (b_larg + vao + z_larg) if com_barra else z_larg
        gx = (lado - grupo) / 2
        z_y = (lado - z_alt) / 2
        zx = gx + (b_larg + vao) if com_barra else gx
        barra = ""
        if com_barra:
            by = z_y + (z_alt - b_alt) / 2
            barra = ('<rect x="{:.2f}" y="{:.2f}" width="{:.2f}" height="{:.2f}" '
                     'rx="{:.2f}" fill="{}"/>').format(gx, by, b_larg, b_alt,
                                                       b_larg / 2, barra_cor)

    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {l} {l}" '
            'width="{l}" height="{l}" role="img" aria-label="Zelo">'
            '<rect width="{l}" height="{l}" rx="{r:.2f}" fill="{f}"/>'
            '{barra}{z}</svg>').format(l=lado, r=lado * raio_frac, f=fundo,
                                       barra=barra, z=z_svg(zx, z_y, z_alt, letra))


VARIANTES = {
    "c-atual": {
        "rotulo": "C — a atual (Z do raster)",
        "nota": "Onde paramos: Z imitado, barra com 20% da altura do Z. Aqui so para comparar.",
        "kw": {"barra_larg": 0.20, "vao_mult": 1.6, "z_frac": 0.48},
    },
    "d-haste": {
        "rotulo": "D — barra na espessura da haste",
        "nota": "Z real do Plex Serif. A barra tem exatamente a espessura do braco horizontal do "
                "Z (9,3%), entao pertence ao mesmo sistema em vez de ser um retangulo avulso.",
        "kw": {},
    },
    "e-presenca": {
        "rotulo": "E — barra reforcada",
        "nota": "Mesma construcao, barra 50% mais grossa que a haste. Concessao ao favicon de "
                "16px, onde a haste pura pode sumir.",
        "kw": {"barra_larg": Z_HASTE * 1.5, "vao_mult": 1.6},
    },
    "f-carimbo": {
        "rotulo": "F — ESCOLHIDA (recipiente de carimbo)",
        "nota": "Raio de 8% no lugar dos 22%: o squircle e um trend de 2020, o quadrado quase reto "
                "puxa carimbo e livro-razao. A barra fica travada em 0.14 -- e a largura exata que "
                "foi aprovada no preview anterior, quando ela saia de Z_HASTE*1.5 com o Z do Plex "
                "cru. Como o redesenho afinou a haste, manter o multiplicador teria estreitado a "
                "barra pelas costas da decisao.",
        "kw": {"barra_larg": 0.14, "vao_mult": 1.6, "raio_frac": 0.08},
    },
    "g-regua": {
        "rotulo": "G — o status vira fio, nao espinha",
        "nota": "O status sai de ao lado do Z e vira um fio sob a letra, na largura dela. Resolve "
                "de vez a leitura 'IZ' -- inclusive em uma cor so, onde nenhum ajuste de espessura "
                "resolvia -- e cita o fio do livro-razao em vez do icone de app.",
        "kw": {"regua": True, "vao_mult": 1.1, "barra_larg": Z_HASTE * 1.2, "raio_frac": 0.08},
    },
}

TAMANHOS = [512, 48, 32, 16]

PROVAS = [
    ({"mono": True}, "1 cor"),
    ({"invertida": True}, "invertida"),
    ({"com_barra": False}, "sem barra"),
]


def _figura(svg, largura, legenda):
    return ('<figure style="width:{w}px"><div class="amostra" style="width:{w}px;height:{w}px">'
            '{s}</div><figcaption>{c}</figcaption></figure>').format(w=largura, s=svg, c=legenda)


def bloco(rotulo, nota, kw, extras=True):
    svg = marca(**kw)
    tam = "".join(_figura(svg, t, "{}px".format(t)) for t in TAMANHOS)
    if not extras:
        return ('<section><h2>{}</h2><p class="nota">{}</p><div class="fileira">{}</div>'
                "</section>").format(rotulo, nota, tam)
    provas = "".join(_figura(marca(**dict(kw, **extra)), 96, leg) for extra, leg in PROVAS)
    return '''<section>
  <h2>{rotulo}</h2><p class="nota">{nota}</p>
  <div class="fileira">{tam}</div>
  <h3>Teste de pressao</h3>
  <div class="fileira">{provas}</div>
  <div class="contexto">
    <div class="aba"><div class="favicon">{svg}</div><span>Zelo — Certidoes</span></div>
    <div class="navbar"><div class="nav-mark">{svg}</div><span class="wordmark">Zelo</span></div>
  </div>
</section>'''.format(rotulo=rotulo, nota=nota, tam=tam, provas=provas, svg=svg)


ESTILO = """
  :root { --ink:#16181C; --paper:#FFFFFF; --mist:#F5F6F8; --line:#E3E6EB; --slate:#6B7280; }
  * { box-sizing:border-box }
  body { font-family:'IBM Plex Sans',sans-serif; margin:0; background:var(--paper); color:var(--ink); }
  .tema { padding:2.5rem 3rem; }
  .tema.dark { --ink:#F2F4F7; --paper:#0F1115; --mist:#16191D; --line:#2A2F35; --slate:#9BA3AD;
               background:var(--paper); color:var(--ink); }
  h1 { font-family:'IBM Plex Serif',serif; font-weight:600; font-size:1.5rem; margin:0 0 .25rem; }
  h2 { font-family:'IBM Plex Serif',serif; font-weight:600; font-size:1.05rem; margin:0 0 .2rem; }
  h3 { font-size:.7rem; text-transform:uppercase; letter-spacing:.09em; color:var(--slate);
       margin:1.6rem 0 .6rem; font-weight:500; }
  .sub { color:var(--slate); margin:0 0 2rem; font-size:.9rem; max-width:70ch; }
  .nota { color:var(--slate); font-size:.85rem; margin:0 0 1rem; max-width:66ch; }
  section { padding:1.75rem 0; border-top:1px solid var(--line); }
  .fileira { display:flex; align-items:flex-end; gap:2rem; flex-wrap:wrap; }
  figure { margin:0 } .amostra svg { width:100%; height:100%; display:block }
  figcaption { font-family:'IBM Plex Mono',monospace; font-size:.7rem; color:var(--slate);
               text-align:center; margin-top:.5rem }
  .contexto { display:flex; gap:2rem; margin-top:1.75rem; flex-wrap:wrap; align-items:center }
  .aba { display:flex; align-items:center; gap:.5rem; background:var(--mist);
         border:1px solid var(--line); border-radius:.5rem .5rem 0 0; padding:.5rem .9rem; font-size:.8rem }
  .favicon { width:16px; height:16px; flex:none } .favicon svg { width:100%; height:100%; display:block }
  .navbar { display:flex; align-items:center; gap:.6rem; background:var(--mist);
            border:1px solid var(--line); border-radius:.5rem; padding:.6rem 1.1rem }
  .nav-mark { width:26px; height:26px; flex:none } .nav-mark svg { width:100%; height:100%; display:block }
  .wordmark { font-family:'IBM Plex Serif',serif; font-weight:600; font-size:1.35rem }
"""

FONTES = ("https://fonts.googleapis.com/css2?family=IBM+Plex+Serif:wght@600"
          "&family=IBM+Plex+Sans:wght@400;500&family=IBM+Plex+Mono&display=swap")


def main():
    destino = Path(__file__).parent / "shots"
    destino.mkdir(exist_ok=True)
    for slug, v in VARIANTES.items():
        (destino / "zelo-mark-{}.svg".format(slug)).write_text(marca(**v["kw"]), encoding="utf-8")

    corpo = "".join(bloco(v["rotulo"], v["nota"], v["kw"], extras=slug != "c-atual")
                    for slug, v in VARIANTES.items())

    html = '''<!doctype html>
<meta charset="utf-8"><title>Marca Zelo — revisao</title>
<link href="{fontes}" rel="stylesheet">
<style>{estilo}</style>
<div class="tema">
  <h1>Marca Zelo — revisao sob o metodo Peters</h1>
  <p class="sub">Tres testes de pressao por variante: <b>1 cor</b> (carimbo, gravacao, PDF em
  preto), <b>invertida</b> (grafite sobre papel) e <b>sem barra</b>. A pergunta dele nao e
  "ficou bonito", e "sobrevive fora do PDF do manual da marca".</p>
  {corpo}
</div>
<div class="tema dark">
  <h1>Tema escuro</h1><p class="sub">O quadrado carrega o proprio fundo.</p>{corpo}
</div>'''.format(fontes=FONTES, estilo=ESTILO, corpo=corpo)

    saida = destino / "zelo-mark.html"
    saida.write_text(html, encoding="utf-8")
    print("preview:", saida)


if __name__ == "__main__":
    main()
