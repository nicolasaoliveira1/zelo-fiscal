"""Orquestracao da emissao de NFSe (NFSE-13/14/16/18).

Espelha o papel do `emissao_service` das certidoes: casa a sessao de navegador,
os steps do assistente e a persistencia do status. As rotas so delegam aqui.

Regra que atravessa o modulo (ND-005): **nos estagios P1 e P2 a automacao nao
emite**. Ela preenche ate a tela de revisao e para; quem clica em emitir e o
operador. O artefato e um documento fiscal — errar nao e rollback, e
cancelamento de nota.
"""
from html import escape
from datetime import date

from app import db
from app.automation import nfse as automacao
from app.automation import nfse_recon
from app.automation.capture import (
    capturar_contexto_falha,
    salvar_artefato_sanitizado,
)
from app.models import NotaNfse, StatusNotaNfse
from app.services import nfse_config
from app.services import nfse_contrato
from app.services.nfse_drift import Diferenca
from app.services.execution_logger import log_event
from app.services.nfse_session import SESSAO

# Status a partir dos quais faz sentido preencher uma nota.
STATUS_EMITIVEIS = (
    StatusNotaNfse.PRONTA,
    StatusNotaNfse.CADASTRO_PENDENTE,   # CNPJ digitado na mao pelo operador
    StatusNotaNfse.PESSOA_FISICA,       # tomador CPF: emite sem virar Empresa
    StatusNotaNfse.FALHA,               # nova tentativa apos erro
    StatusNotaNfse.PULADA,
)

MOTIVO_POR_STATUS = {
    StatusNotaNfse.EMPRESA_PENDENTE:
        'Esta linha ainda nao tem empresa vinculada. Escolha a empresa ou '
        'informe o CNPJ antes de emitir.',
    StatusNotaNfse.INVALIDA:
        'Esta linha veio incompleta do extrato do banco e nao pode ser emitida.',
    StatusNotaNfse.DUPLICATA:
        'Ja existe nota deste tomador nesta competencia — emitida ou preenchida '
        'no portal esperando confirmacao. Libere a duplicata se quiser emitir '
        'mesmo assim.',
    StatusNotaNfse.EMITIDA:
        'Esta nota ja foi emitida.',
    StatusNotaNfse.AGUARDANDO_CONFIRMACAO:
        'Esta nota ja esta preenchida no portal, aguardando sua confirmacao.',
    StatusNotaNfse.DESCRICAO_PENDENTE:
        'A descricao do Pix nao disse a competencia nem o servico, entao nao ha '
        'texto para a nota. Informe a descricao antes de emitir.',
    StatusNotaNfse.CANCELADA:
        'Esta linha foi cancelada. Desfaca o cancelamento se quiser emiti-la.',
    StatusNotaNfse.AGRUPADA:
        'Esta linha foi agrupada em outra nota, que e a que deve ser emitida.',
}

MSG_GRUPO_PENDENTE = (
    'Ha uma proposta de agrupamento em aberto para esta linha (varios '
    'lancamentos do mesmo tomador, ou um estorno). Confirme ou descarte a '
    'proposta antes de emitir — o valor da nota depende dela.')


class NotaNaoEmitivelError(RuntimeError):
    """A nota nao esta em estado de ser preenchida. Mensagem pronta para a UI."""


class AliquotaNaoConfirmadaError(NotaNaoEmitivelError):
    """A aliquota do Simples nao foi conferida nesta sessao.

    Subclasse propria para a rota poder devolver um motivo que a interface
    reconhece e transformar num aviso confirmavel, em vez de um erro seco: a
    conferencia importa (a aliquota sai na nota), mas travar o preenchimento
    obrigaria o operador a passar pelo fluxo de abrir o portal e olhar mesmo
    quando ele ja sabe que esta certa."""


MSG_ALIQUOTA_NAO_CONFERIDA = (
    'A aliquota do Simples Nacional nao foi conferida nesta sessao. Ela muda '
    'mes a mes e sai na nota.')


_ERRO_CONTRATO_INCOMPATIVEL = automacao.ContratoNfseIncompativelError


class NfseDriftError(_ERRO_CONTRATO_INCOMPATIVEL):
    """Diferença estrutural que bloqueia a nota antes de avançar."""

    def __init__(self, mensagem, *, html_seguro=None):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.html_seguro = html_seguro
        self.pausar_lote = True



