"""Orquestracao das notificacoes por e-mail (spec 03, AD-011, AD-029).

**Um e-mail por dia, nao um por achado.** Cada `alertar_*` daqui NAO envia nada:
anota o achado na `PautaNotificacao` e volta. Um unico job diario
(`enviar_resumo_diario`) junta a pauta pendente com a contagem da carteira e
manda **um** e-mail com tudo. Antes cada certificado vencido virava um e-mail
proprio, e a caixa do operador recebia dezenas por dia — o volume fazia o aviso
ser ignorado, que e o oposto do que o alerta existe para fazer.

O `NotificacaoLog` continua sendo o historico duravel (sobrevive a
restart) e agora ganha um segundo guarda: enquanto um achado espera na pauta, a
mesma chave nao e anotada de novo. O transporte fica em `email_sender`
(best-effort). Nada aqui pode derrubar o job do agendador — falhas sao logadas,
nao propagadas.

Anotar na pauta NAO depende de SMTP configurado, de proposito: o achado e real
mesmo sem transporte, e guardado ele sai no primeiro resumo depois que o SMTP
voltar. Recusar a anotacao perderia em silencio exatamente o que o operador
precisava saber.

Este modulo NAO importa `agendador` (evita ciclo): os jobs e que chamam este.
"""
from datetime import date, datetime

from app import captcha_solver, db
from app.models import ConfiguracaoSistema, NotificacaoLog, PautaNotificacao
from app.services import diagnostics, email_sender, snapshot_service
from app.services.execution_logger import log_event

# Cadencia do resumo -> intervalo minimo entre envios (dias). Vale so para o
# resumo DE ROTINA (nada pendente na pauta): com aviso anotado o resumo sai no
# mesmo dia, independente da cadencia — segurar um alerta por uma semana seria
# trocar spam por silencio.
_CADENCIA_DIAS = {'semanal': 7, 'diaria': 1}

# Secoes do resumo, na ordem em que aparecem no corpo. A chave e o `tipo` gravado
# na pauta (mesmo vocabulario do NotificacaoLog); tipo desconhecido cai em Outros.
_SECOES = (
    ('alerta_certificado', 'Certificados digitais'),
    ('alerta_empresa_baixada', 'Situacao na Receita'),
    ('alerta_municipio', 'Municipios'),
    ('alerta_portal', 'Portais pausados'),
    ('alerta_solver', 'Captcha / solver'),
    ('alerta_falha', 'Falhas recorrentes'),
    ('alerta_saldo', 'Saldo do 2captcha'),
)
_SECAO_OUTROS = 'Outros avisos'

# Conselho que vale para a SECAO inteira, impresso uma vez no fim dela. Repetir
# "providencie a renovacao" embaixo de cada certificado transformava um dia com
# dez vencimentos em quarenta linhas de texto identico — trocaria o spam de
# e-mails por spam dentro do e-mail, sem resolver nada.
_RODAPE_SECAO = {
    'alerta_certificado': ('Renove o certificado e rode o inventario do cofre '
                           'para o sistema reconhecer o arquivo novo.'),
}


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


def chaves_ja_avisadas(chaves):
    """Das chaves dadas, quais JA sairam em algum resumo anterior.

    Substitui o antigo `_deduplicado`, e a troca e de proposito: o
    `NotificacaoLog` deixou de decidir QUEM aparece no resumo (agora aparecem
    todos, todo dia, a pedido do usuario) e passou a decidir o que e NOVO.

    Uma consulta so para o lote inteiro — uma por item seria N round-trips no
    caminho do job diario.
    """
    chaves = list(chaves)
    if not chaves:
        return set()
    try:
        linhas = (db.session.query(NotificacaoLog.chave)
                  .filter(NotificacaoLog.chave.in_(chaves))
                  .distinct().all())
    except Exception as exc:
        # Sem saber o historico, o seguro e nao marcar NADA como novo: um
        # "[NOVO]" errado ensina o leitor a desconfiar do marcador, e ai ele
        # deixa de servir para o que existe.
        log_event('notif_historico_falhou', level='WARNING', error=str(exc))
        return set(chaves)
    return {linha[0] for linha in linhas}


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


# --- pauta do dia (o que ainda nao foi contado) ----------------------------

def _pauta_pendente_chave(chave):
    """True se essa chave ja esta anotada e esperando o proximo resumo."""
    try:
        return db.session.query(
            PautaNotificacao.query
            .filter(PautaNotificacao.chave == chave,
                    PautaNotificacao.enviada_em.is_(None))
            .exists()).scalar()
    except Exception:
        return False


