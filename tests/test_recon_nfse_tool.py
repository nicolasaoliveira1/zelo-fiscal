"""Testes estáticos da recon manual, sem abrir navegador nem sessão fiscal."""

import runpy
from pathlib import Path

import dotenv


CAMINHO_TOOL = Path(__file__).parents[1] / 'tools' / 'recon_nfse.py'


def _carregar_tool(monkeypatch):
    monkeypatch.setattr(dotenv, 'load_dotenv', lambda *_args, **_kwargs: None)
    return runpy.run_path(str(CAMINHO_TOOL), run_name='recon_nfse_tool_test')


def test_js_da_recon_nao_trunca_opcoes(monkeypatch):
    namespace = _carregar_tool(monkeypatch)
    assert '.slice(0,40)' not in namespace['JS_INVENTARIO']
    assert 'Array.prototype.slice.call(el.options).map' in namespace['JS_INVENTARIO']
    assert 'create_app' not in CAMINHO_TOOL.read_text(encoding='utf-8')


def test_formatar_preserva_a_ultima_de_919_opcoes(monkeypatch):
    namespace = _carregar_tool(monkeypatch)
    inventario = {
        'url': 'https://portal-sintetico.test/etapa',
        'titulo': 'Etapa sintética',
        'controles': [{
            'tag': 'select',
            'type': 'select',
            'id': 'campo-sintetico',
            'name': 'campo.sintetico',
            'visivel': True,
            'opcoes': [
                {'value': str(indice), 'texto': f'Opção sintética {indice}'}
                for indice in range(919)
            ],
        }],
        'acoes': [],
        'titulos': [],
    }

    texto = namespace['formatar'](inventario)

    assert 'opcao: [918] Opção sintética 918' in texto
