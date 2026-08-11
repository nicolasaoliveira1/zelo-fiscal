"""Testes unitarios da fila duravel de emissao (spec 02, SCHED-01/02/03).

Cada teste ancora num AC das stories P1 (fila duravel) da spec 02.
"""
from datetime import datetime, timedelta
from unittest.mock import patch

from app import db
from app.errors import ErrorType, _CATALOGO
from app.models import Certidao, TarefaEmissao, TipoCertidao
from app.services import fila_emissao


def _nova_tarefa(app, ids, **kwargs):
    """Cria e persiste uma TarefaEmissao para a certidao FGTS semeada."""
    cert = db.session.get(Certidao, ids['fgts'])
    dados = dict(tipo='FGTS', empresa_id=cert.empresa_id, certidao_id=cert.id,
                 status='pendente')
    dados.update(kwargs)
    t = TarefaEmissao(**dados)
    db.session.add(t)
    db.session.commit()
    return t


# --- AC P1.1: enfileirar cria pendente ------------------------------------

def test_enfileirar_cria_pendente(app, ids):
    with app.app_context():
        t = fila_emissao.enfileirar(ids['fgts'], TipoCertidao.FGTS)
        assert t is not None
        assert t.status == 'pendente'
        assert t.tipo == 'FGTS'
        assert t.certidao_id == ids['fgts']
        assert t.empresa_id == ids['empresa']
        assert t.tentativas == 0
        assert TarefaEmissao.query.count() == 1


def test_enfileirar_certidao_inexistente_retorna_none(app, ids):
    with app.app_context():
        assert fila_emissao.enfileirar(999999, TipoCertidao.FGTS) is None
        assert TarefaEmissao.query.count() == 0


def test_enfileirar_falha_commit_faz_rollback(app, ids):
    with app.app_context():
        with patch.object(db.session, 'commit', side_effect=Exception('boom')):
            assert fila_emissao.enfileirar(ids['fgts'], TipoCertidao.FGTS) is None


# --- AC P1.5: idempotencia (nao duplica) ----------------------------------

def test_enfileirar_idempotente_nao_duplica(app, ids):
    with app.app_context():
        primeira = fila_emissao.enfileirar(ids['fgts'], TipoCertidao.FGTS)
        segunda = fila_emissao.enfileirar(ids['fgts'], TipoCertidao.FGTS)
        assert primeira is not None
        assert segunda is None
        assert TarefaEmissao.query.filter_by(certidao_id=ids['fgts']).count() == 1


def test_enfileirar_apos_terminal_permite_nova(app, ids):
    """Tarefa terminal (ok) nao bloqueia uma nova — historico em dias diferentes."""
    with app.app_context():
        _nova_tarefa(app, ids, status='ok')
        nova = fila_emissao.enfileirar(ids['fgts'], TipoCertidao.FGTS)
        assert nova is not None
        assert TarefaEmissao.query.filter_by(certidao_id=ids['fgts']).count() == 2


def test_enfileirar_a_vencer_multi_tipo(app, ids):
    with app.app_context():
        alvos = {'FGTS': [ids['fgts']], 'Estadual': [ids['rs']]}
        resultado = fila_emissao.enfileirar_a_vencer(alvos)
        assert resultado == {'FGTS': [ids['fgts']], 'Estadual': [ids['rs']]}
        # roda de novo: idempotente, nada novo enfileirado
        resultado2 = fila_emissao.enfileirar_a_vencer(alvos)
        assert resultado2 == {'FGTS': [], 'Estadual': []}
        assert TarefaEmissao.query.count() == 2


# --- AC P1.2: transicoes de status ----------------------------------------

def test_marcar_rodando(app, ids):
    with app.app_context():
        t = _nova_tarefa(app, ids)
        assert fila_emissao.marcar_rodando(t, execution_id='exec-1') is True
        assert t.status == 'rodando'
        assert t.iniciada_em is not None
        assert t.execution_id == 'exec-1'


def test_marcar_ok(app, ids):
    with app.app_context():
        t = _nova_tarefa(app, ids, status='rodando', erro='antigo')
        assert fila_emissao.marcar_ok(t) is True
        assert t.status == 'ok'
        assert t.concluida_em is not None
        assert t.erro is None


def test_marcar_falha_persiste_erro(app, ids):
    with app.app_context():
        t = _nova_tarefa(app, ids, status='rodando')
        assert fila_emissao.marcar_falha(t, 'portal fora do ar') is True
        assert t.status == 'falha'
        assert t.concluida_em is not None
        assert t.erro == 'portal fora do ar'


