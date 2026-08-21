"""Gera a marca Zelo em SVG e o preview de conferencia.

v3 -- a marca deixou de ser a letra Z.

Historico curto, porque cada versao morreu por um motivo diferente:

* v1  Z serifado vetorizado de um raster gerado por IA. Proporcao 0,906 contra
      os 0,784 do glifo de verdade, e serifas que eram entalhes retangulares.
* v2  o glifo REAL do IBM Plex Serif SemiBold, com uma barra de status ao lado.
      Melhor desenho, mas o teste de pressao achou o defeito: em uma cor so a
      barra perdia o verde e a marca lia "IZ".
* v3  (esta) o simbolo nao e mais letra nenhuma. Decisao do usuario em
      2026-08-21: a marca nao deve SER um Z, no maximo insinua-lo.

O que a marca e agora: um octogono -- o carimbo de reparticao, e nao o squircle
de app -- com o gesto do Z aberto como VAO. Duas barras horizontais e uma
diagonal, e nada disso e traco desenhado: e o que falta na chapa. Forma negativa
nao le como letra, le como chapa cortada, que era exatamente o pedido.

O verde da diagonal e o unico ponto de cor, e diz o que o produto inteiro diz:
a unica cor e o status. Em uma cor so a marca nao perde nada -- vira o mesmo
octogono vazado, porque o Z nunca dependeu da cor para existir.

Duas regras de geometria que o codigo sozinho nao conta:

1. A espessura das tres fendas e a MESMA (`FENDA`). A diagonal nao pode ser
   mais gorda que as horizontais, senao le como losango solto em vez de traco
   do mesmo sistema.
2. Por isso `_faixa()` resolve uma quadratica. A espessura perpendicular de um
   paralelogramo de pontas horizontais depende da direcao da ARESTA, nao da
   direcao da diagonal da figura -- confundir as duas entregava 151,9 de
   espessura onde o pedido eram 60. O erro so apareceu quando um corte parou de
   cortar; ate ali a marca so parecia "meio desequilibrada".

Sao DOIS arquivos, mesma geometria:

    zelo-mark.svg            chapa em tinta, vao em papel
    zelo-mark-invertida.svg  chapa em papel, vao em tinta

A invertida nasceu para o tema escuro da barra lateral: ali a chapa (#16181C)
encosta na cor da barra (#16191D) e a silhueta some. O fio de contorno resolve
isso ate uns 64px, mas a barra mostra a marca a 30px, e ali ele ja e um fio de
1px tentando separar duas cores quase iguais. Inverter resolve o problema em vez
de compensa-lo -- e por isso a invertida nao leva fio nenhum.

Depois ela virou tambem o FAVICON PADRAO, e o nome diz a arte e nao o uso por
causa disso. Medido a 16px sobre as cores reais da faixa de abas: a normal vira
um borrao escuro na aba clara, enquanto a invertida perde a chapa (branco sobre
branco) mas mantem o gesto em preto, que e a parte reconhecivel -- e na aba
escura ela ganha de longe. Uma so serve nos dois, entao o favicon nao tem
variante por tema.

Nao ha dependencia de fonte nem de rede: o SVG sai so de aritmetica.

Os derivados binarios (zelo.ico, zelo-mark-512.png) NAO saem daqui -- este
gerador e Python puro de proposito. Eles sao rasterizados do SVG com sharp:

    npm install --os=linux --cpu=x64 --include=optional sharp
    node -e "require('sharp')('app/static/images/zelo-mark.svg')
             .resize(512,512).png().toFile('app/static/images/zelo-mark-512.png')"

Cores: tokens do :root de app/static/css/style.css.
"""
import math
from pathlib import Path

INK = "#16181C"     # --zelo-ink (light)
PAPER = "#FFFFFF"   # --zelo-paper (light)
OK = "#2E7D52"      # --zelo-ok (light) -- a unica cor
LINE = "#2A2F35"    # --zelo-line (dark) -- so o fio de contorno; ver FIO abaixo

BOX = 512
C = BOX / 2
RAIO = 240          # circunraio do octogono
FENDA = 60          # espessura das tres fendas -- a mesma para as tres
FOLGA = 30          # vao entre a horizontal e a diagonal
ESQ, DIR = 120, 392  # extremos horizontais do vao
TOPO, BASE = 120, BOX - 120

