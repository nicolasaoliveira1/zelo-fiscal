"""Contrato acessível dos controles da barra lateral recolhida."""

import re


def _atributo(tag, nome):
    encontrado = re.search(rf'\b{nome}="([^"]*)"', tag)
    return encontrado.group(1) if encontrado else ''


def test_todo_controle_do_rail_tem_fonte_de_dica_no_dom(login_as):
    """AC-07: nenhum controle do rail pode ficar sem nome reutilizável."""
    html = login_as('admin').get('/').get_data(as_text=True)

    links = re.findall(
        r'<a\b[^>]*class="[^"]*\bsidebar-link\b[^"]*"[^>]*>.*?</a>',
        html,
        re.DOTALL,
    )
    marca = re.search(
        r'<a\b[^>]*class="[^"]*\bsidebar-marca\b[^"]*"[^>]*>.*?</a>',
        html,
        re.DOTALL,
    )
    botoes = re.findall(
        r'<button\b[^>]*class="[^"]*\bsidebar-mini\b[^"]*"[^>]*>',
        html,
    )

    assert links
    assert all(re.search(r'<span class="sidebar-rotulo">\s*\S', link) for link in links)
    assert marca and re.search(r'<span class="sidebar-title">\s*\S', marca.group(0))
    assert len(botoes) == 3
    assert all(_atributo(botao, 'aria-label').strip() for botao in botoes)