def test_marcar_falha_trunca_erro_em_500(app, ids):
    with app.app_context():
        t = _nova_tarefa(app, ids, status='rodando')
        fila_emissao.marcar_falha(t, 'x' * 800)
        assert len(t.erro) == 500


# --- AC P1.4: retry ate limite --------------------------------------------

def test_resolver_falha_retry_enquanto_ha_tentativas(app, ids):
    with app.app_context():
        t = _nova_tarefa(app, ids, status='rodando')
        status = fila_emissao.resolver_falha(t, 'timeout', max_tentativas=3)
        assert status == 'retry'
        assert t.status == 'retry'
        assert t.tentativas == 1
        assert t.concluida_em is None


def test_resolver_falha_esgota_vira_falha_definitiva(app, ids):
    with app.app_context():
        t = _nova_tarefa(app, ids, status='rodando', tentativas=1)
        status = fila_emissao.resolver_falha(t, 'timeout', max_tentativas=2)
        assert status == 'falha'
        assert t.status == 'falha'
        assert t.tentativas == 2
        assert t.concluida_em is not None


# --- AC P1.3: reconciliacao de orfas --------------------------------------

def test_reconciliar_orfas_rodando_volta_pendente(app, ids):
    with app.app_context():
        t = _nova_tarefa(app, ids, status='rodando', iniciada_em=datetime.now())
        n = fila_emissao.reconciliar_orfas()
        assert n == 1
        db.session.refresh(t)
        assert t.status == 'pendente'
        assert t.iniciada_em is None


def test_reconciliar_orfas_sem_rodando_retorna_zero(app, ids):
    with app.app_context():
        _nova_tarefa(app, ids, status='pendente')
        assert fila_emissao.reconciliar_orfas() == 0


def test_reconciliar_apenas_timeout_ignora_recente(app, ids):
    with app.app_context():
        recente = _nova_tarefa(app, ids, status='rodando',
                               iniciada_em=datetime.now())
        cert_rs = db.session.get(Certidao, ids['rs'])
        antiga = TarefaEmissao(
            tipo='Estadual', empresa_id=cert_rs.empresa_id, certidao_id=cert_rs.id,
            status='rodando',
            iniciada_em=datetime.now() - timedelta(hours=fila_emissao.TIMEOUT_ORFA_HORAS + 1))
        db.session.add(antiga)
        db.session.commit()

        n = fila_emissao.reconciliar_orfas(apenas_timeout=True)
        assert n == 1
        db.session.refresh(recente)
        db.session.refresh(antiga)
        assert recente.status == 'rodando'   # recente preservada
        assert antiga.status == 'pendente'   # travada recuperada


# --- helpers de consulta ---------------------------------------------------

def test_tarefas_elegiveis_inclui_pendente_e_retry_exclui_terminais(app, ids):
    with app.app_context():
        _nova_tarefa(app, ids, status='pendente')
        cert_rs = db.session.get(Certidao, ids['rs'])
        for st in ('retry', 'ok', 'falha'):
            db.session.add(TarefaEmissao(
                tipo='FGTS', empresa_id=cert_rs.empresa_id,
                certidao_id=cert_rs.id, status=st))
        db.session.commit()
        elegiveis = fila_emissao.tarefas_elegiveis(TipoCertidao.FGTS)
        estados = sorted(t.status for t in elegiveis)
        assert estados == ['pendente', 'retry']


# === dead-letter: listar, agrupar e reprocessar (spec 09, RESOP-01) ========

def _falha(app, ids, erro, **kwargs):
    return _nova_tarefa(app, ids, status='falha', tentativas=3, erro=erro,
                        concluida_em=datetime.now(), **kwargs)


def test_tarefas_falhas_traz_so_status_falha(app, ids):
    """RESOP-01.1: a tela e 'o que morreu', nao a fila inteira."""
    with app.app_context():
        _falha(app, ids, 'Timeout aguardando download.')
        _nova_tarefa(app, ids, status='retry', tentativas=1, erro='x')
        _nova_tarefa(app, ids, status='pendente')
        _nova_tarefa(app, ids, status='rodando')
        _nova_tarefa(app, ids, status='ok')

        falhas = fila_emissao.tarefas_falhas()

        assert len(falhas) == 1
        assert falhas[0].status == 'falha'


def test_tarefas_falhas_mais_recentes_primeiro(app, ids):
    with app.app_context():
        antiga = _falha(app, ids, 'erro antigo')
        antiga.concluida_em = datetime.now() - timedelta(days=2)
        db.session.commit()
        nova = _falha(app, ids, 'erro novo')

        falhas = fila_emissao.tarefas_falhas()

        assert [t.id for t in falhas] == [nova.id, antiga.id]