# `pre_avancar` é a única observação feita com a etapa já preenchida por
# inteiro. Antes dela o formulário ainda está revelando campos, e ausência não
# é remoção — ver `nfse_drift.comparar`.
MOMENTO_FINAL_DA_ETAPA = 'pre_avancar'


def _tela_ainda_e_da_etapa(driver, etapa):
    """A janela continua na etapa que se pretende observar?

    `_avancar` clica em "Avançar" e o ChromeDriver só devolve o controle com a
    PRÓXIMA tela carregada. Observar ali inventaria o formulário seguinte e o
    compara com o contrato da etapa anterior: todo controle obrigatório da tela
    nova vira `controle_novo` crítico, a nota é bloqueada e a Central recebe
    incidentes na etapa errada.

    URL que NAO e etapa conhecida tambem nao serve — e este era o furo: sessao
    expirada leva o driver para o login, e a tela de login comparada com o
    contrato transforma todo campo contratado em remocao critica. So se compara
    o que a URL confirma ser ESTA etapa; qualquer outra coisa nao e observacao.
    """

    try:
        atual = nfse_recon.etapa_da_url(getattr(driver, 'current_url', '') or '')
    except Exception:
        return False
    return atual == etapa


def _observar_fronteira_contrato(
    driver, contrato, etapa, momento, *, modo='assistido', execution_id=None,
    acumulador=None,
):
    """Observa uma tela já alcançada, sem navegar ou interagir com ela."""

    # A revisão é relida por pares dt/dd na autorrevisão. Ela não contém os
    # controles input das etapas anteriores e não deve ser inventariada como
    # se fosse mais um formulário.
    if etapa == 'revisao':
        return {'estado': 'observada', 'etapa': etapa, 'momento': momento}
    if not _tela_ainda_e_da_etapa(driver, etapa):
        log_event('nfse_recon_fora_da_etapa', contrato_id=contrato.contrato_id,
                  etapa=etapa, momento=momento, execution_id=execution_id)
        return {'estado': 'ignorada', 'etapa': etapa, 'momento': momento}
    inventario = nfse_recon.inventariar(driver, etapa)
    if inventario.estado != 'ok':
        motivo = inventario.motivo or 'observação inconclusiva'
        log_event(
            'nfse_recon_desconhecida',
            level='WARNING',
            contrato_id=contrato.contrato_id,
            etapa=etapa,
            momento=momento,
            motivo=motivo,
            execution_id=execution_id,
        )
        raise NfseDriftError(
            f'Não foi possível observar com segurança a etapa {etapa} '
            f'({motivo}); a nota foi pausada antes de avançar.'
        )
    if acumulador is not None:
        inventario = acumulador.acumular(
            nfse_recon.rascunho_da_url(driver.current_url), inventario
        )
    # Núcleo compartilhado com a recon assistida: qual diferença vira incidente,
    # contra qual versão, e que evidência fica. O que muda aqui é só a POLÍTICA
    # — esta fronteira levanta para pausar a nota; a recon devolve.
    resultado, _incidentes, html_seguro = nfse_contrato.comparar_e_registrar(
        contrato,
        etapa,
        momento,
        inventario,
        observacao_final=(momento == MOMENTO_FINAL_DA_ETAPA),
        execution_id=execution_id,
    )
    # O modo automático é conservador de propósito, mas conservadorismo que
    # nunca deixa concluir não protege nada: a etapa é um formulário progressivo
    # e "o campo ainda não apareceu" acontece em toda nota.
    if not resultado.diferencas_acionaveis:
        return {'estado': resultado.compatibilidade, 'etapa': etapa, 'momento': momento}

    if resultado.compatibilidade == 'incompativel' or modo == 'automatico':
        raise NfseDriftError(
            f'O formulário da etapa {etapa} divergiu do contrato aprovado; '
            'a nota foi pausada para revisão.',
            html_seguro=html_seguro,
        )
    return {
        'estado': resultado.compatibilidade,
        'etapa': etapa,
        'momento': momento,
        'aviso': True,
    }


