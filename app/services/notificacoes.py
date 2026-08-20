"""Orquestracao das notificacoes por e-mail (spec 03, AD-011).

Decide O QUE enviar (digest periodico de vencimentos; alertas de falha recorrente
e de saldo baixo) e aplica anti-spam DURAVEL via `NotificacaoLog` (chave + janela),
que sobrevive a restart. O transporte fica em `email_sender` (best-effort). Nada
aqui pode derrubar o job do agendador — falhas sao logadas, nao propagadas.

Este modulo NAO importa `agendador` (evita ciclo): os jobs e que chamam este.
"""
from datetime import date, datetime, timedelta

from app import captcha_solver, db
from app.models import Certidao, ConfiguracaoSistema, NotificacaoLog
from app.services import diagnostics, email_sender
from app.services.execution_logger import log_event
from app.services.snapshot_service import classificar_status_certidao

# Cadencia do digest -> intervalo minimo entre envios (dias).
_CADENCIA_DIAS = {'semanal': 7, 'diaria': 1}


# --- config / destinatarios ------------------------------------------------

def _config():
    return db.session.get(ConfiguracaoSistema, 1)


def _destinatarios(cfg):
    """Lista de e-mails de `notif_destinatarios` (separados por virgula/;/linha),
    aparados, sem duplicatas (ordem preservada) e contendo '@'."""
    if cfg is None or not cfg.notif_destinatarios:
        return []
    bruto = cfg.notif_destinatarios.replace(';', ',').replace('\n', ',')
    vistos = []
    for parte in bruto.split(','):
        email = parte.strip()
        if email and '@' in email and email not in vistos:
            vistos.append(email)
    return vistos


# --- anti-spam durável (NotificacaoLog) ------------------------------------

def _ultimo_envio(chave):
    """Datetime do ultimo envio registrado para a chave, ou None."""
    try:
        row = (NotificacaoLog.query
               .filter_by(chave=chave)
               .order_by(NotificacaoLog.enviada_em.desc())
               .first())
    except Exception:
        return None
    return row.enviada_em if row else None


def _deduplicado(chave, janela_horas):
    """True se ja houve envio da chave dentro da janela (nao reenviar)."""
    ultimo = _ultimo_envio(chave)
    if ultimo is None:
        return False
    return (datetime.now() - ultimo) < timedelta(hours=janela_horas)


def _registrar_envio(chave, tipo, detalhe=None):
    """Grava o envio no NotificacaoLog (best-effort; nunca propaga erro)."""
    try:
        db.session.add(NotificacaoLog(
            chave=chave, tipo=tipo,
            detalhe=(detalhe[:500] if detalhe else None)))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        log_event('notif_log_falhou', level='WARNING', chave=chave, error=str(exc))


# --- digest ----------------------------------------------------------------

def _contagem_carteira():
    """Conta a_vencer/vencidas/pendentes pela MESMA classificacao do painel
    (snapshot_service), para os numeros baterem com o que o operador ve."""
    hoje = date.today()
    contagem = {'a_vencer': 0, 'vencidas': 0, 'pendentes': 0}
    for certidao in Certidao.query.all():
        chave = classificar_status_certidao(certidao, hoje)
        if chave in contagem:
            contagem[chave] += 1
    return contagem


def montar_digest():
    """(assunto, corpo, resumo) do digest a partir da contagem da carteira."""
    resumo = _contagem_carteira()
    a_vencer, vencidas, pendentes = (
        resumo['a_vencer'], resumo['vencidas'], resumo['pendentes'])
    vazio = (a_vencer == 0 and vencidas == 0 and pendentes == 0)

    if vazio:
        assunto = '[Zelo] Digest — tudo em dia'
    else:
        assunto = (f'[Zelo] Digest — {a_vencer} a vencer, '
                   f'{vencidas} vencidas, {pendentes} pendentes')

    linhas = [
        f'Resumo da carteira de certidoes — {datetime.now():%d/%m/%Y %H:%M}',
        '',
        f'A vencer (na janela): {a_vencer}',
        f'Vencidas: {vencidas}',
        f'Pendentes: {pendentes}',
    ]
    if vazio:
        linhas += ['', 'Tudo em dia — nenhuma certidao a vencer, vencida ou pendente.']
    return assunto, '\n'.join(linhas), resumo