def test_motivo_classifica_pelo_catalogo_de_erro(app, ids):
    """RESOP-01.2: reusa map_exception_to_error_type — sem vocabulario novo."""
    with app.app_context():
        assert fila_emissao.motivo_da_tarefa(
            _falha(app, ids, 'Timeout aguardando download da certidao.')) == 'TIMEOUT'
        assert fila_emissao.motivo_da_tarefa(
            _falha(app, ids, 'Falha ao resolver o captcha.')) == 'CAPTCHA'


def test_motivo_de_erro_vazio_e_unknown(app, ids):
    """RESOP-01.3: falha antiga sem mensagem nao pode sumir da lista."""
    with app.app_context():
        assert fila_emissao.motivo_da_tarefa(_falha(app, ids, None)) == 'UNKNOWN'
        assert fila_emissao.motivo_da_tarefa(_falha(app, ids, '')) == 'UNKNOWN'


def test_agrupar_falhas_conta_por_motivo(app, ids):
    with app.app_context():
        _falha(app, ids, 'Timeout aguardando download.')
        _falha(app, ids, 'Timeout no portal.')
        _falha(app, ids, 'Falha no captcha.')

        grupos = {g['error_type']: g for g in fila_emissao.agrupar_falhas()}

        assert grupos['TIMEOUT']['total'] == 2
        assert grupos['CAPTCHA']['total'] == 1
        assert len(grupos['TIMEOUT']['itens']) == 2


def test_grupo_traz_titulo_e_acao_do_catalogo(app, ids):
    """O operador le 'Tempo esgotado', nao a sigla crua."""
    with app.app_context():
        _falha(app, ids, 'Timeout aguardando download.')

        grupo = fila_emissao.agrupar_falhas()[0]

        titulo, _causa, acao, _rec = _CATALOGO[ErrorType.TIMEOUT]
        assert grupo['titulo'] == titulo == 'Tempo esgotado'
        assert grupo['acao'] == acao


def test_item_do_grupo_traz_dados_do_alvo(app, ids):
    with app.app_context():
        tarefa = _falha(app, ids, 'Timeout aguardando download.')

        item = fila_emissao.agrupar_falhas()[0]['itens'][0]

        assert item['id'] == tarefa.id
        assert item['certidao_id'] == ids['fgts']
        assert item['tipo'] == 'FGTS'
        assert item['empresa'] == 'Empresa Teste'
        assert item['tentativas'] == 3
        assert item['erro'] == 'Timeout aguardando download.'
        assert item['concluida_em']


def test_agrupar_falhas_sem_nada_devolve_lista_vazia(app, ids):
    with app.app_context():
        assert fila_emissao.agrupar_falhas() == []


# --- devolver a fila / reprocessar (RESOP-01.4 a 01.9) --------------------

def test_devolver_para_fila_reseta_a_tarefa(app, ids):
    """RESOP-01.4: volta a pendente, zera tentativas e limpa os carimbos."""
    with app.app_context():
        tarefa = _falha(app, ids, 'Timeout aguardando download.')
        tarefa.iniciada_em = datetime.now()
        db.session.commit()

        ok, motivo = fila_emissao.devolver_para_fila(tarefa)

        assert (ok, motivo) == (True, None)
        assert tarefa.status == 'pendente'
        assert tarefa.tentativas == 0
        assert tarefa.iniciada_em is None
        assert tarefa.concluida_em is None


def test_devolver_nao_cria_tarefa_nova(app, ids):
    with app.app_context():
        tarefa = _falha(app, ids, 'timeout')
        fila_emissao.devolver_para_fila(tarefa)
        assert TarefaEmissao.query.count() == 1


def test_devolver_recusa_certidao_inexistente(app, ids):
    """RESOP-01.8: certidao que sumiu nao quebra o reprocessamento.

    A tarefa e montada em MEMORIA, sem persistir: no MySQL a FK
    `tarefa_emissao.certidao_id -> certidao.id` recusa gravar um id inexistente
    (o SQLite deixa passar, e foi assim que a versao anterior deste teste passou
    local e quebrou no CI). O que se prova aqui e a guarda do servico, que so
    depende do `db.session.get` devolver None."""
    with app.app_context():
        cert = db.session.get(Certidao, ids['fgts'])
        tarefa = TarefaEmissao(tipo='FGTS', empresa_id=cert.empresa_id,
                               certidao_id=999999, status='falha', tentativas=3,
                               erro='timeout')

        ok, motivo = fila_emissao.devolver_para_fila(tarefa)

        assert ok is False
        assert motivo
        assert tarefa.status == 'falha'