def _resolver_valores_contrato(contrato, nota, config, hoje):
    """Materializa uma vez o catálogo seguro fixado para toda a nota."""

    valores = {}
    for campo in contrato.campos:
        if campo.etapa == 'revisao':
            continue
        valor = nfse_contrato.resolver_valor(campo, nota, config, hoje)
        if campo.fonte == 'municipio_servico_codigo' and valor is not None:
            valor = (valor, config.municipio_servico_nome)
        elif campo.fonte == 'codigo_tributacao' and valor is not None:
            valor = (valor, valor)
        valores[campo.chave_semantica] = valor
    return valores


def _registrar_validacao_portal(
    driver,
    contrato,
    etapa,
    nota,
    descricao,
    execution_id=None,
    valores_contrato=None,
):
    valores_resolvidos = []
    for valor in (valores_contrato or {}).values():
        if isinstance(valor, (tuple, list)):
            valores_resolvidos.extend(str(item or '') for item in valor)
        else:
            valores_resolvidos.append(str(valor or ''))
    mensagens = nfse_recon.mensagens_validacao(
        driver,
        (
            str(nota.documento or ''),
            str(nota.valor_final or ''),
            automacao.formatar_valor(nota.valor_final),
            str(descricao or ''),
            *valores_resolvidos,
        ),
    )
    if not mensagens:
        return []
    linhas = ''.join(f'<li>{escape(mensagem)}</li>' for mensagem in mensagens)
    html_seguro = (
        '<!doctype html><html lang="pt-BR"><meta charset="utf-8">'
        '<body><h1>Validação sanitizada</h1><ul>' + linhas + '</ul></body></html>'
    )
    diferenca = Diferenca(
        etapa=etapa,
        tipo='validacao_portal',
        severidade='fiscal',
        mensagem='O portal rejeitou o avanço e apresentou validação sanitizada.',
    )
    if contrato.contrato_id:
        nfse_contrato.registrar_incidentes(contrato.contrato_id, [diferenca])
    salvar_artefato_sanitizado(
        f'nfse_{etapa}_validacao', html_seguro, execution_id=execution_id
    )
    log_event(
        'nfse_validacao_portal_observada',
        level='WARNING',
        contrato_id=contrato.contrato_id,
        etapa=etapa,
        mensagens=len(mensagens),
        execution_id=execution_id,
    )
    return mensagens


def checar_aliquota(ignorar=False):
    """Guarda da aliquota, aplicada na ENTRADA do fluxo.

    Existe separada de `preencher_nota` porque o preenchimento agora roda em
    thread: se a unica checagem fosse la dentro, o aviso confirmavel apareceria
    so no log do lote, e o operador clicaria em emitir sem nunca ve-lo.
    `preencher_nota` mantem a sua como ultima linha de defesa."""
    if not SESSAO.aliquota_confirmada and not ignorar:
        raise AliquotaNaoConfirmadaError(MSG_ALIQUOTA_NAO_CONFERIDA)


def emitivel(nota):
    """A nota esta em estado de ser preenchida?

    Fonte unica da regra, consultada pela emissao individual (`_pode_emitir`) e
    pela montagem da fila do lote (`nfse_lote._emitivel`). Antes as duas
    listavam os status por conta propria, e uma condicao nova precisava ser
    lembrada nos dois lugares.

    A proposta de agrupamento em aberto barra ANTES do status: a linha pode
    estar perfeitamente Pronta e ainda assim ter o valor errado, porque o
    estorno que a abate ainda nao foi respondido."""
    from app.services import nfse_grupos

    if nfse_grupos.tem_proposta_pendente(nota):
        return False
    if nota.status == StatusNotaNfse.DUPLICATA:
        return bool(nota.duplicata_liberada)
    return nota.status in STATUS_EMITIVEIS


def _pode_emitir(nota):
    from app.services import nfse_grupos

    if emitivel(nota):
        return
    if nfse_grupos.tem_proposta_pendente(nota):
        raise NotaNaoEmitivelError(MSG_GRUPO_PENDENTE)
    raise NotaNaoEmitivelError(
        MOTIVO_POR_STATUS.get(nota.status, 'Esta linha nao pode ser emitida.'))