def registrar_pauta(chave, tipo, titulo, corpo=None):
    """Anota um achado para o proximo resumo diario. True se anotou agora.

    UM guarda, e nao dois: o `_pauta_pendente_chave` evita anotar duas vezes um
    achado que ainda NAO saiu (o produtor pode rodar varias vezes entre dois
    resumos). O segundo guarda — a janela do `NotificacaoLog`, que suprimia o que
    ja tinha saido num resumo recente — foi REMOVIDO por pedido do usuario: um
    achado que persiste continua aparecendo todo dia, para reforcar.

    O custo dessa escolha e real e esta pago noutro lugar: uma secao que repete a
    mesma lista todo dia deixa de ser lida, e o item que entrou hoje some no meio
    dos de ontem. Por isso o resumo marca [NOVO] e ordena os novos primeiro.

    Best-effort: falha de gravacao loga e retorna False, nunca propaga (AD-011).
    """
    if _pauta_pendente_chave(chave):
        return False
    try:
        db.session.add(PautaNotificacao(
            chave=chave[:120], tipo=tipo[:40],
            titulo=(titulo or '')[:200], corpo=corpo))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        log_event('notif_pauta_falhou', level='WARNING', chave=chave, error=str(exc))
        return False
    return True


def pauta_pendente():
    """Achados anotados e ainda nao enviados, mais antigo primeiro."""
    try:
        return (PautaNotificacao.query
                .filter(PautaNotificacao.enviada_em.is_(None))
                .order_by(PautaNotificacao.criada_em.asc(),
                          PautaNotificacao.id.asc())
                .all())
    except Exception as exc:
        log_event('notif_pauta_leitura_falhou', level='WARNING', error=str(exc))
        return []


def _fechar_pauta(itens):
    """Carimba os itens como enviados e alimenta o anti-spam, num commit so.

    Um commit por item custaria N round-trips e deixaria a pauta meio fechada se
    algum falhasse no meio — como o e-mail JA SAIU, meio fechado significa
    repetir os itens restantes no resumo seguinte."""
    if not itens:
        return
    agora = datetime.now()
    try:
        for item in itens:
            item.enviada_em = agora
            db.session.add(NotificacaoLog(
                chave=item.chave, tipo=item.tipo,
                detalhe=(item.titulo or '')[:500], enviada_em=agora))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        log_event('notif_pauta_fechar_falhou', level='WARNING',
                  itens=len(itens), error=str(exc))


# --- resumo diario ---------------------------------------------------------

def _agrupar_por_secao(itens):
    """[(rotulo, [item, ...]), ...] na ordem de `_SECOES`, sem secao vazia."""
    por_tipo = {}
    for item in itens:
        por_tipo.setdefault(item.tipo, []).append(item)

    secoes = []
    for tipo, rotulo in _SECOES:
        do_tipo = por_tipo.pop(tipo, None)
        if do_tipo:
            secoes.append((rotulo, do_tipo))
    restantes = [i for grupo in por_tipo.values() for i in grupo]
    if restantes:
        secoes.append((_SECAO_OUTROS, restantes))
    return secoes


# O resumo por e-mail pergunta "o que pede atencao?" sobre TRES baldes — a
# contagem da carteira devolve cinco (a tela precisa do denominador e trata
# `sem_data` como trabalho). A regra do e-mail fica escrita UMA vez, aqui, e nao
# em cada consumidor: `not any(resumo.values())` parecia dizer isso e passou a
# mentir no dia em que `validas` entrou no dict — silenciosamente, porque
# nenhuma carteira de teste tinha certidao valida.
_BALDES_DO_RESUMO = ('a_vencer', 'vencidas', 'pendentes')


def _carteira_vazia(resumo):
    """Nada a vencer, vencido ou pendente — pelos baldes que o e-mail conta."""
    return not any(resumo[balde] for balde in _BALDES_DO_RESUMO)


