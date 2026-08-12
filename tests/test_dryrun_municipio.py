"""Testes do dry-run de municipio (COV-05 fatia A).

Sem Selenium/rede: driver falso + o engine de steps mockado no seam
`steps_engine.executar_municipio`. Foco no vocabulario de resultado
(ok/quebrado/parcial/pulado/erro) e em qual etapa e apontada como quebrada.
"""
import copy
import time
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
    # Nome neutro de proposito: 'Imbé' dispara a segunda passada (variante geral),
    # entao os testes que a querem pedem o nome explicitamente.
    base = dict(nome='Vila Teste', url_certidao='https://portal.exemplo/cnd',
                automacao_ativa=True, cnpj_field_id='campoCnpj', by='id',
                pre_fill_click_id=None, pre_fill_click_by=None)
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
    rel = dr.verificar_municipio(_municipio(), drv, config={}, timeout=0)
    assert rel['resultado'] == dr.QUEBRADO
    assert rel['quebrados'] == ['cnpj: id=campoCnpj']
    assert 'CNPJ' in rel['mensagem']


def test_espera_o_campo_carregar_antes_de_acusar_quebrado():
    """Regressao (relatado no Imbé): o portal demora a renderizar e a checagem
    instantanea acusava 'quebrado' num campo que so ainda nao tinha carregado."""
    class _DriverLento(_FakeDriver):
        def __init__(self):
            super().__init__()
            self.tentativas = 0

        def find_elements(self, by, locator):
            self.tentativas += 1
            return ['elemento'] if self.tentativas >= 3 else []   # aparece no 3o poll

    drv = _DriverLento()
    rel = dr.verificar_municipio(_municipio(), drv, config={}, timeout=5)
    assert rel['resultado'] == dr.OK          # esperou em vez de acusar drift
    assert drv.tentativas >= 3


def test_pre_fill_click_e_executado_antes_de_procurar_o_cnpj():
    """Regressao (relatado no Imbé): o campo `form:cnpjDI` so existe DEPOIS do
    radio "Pessoa Juridica", que fica em coluna do municipio e nao em
    `before_cnpj`. Sem executar o clique, o dry-run acusava drift num portal
    intacto — clicando a mao, o mesmo fluxo dava OK."""
    ordem = []

    class _DriverComRadio(_FakeDriver):
        def find_elements(self, by, locator):
            # o campo so aparece depois do clique no radio
            if locator == "input[id='form:cnpjDI']" and 'clicou' not in ordem:
                return []
            return ['elemento']

    drv = _DriverComRadio()
    muni = _municipio(cnpj_field_id="input[id='form:cnpjDI']", by='css_selector',
                      pre_fill_click_id="input[value='J']", pre_fill_click_by='css_selector')

    def _clicar(info_site, wait, by_padrao=None, pausa=0.0):
        ordem.append('clicou')
        return True

    with patch.object(dr.steps_engine, 'clicar_pre_fill', side_effect=_clicar) as pre:
        rel = dr.verificar_municipio(muni, drv, config={}, timeout=0)

    pre.assert_called_once()
    info = pre.call_args.args[0]
    assert info['pre_fill_click_id'] == "input[value='J']"
    assert rel['resultado'] == dr.OK
    assert [c['etapa'] for c in rel['checagens']] == ['url', 'pre_fill_click', 'cnpj']


def test_pre_fill_click_roda_depois_do_before_cnpj():
    # Mesma ordem da emissao real: before_cnpj -> pre_fill_click -> campo de CNPJ.
    ordem = []
    cfg = {'before_cnpj': [{'tipo': 'click', 'by': 'id', 'locator': 'btnAba'}],
           'after_cnpj': []}
    muni = _municipio(pre_fill_click_id="input[value='J']", pre_fill_click_by='css_selector')
    with patch.object(dr.steps_engine, 'executar_municipio',
                      side_effect=lambda *a, **k: ordem.append('step')), \
            patch.object(dr.steps_engine, 'clicar_pre_fill',
                         side_effect=lambda *a, **k: ordem.append('pre') or True):
        dr.verificar_municipio(muni, _FakeDriver(encontra=['campoCnpj']), config=cfg)
    assert ordem == ['step', 'pre']


def test_pre_fill_click_que_sumiu_e_quebrado_no_passo_certo():
    # O radio some -> a mensagem aponta o passo pre-CNPJ, nao o campo de CNPJ.
    muni = _municipio(pre_fill_click_id="input[value='J']", pre_fill_click_by='css_selector')
    with patch.object(dr.steps_engine, 'clicar_pre_fill', return_value=False):
        rel = dr.verificar_municipio(muni, _FakeDriver(), config={}, timeout=0)
    assert rel['resultado'] == dr.QUEBRADO
    assert rel['quebrados'] == ["pre_fill_click: css_selector=input[value='J']"]
    assert 'pré-CNPJ' in rel['mensagem']
    assert all(c['etapa'] != 'cnpj' for c in rel['checagens'])   # nao acusa o campo errado