def _digest_devido(cfg):
    """True se ja passou o intervalo da cadencia desde o ultimo digest enviado."""
    ultimo = _ultimo_envio('digest')
    if ultimo is None:
        return True
    cadencia = (cfg.notif_cadencia if cfg else 'semanal') or 'semanal'
    dias = _CADENCIA_DIAS.get(cadencia, 7)
    return (date.today() - ultimo.date()).days >= dias


def enviar_digest_se_devido(app):
    """Envia o digest se a cadencia venceu. Retorna True se enviou.

    - Sem SMTP/destinatario: nao envia e loga aviso acionavel (AC P1.3).
    - 0/0/0: envia "tudo em dia", salvo NOTIF_DIGEST_ENVIAR_VAZIO=false (AC P1.5).
    - So registra no NotificacaoLog quando o envio de fato ocorre (retry no proximo
      tick se o SMTP estiver fora)."""
    cfg = _config()
    if not _digest_devido(cfg):
        return False

    destinatarios = _destinatarios(cfg)
    if not email_sender.smtp_configurado(app.config) or not destinatarios:
        log_event('notif_digest_sem_smtp', level='WARNING',
                  tem_smtp=email_sender.smtp_configurado(app.config),
                  destinatarios=len(destinatarios))
        return False

    assunto, corpo, resumo = montar_digest()
    vazio = not any(resumo.values())
    if vazio and not app.config.get('NOTIF_DIGEST_ENVIAR_VAZIO', True):
        log_event('notif_digest_omitido_vazio')
        return False

    enviado = email_sender.enviar(app.config, destinatarios, assunto, corpo)
    if enviado:
        _registrar_envio('digest', 'digest', detalhe=str(resumo))
    return enviado


# --- alertas (falha recorrente + saldo 2captcha) ---------------------------

def _enviar_alerta(app, destinatarios, chave, tipo, assunto, corpo, janela, detalhe):
    """Envia um alerta respeitando o anti-spam. Retorna True se enviou agora."""
    if _deduplicado(chave, janela):
        return False
    if email_sender.enviar(app.config, destinatarios, assunto, corpo):
        _registrar_envio(chave, tipo, detalhe)
        return True
    return False


def enviar_alertas(app):
    """Empurra alertas de falha recorrente (via diagnostics) e de saldo baixo do
    2captcha, com a janela anti-spam. Retorna quantos alertas enviou agora.

    - Falha recorrente: um alerta por (error_type, alvo) ativo; reenvio bloqueado
      dentro da janela (AC P2 falha).
    - Saldo: alerta so quando abaixo do limiar; saldo None (API fora) NAO gera
      falso-baixo; mantem tambem o WARNING no painel de diagnostico (spec 02)."""
    cfg = _config()
    destinatarios = _destinatarios(cfg)
    if not email_sender.smtp_configurado(app.config) or not destinatarios:
        log_event('notif_alertas_sem_smtp', level='WARNING',
                  tem_smtp=email_sender.smtp_configurado(app.config),
                  destinatarios=len(destinatarios))
        return 0

    janela = app.config.get('NOTIF_ALERTA_JANELA_HORAS', 24)
    enviados = 0

    for alerta in diagnostics.alertas_ativos():
        error_type = alerta.get('error_type')
        alvo = alerta.get('alvo')
        chave = f'falha:{error_type}:{alvo}'
        assunto = f'[Zelo] Alerta: falha recorrente {error_type} em {alvo}'
        corpo = '\n'.join([
            f'Falha recorrente detectada em {alvo}.',
            f'Tipo de erro: {error_type}',
            f'Ocorrencias: {alerta.get("ocorrencias")}',
            f'Hipotese: {alerta.get("hipotese")}',
        ])
        if _enviar_alerta(app, destinatarios, chave, 'alerta_falha', assunto,
                          corpo, janela, detalhe=str(alerta)):
            enviados += 1

    saldo = captcha_solver.consultar_saldo(app.config)
    minimo = app.config.get('CAPTCHA_2_SALDO_MINIMO', 0)
    if saldo is not None and saldo < minimo:
        # o aviso no painel de diagnostico e responsabilidade do agendador
        # (_avisar_saldo_baixo, spec 02); aqui so cuidamos do push por e-mail.
        assunto = '[Zelo] Alerta: saldo 2captcha baixo'
        corpo = '\n'.join([
            f'Saldo atual do 2captcha: {saldo:.2f} USD',
            f'Limiar minimo configurado: {minimo:.2f} USD',
            'Recarregue para nao interromper os lotes automatizados.',
        ])
        if _enviar_alerta(app, destinatarios, 'saldo_baixo', 'alerta_saldo',
                          assunto, corpo, janela, detalhe=f'saldo={saldo}'):
            enviados += 1

    return enviados


