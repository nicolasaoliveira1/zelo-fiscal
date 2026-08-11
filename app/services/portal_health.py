"""Semaforo de saude dos portais (spec 09, RESOP-03).

Responde "vale disparar um lote agora?" antes de o operador descobrir isso
depois de 20 falhas. Duas fontes, de proposito:

- **Portais de tipo unico** (Federal, FGTS, Estadual RS, Trabalhista): ping HTTP
  leve, no molde de `health._timed_check` — sem Selenium, sem captcha.
- **Municipios**: o resultado do **dry-run diario que ja existe**. Um 200 no
  portal do municipio nao diz nada sobre o fluxo; o dry-run diz, e sai de graca.

Verde significa "o portal responde", NAO "a emissao funciona" — o rotulo na tela
precisa dizer isso, senao vira falsa confianca.

O resultado e cacheado por uma janela curta: recarregar o painel nao pode virar
uma rajada de requisicoes contra o portal.
"""
import threading
import time
from datetime import datetime, timedelta

import requests

from app.automation import SITES_CERTIDOES
from app.services import circuit_breaker, dryrun_municipio
from app.utils import normalizar_cidade

TTL_MINUTOS_PADRAO = 5
TIMEOUT_S_PADRAO = 6
# Portais gov. costumam recusar cliente anonimo (a BrasilAPI devolve 403 sem
# User-Agent — spec 08); sem isto o semaforo ficaria vermelho por engano.
USER_AGENT = 'Zelo-Certidoes/1.0 (monitor de disponibilidade)'

_lock = threading.Lock()
# Lock separado, so para a medicao: segurar `_lock` durante os pings (ate
# 4 x timeout) travaria ate a leitura do cache.
_medindo = threading.Lock()
_cache = {}  # chave -> item medido
_medido_em = None


def _agora():
    """Isolado para os testes controlarem o TTL sem dormir."""
    return datetime.now()


def _config_int(config, nome, padrao):
    try:
        valor = int((config or {}).get(nome, padrao))
    except (TypeError, ValueError):
        return padrao
    return valor if valor > 0 else padrao


def _portais_fixos():
    """(chave, nome, url) dos portais de tipo unico, lidos do mapa da automacao
    — nenhuma URL hardcodada aqui."""
    estadual_rs = (SITES_CERTIDOES.get('ESTADUAL') or {}).get('RS') or {}
    # A chave e o ALVO do breaker (nao um rotulo proprio): e assim que o painel
    # consegue dizer "este portal esta pausado".
    return [
        (circuit_breaker.ALVO_FEDERAL, 'Federal (Receita)',
         (SITES_CERTIDOES.get('FEDERAL') or {}).get('url')),
        (circuit_breaker.ALVO_FGTS, 'FGTS (Caixa)',
         (SITES_CERTIDOES.get('FGTS') or {}).get('url')),
        (circuit_breaker.ALVO_ESTADUAL_RS, 'Estadual RS (Sefaz)',
         estadual_rs.get('url')),
        (circuit_breaker.ALVO_TRABALHISTA, 'Trabalhista (CNDT/TST)',
         (SITES_CERTIDOES.get('TRABALHISTA') or {}).get('url')),
    ]


def _pingar(url, timeout):
    """(estado, latencia_ms, mensagem). Nunca levanta: portal fora e resposta,
    nao excecao."""
    inicio = time.time()
    try:
        resposta = requests.get(url, timeout=timeout,
                                headers={'User-Agent': USER_AGENT},
                                allow_redirects=True)
    except Exception as exc:
        return 'fora', int((time.time() - inicio) * 1000), str(exc)
    latencia = int((time.time() - inicio) * 1000)
    if resposta.status_code >= 500:
        return 'fora', latencia, f'HTTP {resposta.status_code}'
    # 4xx = o portal respondeu e recusou (bloqueio de robo, pagina movida);
    # isso e "esta no ar", diferente de nao responder.
    return 'ok', latencia, f'HTTP {resposta.status_code}'