def preparar_sessao():
    """Garante o login e devolve a aliquota lida, para o operador conferir.

    Nao libera emissao nenhuma: a liberacao e a confirmacao explicita do
    operador (NFSE-12). Aliquota ilegivel devolve None, e quem chama pede
    confirmacao manual em vez de seguir calado.
    """
    SESSAO.garantir()
    aliquota = SESSAO.ler_aliquota()
    log_event('nfse_sessao_preparada', aliquota=aliquota)
    return {
        'aliquota': aliquota,
        'aliquota_confirmada': SESSAO.aliquota_confirmada,
    }


def preencher_nota(
    nota_id,
    hoje=None,
    execution_id=None,
    ignorar_aliquota=False,
    contrato_id=None,
    validacao_contrato_id=None,
    modo='assistido',
):
    """Preenche uma nota no portal ate a tela de revisao e PARA (NFSE-14).

    Nunca clica no botao de emitir. Ao final a nota fica
    `aguardando_confirmacao` e o operador confere e emite no navegador.
    """
    nota = db.session.get(NotaNfse, nota_id)
    if nota is None:
        raise NotaNaoEmitivelError('Nota nao encontrada.')

    _pode_emitir(nota)

    checar_aliquota(ignorar_aliquota)

    config = nfse_config.get_config_nfse()
    descricao = nfse_config.descricao_da_nota(config, nota)
    hoje = hoje or date.today()
    contrato_escolhido = validacao_contrato_id or contrato_id
    contrato = (
        nfse_contrato.carregar_execucao(contrato_escolhido)
        if contrato_escolhido is not None
        else nfse_contrato.contrato_inicial_execucao()
    )
    valores_contrato = _resolver_valores_contrato(
        contrato, nota, config, hoje
    )
    fronteira = {'etapa': None, 'momento': None}
    avisos_recon = []

    # Um acumulador por emissão: a comparação de cada etapa é feita contra a
    # união do que aquela etapa já mostrou, não contra o instantâneo da vez.
    acumulador = nfse_recon.AcumuladorRecon()

    def observar(driver_atual, etapa, momento):
        fronteira['etapa'] = etapa
        fronteira['momento'] = momento
        resultado = _observar_fronteira_contrato(
            driver_atual,
            contrato,
            etapa,
            momento,
            modo=modo,
            execution_id=execution_id,
            acumulador=acumulador,
        )
        if resultado.get('aviso'):
            avisos_recon.append(
                {'etapa': resultado['etapa'], 'momento': resultado['momento']}
            )
        return resultado

    nota.status = StatusNotaNfse.PREENCHENDO
    nota.erro = None
    db.session.commit()

    if ignorar_aliquota and not SESSAO.aliquota_confirmada:
        # deixa rastro: se a nota sair com tributo errado, o log diz que o
        # operador seguiu sem conferir
        log_event('nfse_aliquota_nao_conferida', level='WARNING',
                  nota_id=nota.id, execution_id=execution_id)

    log_event('nfse_preenchimento_inicio', nota_id=nota.id,
              competencia=nota.competencia, execution_id=execution_id)

    driver = SESSAO.garantir()
    try:
        automacao.abrir_nova_dps(driver)
        automacao.preencher_etapa_pessoas(
            driver,
            nota,
            config,
            hoje,
            contrato=contrato,
            observar=observar,
            valores_contrato=valores_contrato,
        )
        automacao.preencher_etapa_servico(
            driver,
            nota,
            config,
            descricao,
            contrato=contrato,
            observar=observar,
            valores_contrato=valores_contrato,
        )
        automacao.preencher_etapa_tributacao(
            driver,
            nota,
            config,
            contrato=contrato,
            observar=observar,
            valores_contrato=valores_contrato,
        )

        if not automacao.esperar_revisao(driver):
            raise automacao.InteracaoPortalError(
                'O portal nao chegou a tela de revisao apos as tres etapas.')
        observar(driver, 'revisao', 'entrada')
    except Exception as exc:
        if (
            fronteira['momento'] == 'pre_avancar' and
            not isinstance(
                exc,
                (_ERRO_CONTRATO_INCOMPATIVEL, nfse_contrato.ContratoNfseError),
            )
        ):
            _registrar_validacao_portal(
                driver,
                contrato,
                fronteira['etapa'],
                nota,
                descricao,
                execution_id=execution_id,
                valores_contrato=valores_contrato,
            )
        return _registrar_falha(nota, driver, exc, execution_id)

    nota.status = StatusNotaNfse.AGUARDANDO_CONFIRMACAO
    db.session.commit()
    log_event('nfse_preenchimento_ok', nota_id=nota.id, execution_id=execution_id)

    # `.get(chave, padrao)` nunca cai no padrao aqui: a chave SEMPRE existe, e
    # vale `None` quando o contrato marca a descricao como intocavel. O JSON
    # entregue ao operador dizia `"descricao": null` para uma nota que tem
    # descricao.
    descricao_aplicada = valores_contrato.get('ServicoPrestado_Descricao') or descricao
    return {
        'status': 'aguardando_confirmacao',
        'nota_id': nota.id,
        'competencia': nota.competencia,
        'documento': nota.documento,
        'valor': automacao.formatar_valor(nota.valor_final),
        'descricao': descricao_aplicada,
        'avisos_recon': avisos_recon,
        'message': 'Nota preenchida no portal. Confira os dados e emita no navegador.',
    }