def test_sem_pre_fill_click_nao_registra_etapa():
    # Maioria dos municipios nao tem o passo: nada muda no relatorio.
    drv = _FakeDriver(encontra=['campoCnpj'])
    with patch.object(dr.steps_engine, 'clicar_pre_fill', return_value=None) as pre:
        rel = dr.verificar_municipio(_municipio(), drv, config={})
    pre.assert_called_once()
    assert [c['etapa'] for c in rel['checagens']] == ['url', 'cnpj']


def test_pre_fill_click_nao_roda_com_skip_cnpj_fill():
    # Sem campo proprio de CNPJ o clique perde a finalidade, e a pagina esta num
    # estado que o fluxo real nunca alcanca (o passo que emite nao foi executado).
    cfg = {'skip_cnpj_fill': True, 'before_cnpj': []}
    muni = _municipio(pre_fill_click_id="input[value='J']", pre_fill_click_by='css_selector')
    with patch.object(dr.steps_engine, 'clicar_pre_fill') as pre:
        dr.verificar_municipio(muni, _FakeDriver(), config=cfg)
    pre.assert_not_called()


def test_localiza_respeita_o_teto_e_desiste():
    # Sem elemento nenhum, desiste no teto (nao trava o dry-run).
    inicio = time.monotonic()
    achou = dr._localiza(_FakeDriver(), 'id', 'inexistente', timeout=0.6)
    assert achou is False
    assert 0.5 <= (time.monotonic() - inicio) < 3


def test_skip_cnpj_fill_nao_checa_campo():
    # Quando o CNPJ e preenchido pelos steps, nao ha campo proprio para checar.
    drv = _FakeDriver(encontra=[])
    with _ok_engine():
        rel = dr.verificar_municipio(_municipio(), drv, config={'skip_cnpj_fill': True})
    assert rel['resultado'] == dr.OK
    assert all(c['etapa'] != 'cnpj' for c in rel['checagens'])


def test_before_cnpj_quebrado_aponta_a_etapa():
    # Falha de click num passo de navegacao = o elemento nao esta la -> drift.
    # (com after_cnpj presente, os passos de before_cnpj sao todos executaveis)
    cfg = {'before_cnpj': [
        {'tipo': 'click', 'by': 'id', 'locator': 'btnEmitir'},
        {'tipo': 'click', 'by': 'id', 'locator': 'btnSegundo'},
    ], 'after_cnpj': [{'tipo': 'click', 'by': 'id', 'locator': 'btnImprimir'}]}
    with patch.object(dr.steps_engine, 'executar_municipio',
                      side_effect=[None, TimeoutException('sumiu')]):
        rel = dr.verificar_municipio(_municipio(), _FakeDriver(), config=cfg)
    assert rel['resultado'] == dr.QUEBRADO
    assert rel['quebrados'] == ['before_cnpj[2]: id=btnSegundo']
    assert 'seletor mudou' in rel['mensagem']


def test_wait_for_expirado_e_parcial_nao_quebrado():
    # Gate de captcha (IPM espera opcaoEmissao por 120s) NAO e drift: vira
    # parcial, senao o job diario alertaria falso todo dia.
    cfg = {'before_cnpj': [
        {'tipo': 'wait_for', 'by': 'name', 'locator': 'opcaoEmissao', 'timeout': 120},
    ], 'skip_cnpj_fill': True}
    with patch.object(dr.steps_engine, 'executar_municipio',
                      side_effect=TimeoutException('captcha')):
        rel = dr.verificar_municipio(_municipio(), _FakeDriver(), config=cfg)
    assert rel['resultado'] == dr.PARCIAL
    assert rel['quebrados'] == []          # nao entra na lista de quebrados
    assert 'captcha' in rel['mensagem']


def test_passo_que_emite_nao_e_executado_apenas_verificado():
    # Ultimo click de before_cnpj sem after_cnpj = acao terminal (emite).
    cfg = {'before_cnpj': [
        {'tipo': 'select', 'by': 'name', 'locator': 'opcaoEmissao'},
        {'tipo': 'click', 'by': 'name', 'locator': 'confirmar'},
    ], 'skip_cnpj_fill': True}
    drv = _FakeDriver(encontra=['confirmar'])
    with patch.object(dr.steps_engine, 'executar_municipio', return_value=None) as eng:
        rel = dr.verificar_municipio(_municipio(), drv, config=cfg)
    # so o select foi executado; o click de emissao nunca rodou
    assert eng.call_count == 1
    assert eng.call_args_list[0].args[2][0]['locator'] == 'opcaoEmissao'
    assert rel['resultado'] == dr.PARCIAL
    assert rel['checagens'][-1]['detalhe'] == 'passo que emite: verificado sem clicar'