def alertar_empresas_baixadas(app, baixadas):
    """Alerta as empresas que o recheck viu passar de ATIVA para nao-ativa.

    Recebe so as TRANSICOES (`receita_service.rechecar_lote` ja filtra): empresa
    que ja estava baixada nao realerta todo dia, e ligar a feature nao dispara um
    alerta retroativo para a carteira inteira.

    Um alerta POR EMPRESA (chave anti-spam propria), como o de municipio: resolver
    uma nao pode silenciar as outras dentro da janela. Retorna quantos foram
    enviados agora."""
    cfg = _config()
    destinatarios = _destinatarios(cfg)
    if not email_sender.smtp_configurado(app.config) or not destinatarios:
        log_event('notif_baixadas_sem_smtp', level='WARNING',
                  destinatarios=len(destinatarios))
        return 0

    janela = app.config.get('NOTIF_ALERTA_JANELA_HORAS', 24)
    enviados = 0

    for empresa_id, nome, situacao in baixadas or []:
        situacao_txt = situacao or 'nao ativa'
        assunto = f'[Zelo] {nome} consta como {situacao_txt} na Receita'
        corpo = '\n'.join([
            f'A verificacao diaria detectou que "{nome}" deixou de constar como',
            f'ATIVA na Receita. Situacao atual: {situacao_txt}.',
            '',
            'A partir de agora ela fica FORA do lote automatico, para nao gastar',
            'captcha tentando emitir certidao de CNPJ morto. A emissao individual',
            'continua liberada, caso ainda seja necessaria (encerramento, baixa',
            'recente).',
            '',
            'Confira a situacao na tela da empresa e decida se ela sai da carteira.',
        ])
        if _enviar_alerta(app, destinatarios, f'empresa_baixada:{empresa_id}',
                          'alerta_empresa_baixada', assunto, corpo, janela,
                          detalhe=situacao_txt):
            enviados += 1

    return enviados


_ALERTA_CERTIFICADO_POR_CAUSA = {
    'vencido': {
        'chave': 'certificado_vencido:{empresa_id}',
        'assunto': '[Zelo] Alerta: certificado vencido de {empresa_nome}',
        'linhas': [
            'O certificado da empresa {empresa_nome} venceu em {data_vencimento}.',
            '',
            'A manifestacao dessa empresa esta parada ate renovar o certificado.',
            'Renove o certificado e atualize o inventario do cofre antes de retomar.',
        ],
    },
    'vencendo': {
        'chave': 'certificado_vencendo:{empresa_id}',
        'assunto': '[Zelo] Alerta: certificado vencendo de {empresa_nome}',
        'linhas': [
            'O certificado da empresa {empresa_nome} vence em {data_vencimento}.',
            'Faltam {dias_restantes} dia(s) para o vencimento.',
            '',
            'Providencie a renovacao para evitar a interrupcao da manifestacao.',
        ],
    },
}


