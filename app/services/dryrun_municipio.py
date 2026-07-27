"""Dry-run de municipio: valida seletores SEM emitir (COV-05 fatia A).

Portais mudam com o tempo e quebram seletores; hoje isso so aparece quando uma
emissao falha em producao. Este modulo navega o fluxo de um municipio ate a
fronteira da emissao e reporta qual passo/seletor deixou de resolver.

Fronteira de seguranca (por que e seguro):
- `before_cnpj` sao passos de navegacao/selecao, executados ANTES de qualquer
  emissao -> executados de verdade (fidelidade real), um a um, reusando
  `steps.executar_municipio` (sem duplicar o engine) para granularidade por passo.
- o campo de CNPJ e apenas localizado/preenchido (inofensivo);
- `after_cnpj` contem o clique que GERA o PDF -> apenas verificamos se o
  localizador resolve (`find_elements`), nunca clicamos. Os passos seguintes
  dependem desse clique e sao reportados como nao verificados (honestidade em vez
  de cobertura falsa).

Nao gasta captcha: `wait_for` de portais com captcha (ex.: IPM) expira no teto do
dry-run e vira `parcial` — sinal de "nao deu para verificar", nao de quebra.
"""
import copy

from selenium.webdriver.support.ui import WebDriverWait

from app.automation import steps as steps_engine
from app.automation.driver import (
    UcIndisponivelError,
    _criar_driver_chrome,
    _criar_driver_uc,
    _municipal_profile_acquire,
    _municipal_profile_release,
)
from app.automation.emissao import _carregar_config_municipio, _normalizar_cnpj
from app.automation.sites import is_ipm_atende
from app.services.execution_logger import log_event
from app.utils import normalizar_cidade

# Vocabulario de resultado (usado pela rota, pelo job e pelo alerta).
OK = 'ok'                  # tudo que da para verificar resolveu
QUEBRADO = 'quebrado'      # um seletor verificavel nao resolve -> drift real
PARCIAL = 'parcial'        # interrompido por captcha/gate; sem garantia total
PULADO = 'pulado'          # automacao_ativa=false (ex.: Sao Paulo)
ERRO = 'erro'              # falha de infra (driver/rede), nao do municipio

# CNPJ valido e publico (Banco do Brasil) para portais que validam digito.
CNPJ_TESTE = '00000000000191'

# Passos com efeito destrutivo/condicional que o dry-run nunca executa.
_TIPOS_NAO_EXECUTAVEIS = {'click_if_text_or_close'}

# Passos que disparam acao no portal (podem concluir a emissao).
_TIPOS_ACIONAM = {'click', 'click_js'}


def _passo_emite(step, indice, total, tem_after_cnpj, skip_cnpj_fill):
    """True se o passo pode CONCLUIR a emissao — nunca executado no dry-run.

    A fronteira nao pode ser posicional: nas configs reais (Gravatai/Osorio/Novo
    Hamburgo com `confirmar`, Ponta Pora/Xangri-La com `Imprimir`) o passo que
    emite e o ULTIMO de `before_cnpj`, porque `skip_cnpj_fill` move o fluxo todo
    para la e nao existe `after_cnpj`. Regra:
      - `emite: true|false` explicito no passo (anotacao vence sempre);
      - `skip_cnpj_fill` falso => NENHUM passo de `before_cnpj` pode emitir: o CNPJ
        ainda nao foi preenchido nessa fase, entao nao ha o que emitir (evita
        marcar navegacao inofensiva, como o radio de Porto Alegre, de "emite");
      - conservador: ultimo passo de `before_cnpj`, do tipo que aciona, quando NAO
        ha `after_cnpj` — nessa forma a acao terminal do fluxo e a propria emissao.
    Conservador erra para o lado seguro (marca `parcial`, nunca emite)."""
    anotacao = (step or {}).get('emite')
    if anotacao is not None:
        return bool(anotacao)
    if not skip_cnpj_fill:
        return False
    return (not tem_after_cnpj
            and indice == total
            and (step or {}).get('tipo') in _TIPOS_ACIONAM)


def _cap_timeout(step, teto):
    """Copia o passo com o timeout limitado ao teto do dry-run (portais com
    captcha usam timeout de 120s esperando o operador; aqui nao ha operador)."""
    novo = copy.deepcopy(step or {})
    try:
        if float(novo.get('timeout', 0)) > teto:
            novo['timeout'] = teto
    except (TypeError, ValueError):
        novo['timeout'] = teto
    return novo


def _descrever(step):
    by = (step or {}).get('by') or '?'
    locator = (step or {}).get('locator') or '?'
    return f"{by}={locator}"


def _localiza(driver, by_nome, locator):
    """True se o localizador resolve na pagina atual. Nao age sobre o elemento."""
    by = steps_engine.BY_MAP.get(by_nome)
    if not by or not locator:
        return False
    try:
        return bool(driver.find_elements(by, locator))
    except Exception:
        return False