# Erros que a propria automacao levanta ja tem texto escrito para o operador.
_ERROS_COM_MENSAGEM_PRONTA = (
    automacao.InteracaoPortalError,
    automacao.LoginNfseError,
)

# Traducao das falhas do Selenium que este fluxo realmente produz. Nao usa o
# `errors.mensagem_usuario` compartilhado: a taxonomia dele foi calibrada para
# os fluxos de certidao e classifica "element not interactable" como "portal
# indisponivel" — amigavel, porem falso, e mandaria o operador conferir a coisa
# errada. Aqui e melhor ser curto e correto.
_FALHAS_SELENIUM = {
    'ElementNotInteractableException':
        'O campo existe mas nao aceitou interacao — a tela pode nao ter '
        'terminado de carregar ou ter um overlay aberto.',
    'ElementClickInterceptedException':
        'Algo ficou por cima do elemento (aviso ou calendario aberto) e '
        'bloqueou o clique.',
    'NoSuchElementException':
        'Um campo esperado nao existe nesta tela. O portal pode ter mudado o '
        'formulario.',
    'StaleElementReferenceException':
        'A tela mudou no meio do preenchimento.',
    'TimeoutException':
        'O portal demorou demais para responder.',
    'InvalidSessionIdException':
        'A sessao do navegador foi encerrada.',
    'NoSuchWindowException':
        'A janela do navegador foi fechada durante o preenchimento.',
}


# --- destravar a nota que ficou esperando confirmacao (ND-011) --------------

def evidencia_de_emissao(nota, hoje=None):
    """Notas do portal que PODEM ser esta, lidas do espelho.

    Devolve `None` quando nao deu para conferir — sem navegador aberto ou o
    portal recusou —, e uma lista (possivelmente vazia) quando a conferencia
    aconteceu. `None` e lista vazia sao coisas diferentes de proposito: a
    primeira e "nao sei", a segunda e "olhei e nao ha".

    NAO abre navegador. Abrir aqui pediria certificado de novo e, pior,
    deixaria a sessao do modo assistido apontando para uma janela que o
    operador nao pediu — e e nessa janela que ele emite.

    O casamento e por documento + `competencia_dps`, que e o mes da EMISSAO e
    nao o mes de referencia do honorario (ND-027). Ele acha demais de
    proposito: duas notas para o mesmo tomador no mesmo mes sao um caso real, e
    quem decide se e esta ou outra e o operador, olhando valor e data.
    """
    from app.models import NotaEmitidaNfse
    from app.services import nfse_emitidas

    if not nota.documento:
        return None
    if not SESSAO.driver_vivo():
        return None
    if not SESSAO.adquirir():
        return None

    hoje = hoje or date.today()
    try:
        nfse_emitidas.consultar(hoje.replace(day=1), hoje)
    except Exception as exc:
        log_event('nfse_conferencia_portal_falhou', nota_id=nota.id,
                  level='WARNING', error_type=type(exc).__name__)
        return None
    finally:
        # A janela FICA ABERTA, como na consulta do painel: e a mesma sessao
        # autenticada do preenchimento, e fecha-la pediria certificado na
        # proxima nota. Quem fecha e o "Encerrar sessao".
        SESSAO.liberar()

    competencia = f'{hoje.month:02d}/{hoje.year}'
    achadas = (
        NotaEmitidaNfse.query
        .filter(NotaEmitidaNfse.documento == nota.documento)
        .filter(NotaEmitidaNfse.competencia_dps == competencia)
        .order_by(NotaEmitidaNfse.data_geracao.desc())
        .all()
    )
    log_event('nfse_conferencia_portal', nota_id=nota.id,
              encontradas=len(achadas))
    return achadas