def test_passo_que_emite_sumiu_e_quebrado():
    cfg = {'before_cnpj': [{'tipo': 'click', 'by': 'name', 'locator': 'confirmar'}],
           'skip_cnpj_fill': True}
    with patch.object(dr.steps_engine, 'executar_municipio') as eng:
        rel = dr.verificar_municipio(_municipio(), _FakeDriver(encontra=[]), config=cfg, timeout=0)
    eng.assert_not_called()
    assert rel['resultado'] == dr.QUEBRADO
    assert rel['quebrados'] == ['before_cnpj[1]: name=confirmar']


def test_flag_emite_explicita_vence_a_posicao():
    # Anotacao explicita protege um passo no MEIO do fluxo (onde a regra
    # conservadora por posicao nao alcanca). O passo seguinte (fill, nao aciona)
    # segue executavel.
    cfg = {'before_cnpj': [
        {'tipo': 'click', 'by': 'id', 'locator': 'btnEmite', 'emite': True},
        {'tipo': 'fill', 'by': 'id', 'locator': 'btnDepois', 'value': 'cnpj'},
    ], 'skip_cnpj_fill': True}
    drv = _FakeDriver(encontra=['btnEmite'])
    with patch.object(dr.steps_engine, 'executar_municipio', return_value=None) as eng:
        rel = dr.verificar_municipio(_municipio(), drv, config=cfg)
    # o 1o (emite) nao roda; o 2o roda normalmente
    assert eng.call_count == 1
    assert eng.call_args_list[0].args[2][0]['locator'] == 'btnDepois'
    assert rel['resultado'] == dr.PARCIAL


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
    rel = dr.verificar_municipio(_municipio(), drv, config=cfg, timeout=0)
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


def _patch_driver(chrome=None, uc=None, adquire=True, uc_erro=None):
    """Contexto com as fabricas de driver e o lock do perfil municipal mockados."""
    from contextlib import ExitStack
    pilha = ExitStack()
    p = pilha.enter_context
    alvo_uc = patch.object(dr, '_criar_driver_uc', side_effect=uc_erro) if uc_erro \
        else patch.object(dr, '_criar_driver_uc', return_value=uc or _FakeDriver())
    mocks = {
        'chrome': p(patch.object(dr, '_criar_driver_chrome', return_value=chrome or _FakeDriver())),
        'uc': p(alvo_uc),
        'adquire': p(patch.object(dr, '_municipal_profile_acquire', return_value=adquire)),
        'libera': p(patch.object(dr, '_municipal_profile_release')),
        'config': p(patch.object(dr, '_carregar_config_municipio', return_value={})),
    }
    return pilha, mocks


def test_executar_dry_run_nao_ipm_usa_chrome_e_fecha():
    drv = _FakeDriver(encontra=['campoCnpj'])
    drv.quit = lambda: setattr(drv, 'fechado', True)
    pilha, mocks = _patch_driver(chrome=drv)
    with pilha:
        rel = dr.executar_dry_run(_municipio())
    assert rel['resultado'] == dr.OK
    assert drv.fechado is True
    mocks['uc'].assert_not_called()
    mocks['adquire'].assert_not_called()   # lock so para IPM


def test_executar_dry_run_ipm_usa_uc_e_libera_o_lock():
    drv = _FakeDriver(encontra=['campoCnpj'])
    drv.quit = lambda: None
    muni = _municipio(nome='Gravataí', url_certidao='https://gravatai.atende.net/cnd')
    pilha, mocks = _patch_driver(uc=drv)
    with pilha:
        rel = dr.executar_dry_run(muni)
    assert rel['resultado'] == dr.OK
    mocks['uc'].assert_called_once()
    mocks['chrome'].assert_not_called()
    mocks['libera'].assert_called_once()   # lock sempre devolvido


def test_executar_dry_run_perfil_ocupado_e_erro_sem_driver():
    muni = _municipio(url_certidao='https://osorio.atende.net/cnd')
    pilha, mocks = _patch_driver(adquire=False)
    with pilha:
        rel = dr.executar_dry_run(muni)
    assert rel['resultado'] == dr.ERRO       # infra, nao drift do portal
    assert 'em uso' in rel['mensagem']
    mocks['uc'].assert_not_called()
    mocks['libera'].assert_not_called()      # nao adquiriu, nao libera


def test_executar_dry_run_uc_indisponivel_libera_lock():
    from app.automation.driver import UcIndisponivelError
    muni = _municipio(url_certidao='https://gravatai.atende.net/cnd')
    pilha, mocks = _patch_driver(uc_erro=UcIndisponivelError('uc fora'))
    with pilha:
        rel = dr.executar_dry_run(muni)
    assert rel['resultado'] == dr.ERRO
    mocks['libera'].assert_called_once()     # sem vazar o perfil


