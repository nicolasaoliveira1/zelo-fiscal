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
from app.services.execution_logger import log_event

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


def verificar_municipio(municipio, driver, config=None, timeout=8):
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

    # 1) before_cnpj: executa de verdade, um passo por vez (reuso do engine).
    for idx, step in enumerate(config.get('before_cnpj') or [], start=1):
        etapa = f'before_cnpj[{idx}]'
        tipo = (step or {}).get('tipo')
        if tipo in _TIPOS_NAO_EXECUTAVEIS:
            _registrar(etapa, _descrever(step), PARCIAL,
                       f'passo "{tipo}" não é executado no dry-run')
            continue
        try:
            steps_engine.executar_municipio(
                driver, wait, [_cap_timeout(step, timeout)],
                CNPJ_TESTE, '', etapa_label='dryrun')
            _registrar(etapa, _descrever(step), OK)
        except Exception as exc:
            # Timeout aqui = seletor sumiu OU gate de captcha (portais IPM).
            relatorio['resultado'] = QUEBRADO
            _registrar(etapa, _descrever(step), QUEBRADO, type(exc).__name__)
            relatorio['mensagem'] = (
                f'Passo {etapa} ({_descrever(step)}) não resolveu — '
                'seletor mudou ou o portal exige captcha.')
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