def test_devolver_recusa_quando_ja_ha_tarefa_ativa(app, ids):
    """RESOP-01.7: idempotencia — nao pode existir uma segunda tarefa ativa."""
    with app.app_context():
        tarefa = _falha(app, ids, 'timeout')
        _nova_tarefa(app, ids, status='pendente')

        ok, motivo = fila_emissao.devolver_para_fila(tarefa)

        assert ok is False
        assert motivo
        assert tarefa.status == 'falha'


def test_devolver_falha_de_commit_faz_rollback(app, ids):
    """RESOP-01.9: sem estado misto quando o commit falha."""
    with app.app_context():
        tarefa = _falha(app, ids, 'timeout')
        with patch.object(db.session, 'commit', side_effect=Exception('boom')):
            ok, motivo = fila_emissao.devolver_para_fila(tarefa)
        assert ok is False
        assert motivo
        assert db.session.get(TarefaEmissao, tarefa.id).status == 'falha'


def test_reprocessar_por_ids(app, ids):
    with app.app_context():
        t1 = _falha(app, ids, 'timeout')
        cert_rs = db.session.get(Certidao, ids['rs'])
        t2 = TarefaEmissao(tipo='Estadual', empresa_id=cert_rs.empresa_id,
                           certidao_id=cert_rs.id, status='falha', tentativas=3,
                           erro='captcha', concluida_em=datetime.now())
        db.session.add(t2)
        db.session.commit()

        resultado = fila_emissao.reprocessar(ids=[t1.id, t2.id])

        assert sorted(resultado['devolvidas']) == sorted([t1.id, t2.id])
        assert resultado['recusadas'] == []
        assert t1.status == 'pendente' and t2.status == 'pendente'


def test_reprocessar_por_motivo_pega_so_o_grupo(app, ids):
    """RESOP-01.5: reprocessar um grupo aplica so aos itens daquele motivo."""
    with app.app_context():
        timeout = _falha(app, ids, 'Timeout aguardando download.')
        cert_rs = db.session.get(Certidao, ids['rs'])
        captcha = TarefaEmissao(tipo='Estadual', empresa_id=cert_rs.empresa_id,
                                certidao_id=cert_rs.id, status='falha',
                                tentativas=3, erro='Falha no captcha.',
                                concluida_em=datetime.now())
        db.session.add(captcha)
        db.session.commit()

        resultado = fila_emissao.reprocessar(error_type='TIMEOUT')

        assert resultado['devolvidas'] == [timeout.id]
        assert timeout.status == 'pendente'
        assert captcha.status == 'falha'


def test_reprocessar_e_parcial_o_que_nao_der_volta_nomeado(app, ids):
    """O que der certo e aplicado; o que nao der volta em 'recusadas'.

    A recusa aqui e por JA HAVER tarefa ativa para a mesma certidao (RESOP-01.7):
    e um motivo real, e — diferente de apontar para uma certidao inexistente —
    respeita a FK que o MySQL impoe."""
    with app.app_context():
        boa = _falha(app, ids, 'timeout')

        cert_rs = db.session.get(Certidao, ids['rs'])
        ruim = TarefaEmissao(tipo='Estadual', empresa_id=cert_rs.empresa_id,
                             certidao_id=cert_rs.id, status='falha', tentativas=3,
                             erro='timeout', concluida_em=datetime.now())
        db.session.add(ruim)
        db.session.add(TarefaEmissao(
            tipo='Estadual', empresa_id=cert_rs.empresa_id,
            certidao_id=cert_rs.id, status='pendente'))
        db.session.commit()

        resultado = fila_emissao.reprocessar(ids=[boa.id, ruim.id])

        assert resultado['devolvidas'] == [boa.id]
        assert [r['id'] for r in resultado['recusadas']] == [ruim.id]
        assert resultado['recusadas'][0]['motivo']
        assert boa.status == 'pendente'
        assert ruim.status == 'falha'


def test_reprocessar_ignora_tarefa_que_nao_esta_em_falha(app, ids):
    with app.app_context():
        pendente = _nova_tarefa(app, ids, status='pendente')

        resultado = fila_emissao.reprocessar(ids=[pendente.id])

        assert resultado['devolvidas'] == []
        assert [r['id'] for r in resultado['recusadas']] == [pendente.id]


def test_reprocessar_sem_alvo_nao_faz_nada(app, ids):
    with app.app_context():
        _falha(app, ids, 'timeout')
        assert fila_emissao.reprocessar() == {'devolvidas': [], 'recusadas': []}