# O fio de contorno resolve um problema de tema, nao de desenho: no tema escuro
# a chapa (#16181C) encosta na cor da barra lateral (#16191D) e a silhueta some.
# Um fio em --zelo-line devolve a borda do selo sem precisar de um segundo
# arquivo por tema -- e no tema claro ele e imperceptivel, porque fica um fio
# mais claro sobre a propria chapa.
#
# 16 e nao 8: a barra lateral mostra a marca a 30px, e 8/512 dava 0,47px ali --
# o fio sumia justamente no tamanho em que ele e necessario.
FIO = 16


def _octogono(r=RAIO):
    pts = [(C + r * math.cos(math.radians(22.5 + i * 45)),
            C + r * math.sin(math.radians(22.5 + i * 45))) for i in range(8)]
    return " ".join("{:.1f},{:.1f}".format(*p) for p in pts)


def _faixa(y0, y1, x_dir, x_esq, larg):
    """Paralelogramo de pontas horizontais, de (x_dir, y0) descendo ate
    (x_esq, y1), com espessura `larg` medida PERPENDICULAR as arestas.

    Os vertices sao (x_dir,y0) (x_dir-d,y0) (x_esq,y1) (x_esq+d,y1), entao a
    aresta inclinada tem direcao (dx+d, dy). Resolvendo
    d*|dy| / |(dx+d, dy)| == larg chega-se a

        d^2 (larg^2 - dy^2) + 2 larg^2 dx d + larg^2 (dx^2 + dy^2) = 0
    """
    dx, dy = x_esq - x_dir, y1 - y0
    k = larg * larg
    a, b, c = k - dy * dy, 2 * k * dx, k * (dx * dx + dy * dy)
    delta = b * b - 4 * a * c
    if a == 0 or delta < 0:
        raise ValueError("faixa de {} nao cabe em dy={}".format(larg, dy))
    d = min(r for r in ((-b + delta ** 0.5) / (2 * a),
                        (-b - delta ** 0.5) / (2 * a)) if r > 0)
    return "{},{} {:.1f},{} {},{} {:.1f},{}".format(
        x_dir, y0, x_dir - d, y0, x_esq, y1, x_esq + d, y1)


def marca(lado=512, com_status=True, invertida=False, mono=False, com_fio=True):
    """invertida  chapa clara com o vao em tinta -- a variante de tema escuro
    mono       uma cor so (a diagonal perde o verde)
    com_status desliga a diagonal, para ver o que sobra sem ela
    com_fio    o fio de contorno; a invertida nao usa (ver FIO)

    A invertida NAO e monocromatica: a diagonal segue verde, porque o verde
    ali e o status e nao decoracao. E o mesmo #2E7D52 da versao normal por um
    motivo exato -- aquele token e o verde calibrado para fundo de PAPEL, e na
    invertida a chapa e papel. Trocar pelo verde do tema escuro (#4FB07E), que
    e calibrado para fundo quase-preto, erraria o contraste.
    """
    chapa, vao = (PAPER, INK) if invertida else (INK, PAPER)
    status = vao if mono else OK
    oct_pts = _octogono()
    fio = ('<polygon points="{}" fill="none" stroke="{}" stroke-width="{}"/>'
           .format(oct_pts, LINE, FIO) if com_fio and not invertida else "")
    diagonal = ('<polygon points="{}" fill="{}"/>'.format(
        _faixa(TOPO + FENDA + FOLGA, BASE - FENDA - FOLGA, DIR, ESQ, FENDA), status)
        if com_status else "")
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {b} {b}" '
            'width="{l}" height="{l}" role="img" aria-label="Zelo">'
            '<polygon points="{o}" fill="{ch}"/>{fio}'
            '<rect x="{e}" y="{t}" width="{w}" height="{f}" fill="{v}"/>'
            '{d}'
            '<rect x="{e}" y="{by}" width="{w}" height="{f}" fill="{v}"/>'
            "</svg>").format(b=BOX, l=lado, o=oct_pts, ch=chapa, fio=fio,
                             e=ESQ, t=TOPO, w=DIR - ESQ, f=FENDA, v=vao,
                             by=BASE - FENDA, d=diagonal)