def liberar_preenchimento(nota, confirmado=False, hoje=None):
    """Devolve a fila a nota que ficou `aguardando_confirmacao`.

    A ND-011 manda o sistema NAO chutar o desfecho de um preenchimento cujo
    navegador foi fechado — e continua valendo. O que esta funcao acrescenta e
    o outro lado: o operador, que sabe o desfecho, pode declara-lo. Antes so
    dava para declarar "emiti", e quem nao emitiu ficava sem saida nenhuma.

    Confere no portal antes, quando ha navegador aberto. Se o espelho mostrar
    nota que pode ser esta, a liberacao para e devolve as candidatas: emitir de
    novo o que ja foi emitido gera duplicata na prefeitura, e isso nao tem
    rollback. O operador confirma com `confirmado=True` depois de olhar.

    Devolve `(erro, evidencias)`.
    """
    from app.services import nfse_import

    if nota.status != StatusNotaNfse.AGUARDANDO_CONFIRMACAO:
        return ('Esta nota não está esperando confirmação.', None)

    evidencias = None
    if not confirmado:
        evidencias = evidencia_de_emissao(nota, hoje=hoje)
        if evidencias:
            return (None, evidencias)

    nota.status = nfse_import.recalcular_status(nota)
    # A falha de antes nao vale mais: deixa-la mostraria a nota como Pronta com
    # um erro embaixo que nao quer dizer nada (mesmo motivo do `_cancelar`).
    nota.erro = None
    log_event('nfse_preenchimento_liberado', nota_id=nota.id,
              status=nota.status, conferido=evidencias is not None)
    return (None, evidencias)


def mensagem_da_falha(exc):
    """Frase curta e correta para mostrar na linha da nota.

    Excecao do Selenium traz stacktrace e endereco de memoria — util no log,
    ilegivel na tabela. O texto cru continua inteiro no log e na captura,
    alcancavel pelo request_id.
    """
    if isinstance(exc, _ERROS_COM_MENSAGEM_PRONTA):
        return str(getattr(exc, 'mensagem', exc)).strip()

    conhecida = _FALHAS_SELENIUM.get(type(exc).__name__)
    if conhecida:
        return conhecida

    primeira = (str(exc).strip().splitlines() or [''])[0].strip()
    return primeira[:200] or 'Falha na automacao.'


def _registrar_falha(nota, driver, exc, execution_id):
    """Marca SO esta nota como falha; as demais do lote ficam intactas."""
    if isinstance(
        exc, (_ERRO_CONTRATO_INCOMPATIVEL, nfse_contrato.ContratoNfseError)
    ):
        html_seguro = getattr(exc, 'html_seguro', None)
        if html_seguro:
            salvar_artefato_sanitizado(
                f'nfse_nota_{nota.id}', html_seguro, execution_id=execution_id
            )
    else:
        try:
            capturar_contexto_falha(
                driver,
                contexto=f'nfse_nota_{nota.id}',
                execution_id=execution_id,
            )
        except Exception:
            pass

    mensagem = mensagem_da_falha(exc)
    nota.status = StatusNotaNfse.FALHA
    nota.erro = mensagem[:300]
    db.session.commit()

    # o texto cru (com stacktrace) fica so no log, alcancavel pelo request_id
    log_event('nfse_preenchimento_erro', level='ERROR', nota_id=nota.id,
              error=str(exc), execution_id=execution_id)
    resultado = {
        'status': 'error',
        'nota_id': nota.id,
        'message': mensagem,
    }
    if isinstance(
        exc, (_ERRO_CONTRATO_INCOMPATIVEL, nfse_contrato.ContratoNfseError)
    ):
        resultado['pausar_lote'] = True
    return resultado