def montar_resumo(itens=None):
    """(assunto, corpo, resumo) do resumo do dia: carteira + pauta pendente.

    O assunto diz o que manda a atencao: havendo aviso, o numero de avisos vem
    primeiro; sem aviso, a contagem da carteira; sem nada, "tudo em dia"."""
    itens = list(itens or [])
    # NOVO = a chave nunca saiu em resumo nenhum. A leitura e a que o operador
    # espera: certificado que virou "vencido" tem chave propria e por isso
    # reaparece marcado no dia da virada, mesmo depois de semanas avisando que
    # estava vencendo.
    ja_avisadas = chaves_ja_avisadas({item.chave for item in itens})
    e_novo = {item.chave: item.chave not in ja_avisadas for item in itens}
    # Novos primeiro DENTRO de cada secao. Sem isso, o item que entrou hoje cai
    # no meio de uma lista que o leitor ja percorreu ontem — que e exatamente o
    # jeito de a repeticao apagar o que ela deveria reforcar. `sorted` e estavel,
    # entao a ordem da pauta (mais antigo primeiro) sobrevive dentro de cada
    # metade.
    itens = sorted(itens, key=lambda item: not e_novo[item.chave])
    quantos_novos = sum(e_novo.values())
    resumo = snapshot_service.contagem_carteira()
    a_vencer, vencidas, pendentes = (
        resumo['a_vencer'], resumo['vencidas'], resumo['pendentes'])
    carteira_vazia = _carteira_vazia(resumo)

    if itens:
        # O assunto diz quantos SAO e quantos MUDARAM: numa carteira que repete
        # os mesmos doze avisos por semanas, "12 avisos" nao distingue o dia em
        # que apareceu o decimo terceiro do dia em que nada aconteceu.
        assunto = (f'[Zelo] Resumo do dia — {len(itens)} aviso(s), '
                   + (f'{quantos_novos} novo(s)' if quantos_novos
                      else 'nenhum novo'))
    elif not carteira_vazia:
        assunto = (f'[Zelo] Resumo do dia — {a_vencer} a vencer, '
                   f'{vencidas} vencidas, {pendentes} pendentes')
    else:
        assunto = '[Zelo] Resumo do dia — tudo em dia'

    linhas = [
        f'Resumo do dia — {datetime.now():%d/%m/%Y %H:%M}',
        '',
        'CARTEIRA DE CERTIDOES',
        f'  A vencer (na janela): {a_vencer}',
        f'  Vencidas: {vencidas}',
        f'  Pendentes: {pendentes}',
    ]
    if carteira_vazia:
        linhas.append('  Tudo em dia — nada a vencer, vencido ou pendente.')

    for rotulo, do_tipo in _agrupar_por_secao(itens):
        linhas += ['', f'{rotulo.upper()} ({len(do_tipo)})']
        for item in do_tipo:
            marca = '[NOVO] ' if e_novo[item.chave] else ''
            linhas.append(f'  - {marca}{item.titulo}')
            for linha in (item.corpo or '').splitlines():
                linhas.append(f'    {linha}' if linha else '')
        rodape = _RODAPE_SECAO.get(do_tipo[0].tipo)
        if rodape:
            linhas.append(f'  {rodape}')

    if not itens:
        linhas += ['', 'Nenhum alerta novo desde o ultimo resumo.']
    linhas += ['', 'Este e o unico e-mail do dia: os avisos sao acumulados e',
               'enviados juntos. Enquanto o problema existir ele continua',
               'sendo listado, e o que aparece pela primeira vez vem marcado.',
               'Os destinatarios e a cadencia ficam em Configuracoes.']
    return assunto, '\n'.join(linhas), resumo


def _resumo_devido(cfg, itens):
    """True se o resumo deve sair agora.

    Com aviso na pauta sai sempre — a cadencia existe para nao encher a caixa com
    resumo de rotina, nao para segurar alerta. Sem aviso, respeita a cadencia."""
    if itens:
        return True
    ultimo = _ultimo_envio('digest')
    if ultimo is None:
        return True
    cadencia = (cfg.notif_cadencia if cfg else 'semanal') or 'semanal'
    dias = _CADENCIA_DIAS.get(cadencia, 7)
    return (date.today() - ultimo.date()).days >= dias


def enviar_resumo_diario(app):
    """Envia UM e-mail com a pauta do dia + a carteira. True se enviou.

    - Sem SMTP/destinatario: nao envia, loga aviso acionavel e **deixa a pauta
      intacta** (sai no proximo resumo que der certo).
    - Nada pendente e carteira 0/0/0: envia "tudo em dia", salvo
      NOTIF_DIGEST_ENVIAR_VAZIO=false.
    - So fecha a pauta e registra o envio quando o e-mail de fato saiu."""
    cfg = _config()
    itens = pauta_pendente()
    if not _resumo_devido(cfg, itens):
        return False

    destinatarios = _destinatarios(cfg)
    if not email_sender.smtp_configurado(app.config) or not destinatarios:
        log_event('notif_resumo_sem_smtp', level='WARNING',
                  tem_smtp=email_sender.smtp_configurado(app.config),
                  destinatarios=len(destinatarios), pendentes=len(itens))
        return False

    assunto, corpo, resumo = montar_resumo(itens)
    vazio = not itens and _carteira_vazia(resumo)
    if vazio and not app.config.get('NOTIF_DIGEST_ENVIAR_VAZIO', True):
        log_event('notif_resumo_omitido_vazio')
        return False

    if not email_sender.enviar(app.config, destinatarios, assunto, corpo):
        return False

    _registrar_envio('digest', 'digest', detalhe=str(resumo))
    _fechar_pauta(itens)
    log_event('notif_resumo_enviado', status='ok', avisos=len(itens), **resumo)
    return True