def cnpj_para_teste(municipio):
    """CNPJ de uma empresa REAL da cidade do municipio, com fallback no publico.

    Portais que consultam cadastro (ex.: Xangri-La/Ponta Pora listam imoveis do
    contribuinte) devolvem lista vazia para um CNPJ nao inscrito, e a falha do
    passo seguinte pareceria drift. Usar um contribuinte de verdade torna o
    dry-run fiel ao que a emissao faz. Best-effort: nunca levanta."""
    try:
        from app.models import Empresa
        alvo = normalizar_cidade(getattr(municipio, 'nome', '') or '')
        for empresa in Empresa.query.all():
            if normalizar_cidade(empresa.cidade or '') == alvo and (empresa.cnpj or '').strip():
                return empresa.cnpj
    except Exception as exc:
        log_event('dryrun_cnpj_empresa_falhou', level='WARNING', error=str(exc))
    return CNPJ_TESTE


def verificar_municipio(municipio, driver, config=None, timeout=8, cnpj=None):
    """Roda o dry-run de um municipio e devolve um relatorio estruturado.

    `config` e o `config_automacao` ja desserializado (dict) ou None. Nunca
    levanta: falhas viram `resultado='erro'`. Retorna:
    {'municipio', 'resultado', 'checagens': [{'etapa','alvo','status','detalhe'}],
     'quebrados': [str], 'mensagem': str|None}
    """
    nome = getattr(municipio, 'nome', None) or '?'
    relatorio = {'municipio': nome, 'resultado': OK, 'checagens': [],
                 'quebrados': [], 'mensagem': None}

    def _registrar(etapa, alvo, status, detalhe=None):
        relatorio['checagens'].append(
            {'etapa': etapa, 'alvo': alvo, 'status': status, 'detalhe': detalhe})
        if status == QUEBRADO:
            relatorio['quebrados'].append(f'{etapa}: {alvo}')

    if not getattr(municipio, 'automacao_ativa', True):
        relatorio['resultado'] = PULADO
        relatorio['mensagem'] = 'Automação inativa para este município.'
        return relatorio

    url = getattr(municipio, 'url_certidao', None)
    if not url:
        relatorio['resultado'] = QUEBRADO
        relatorio['mensagem'] = 'Município sem URL de certidão cadastrada.'
        _registrar('url', '(vazia)', QUEBRADO, 'url_certidao ausente')
        return relatorio

    config = config or {}
    try:
        driver.get(url)
        _registrar('url', url, OK)
    except Exception as exc:
        relatorio['resultado'] = ERRO
        relatorio['mensagem'] = f'Não foi possível abrir o portal: {exc}'
        _registrar('url', url, ERRO, str(exc))
        return relatorio

    wait = WebDriverWait(driver, timeout)

    # 1) before_cnpj: executa de verdade, um passo por vez (reuso do engine),
    #    exceto os passos que podem concluir a emissao (esses so sao verificados).
    antes = list(config.get('before_cnpj') or [])
    tem_after = bool(config.get('after_cnpj'))
    pula_cnpj = bool(config.get('skip_cnpj_fill'))
    for idx, step in enumerate(antes, start=1):
        etapa = f'before_cnpj[{idx}]'
        tipo = (step or {}).get('tipo')
        if tipo in _TIPOS_NAO_EXECUTAVEIS:
            _registrar(etapa, _descrever(step), PARCIAL,
                       f'passo "{tipo}" não é executado no dry-run')
            continue
        if _passo_emite(step, idx, len(antes), tem_after, pula_cnpj):
            # Nao clicar: este passo emitiria a certidao. Só confere se o
            # localizador ainda resolve — e o que interessa para detectar drift.
            alvo = _descrever(step)
            if _localiza(driver, step.get('by'), step.get('locator')):
                _registrar(etapa, alvo, PARCIAL, 'passo que emite: verificado sem clicar')
                continue
            relatorio['resultado'] = QUEBRADO
            _registrar(etapa, alvo, QUEBRADO, 'elemento não encontrado')
            relatorio['mensagem'] = f'Passo {etapa} ({alvo}) não existe mais no portal.'
            return relatorio
        try:
            steps_engine.executar_municipio(
                driver, wait, [_cap_timeout(step, timeout)],
                _normalizar_cnpj(cnpj or CNPJ_TESTE), '', etapa_label='dryrun')
            _registrar(etapa, _descrever(step), OK)
        except Exception as exc:
            # `wait_for` que expira e o idioma de "esperar o operador/captcha"
            # (ex.: IPM espera opcaoEmissao por 120s) -> parcial, nao drift.
            # Falha de click/fill/select = o elemento nao esta la -> drift real.
            if tipo == 'wait_for':
                _registrar(etapa, _descrever(step), PARCIAL,
                           'aguardando condição que depende de captcha/operador')
                relatorio['resultado'] = PARCIAL
                relatorio['mensagem'] = (
                    f'Verificação parou em {etapa}: o portal exige captcha/ação manual.')
                return relatorio
            relatorio['resultado'] = QUEBRADO
            _registrar(etapa, _descrever(step), QUEBRADO, type(exc).__name__)
            relatorio['mensagem'] = (
                f'Passo {etapa} ({_descrever(step)}) não resolveu — o seletor mudou.')
            return relatorio

    # 2) campo de CNPJ: so localiza (nao submete nada).
    if not config.get('skip_cnpj_fill'):
        campo = getattr(municipio, 'cnpj_field_id', None)
        by_nome = getattr(municipio, 'by', None)
        if campo:
            alvo = f'{by_nome}={campo}'
            if _localiza(driver, by_nome, campo):
                _registrar('cnpj', alvo, OK)
            else:
                relatorio['resultado'] = QUEBRADO
                _registrar('cnpj', alvo, QUEBRADO, 'campo não encontrado')
                relatorio['mensagem'] = f'Campo de CNPJ ({alvo}) não existe mais no portal.'
                return relatorio

    # 3) after_cnpj: NAO executa (e o clique que emite). Verifica o primeiro
    #    localizador; os seguintes dependem dele e ficam sem verificacao.
    depois = [s for s in (config.get('after_cnpj') or []) if (s or {}).get('locator')]
    for idx, step in enumerate(depois, start=1):
        etapa = f'after_cnpj[{idx}]'
        alvo = _descrever(step)
        if idx > 1:
            _registrar(etapa, alvo, PARCIAL, 'depende do passo anterior (não emitido)')
            continue
        if _localiza(driver, step.get('by'), step.get('locator')):
            _registrar(etapa, alvo, OK)
        else:
            relatorio['resultado'] = QUEBRADO
            _registrar(etapa, alvo, QUEBRADO, 'elemento não encontrado')
            relatorio['mensagem'] = f'Passo {etapa} ({alvo}) não existe mais no portal.'
            return relatorio

    if relatorio['resultado'] == OK and any(
            c['status'] == PARCIAL for c in relatorio['checagens']):
        relatorio['resultado'] = PARCIAL
        relatorio['mensagem'] = 'Verificação parcial: alguns passos dependem da emissão/captcha.'

    log_event('municipio_dryrun', municipio=nome, resultado=relatorio['resultado'],
              quebrados=len(relatorio['quebrados']))
    return relatorio