def alertar_certificados_vencendo(app, itens):
    """Alerta certificados vencidos ou proximos de vencer, um por empresa/causa.

    Recebe a selecao ja pronta de ``manifestador_cofre.certificados_a_vencer``:
    consultar o banco e responsabilidade do chamador. A chave separa vencido de
    vencendo porque a transicao entre os estados pede novo alerta, mas mantem o
    anti-spam duravel para cada empresa dentro da mesma causa.
    """
    cfg = _config()
    destinatarios = _destinatarios(cfg)
    tem_smtp = email_sender.smtp_configurado(app.config)
    if not tem_smtp or not destinatarios:
        log_event('notif_certificados_sem_smtp', level='WARNING',
                  tem_smtp=tem_smtp, destinatarios=len(destinatarios))
        return 0

    janela = app.config.get('NOTIF_ALERTA_JANELA_HORAS', 24)
    enviados = 0
    for item in itens or []:
        modelo = _ALERTA_CERTIFICADO_POR_CAUSA.get(item.get('causa'))
        if modelo is None:
            continue

        not_after = item.get('not_after')
        if not_after is None:
            continue
        dados = {
            'empresa_id': item.get('empresa_id'),
            'empresa_nome': item.get('empresa_nome') or '?',
            'data_vencimento': not_after.strftime('%d/%m/%Y'),
            'dias_restantes': item.get('dias_restantes'),
        }
        corpo = '\n'.join(linha.format(**dados) for linha in modelo['linhas'])
        if _enviar_alerta(
                app, destinatarios, modelo['chave'].format(**dados),
                'alerta_certificado', modelo['assunto'].format(**dados), corpo,
                janela, detalhe=item['causa']):
            enviados += 1

    return enviados


# Causas de abertura do breaker que geram mensagens DIFERENTES. O breaker e o
# mesmo (parar de gastar em cima de falha repetida), mas o que o operador faz e
# outro: portal fora se resolve esperando; solver falhando se resolve na conta
# do 2captcha. Dizer "portal fora" quando o problema e o solver manda a pessoa
# depurar o site errado.
_ALERTA_POR_CAUSA = {
    'portal': {
        'chave': 'portal_fora:{alvo}',
        'tipo': 'alerta_portal',
        'assunto': '[Zelo] Alerta: portal {alvo} pausado (fora do ar)',
        'linhas': [
            'O sistema detectou falhas seguidas no portal {alvo} e pausou a emissao',
            'nele para nao gastar creditos de captcha contra um portal fora.',
        ],
    },
    'captcha': {
        'chave': 'solver_captcha:{alvo}',
        'tipo': 'alerta_solver',
        'assunto': '[Zelo] Alerta: captcha falhando em {alvo} (emissao pausada)',
        'linhas': [
            'O sistema detectou falhas seguidas de CAPTCHA em {alvo} e pausou a',
            'emissao para nao queimar mais chamadas pagas do solver.',
            '',
            'O portal pode estar no ar: o que falhou foi a resolucao do captcha.',
            'Confira saldo e chave do 2captcha antes de investigar o site.',
        ],
    },
}


