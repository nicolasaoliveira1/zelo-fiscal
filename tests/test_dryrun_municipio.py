"""Testes do dry-run de municipio (COV-05 fatia A).

Sem Selenium/rede: driver falso + o engine de steps mockado no seam
`steps_engine.executar_municipio`. Foco no vocabulario de resultado
(ok/quebrado/parcial/pulado/erro) e em qual etapa e apontada como quebrada.
"""
from types import SimpleNamespace
from unittest.mock import patch

from selenium.common.exceptions import TimeoutException

from app.services import dryrun_municipio as dr


class _FakeDriver:
    """Driver falso: `encontra` sao os locators que resolvem em find_elements."""

    def __init__(self, encontra=(), erro_get=None):
        self.encontra = set(encontra)
        self.erro_get = erro_get
        self.url = None

    def get(self, url):
        if self.erro_get:
            raise self.erro_get
        self.url = url

    def find_elements(self, by, locator):
        return ['elemento'] if locator in self.encontra else []


def _municipio(**kw):
    base = dict(nome='Imbé', url_certidao='https://portal.exemplo/cnd',
                automacao_ativa=True, cnpj_field_id='campoCnpj', by='id')
    base.update(kw)
    return SimpleNamespace(**base)


def _ok_engine():
    return patch.object(dr.steps_engine, 'executar_municipio', return_value=None)


def test_automacao_inativa_e_pulado():
    rel = dr.verificar_municipio(_municipio(automacao_ativa=False), _FakeDriver())
    assert rel['resultado'] == dr.PULADO
    assert rel['quebrados'] == []


def test_sem_url_e_quebrado():
    rel = dr.verificar_municipio(_municipio(url_certidao=None), _FakeDriver())
    assert rel['resultado'] == dr.QUEBRADO
    assert 'URL' in rel['mensagem']


def test_portal_inacessivel_e_erro_nao_quebrado():
    # Falha de infra/rede nao deve ser reportada como drift do municipio.
    drv = _FakeDriver(erro_get=RuntimeError('sem rede'))
    rel = dr.verificar_municipio(_municipio(), drv)
    assert rel['resultado'] == dr.ERRO
    assert rel['quebrados'] == []


def test_fluxo_simples_ok():
    # Municipio sem steps (ex.: Imbé): basta URL abrir e o campo de CNPJ existir.
    drv = _FakeDriver(encontra=['campoCnpj'])
    rel = dr.verificar_municipio(_municipio(), drv, config={})
    assert rel['resultado'] == dr.OK
    assert drv.url == 'https://portal.exemplo/cnd'
    assert [c['etapa'] for c in rel['checagens']] == ['url', 'cnpj']


def test_campo_cnpj_sumiu_e_quebrado():
    drv = _FakeDriver(encontra=[])  # campo nao resolve
    rel = dr.verificar_municipio(_municipio(), drv, config={})
    assert rel['resultado'] == dr.QUEBRADO
    assert rel['quebrados'] == ['cnpj: id=campoCnpj']
    assert 'CNPJ' in rel['mensagem']


def test_skip_cnpj_fill_nao_checa_campo():
    # Quando o CNPJ e preenchido pelos steps, nao ha campo proprio para checar.
    drv = _FakeDriver(encontra=[])
    with _ok_engine():
        rel = dr.verificar_municipio(_municipio(), drv, config={'skip_cnpj_fill': True})
    assert rel['resultado'] == dr.OK
    assert all(c['etapa'] != 'cnpj' for c in rel['checagens'])


def test_before_cnpj_quebrado_aponta_a_etapa():
    # Timeout num passo de navegacao = seletor sumiu (ou captcha) -> quebrado.
    cfg = {'before_cnpj': [
        {'tipo': 'click', 'by': 'id', 'locator': 'btnEmitir'},
        {'tipo': 'click', 'by': 'id', 'locator': 'btnSegundo'},
    ]}
    with patch.object(dr.steps_engine, 'executar_municipio',
                      side_effect=[None, TimeoutException('sumiu')]):
        rel = dr.verificar_municipio(_municipio(), _FakeDriver(), config=cfg)
    assert rel['resultado'] == dr.QUEBRADO
    assert rel['quebrados'] == ['before_cnpj[2]: id=btnSegundo']
    assert 'before_cnpj[2]' in rel['mensagem']