# --- alertas (falha recorrente + saldo 2captcha) ---------------------------

def _anotar_alerta(chave, tipo, titulo, corpo):
    """Anota um alerta na pauta. True se anotou agora.

    Ponto unico por onde TODO alerta passa: era aqui que ficava o envio imediato,
    e trocar o envio pela anotacao num lugar so e o que garante que nenhum
    caminho continue mandando e-mail avulso (AD-029)."""
    return registrar_pauta(chave, tipo, titulo, corpo)


def apurar_alertas(app):
    """Anota alertas de falha recorrente (via diagnostics) e de saldo baixo do
    2captcha. Retorna quantos anotou agora.

    - Falha recorrente: um alerta por (error_type, alvo) ativo; repeticao
      um por dia, porque o `_pauta_pendente_chave` fecha os repetidos do dia.
    - Saldo: alerta so quando abaixo do limiar; saldo None (API fora) NAO gera
      falso-baixo; mantem tambem o WARNING no painel de diagnostico (spec 02)."""
    enviados = 0

    for alerta in diagnostics.alertas_ativos():
        error_type = alerta.get('error_type')
        alvo = alerta.get('alvo')
        chave = f'falha:{error_type}:{alvo}'
        titulo = f'{alvo} — {error_type} ({alerta.get("ocorrencias")}x)'
        corpo = '\n'.join([
            f'Falha recorrente detectada em {alvo}.',
            f'Tipo de erro: {error_type}',
            f'Ocorrencias: {alerta.get("ocorrencias")}',
            f'Hipotese: {alerta.get("hipotese")}',
        ])
        if _anotar_alerta(chave, 'alerta_falha', titulo, corpo):
            enviados += 1

    saldo = captcha_solver.consultar_saldo(app.config)
    minimo = app.config.get('CAPTCHA_2_SALDO_MINIMO', 0)
    if saldo is not None and saldo < minimo:
        # o aviso no painel de diagnostico e responsabilidade do agendador
        # (_avisar_saldo_baixo, spec 02); aqui so cuidamos do push por e-mail.
        titulo = f'Saldo baixo: {saldo:.2f} USD (minimo {minimo:.2f})'
        corpo = '\n'.join([
            f'Saldo atual do 2captcha: {saldo:.2f} USD',
            f'Limiar minimo configurado: {minimo:.2f} USD',
            'Recarregue para nao interromper os lotes automatizados.',
        ])
        if _anotar_alerta('saldo_baixo', 'alerta_saldo', titulo, corpo):
            enviados += 1

    return enviados


def alertar_empresas_baixadas(app, baixadas):
    """Alerta as empresas que o recheck viu passar de ATIVA para nao-ativa.

    Recebe so as TRANSICOES (`receita_service.rechecar_lote` ja filtra): empresa
    que ja estava baixada nao realerta todo dia, e ligar a feature nao dispara um
    alerta retroativo para a carteira inteira.

    Uma ENTRADA POR EMPRESA na pauta (chave anti-spam propria), como a de
    municipio: resolver uma nao pode silenciar as outras dentro da janela. Todas
    saem juntas na secao "Situacao na Receita" do resumo do dia (AD-029). Retorna
    quantas foram anotadas agora."""
    enviados = 0

    for empresa_id, nome, situacao in baixadas or []:
        situacao_txt = situacao or 'nao ativa'
        titulo = f'{nome} consta como {situacao_txt}'
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
        if _anotar_alerta(f'empresa_baixada:{empresa_id}',
                          'alerta_empresa_baixada', titulo, corpo):
            enviados += 1

    return enviados


