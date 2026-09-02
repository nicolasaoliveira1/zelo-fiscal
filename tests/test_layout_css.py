from pathlib import Path
import re


RAIZ = Path(__file__).resolve().parents[1]


def _css():
    return (RAIZ / 'app/static/css/style.css').read_text(encoding='utf-8')


def test_conteudo_principal_tem_teto_gutter_e_respiro():
    css = _css()

    assert '--app-main-max-width: 1600px' in css
    assert 'max-width: var(--app-main-max-width)' in css
    assert 'margin-inline: auto' in css
    assert ('padding-inline: clamp(var(--zl-space-4), 3vw, '
            'var(--zl-space-7))') in css
    assert 'padding-bottom: var(--zl-space-7)' in css


def test_mosaico_muda_de_duas_para_quatro_colunas_com_espaco():
    css = _css()

    assert 'align-items: stretch' in css
    assert '.vg-mosaico .vg-card-b' in css
    assert 'flex: 1 1 auto' in css
    assert '.vg-mosaico:not(.is-a1-curto) > .vg-a-a1 .vg-lista' in css
    assert 'flex: 1 1 15rem' in css
    assert 'max-height: none' in css
    assert 'grid-template-columns: repeat(2, minmax(0, 1fr))' in css
    assert '@media (min-width: 1440px)' in css
    assert 'grid-template-columns: repeat(4, minmax(0, 1fr))' in css
    assert '@media (max-width: 767px)' in css
    assert 'grid-template-columns: minmax(0, 1fr)' in css


def test_nfse_mantem_seu_teto_de_largura():
    template = (RAIZ / 'app/templates/nfse.html').read_text(encoding='utf-8')

    assert '.app-main { --app-main-max-width: 1680px; }' in template


def test_ficha_da_fila_nao_repete_a_borda_superior():
    css = _css()

    ficha = re.search(
        r'^\.vg-mosaico > \.vg-a-fila \.zl-ficha\s*\{([^}]*)\}',
        css,
        re.MULTILINE,
    )
    ficha_base = re.search(r'^\.zl-ficha\s*\{([^}]*)\}', css, re.MULTILINE)
    linha = re.search(r'^\.zl-ficha-row\s*\{([^}]*)\}', css, re.MULTILINE)

    assert ficha and 'border-top: 0' in ficha.group(1)
    assert ficha_base and 'border-top: 1px solid var(--zelo-line)' in ficha_base.group(1)
    assert linha and 'border-bottom: 1px solid var(--zelo-line)' in linha.group(1)