# ---- Variantes: Imbé tem DUAS telas (mobiliário e geral) ----

# Config REAL do Imbé (migration c9f1a2d4e7b3): as colunas descrevem a tela
# MOBILIARIA e a tela GERAL vive em `imbe_variantes`.
_CONFIG_IMBE_REAL = {
    'before_cnpj': [], 'after_cnpj': [],
    'imbe_variantes': {'geral': {
        'url': 'https://grp.imbe.rs.gov.br/grp/acessoexterno/programaAcessoExterno.faces?codigo=684509',
        'cnpj_field_id': 'form:cnpjD', 'by': 'name',
        'pre_fill_click_id': "input[value='J']", 'pre_fill_click_by': 'css_selector'}},
}


def _municipio_imbe(**kw):
    base = dict(
        nome='Imbé',
        url_certidao='https://grp.imbe.rs.gov.br/grp/acessoexterno/programaAcessoExterno.faces?codigo=689513',
        cnpj_field_id="input[id='form:cnpjDI']", by='css_selector',
        pre_fill_click_id="input[value='J']", pre_fill_click_by='css_selector')
    base.update(kw)
    return _municipio(**base)


def test_variantes_imbe_descreve_as_duas_telas():
    passadas = dr.variantes(_municipio_imbe(), _CONFIG_IMBE_REAL)
    assert [rot for rot, _m, _c in passadas] == ['mobiliário', 'geral']

    (_r1, mob, _c1), (_r2, geral, cfg_geral) = passadas
    assert mob.cnpj_field_id == "input[id='form:cnpjDI']" and mob.by == 'css_selector'
    # a tela geral vem do nucleo da emissao (_aplicar_variantes_imbe): outra URL,
    # outro campo, outro `by` — e o radio PJ herdado das colunas.
    assert geral.cnpj_field_id == 'form:cnpjD' and geral.by == 'name'
    assert geral.url_certidao.endswith('codigo=684509')
    assert geral.pre_fill_click_id == "input[value='J']"
    assert cfg_geral['after_cnpj'][-1]['tipo'] == 'press_tab'   # o que a emissao faz


def test_variantes_nao_contaminam_o_config_original():
    # `_aplicar_variantes_imbe` mexe no after_cnpj: cada passada leva sua copia.
    original = copy.deepcopy(_CONFIG_IMBE_REAL)
    dr.variantes(_municipio_imbe(), _CONFIG_IMBE_REAL)
    assert _CONFIG_IMBE_REAL == original


def test_variantes_municipio_comum_tem_uma_passada_sem_rotulo():
    passadas = dr.variantes(_municipio(nome='Tramandaí'), {'before_cnpj': []})
    assert len(passadas) == 1
    rotulo, muni, _cfg = passadas[0]
    assert rotulo == '' and muni.nome == 'Tramandaí'


def test_dry_run_do_imbe_verifica_as_duas_telas():
    # Contrato central: as DUAS URLs sao abertas e os DOIS campos conferidos.
    visitadas = []

    class _DriverImbe(_FakeDriver):
        def get(self, url):
            visitadas.append(url)

        def find_elements(self, by, locator):
            return ['elemento'] if locator in ("input[id='form:cnpjDI']", 'form:cnpjD') else []

    drv = _DriverImbe()
    drv.quit = lambda: None
    pilha, _mocks = _patch_driver(chrome=drv)
    with pilha, patch.object(dr, '_carregar_config_municipio', return_value=_CONFIG_IMBE_REAL), \
            patch.object(dr.steps_engine, 'clicar_pre_fill', return_value=True):
        rel = dr.executar_dry_run(_municipio_imbe())

    assert [u.rsplit('=', 1)[-1] for u in visitadas] == ['689513', '684509']
    etapas = [c['etapa'] for c in rel['checagens']]
    assert 'mobiliário/cnpj' in etapas and 'geral/cnpj' in etapas
    assert rel['resultado'] == dr.OK


def test_quebra_so_na_tela_geral_e_reportada_com_o_rotulo():
    # A regressao que motivou as duas passadas: a tela mobiliaria segue de pe e a
    # geral quebra — com uma passada so, isso passava verde.
    class _DriverGeralQuebrada(_FakeDriver):
        def find_elements(self, by, locator):
            return ['elemento'] if locator == "input[id='form:cnpjDI']" else []

    drv = _DriverGeralQuebrada()
    drv.quit = lambda: None
    pilha, _mocks = _patch_driver(chrome=drv)
    with pilha, patch.object(dr, '_carregar_config_municipio', return_value=_CONFIG_IMBE_REAL), \
            patch.object(dr.steps_engine, 'clicar_pre_fill', return_value=True):
        rel = dr.executar_dry_run(_municipio_imbe(), timeout=0)

    assert rel['resultado'] == dr.QUEBRADO
    assert rel['quebrados'] == ['geral/cnpj: name=form:cnpjD']   # diz QUAL tela
    assert rel['mensagem'].startswith('[geral]')
    # o municipio continua com UMA linha no painel/alerta
    assert dr.ultimos_resultados()['Imbé']['resultado'] == dr.QUEBRADO