# dry-run -> estado do semaforo. `parcial` (captcha barrou a verificacao) nao e
# quebra: o portal respondeu ate onde deu para ir.
_ESTADO_POR_DRYRUN = {
    dryrun_municipio.OK: 'ok',
    dryrun_municipio.PARCIAL: 'ok',
    dryrun_municipio.QUEBRADO: 'fora',
    dryrun_municipio.ERRO: 'desconhecido',
    dryrun_municipio.PULADO: 'desconhecido',
}


def _municipios():
    """Estado dos portais municipais a partir do ultimo dry-run (sem rede)."""
    from app.models import Municipio

    try:
        municipios = Municipio.query.order_by(Municipio.nome).all()
    except Exception:
        return []

    ultimos = dryrun_municipio.ultimos_resultados()
    itens = []
    for municipio in municipios:
        relatorio = ultimos.get(municipio.nome) or {}
        resultado = relatorio.get('resultado')
        itens.append({
            # chave = alvo do breaker municipal (cidade canonica), para 'Imbé' e
            # 'IMBE' nao virarem dois portais diferentes no painel
            'chave': normalizar_cidade(municipio.nome) or municipio.nome,
            'municipio': municipio.nome,
            'nome': f'Municipal — {municipio.nome}',
            'url': municipio.url_certidao,
            'fonte': 'dry-run',
            'estado': _ESTADO_POR_DRYRUN.get(resultado, 'desconhecido'),
            'latencia_ms': None,
            'mensagem': relatorio.get('mensagem') or (
                None if resultado else 'sem verificação recente'),
            'medido_em': relatorio.get('medido_em'),
        })
    return itens


def _medir(config):
    timeout = _config_int(config, 'PORTAL_PING_TIMEOUT_S', TIMEOUT_S_PADRAO)
    agora = _agora().isoformat()
    itens = []
    for chave, nome, url in _portais_fixos():
        if not url:
            continue
        estado, latencia, mensagem = _pingar(url, timeout)
        itens.append({
            'chave': chave, 'nome': nome, 'url': url, 'fonte': 'ping',
            'municipio': None,
            'estado': estado, 'latencia_ms': latencia, 'mensagem': mensagem,
            'medido_em': agora,
        })
    return itens


def snapshot(config, forcar=False):
    """Estado de cada portal + os breakers abertos.

    O ping so e refeito quando o cache expira (ou `forcar=True`); os municipios
    saem do dry-run, que nao tem custo de rede."""
    global _medido_em
    ttl = timedelta(minutes=_config_int(config, 'PORTAL_PING_TTL_MINUTOS',
                                        TTL_MINUTOS_PADRAO))

    def _do_cache():
        with _lock:
            valido = (_medido_em is not None and not forcar
                      and _agora() - _medido_em < ttl)
            return list(_cache.get('fixos') or []) if valido else None

    fixos = _do_cache()
    if fixos is None:
        # Serializa a MEDICAO (nao a leitura): dois carregamentos simultaneos do
        # painel com o cache vencido fariam, cada um, a rodada inteira de pings
        # — exatamente o "martelar o portal" que o cache existe para evitar. O
        # segundo espera o primeiro e reaproveita o resultado (dupla checagem).
        with _medindo:
            fixos = _do_cache()
            if fixos is None:
                fixos = _medir(config)
                with _lock:
                    _cache['fixos'] = fixos
                    _medido_em = _agora()

    abertos = circuit_breaker.abertos()
    alvos_abertos = {b['alvo'] for b in abertos}
    portais = fixos + _municipios()
    for item in portais:
        item['breaker_aberto'] = item['chave'] in alvos_abertos

    return {'portais': portais, 'breakers': abertos}


def limpar_cache():
    global _medido_em
    with _lock:
        _cache.clear()
        _medido_em = None