TAMANHOS = [512, 48, 32, 16]
PROVAS = [
    ({"mono": True}, "1 cor"),
    ({"invertida": True}, "invertida (tema escuro)"),
    ({"com_status": False}, "sem status"),
    ({"com_fio": False}, "sem fio"),
]

ESTILO = """
  :root { --ink:#16181C; --paper:#FFFFFF; --mist:#F5F6F8; --line:#E3E6EB; --slate:#6B7280; }
  * { box-sizing:border-box }
  body { font-family:'IBM Plex Sans',sans-serif; margin:0; background:var(--paper); color:var(--ink); }
  .tema { padding:2.5rem 3rem; }
  .tema.dark { --ink:#F2F4F7; --paper:#0F1115; --mist:#16191D; --line:#2A2F35; --slate:#9BA3AD;
               background:var(--paper); color:var(--ink); }
  h1 { font-family:'IBM Plex Serif',serif; font-weight:600; font-size:1.5rem; margin:0 0 .25rem; }
  h3 { font-size:.7rem; text-transform:uppercase; letter-spacing:.09em; color:var(--slate);
       margin:2rem 0 .6rem; font-weight:500; }
  .sub { color:var(--slate); margin:0 0 1rem; font-size:.9rem; max-width:72ch; }
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
  .nav-mark { width:30px; height:30px; flex:none } .nav-mark svg { width:100%; height:100%; display:block }
  .wordmark { font-family:'IBM Plex Serif',serif; font-weight:600; font-size:1.35rem;
              letter-spacing:.04em }
"""

FONTES = ("https://fonts.googleapis.com/css2?family=IBM+Plex+Serif:wght@600"
          "&family=IBM+Plex+Sans:wght@400;500&family=IBM+Plex+Mono&display=swap")


def _fig(svg, largura, legenda):
    return ('<figure style="width:{w}px"><div class="amostra" style="width:{w}px;height:{w}px">'
            '{s}</div><figcaption>{c}</figcaption></figure>').format(w=largura, s=svg, c=legenda)


def _bloco():
    tam = "".join(_fig(marca(), t, "{}px".format(t)) for t in TAMANHOS)
    provas = "".join(_fig(marca(**extra), 96, leg) for extra, leg in PROVAS)
    svg = marca()
    return '''<h3>Tamanhos</h3><div class="fileira">{tam}</div>
  <h3>Teste de pressao</h3><div class="fileira">{provas}</div>
  <div class="contexto">
    <div class="aba"><div class="favicon">{svg}</div><span>Zelo — Certidoes</span></div>
    <div class="navbar"><div class="nav-mark">{svg}</div><span class="wordmark">Zelo</span></div>
  </div>'''.format(tam=tam, provas=provas, svg=svg)


def main():
    raiz = Path(__file__).resolve().parents[2]
    imagens = raiz / "app" / "static" / "images"
    for nome, kw in (("zelo-mark.svg", {}), ("zelo-mark-invertida.svg", {"invertida": True})):
        destino = imagens / nome
        destino.write_text(marca(**kw), encoding="utf-8")
        print("marca:", destino, "({} bytes)".format(destino.stat().st_size))

    shots = Path(__file__).parent / "shots"
    shots.mkdir(exist_ok=True)
    corpo = _bloco()
    html = '''<!doctype html>
<meta charset="utf-8"><title>Marca Zelo — v3</title>
<link href="{fontes}" rel="stylesheet"><style>{estilo}</style>
<div class="tema">
  <h1>Marca Zelo — o octogono vazado</h1>
  <p class="sub">A marca deixou de ser a letra Z. Quatro testes de pressao: <b>1 cor</b>
  (carimbo, gravacao, PDF em preto), <b>invertida</b>, <b>sem status</b> e <b>sem o fio</b> de
  contorno — este ultimo mostra por que o fio existe, e so no tema escuro.</p>
  {corpo}
</div>
<div class="tema dark">
  <h1>Tema escuro</h1>
  <p class="sub">Aqui a chapa encosta na cor da barra lateral. Compare "sem fio" com os
  demais.</p>{corpo}
</div>'''.format(fontes=FONTES, estilo=ESTILO, corpo=corpo)
    saida = shots / "zelo-mark.html"
    saida.write_text(html, encoding="utf-8")
    print("preview:", saida)


if __name__ == "__main__":
    main()