def test_mescla_fica_com_o_pior_resultado():
    piores = [
        ([dr.OK, dr.OK], dr.OK),
        ([dr.OK, dr.PARCIAL], dr.PARCIAL),
        ([dr.PARCIAL, dr.QUEBRADO], dr.QUEBRADO),
        ([dr.OK, dr.ERRO], dr.ERRO),
        ([dr.ERRO, dr.QUEBRADO], dr.QUEBRADO),   # drift vence infra: alerta sai
        ([dr.PULADO, dr.PULADO], dr.PULADO),     # automacao inativa em ambas
    ]
    for resultados, esperado in piores:
        relatorios = [{'municipio': 'Imbé', 'resultado': r, 'checagens': [],
                       'quebrados': [], 'mensagem': None, 'variante': 'x'}
                      for r in resultados]
        assert dr._mesclar(relatorios)['resultado'] == esperado, resultados


def test_mescla_junta_as_mensagens_rotuladas():
    relatorios = [
        {'municipio': 'Imbé', 'resultado': dr.PARCIAL, 'checagens': [], 'quebrados': ['a'],
         'mensagem': 'captcha', 'variante': 'mobiliário'},
        {'municipio': 'Imbé', 'resultado': dr.QUEBRADO, 'checagens': [], 'quebrados': ['b'],
         'mensagem': 'campo sumiu', 'variante': 'geral'},
    ]
    fundido = dr._mesclar(relatorios)
    assert fundido['quebrados'] == ['a', 'b']
    assert fundido['mensagem'] == '[mobiliário] captcha | [geral] campo sumiu'


def test_executar_dry_run_varios_resume_por_resultado():
    with patch.object(dr, 'executar_dry_run', side_effect=[
            {'resultado': dr.OK}, {'resultado': dr.QUEBRADO}, {'resultado': dr.OK}]):
        relatorios, resumo = dr.executar_dry_run_varios([1, 2, 3])
    assert len(relatorios) == 3
    assert resumo['total'] == 3
    assert resumo[dr.OK] == 2 and resumo[dr.QUEBRADO] == 1
    assert resumo[dr.ERRO] == 0


# Config REAL do Gravatai (migration e6f2c1b9a4d8) — Osorio/Novo Hamburgo tem a
# mesma forma. Regressao: a fronteira posicional ingenua ("before_cnpj e seguro")
# executaria o `click confirmar` final, que EMITE a certidao.
_CONFIG_GRAVATAI_REAL = {
    'skip_cnpj_fill': True, 'classificar_pdf_status': True,
    'before_cnpj': [
        {'tipo': 'wait_for', 'by': 'name', 'locator': 'opcaoEmissao',
         'timeout': 120, 'state': 'clickable'},
        {'tipo': 'select', 'by': 'name', 'locator': 'opcaoEmissao', 'text_contains': 'CNPJ'},
        {'tipo': 'fill', 'by': 'name', 'locator': 'cpfCnpj', 'value': 'cnpj'},
        {'tipo': 'select', 'by': 'name', 'locator': 'FinalidadeCertidaoDebito.codigo',
         'text_contains': 'CONTRIBUINTE'},
        {'tipo': 'click', 'by': 'name', 'locator': 'confirmar'},
    ],
}


def test_config_real_ipm_nunca_clica_no_passo_que_emite():
    drv = _FakeDriver(encontra=['confirmar'])
    with patch.object(dr.steps_engine, 'executar_municipio', return_value=None) as eng:
        rel = dr.verificar_municipio(_municipio(nome='Gravataí'), drv,
                                     config=_CONFIG_GRAVATAI_REAL)
    executados = [c.args[2][0]['locator'] for c in eng.call_args_list]
    assert 'confirmar' not in executados     # o passo que emite NUNCA roda
    assert executados == ['opcaoEmissao', 'opcaoEmissao', 'cpfCnpj',
                          'FinalidadeCertidaoDebito.codigo']
    assert rel['resultado'] == dr.PARCIAL


