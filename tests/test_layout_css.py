from pathlib import Path


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