def test_before_cnpj_executa_um_passo_por_vez_com_timeout_limitado():
    # Reuso do engine com granularidade por passo + teto de timeout (portais com
    # captcha usam 120s esperando operador; no dry-run nao ha operador).
    cfg = {'before_cnpj': [
        {'tipo': 'wait_for', 'by': 'name', 'locator': 'opcaoEmissao', 'timeout': 120},
    ], 'skip_cnpj_fill': True}
    with patch.object(dr.steps_engine, 'executar_municipio', return_value=None) as eng:
        dr.verificar_municipio(_municipio(), _FakeDriver(), config=cfg, timeout=8)
    (_drv, _wait, lista, cnpj, _insc), _kw = eng.call_args
    assert len(lista) == 1                  # um passo por chamada
    assert lista[0]['timeout'] == 8         # teto aplicado
    assert cnpj == dr.CNPJ_TESTE


def test_step_destrutivo_nao_e_executado():
    # click_if_text_or_close fecha a janela: nunca roda no dry-run.
    cfg = {'before_cnpj': [
        {'tipo': 'click_if_text_or_close', 'by': 'xpath', 'locator': '//a'},
    ], 'skip_cnpj_fill': True}
    with patch.object(dr.steps_engine, 'executar_municipio') as eng:
        rel = dr.verificar_municipio(_municipio(), _FakeDriver(), config=cfg)
    eng.assert_not_called()
    assert rel['resultado'] == dr.PARCIAL


def test_after_cnpj_primeiro_verificado_sem_clicar():
    # O primeiro locator pos-CNPJ e checado por presenca; nunca clicado (emitiria).
    cfg = {'after_cnpj': [{'tipo': 'click', 'by': 'id', 'locator': 'btnImprimir'}]}
    drv = _FakeDriver(encontra=['campoCnpj', 'btnImprimir'])
    rel = dr.verificar_municipio(_municipio(), drv, config=cfg)
    assert rel['resultado'] == dr.OK
    assert rel['checagens'][-1] == {
        'etapa': 'after_cnpj[1]', 'alvo': 'id=btnImprimir', 'status': dr.OK, 'detalhe': None}


def test_after_cnpj_primeiro_sumiu_e_quebrado():
    cfg = {'after_cnpj': [{'tipo': 'click', 'by': 'id', 'locator': 'btnImprimir'}]}
    drv = _FakeDriver(encontra=['campoCnpj'])  # botao sumiu
    rel = dr.verificar_municipio(_municipio(), drv, config=cfg)
    assert rel['resultado'] == dr.QUEBRADO
    assert rel['quebrados'] == ['after_cnpj[1]: id=btnImprimir']


def test_after_cnpj_seguintes_ficam_parciais():
    # Passos 2+ dependem do clique que emite -> reportados como nao verificados.
    cfg = {'after_cnpj': [
        {'tipo': 'click', 'by': 'id', 'locator': 'btnImprimir'},
        {'tipo': 'click', 'by': 'id', 'locator': 'btnConfirmar'},
    ]}
    drv = _FakeDriver(encontra=['campoCnpj', 'btnImprimir'])
    rel = dr.verificar_municipio(_municipio(), drv, config=cfg)
    assert rel['resultado'] == dr.PARCIAL
    ultimo = rel['checagens'][-1]
    assert ultimo['etapa'] == 'after_cnpj[2]' and ultimo['status'] == dr.PARCIAL


def test_cap_timeout_preserva_valores_menores():
    assert dr._cap_timeout({'timeout': 3}, 8)['timeout'] == 3
    assert dr._cap_timeout({'timeout': 120}, 8)['timeout'] == 8
    assert dr._cap_timeout({'timeout': 'xx'}, 8)['timeout'] == 8
    original = {'timeout': 120}
    dr._cap_timeout(original, 8)
    assert original['timeout'] == 120  # nao muta o passo original