def test_config_real_xangri_la_nao_clica_em_imprimir():
    # Ponta Pora/Xangri-La: o passo terminal e um clique em "Imprimir" (emite).
    cfg = {'skip_cnpj_fill': True, 'before_cnpj': [
        {'tipo': 'fill', 'by': 'css_selector', 'locator': '#itIdent', 'value': 'cnpj'},
        {'tipo': 'click', 'by': 'xpath', 'locator': "//span[contains(text(),'Imprimir')]"},
    ]}
    drv = _FakeDriver(encontra=["//span[contains(text(),'Imprimir')]"])
    with patch.object(dr.steps_engine, 'executar_municipio', return_value=None) as eng:
        rel = dr.verificar_municipio(_municipio(nome='Xangri-Lá'), drv, config=cfg)
    executados = [c.args[2][0]['locator'] for c in eng.call_args_list]
    assert executados == ['#itIdent']        # o "Imprimir" nao foi clicado
    assert rel['resultado'] == dr.PARCIAL


def test_sem_skip_cnpj_fill_nenhum_passo_emite():
    # Porto Alegre: before_cnpj e so um radio pre-CNPJ. Com skip_cnpj_fill=False o
    # CNPJ ainda nao foi preenchido nessa fase, entao nada pode emitir -> executa.
    cfg = {'before_cnpj': [{'tipo': 'click', 'by': 'id', 'locator': 'gwt-uid-2'}],
           'after_cnpj': []}
    drv = _FakeDriver(encontra=['campoCnpj'])
    with patch.object(dr.steps_engine, 'executar_municipio', return_value=None) as eng:
        rel = dr.verificar_municipio(_municipio(nome='Porto Alegre'), drv, config=cfg)
    assert eng.call_count == 1                       # o radio FOI exercido
    assert rel['resultado'] == dr.OK                 # nao fica 'parcial' eterno


def test_emite_false_explicito_libera_a_execucao():
    cfg = {'before_cnpj': [{'tipo': 'click', 'by': 'id', 'locator': 'btnSeguro',
                            'emite': False}], 'skip_cnpj_fill': True}
    with patch.object(dr.steps_engine, 'executar_municipio', return_value=None) as eng:
        dr.verificar_municipio(_municipio(), _FakeDriver(), config=cfg)
    eng.assert_called_once()


def test_cnpj_de_empresa_da_cidade_e_preferido(app, ids):
    # Portais que consultam cadastro devolvem vazio para CNPJ nao inscrito; usar
    # um contribuinte real da cidade evita falso 'quebrado'. A empresa semeada
    # pelo fixture fica em Tramandai.
    with app.app_context():
        muni_ok = _municipio(nome='Tramandaí')
        assert dr.cnpj_para_teste(muni_ok) == '11.111.111/1111-11'
        # cidade sem empresa -> cai no CNPJ publico
        assert dr.cnpj_para_teste(_municipio(nome='Cidade Sem Empresa')) == dr.CNPJ_TESTE


def test_contrato_configs_reais_nunca_executam_passo_que_emite():
    """Contrato sobre as formas REAIS de config (o que deixou o blocker passar).

    Para cada forma, o(s) locator(s) de emissao NAO podem aparecer entre os
    passos executados. Cobre as duas familias do seed: IPM (confirmar) e
    JSF/imobiliario (Imprimir)."""
    formas = [
        # (nome, config, locators que NUNCA podem ser executados)
        ('Gravataí/Osório/Novo Hamburgo', _CONFIG_GRAVATAI_REAL, ['confirmar']),
        ('Ponta Porã/Xangri-Lá', {
            'skip_cnpj_fill': True, 'before_cnpj': [
                {'tipo': 'click', 'by': 'xpath', 'locator': "//a[span[contains(text(),'Contribuinte')]]"},
                {'tipo': 'fill', 'by': 'css_selector', 'locator': '#itIdent', 'value': 'cnpj'},
                {'tipo': 'click', 'by': 'css_selector', 'locator': '#btnValidar'},
                {'tipo': 'click', 'by': 'xpath', 'locator': "//span[contains(text(),'Imprimir')]"},
            ]}, ["//span[contains(text(),'Imprimir')]"]),
    ]
    for nome, cfg, proibidos in formas:
        drv = _FakeDriver(encontra=proibidos + ['campoCnpj'])
        with patch.object(dr.steps_engine, 'executar_municipio', return_value=None) as eng:
            dr.verificar_municipio(_municipio(nome=nome), drv, config=cfg)
        executados = [c.args[2][0]['locator'] for c in eng.call_args_list]
        for proibido in proibidos:
            assert proibido not in executados, f'{nome}: executou passo que emite {proibido}'


def test_downloads_bloqueados_no_driver_do_dryrun():
    # Defesa estrutural: mesmo que algo dispare a emissao, nada cai em ~/Downloads
    # (a emissao real pega "o PDF mais novo" de la -> evitaria anexo trocado).
    drv = _FakeDriver(encontra=['campoCnpj'])
    drv.quit = lambda: None
    chamadas = []
    drv.execute_cdp_cmd = lambda cmd, args: chamadas.append((cmd, args))
    pilha, _mocks = _patch_driver(chrome=drv)
    with pilha:
        dr.executar_dry_run(_municipio())
    assert ('Page.setDownloadBehavior', {'behavior': 'deny'}) in chamadas