# Este alerta TINHA janela propria (7 dias vencendo, 3 dias vencido) para nao
# repetir o mesmo certificado em todo resumo. A janela saiu por pedido do
# usuario: a condicao persiste por semanas, e ver a lista inteira todo dia
# reforca. A troca de vencendo->vencido continua sendo evento proprio porque a
# CHAVE muda, entao ela reaparece marcada [NOVO] no dia em que acontece.
_ALERTA_CERTIFICADO_POR_CAUSA = {
    'vencido': {
        'chave': 'certificado_vencido:{empresa_id}',
        'titulo': 'Vencido em {data_vencimento} — {empresa_nome}',
        # So o que e proprio DESTE item; o conselho comum esta no _RODAPE_SECAO.
        'linhas': ['A manifestacao dessa empresa esta parada ate renovar.'],
    },
    'vencendo': {
        'chave': 'certificado_vencendo:{empresa_id}',
        'titulo': ('Vence em {data_vencimento} — {empresa_nome} '
                   '(faltam {dias_restantes} dia(s))'),
        # O titulo ja diz tudo: data, empresa e quanto falta. Uma segunda linha
        # aqui so repetiria o que o operador acabou de ler.
        'linhas': [],
    },
}


def alertar_certificados_vencendo(app, itens):
    """Anota certificados vencidos ou proximos de vencer, um por empresa/causa.

    Este era o maior gerador de spam do sistema: uma carteira com dezenas de
    certificados na janela rendia dezenas de e-mails no mesmo minuto. Agora cada
    um vira uma LINHA da secao "Certificados digitais" do resumo do dia (AD-029);
    a chave anti-spam por empresa/causa continua igual, so muda o que ela guarda.

    Recebe a selecao ja pronta de ``manifestador_cofre.certificados_a_vencer``:
    consultar o banco e responsabilidade do chamador. A chave separa vencido de
    vencendo porque a transicao entre os estados pede novo alerta, mas mantem o
    anti-spam duravel para cada empresa dentro da mesma causa.
    """
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
        if _anotar_alerta(modelo['chave'].format(**dados), 'alerta_certificado',
                          modelo['titulo'].format(**dados), corpo):
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
        'titulo': 'Portal {alvo} pausado (fora do ar)',
        'linhas': [
            'O sistema detectou falhas seguidas no portal {alvo} e pausou a emissao',
            'nele para nao gastar creditos de captcha contra um portal fora.',
        ],
    },
    'captcha': {
        'chave': 'solver_captcha:{alvo}',
        'tipo': 'alerta_solver',
        'titulo': 'Captcha falhando em {alvo} (emissao pausada)',
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
    silenciar o outro dentro da janela. Best-effort (AD-011): nao levanta, e o
    breaker abre do mesmo jeito. Retorna True se anotou agora.

    Unico produtor que NAO roda no agendador — o breaker abre no meio de um lote,
    a qualquer hora. Por isso a pauta e tabela e nao buffer em memoria (AD-029):
    o achado precisa sobreviver ate o resumo da madrugada seguinte."""
    modelo = _ALERTA_POR_CAUSA.get(causa) or _ALERTA_POR_CAUSA['portal']
    corpo = '\n'.join(
        [linha.format(alvo=alvo) for linha in modelo['linhas']]
        + [
            '',
            f'Ultimo erro: {motivo or "-"}',
            '',
            'Nada precisa ser religado: o bloqueio expira sozinho e o proximo ciclo',
            'tenta de novo. Se o problema seguir, o aviso volta no proximo resumo.',
        ])
    return _anotar_alerta(modelo['chave'].format(alvo=alvo), modelo['tipo'],
                          modelo['titulo'].format(alvo=alvo), corpo)


def alertar_municipios_quebrados(app, relatorios):
    """Alerta os municipios cujo dry-run acusou seletor quebrado (COV-05 A3).

    Uma entrada POR MUNICIPIO (chave anti-spam propria), para que consertar um nao
    silencie os demais dentro da janela; todas saem na secao "Municipios" do
    resumo do dia (AD-029). So `quebrado` alerta: `erro` e infra (driver/perfil
    ocupado) e `parcial` e captcha — nenhum dos dois e drift. Retorna quantos
    foram anotados agora."""
    from app.services.dryrun_municipio import QUEBRADO, falhou_ao_abrir
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
            titulo = f'{nome} — portal nao respondeu'
            fecho = [
                'O portal nao chegou a abrir: o endereco pode ter mudado ou o site',
                'esta fora do ar. Confira a URL do municipio (tabela municipio,',
                'coluna url_certidao) abrindo-a no navegador e rode a verificacao',
                'novamente. Enquanto isso a emissao deste municipio nao funciona.',
            ]
        else:
            titulo = f'{nome} — automacao pode ter quebrado'
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
        if _anotar_alerta(f'municipio_quebrado:{nome}', 'alerta_municipio',
                          titulo, corpo):
            enviados += 1

    return enviados