def alertar_portal_fora(app, alvo, motivo=None, causa='portal'):
    """Alerta que o circuit breaker abriu para um alvo (spec 09, RESOP-02.7).

    `causa` escolhe a mensagem: 'portal' (o site nao respondeu) ou 'captcha' (o
    solver falhou; o portal pode estar de pe). Cada causa tem chave anti-spam
    propria — sao problemas diferentes, com acoes diferentes, e um nao pode
    silenciar o outro dentro da janela. Best-effort (AD-011): sem SMTP nao envia,
    nao levanta e o breaker abre do mesmo jeito. Retorna True se enviou agora."""
    cfg = _config()
    destinatarios = _destinatarios(cfg)
    if not email_sender.smtp_configurado(app.config) or not destinatarios:
        log_event('notif_portal_sem_smtp', level='WARNING', alvo=alvo,
                  destinatarios=len(destinatarios))
        return False

    modelo = _ALERTA_POR_CAUSA.get(causa) or _ALERTA_POR_CAUSA['portal']
    janela = app.config.get('NOTIF_ALERTA_JANELA_HORAS', 24)
    corpo = '\n'.join(
        [linha.format(alvo=alvo) for linha in modelo['linhas']]
        + [
            '',
            f'Ultimo erro: {motivo or "-"}',
            '',
            'Nada precisa ser religado: o bloqueio expira sozinho e o proximo ciclo',
            'tenta de novo. Se o problema seguir, o alerta se repete na proxima janela.',
        ])
    return _enviar_alerta(app, destinatarios, modelo['chave'].format(alvo=alvo),
                          modelo['tipo'], modelo['assunto'].format(alvo=alvo),
                          corpo, janela, detalhe=motivo)


def alertar_municipios_quebrados(app, relatorios):
    """Alerta os municipios cujo dry-run acusou seletor quebrado (COV-05 A3).

    Um alerta POR MUNICIPIO (chave anti-spam propria), para que consertar um nao
    silencie os demais dentro da janela. So `quebrado` alerta: `erro` e infra
    (driver/perfil ocupado) e `parcial` e captcha — nenhum dos dois e drift.
    Retorna quantos alertas foram enviados agora."""
    from app.services.dryrun_municipio import QUEBRADO, falhou_ao_abrir

    cfg = _config()
    destinatarios = _destinatarios(cfg)
    if not email_sender.smtp_configurado(app.config) or not destinatarios:
        log_event('notif_municipios_sem_smtp', level='WARNING',
                  destinatarios=len(destinatarios))
        return 0

    janela = app.config.get('NOTIF_ALERTA_JANELA_HORAS', 24)
    enviados = 0

    for relatorio in relatorios or []:
        if (relatorio or {}).get('resultado') != QUEBRADO:
            continue
        nome = relatorio.get('municipio') or '?'
        quebrados = relatorio.get('quebrados') or []
        detalhe = '; '.join(quebrados) or 'seletor nao identificado'
        # Mesma chave anti-spam nos dois textos (um alerta por municipio por
        # janela), mas o conselho muda com a causa: "portal nao abriu" se
        # resolve conferindo endereco/ar do site, "seletor mudou" se resolve no
        # config_automacao. Mandar revisar seletor de um portal inalcançavel faz
        # o operador depurar o lugar errado (mesma razao do alerta_solver).
        if falhou_ao_abrir(relatorio):
            assunto = f'[Zelo] Alerta: portal do municipio {nome} nao respondeu'
            fecho = [
                'O portal nao chegou a abrir: o endereco pode ter mudado ou o site',
                'esta fora do ar. Confira a URL do municipio (tabela municipio,',
                'coluna url_certidao) abrindo-a no navegador e rode a verificacao',
                'novamente. Enquanto isso a emissao deste municipio nao funciona.',
            ]
        else:
            assunto = f'[Zelo] Alerta: automacao do municipio {nome} pode ter quebrado'
            fecho = [
                'Provavel mudanca de layout do portal. Revise os seletores do municipio',
                '(tabela municipio / config_automacao) e rode a verificacao novamente.',
            ]
        corpo = '\n'.join([
            f'A verificacao diaria (sem emitir) falhou em {nome}.',
            f'Passos que nao resolveram: {detalhe}',
            f'Detalhe: {relatorio.get("mensagem") or "-"}',
            '',
        ] + fecho)
        if _enviar_alerta(app, destinatarios, f'municipio_quebrado:{nome}',
                          'alerta_municipio', assunto, corpo, janela, detalhe=detalhe):
            enviados += 1

    return enviados