def _erro(nome, mensagem):
    return {'municipio': nome, 'resultado': ERRO, 'checagens': [],
            'quebrados': [], 'mensagem': mensagem}


def _bloquear_downloads(driver):
    """Nega downloads no driver do dry-run (defesa em profundidade).

    As fabricas de driver habilitam download automatico; aqui invertemos por CDP
    para que, mesmo se um clique inesperado disparar a emissao, nenhum PDF caia em
    ~/Downloads — de onde a emissao real pega "o PDF mais novo" (evita anexar um
    arquivo a certidao errada). Best-effort: nunca impede o dry-run de rodar."""
    try:
        driver.execute_cdp_cmd('Page.setDownloadBehavior', {'behavior': 'deny'})
    except Exception as exc:
        log_event('dryrun_bloqueio_download_falhou', level='WARNING', error=str(exc))


def executar_dry_run(municipio, timeout=8):
    """Abre o driver adequado, roda o dry-run e fecha o driver. Nunca levanta.

    A escolha do driver segue o mesmo predicado por URL da emissao (AD-001/AD-002):
    portal IPM Atende.Net -> `_criar_driver_uc` com o perfil dedicado (serializado
    pelo lock); demais -> `_criar_driver_chrome`. Sem fallback silencioso: perfil
    ocupado ou uc indisponivel viram `erro` acionavel (nao 'quebrado', que
    significaria drift do portal)."""
    nome = getattr(municipio, 'nome', None) or '?'
    ipm = is_ipm_atende(getattr(municipio, 'url_certidao', None))
    driver = None
    lock_ativo = False

    try:
        if ipm:
            if not _municipal_profile_acquire(blocking=False):
                return _erro(nome, 'Perfil municipal em uso: aguarde a emissão atual terminar.')
            lock_ativo = True
            try:
                driver = _criar_driver_uc()
            except UcIndisponivelError as exc:
                return _erro(nome, exc.message)
        else:
            driver = _criar_driver_chrome()

        _bloquear_downloads(driver)
        config = _carregar_config_municipio(municipio)
        return verificar_municipio(municipio, driver, config=config, timeout=timeout,
                                   cnpj=cnpj_para_teste(municipio))
    except Exception as exc:
        log_event('municipio_dryrun_falha', level='ERROR', municipio=nome, error=str(exc))
        return _erro(nome, f'Falha ao executar a verificação: {exc}')
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
        if lock_ativo:
            _municipal_profile_release()


def executar_dry_run_varios(municipios, timeout=8):
    """Roda o dry-run de varios municipios (um driver por vez, isolado) e devolve
    (relatorios, resumo). Usado pela rota sob demanda e pelo job agendado."""
    relatorios = [executar_dry_run(m, timeout=timeout) for m in municipios]
    resumo = {'total': len(relatorios)}
    for chave in (OK, QUEBRADO, PARCIAL, PULADO, ERRO):
        resumo[chave] = sum(1 for r in relatorios if r['resultado'] == chave)
    return relatorios, resumo