# ---- Rotas de diagnostico (COV-05 A2) ----

def _semear_municipio(app, nome='Vila Dryrun Teste'):
    """Cria um municipio de teste (nome proprio: o seed baseline das migrations
    ja traz os reais, e `nome` e UNIQUE)."""
    from app import db
    from app.models import Municipio
    with app.app_context():
        m = Municipio(nome=nome, url_certidao='https://portal.exemplo/cnd',
                      automacao_ativa=True, cnpj_field_id='campoCnpj', by='id')
        db.session.add(m)
        db.session.commit()
        return m.id


def test_rota_lista_municipios(app, client):
    _semear_municipio(app)
    resp = client.get('/diagnostico/municipios')
    assert resp.status_code == 200
    dados = resp.get_json()
    assert dados['status'] == 'ok'
    nosso = [m for m in dados['municipios'] if m['nome'] == 'Vila Dryrun Teste']
    assert len(nosso) == 1
    assert nosso[0]['automacao_ativa'] is True
    assert nosso[0]['url'] == 'https://portal.exemplo/cnd'


def test_rota_dryrun_devolve_relatorio(app, client):
    mid = _semear_municipio(app)
    from app.routes import dryrun_municipio as dr_rota
    esperado = {'municipio': 'Imbé', 'resultado': dr.QUEBRADO,
                'checagens': [], 'quebrados': ['cnpj: id=campoCnpj'], 'mensagem': 'sumiu'}
    with patch.object(dr_rota, 'executar_dry_run', return_value=esperado) as exe:
        resp = client.post(f'/diagnostico/municipios/dryrun/{mid}')
    assert resp.status_code == 200
    assert resp.get_json()['relatorio'] == esperado
    exe.assert_called_once()


def test_rota_dryrun_municipio_inexistente_404(app, client):
    resp = client.post('/diagnostico/municipios/dryrun/999999')
    assert resp.status_code == 404
    assert resp.get_json()['status'] == 'error'


def test_rota_dryrun_exige_admin(app, login_as):
    mid = _semear_municipio(app)
    resp = login_as('operador').post(f'/diagnostico/municipios/dryrun/{mid}')
    assert resp.status_code == 403   # AD-005: diagnostico e admin-only


# ---- Alerta + job diario (COV-05 A3) ----

def _mocks_alerta(pilha, smtp=True, destinatarios=('op@escritorio.com',)):
    from app.services import notificacoes as nt
    p = pilha.enter_context
    p(patch.object(nt, '_config', return_value=object()))
    p(patch.object(nt, '_destinatarios', return_value=list(destinatarios)))
    p(patch.object(nt.email_sender, 'smtp_configurado', return_value=smtp))
    return p(patch.object(nt, '_enviar_alerta', return_value=True))


def test_alerta_so_para_quebrados(app):
    # erro (infra) e parcial (captcha) NAO sao drift -> nao alertam.
    from contextlib import ExitStack

    from app.services import notificacoes as nt
    relatorios = [
        {'municipio': 'Imbé', 'resultado': dr.QUEBRADO,
         'quebrados': ['cnpj: id=campoCnpj'], 'mensagem': 'campo sumiu'},
        {'municipio': 'Gravataí', 'resultado': dr.ERRO, 'quebrados': [], 'mensagem': 'uc fora'},
        {'municipio': 'Osório', 'resultado': dr.PARCIAL, 'quebrados': [], 'mensagem': 'captcha'},
        {'municipio': 'Canoas', 'resultado': dr.OK, 'quebrados': [], 'mensagem': None},
    ]
    with ExitStack() as pilha:
        enviar = _mocks_alerta(pilha)
        enviados = nt.alertar_municipios_quebrados(app, relatorios)
    assert enviados == 1
    (_app, _dest, chave, tipo, assunto, corpo, _janela), _kw = enviar.call_args
    assert chave == 'municipio_quebrado:Imbé'   # anti-spam por municipio
    assert tipo == 'alerta_municipio'
    assert 'Imbé' in assunto
    assert 'cnpj: id=campoCnpj' in corpo


def test_alerta_um_por_municipio(app):
    # Dois quebrados -> duas chaves distintas (consertar um nao silencia o outro).
    from contextlib import ExitStack

    from app.services import notificacoes as nt
    relatorios = [
        {'municipio': 'Imbé', 'resultado': dr.QUEBRADO, 'quebrados': ['a'], 'mensagem': ''},
        {'municipio': 'Canoas', 'resultado': dr.QUEBRADO, 'quebrados': ['b'], 'mensagem': ''},
    ]
    with ExitStack() as pilha:
        enviar = _mocks_alerta(pilha)
        enviados = nt.alertar_municipios_quebrados(app, relatorios)
    assert enviados == 2
    chaves = {c.args[2] for c in enviar.call_args_list}
    assert chaves == {'municipio_quebrado:Imbé', 'municipio_quebrado:Canoas'}


def test_alerta_sem_smtp_nao_envia(app):
    from contextlib import ExitStack

    from app.services import notificacoes as nt
    with ExitStack() as pilha:
        enviar = _mocks_alerta(pilha, smtp=False)
        enviados = nt.alertar_municipios_quebrados(
            app, [{'municipio': 'Imbé', 'resultado': dr.QUEBRADO, 'quebrados': [], 'mensagem': ''}])
    assert enviados == 0
    enviar.assert_not_called()


def test_job_diario_so_verifica_ativos_e_alerta(app, ids):
    from app import db
    from app.models import Municipio
    from app.services import agendador
    with app.app_context():
        db.session.add(Municipio(nome='Vila Ativa Teste', url_certidao='https://a.exemplo',
                                 automacao_ativa=True))
        db.session.add(Municipio(nome='Vila Inativa Teste', url_certidao='https://b.exemplo',
                                 automacao_ativa=False))
        db.session.commit()

    relatorio = {'municipio': 'Vila Ativa Teste', 'resultado': dr.QUEBRADO,
                 'quebrados': ['x'], 'mensagem': 'y'}
    with patch.object(dr, 'executar_dry_run_varios',
                      return_value=([relatorio], {'total': 1, dr.QUEBRADO: 1})) as varios, \
            patch('app.services.notificacoes.alertar_municipios_quebrados',
                  return_value=1) as alerta:
        agendador.job_verificacao_municipios(app)

    (municipios,), _kw = varios.call_args
    nomes = [m.nome for m in municipios]
    assert 'Vila Ativa Teste' in nomes
    assert 'Vila Inativa Teste' not in nomes   # automacao_ativa=False fica de fora
    alerta.assert_called_once()
    assert alerta.call_args.args[1] == [relatorio]


def test_cap_timeout_preserva_valores_menores():
    assert dr._cap_timeout({'timeout': 3}, 8)['timeout'] == 3
    assert dr._cap_timeout({'timeout': 120}, 8)['timeout'] == 8
    assert dr._cap_timeout({'timeout': 'xx'}, 8)['timeout'] == 8
    original = {'timeout': 120}
    dr._cap_timeout(original, 8)
    assert original['timeout'] == 120  # nao muta o passo original


# === ultimo resultado em memoria (spec 09, RESOP-03.3) ====================

def test_executar_dry_run_publica_o_ultimo_resultado():
    dr.limpar_resultados()
    relatorio = {'municipio': 'Imbe', 'resultado': dr.OK, 'checagens': [],
                 'quebrados': [], 'mensagem': None}
    with patch.object(dr, '_executar_dry_run', return_value=relatorio):
        dr.executar_dry_run(SimpleNamespace(nome='Imbe', url_certidao='http://x'))

    ultimos = dr.ultimos_resultados()
    assert ultimos['Imbe']['resultado'] == dr.OK
    assert ultimos['Imbe']['medido_em']


def test_ultimo_resultado_e_sobrescrito_pela_verificacao_nova():
    dr.limpar_resultados()
    municipio = SimpleNamespace(nome='Imbe', url_certidao='http://x')
    for resultado in (dr.OK, dr.QUEBRADO):
        relatorio = {'municipio': 'Imbe', 'resultado': resultado, 'checagens': [],
                     'quebrados': [], 'mensagem': None}
        with patch.object(dr, '_executar_dry_run', return_value=relatorio):
            dr.executar_dry_run(municipio)

    assert dr.ultimos_resultados()['Imbe']['resultado'] == dr.QUEBRADO


def test_municipio_nunca_verificado_nao_aparece():
    dr.limpar_resultados()
    assert dr.ultimos_resultados() == {}


def test_varios_publica_cada_municipio():
    dr.limpar_resultados()
    municipios = [SimpleNamespace(nome='Imbe', url_certidao='http://x'),
                  SimpleNamespace(nome='Tramandai', url_certidao='http://y')]

    def _fake(municipio, timeout=20):
        return {'municipio': municipio.nome, 'resultado': dr.OK, 'checagens': [],
                'quebrados': [], 'mensagem': None}

    with patch.object(dr, '_executar_dry_run', side_effect=_fake):
        dr.executar_dry_run_varios(municipios)

    assert set(dr.ultimos_resultados()) == {'Imbe', 'Tramandai'}
