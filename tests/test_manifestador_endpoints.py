"""Rotas do manifestador (MANIF-09/11/14/21).

Rotas finas: o que se prova aqui e autorizacao, validacao de entrada e o
contrato da resposta — nao a regra de negocio, que tem testes proprios.

Um teste em especial vale por muitos: o filtro da lista tem de ser EXATAMENTE o
que o lote enfileira. Divergir os dois manifestaria notas que o operador nao
esta olhando.
"""
from threading import Barrier, Thread

from app import db
from app.models import (
    CertificadoEmpresa,
    ChaveManifestacao,
    Empresa,
    EstadoCertificado,
    StatusManifestacao,
)
from app.automation import batch_state
from app.automation.batch_state import (
    MANIF_BATCH_STATE,
    definir_manif_opcoes,
    manif_batch_opcoes,
)
from app.services import batch_engine, manifestador_lote, manifestador_service

CHAVE_A = '43170122333444000181650010000045391000045393'
CHAVE_B = '43170122333444000181650010000045401000045408'
DV_ERRADO = CHAVE_A[:43] + str((int(CHAVE_A[43]) + 1) % 10)


def _empresa(nome='EMPRESA A', cnpj='11.222.333/0001-81', estado_cert=None):
    emp = Empresa(nome=nome, cnpj=cnpj, estado='RS', cidade='Imbé')
    db.session.add(emp)
    db.session.commit()
    if estado_cert:
        emp.certificado = CertificadoEmpresa(caminho=f'Z:/{nome}.pfx',
                                             estado=estado_cert)
        db.session.commit()
    return emp


def _chave(empresa, chave=CHAVE_A, status=StatusManifestacao.PENDENTE,
           competencia='2017-01'):
    linha = ChaveManifestacao(chave=chave, empresa_id=empresa.id,
                              competencia=competencia, status=status)
    db.session.add(linha)
    db.session.commit()
    return linha


# --- pagina e autorizacao ---------------------------------------------------

def test_pagina_abre_para_operador(client):
    assert client.get('/manifestador').status_code == 200


def test_pagina_exige_login(client_anon):
    resposta = client_anon.get('/manifestador')
    assert resposta.status_code in (302, 401)


def test_leitura_nao_manifesta(login_as):
    """Papel `leitura` nao pode disparar ato fiscal (AD-005)."""
    leitor = login_as('leitura')
    assert leitor.post('/manifestador/lote/iniciar', json={
        'modo': 'carteira', 'tipo_evento': '210200'}).status_code == 403


def test_senha_do_cofre_exige_admin(app, ids, login_as):
    """Credencial de cliente e um degrau acima das demais acoes."""
    with app.app_context():
        emp = _empresa(estado_cert=EstadoCertificado.SENHA_PENDENTE)
        empresa_id = emp.id

    operador = login_as('operador')
    resposta = operador.post(f'/manifestador/cofre/senha/{empresa_id}',
                             json={'senha': 'x'})
    assert resposta.status_code == 403


# --- cofre (MANIF-21) -------------------------------------------------------

def test_pre_voo_conta_por_estado_e_nomeia_os_problemas(app, ids, client):
    with app.app_context():
        _empresa('OK', '11.222.333/0001-81', EstadoCertificado.PRONTO)
        _empresa('VENCIDA', '22.333.444/0001-92', EstadoCertificado.VENCIDO)

    dados = client.get('/manifestador/cofre').get_json()

    assert dados['prontas'] == 1
    assert dados['inventariado'] is True
    problemas = {p['empresa']: p['estado'] for p in dados['problemas']}
    assert problemas == {'VENCIDA': 'vencido'}


def test_pre_voo_sugere_a_senha_lida_do_caminho(app, ids, client):
    """Sugestao, nunca aplicacao: o operador confirma."""
    with app.app_context():
        emp = _empresa('SIGLA', '11.222.333/0001-81')
        emp.certificado = CertificadoEmpresa(
            caminho=r'Z:\SIGLA\CERTIFICADO SENHA 042026\sigla.pfx',
            estado=EstadoCertificado.SENHA_PENDENTE)
        db.session.commit()

    problemas = client.get('/manifestador/cofre').get_json()['problemas']

    assert problemas[0]['sugestao_senha'] == '042026'


def test_pre_voo_devolve_os_vencimentos_da_mesma_fonte_do_cartao(app, ids, client):
    """A regua e o cartao da Visao Geral leem `resumo_de_vencimento`.

    Duas contagens do mesmo fato divergem: se a rota recontasse aqui, um dia o
    cartao diria 3 e a regua 2, e nao haveria como saber qual mentiu.
    """
    from datetime import datetime, timedelta

    with app.app_context():
        vencida = _empresa('VENCE JA', '11.222.333/0001-81')
        vencida.certificado = CertificadoEmpresa(
            caminho='Z:/a.pfx', estado=EstadoCertificado.PRONTO,
            not_after=datetime.now() - timedelta(days=2))
        proxima = _empresa('VENCE LOGO', '22.333.444/0001-92')
        proxima.certificado = CertificadoEmpresa(
            caminho='Z:/b.pfx', estado=EstadoCertificado.PRONTO,
            not_after=datetime.now() + timedelta(days=3))
        db.session.commit()

    vencimentos = client.get('/manifestador/cofre').get_json()['vencimentos']

    assert vencimentos['com_vencimento'] == 2
    assert vencimentos['janela_dias'] > 0
    # o mais urgente primeiro, como no cartao
    causas = [item['causa'] for item in vencimentos['itens']]
    nomes = [item['empresa_nome'] for item in vencimentos['itens']]
    assert causas == ['vencido', 'vencendo']
    assert nomes == ['VENCE JA', 'VENCE LOGO']
    # `not_after` sai serializado: a regua le a data sem parse de datetime
    assert isinstance(vencimentos['itens'][0]['not_after'], str)


def test_pre_voo_sem_vencimento_conhecido_nao_inventa_alivio(app, ids, client):
    """Certificado sem `not_after` fica FORA do denominador.

    Conta-lo diria "nenhum dos 2 vence", que e desconhecido disfarcado de boa
    noticia — o erro que o cartao da Visao Geral evita de proposito.
    """
    with app.app_context():
        _empresa('SEM DATA', '11.222.333/0001-81', EstadoCertificado.SEM_ARQUIVO)

    vencimentos = client.get('/manifestador/cofre').get_json()['vencimentos']

    assert vencimentos['com_vencimento'] == 0
    assert vencimentos['itens'] == []


def test_cofre_sem_inventario_diz_que_nao_foi_inventariado(app, ids, client):
    assert client.get('/manifestador/cofre').get_json()['inventariado'] is False


def test_inventariar_com_varredura_em_curso_devolve_409(app, ids, client):
    """Lock nao-bloqueante, como a `NfseSession`: duas varreduras gravam a linha
    da MESMA empresa e a perdedora derruba o commit unico do fim."""
    from app.services import manifestador_cofre

    assert manifestador_cofre._inventario_acquire() is True
    try:
        resposta = client.post('/manifestador/cofre/inventariar')
    finally:
        manifestador_cofre._inventario_release()

    assert resposta.status_code == 409
    assert 'andamento' in resposta.get_json()['message'].lower()


def test_inventariar_libera_o_lock_mesmo_com_drive_fora(app, ids, client,
                                                        monkeypatch):
    """Sem o `finally`, um drive fora deixaria o lock preso e o job diario
    nunca mais varreria — ate reiniciar o processo."""
    from app.services import manifestador_cofre

    monkeypatch.setattr(manifestador_cofre, 'rede_disponivel', lambda: False)
    client.post('/manifestador/cofre/inventariar')

    assert manifestador_cofre.inventario_em_curso() is False


def test_inventariar_com_drive_fora_devolve_503(app, ids, client, monkeypatch):
    from app.services import manifestador_cofre

    monkeypatch.setattr(manifestador_cofre, 'rede_disponivel', lambda: False)
    resposta = client.post('/manifestador/cofre/inventariar')

    assert resposta.status_code == 503
    assert 'drive' in resposta.get_json()['message'].lower()


def test_senha_errada_e_recusada_e_nao_volta_no_json(app, ids, login_as,
                                                     monkeypatch):
    from app.services import manifestador_cofre

    with app.app_context():
        emp = _empresa(estado_cert=EstadoCertificado.SENHA_PENDENTE)
        empresa_id = emp.id

    monkeypatch.setattr(manifestador_cofre, 'gravar_senha',
                        lambda empresa, senha: False)
    resposta = login_as('admin').post(
        f'/manifestador/cofre/senha/{empresa_id}', json={'senha': 'Isa@2110'})

    assert resposta.status_code == 400
    assert 'Isa@2110' not in resposta.get_data(as_text=True)


def test_senha_certa_em_certificado_vencido_devolve_o_estado_vencido(
        app, ids, login_as, monkeypatch):
    """A senha e aceita e a pendencia CONTINUA — sao coisas diferentes.

    `gravar_senha` grava a senha e marca VENCIDO quando o .pfx abre mas ja
    passou da validade. A tela depende deste `estado` para dizer o que
    aconteceu: sem ele, a linha que fica na lista (com razao) parece defeito.
    """
    from app.services import manifestador_cofre

    with app.app_context():
        emp = _empresa(estado_cert=EstadoCertificado.SENHA_PENDENTE)
        empresa_id = emp.id

    def _grava_vencido(empresa, senha):
        empresa.certificado.estado = EstadoCertificado.VENCIDO
        db.session.commit()
        return True

    monkeypatch.setattr(manifestador_cofre, 'gravar_senha', _grava_vencido)
    resposta = login_as('admin').post(
        f'/manifestador/cofre/senha/{empresa_id}', json={'senha': 'Isa@2110'})

    assert resposta.status_code == 200
    assert resposta.get_json()['estado'] == 'vencido'


# --- importacao (MANIF-09, MANIF-11) ----------------------------------------

def test_importar_colagem_devolve_o_balanco_nomeado(app, ids, client):
    with app.app_context():
        empresa_id = _empresa().id

    resposta = client.post('/manifestador/importar', json={
        'empresa_id': empresa_id, 'texto': f'{CHAVE_A}\n{DV_ERRADO}\n{CHAVE_B}'})
    balanco = resposta.get_json()['balanco']

    assert balanco['aceitas'] == [CHAVE_A, CHAVE_B]
    assert balanco['dv_invalido'] == [DV_ERRADO]
    assert balanco['total_lidas'] == 3


def test_importar_sem_empresa_e_recusado(app, ids, client):
    resposta = client.post('/manifestador/importar',
                           json={'texto': CHAVE_A})
    assert resposta.status_code == 400
    assert 'empresa' in resposta.get_json()['message'].lower()


def test_importar_sem_texto_e_recusado(app, ids, client):
    with app.app_context():
        empresa_id = _empresa().id
    resposta = client.post('/manifestador/importar',
                           json={'empresa_id': empresa_id, 'texto': '  '})
    assert resposta.status_code == 400


def test_importar_xml_sem_arquivo_e_recusado(client):
    resposta = client.post('/manifestador/importar/xml', data={})
    assert resposta.status_code == 400


# --- lista e conferencia ----------------------------------------------------

def test_lista_filtra_por_empresa_competencia_e_status(app, ids, client):
    with app.app_context():
        emp_a = _empresa('A', '11.222.333/0001-81')
        emp_b = _empresa('B', '22.333.444/0001-92')
        _chave(emp_a, CHAVE_A, competencia='2017-01')
        _chave(emp_b, CHAVE_B, competencia='2017-01')
        empresa_id = emp_a.id

    dados = client.get(f'/manifestador/chaves?empresa_id={empresa_id}').get_json()
    assert [c['chave'] for c in dados['chaves']] == [CHAVE_A]

    dados = client.get('/manifestador/chaves?competencia=2017-99').get_json()
    assert dados['chaves'] == []


def test_filtro_da_lista_e_o_mesmo_que_o_lote_enfileira(app, ids, client):
    """Se divergissem, o lote manifestaria notas fora da tela."""
    with app.app_context():
        emp = _empresa()
        _chave(emp, CHAVE_A, competencia='2017-07')
        _chave(emp, CHAVE_B, competencia='2017-08')
        empresa_id = emp.id

        alvos = manifestador_lote.calcular_alvos(
            modo='empresa', empresa_id=empresa_id, competencia='2017-07')

    dados = client.get(
        f'/manifestador/chaves?empresa_id={empresa_id}&competencia=2017-07'
    ).get_json()

    assert [c['id'] for c in dados['chaves']] == alvos['ids']


def test_ajustar_competencia_aceita_e_marca(app, ids, client):
    with app.app_context():
        chave_id = _chave(_empresa()).id

    dados = client.post(f'/manifestador/chave/{chave_id}/competencia',
                        json={'competencia': '2017-02'}).get_json()

    assert dados['chave']['competencia'] == '2017-02'
    assert dados['chave']['competencia_ajustada'] is True


def test_competencia_invalida_e_recusada(app, ids, client):
    with app.app_context():
        chave_id = _chave(_empresa()).id

    resposta = client.post(f'/manifestador/chave/{chave_id}/competencia',
                           json={'competencia': '2017-13'})
    assert resposta.status_code == 400


def test_liberar_manifestada_exige_confirmacao_explicita(app, ids, client):
    with app.app_context():
        chave_id = _chave(_empresa(),
                          status=StatusManifestacao.MANIFESTADA).id

    resposta = client.post(f'/manifestador/chave/{chave_id}/liberar', json={})
    assert resposta.status_code == 409
    assert resposta.get_json()['motivo'] == 'confirmacao_necessaria'

    resposta = client.post(f'/manifestador/chave/{chave_id}/liberar',
                           json={'confirmar': True})
    assert resposta.status_code == 200
    assert resposta.get_json()['chave']['status'] == 'pendente'


def test_reprocessar_aceita_rejeitada_e_indefinida(app, ids, client):
    with app.app_context():
        emp = _empresa()
        rejeitada = _chave(emp, CHAVE_A, StatusManifestacao.REJEITADA).id
        indefinida = _chave(emp, CHAVE_B, StatusManifestacao.INDEFINIDA).id

    for chave_id in (rejeitada, indefinida):
        resposta = client.post(f'/manifestador/chave/{chave_id}/reprocessar')
        assert resposta.status_code == 200
        assert resposta.get_json()['chave']['status'] == 'pendente'


def test_reprocessar_preserva_cstat_para_contar_mesma_rejeicao(app, ids, client):
    with app.app_context():
        emp = _empresa()
        linha = _chave(emp, CHAVE_A, StatusManifestacao.REJEITADA)
        linha.cstat = '596'
        linha.xmotivo = 'Rejeição sintética'
        db.session.commit()
        chave_id = linha.id

    resposta = client.post(f'/manifestador/chave/{chave_id}/reprocessar')

    assert resposta.status_code == 200
    assert resposta.get_json()['chave']['status'] == 'pendente'
    assert resposta.get_json()['chave']['cstat'] == '596'


def test_reprocessar_recusa_manifestada(app, ids, client):
    """Fato fiscal consumado nao volta a fila por um clique de reprocessar."""
    with app.app_context():
        chave_id = _chave(_empresa(),
                          status=StatusManifestacao.MANIFESTADA).id

    resposta = client.post(f'/manifestador/chave/{chave_id}/reprocessar')
    assert resposta.status_code == 400


def test_recuperar_envio_interrompido_preserva_desfecho_desconhecido(app, ids, client):
    with app.app_context():
        chave_id = _chave(_empresa(), status=StatusManifestacao.ENVIANDO).id

    resposta = client.post(f'/manifestador/chave/{chave_id}/recuperar')

    assert resposta.status_code == 200
    assert resposta.get_json()['chave']['status'] == 'indefinida'
    with app.app_context():
        linha = db.session.get(ChaveManifestacao, chave_id)
        assert linha.status == StatusManifestacao.INDEFINIDA
        assert 'interrompido' in linha.xmotivo


def test_recuperar_envio_nao_reenvia_chave_que_nao_esta_enviando(app, ids, client):
    with app.app_context():
        chave_id = _chave(_empresa(), status=StatusManifestacao.PENDENTE).id

    resposta = client.post(f'/manifestador/chave/{chave_id}/recuperar')

    assert resposta.status_code == 400


def test_recuperar_envio_recusa_item_ainda_ativo(app, ids, client):
    with app.app_context():
        chave_id = _chave(_empresa(), status=StatusManifestacao.ENVIANDO).id
        with batch_state.MANIF_BATCH_LOCK:
            MANIF_BATCH_STATE['worker_active'] = True
            MANIF_BATCH_STATE['current_id'] = chave_id
    try:
        resposta = client.post(f'/manifestador/chave/{chave_id}/recuperar')
    finally:
        with batch_state.MANIF_BATCH_LOCK:
            MANIF_BATCH_STATE['worker_active'] = False
            MANIF_BATCH_STATE['current_id'] = None

    assert resposta.status_code == 409


def test_rotas_de_chave_inexistente_devolvem_404(client):
    assert client.post('/manifestador/chave/999999/competencia',
                       json={'competencia': '2017-01'}).status_code == 404
    assert client.post('/manifestador/chave/999999/liberar',
                       json={}).status_code == 404
    assert client.post('/manifestador/chave/999999/reprocessar').status_code == 404


def test_lista_expoe_ja_existia_para_a_tela_distinguir(app, ids, client):
    """cStat 573 fecha como `manifestada` — a nota JA estava manifestada na
    SEFAZ, e isso e desfecho bom. Mas sem `ja_existia` na resposta a tela
    mostrava a pilula verde "Manifestada" com "Rejeicao: Duplicidade de evento"
    logo abaixo, dizendo duas coisas opostas na mesma linha."""
    with app.app_context():
        emp = _empresa()
        linha = _chave(emp, status=StatusManifestacao.MANIFESTADA)
        linha.ja_existia = True
        linha.cstat = '573'
        linha.xmotivo = 'Rejeicao: Duplicidade de evento'
        db.session.commit()

    chave = client.get('/manifestador/chaves').get_json()['chaves'][0]

    assert chave['status'] == 'manifestada'
    assert chave['ja_existia'] is True
    assert chave['cstat'] == '573'


def test_manifestada_por_nos_nao_marca_ja_existia(app, ids, client):
    """O contrario do teste acima: sem a distincao, as duas situacoes ficariam
    indistinguiveis na tela."""
    with app.app_context():
        emp = _empresa()
        linha = _chave(emp, status=StatusManifestacao.MANIFESTADA)
        linha.cstat = '135'
        linha.protocolo = '143210000123456'
        db.session.commit()

    chave = client.get('/manifestador/chaves').get_json()['chaves'][0]

    assert chave['ja_existia'] is False
    assert chave['protocolo'] == '143210000123456'


# --- lote (MANIF-14) --------------------------------------------------------

def test_iniciar_exige_tipo_de_evento(app, ids, client):
    """Confirmacao da Operacao e irreversivel: nao sai por omissao."""
    resposta = client.post('/manifestador/lote/iniciar',
                           json={'modo': 'carteira'})
    assert resposta.status_code == 400
    assert 'omiss' in resposta.get_json()['message'].lower()


def test_iniciar_recusa_modo_desconhecido(client):
    resposta = client.post('/manifestador/lote/iniciar',
                           json={'modo': 'tudo', 'tipo_evento': '210200'})
    assert resposta.status_code == 400


def test_iniciar_individual_exige_selecao(client):
    resposta = client.post('/manifestador/lote/iniciar',
                           json={'modo': 'individual', 'tipo_evento': '210200'})
    assert resposta.status_code == 400


def test_iniciar_recusa_selecao_explicita_vazia(client):
    resposta = client.post('/manifestador/lote/iniciar', json={
        'modo': 'carteira', 'tipo_evento': '210200', 'chave_ids': [],
    })
    assert resposta.status_code == 400
    assert MANIF_BATCH_STATE['status'] == 'idle'


def test_iniciar_recusa_selecao_explicita_invalida_ou_repetida(client):
    for chave_ids in ([0, 2], [2, '3'], [2, 2]):
        resposta = client.post('/manifestador/lote/iniciar', json={
            'modo': 'carteira', 'tipo_evento': '210200',
            'chave_ids': chave_ids,
        })
        assert resposta.status_code == 400
        assert MANIF_BATCH_STATE['status'] == 'idle'


def test_iniciar_enfileira_somente_a_selecao_explicita(app, ids, client,
                                                       monkeypatch):
    with app.app_context():
        emp_a = _empresa('A', '11.222.333/0001-81', EstadoCertificado.PRONTO)
        emp_b = _empresa('B', '22.333.444/0001-92', EstadoCertificado.PRONTO)
        a_selecionada = _chave(emp_a, CHAVE_A)
        _chave(emp_a, CHAVE_B)
        b_selecionada = _chave(emp_b, '43170122333444000181650010000045751000045756')
        empresa_id = emp_a.id
        ids_selecionados = [b_selecionada.id, a_selecionada.id]
        ids_esperados = sorted(ids_selecionados)

    monkeypatch.setattr(manifestador_lote, 'worker', lambda app_obj: None)
    try:
        resposta = client.post('/manifestador/lote/iniciar', json={
            'modo': 'carteira', 'tipo_evento': '210200',
            'empresa_id': empresa_id, 'competencia': '1900-01',
            'chave_ids': ids_selecionados,
        })

        assert resposta.status_code == 200
        assert resposta.get_json()['total'] == 2
        assert MANIF_BATCH_STATE['ids'] == ids_esperados
    finally:
        batch_engine.reset_batch_state(MANIF_BATCH_STATE)


def test_selecao_unica_em_carteira_nao_grava_chave_individual(
        app, ids, client, monkeypatch):
    with app.app_context():
        emp = _empresa('A', '11.222.333/0001-81', EstadoCertificado.PRONTO)
        selecionada = _chave(emp, CHAVE_A)
        chave_id = selecionada.id

    monkeypatch.setattr(manifestador_lote, 'worker', lambda app_obj: None)
    try:
        resposta = client.post('/manifestador/lote/iniciar', json={
            'modo': 'carteira', 'tipo_evento': '210200',
            'chave_ids': [chave_id],
        })

        assert resposta.status_code == 200
        assert MANIF_BATCH_STATE['ids'] == [chave_id]
        assert manif_batch_opcoes()['modo'] == 'carteira'
        assert manif_batch_opcoes()['chave_id'] is None
    finally:
        batch_engine.reset_batch_state(MANIF_BATCH_STATE)
        definir_manif_opcoes(modo='empresa', tipo_evento='210200',
                             empresa_id=None, competencia=None, chave_id=None)


def test_iniciar_revalida_selecao_que_mudou_antes_da_admissao(
        app, ids, client, monkeypatch):
    with app.app_context():
        emp = _empresa('A', '11.222.333/0001-81', EstadoCertificado.PRONTO)
        continua = _chave(emp, CHAVE_A)
        ficou_terminal = _chave(emp, CHAVE_B)
        ficou_terminal.status = StatusManifestacao.MANIFESTADA
        db.session.commit()
        ids_selecionados = [continua.id, ficou_terminal.id]
        id_continua = continua.id

    monkeypatch.setattr(manifestador_lote, 'worker', lambda app_obj: None)
    try:
        resposta = client.post('/manifestador/lote/iniciar', json={
            'modo': 'carteira', 'tipo_evento': '210200',
            'chave_ids': ids_selecionados,
        })

        assert resposta.status_code == 200
        assert resposta.get_json()['total'] == 1
        assert MANIF_BATCH_STATE['ids'] == [id_continua]
    finally:
        batch_engine.reset_batch_state(MANIF_BATCH_STATE)


def test_iniciar_sem_inventario_do_cofre_e_recusado(app, ids, client):
    """Manifestar as cegas e exatamente o que o pre-voo existe para impedir."""
    with app.app_context():
        _chave(_empresa())

    resposta = client.post('/manifestador/lote/iniciar',
                           json={'modo': 'carteira', 'tipo_evento': '210200'})

    assert resposta.status_code == 409
    assert resposta.get_json()['motivo'] == 'cofre_vazio'


def test_iniciar_nomeia_as_empresas_puladas(app, ids, client, monkeypatch):
    """O operador ve na hora quem ficou de fora, em vez de descobrir no fim."""
    from app.services import manifestador_lote as lote_mod

    with app.app_context():
        ruim = _empresa('SEM CERT', '11.222.333/0001-81',
                        EstadoCertificado.VENCIDO)
        _chave(ruim, CHAVE_A)

    monkeypatch.setattr(lote_mod, 'worker', lambda app_obj: None)
    try:
        resposta = client.post('/manifestador/lote/iniciar',
                               json={'modo': 'carteira',
                                     'tipo_evento': '210200'})

        assert resposta.status_code == 200
        assert 'SEM CERT' in resposta.get_json()['empresas_puladas']
    finally:
        # O worker falso nao fecha o lote, entao o estado ficaria `running` no
        # processo — e MANIF_BATCH_STATE e global. Sem esta limpeza, o proximo
        # arquivo de teste a rodar neste worker do xdist receberia 409 "ja
        # existe manifestacao em andamento" e falharia longe daqui.
        batch_engine.reset_batch_state(MANIF_BATCH_STATE)


def test_status_do_lote_traz_modo_e_chave(client):
    dados = client.get('/manifestador/lote/status').get_json()['lote']
    assert 'modo' in dados
    assert 'chave_id' in dados
    assert 'tipo_evento' in dados


def test_retomar_sem_lote_pausado_devolve_409(client):
    assert client.post('/manifestador/lote/retomar').status_code == 409


def test_pausar_e_parar_sem_lote_sao_recusados(client):
    assert client.post('/manifestador/lote/pausar').status_code == 409
    assert client.post('/manifestador/lote/parar').status_code == 409


def test_inicios_concorrentes_preservam_opcoes_do_vencedor(
    app, ids, monkeypatch
):
    """O pedido recusado não pode trocar o evento ou a fila já admitidos."""
    with app.app_context():
        empresa_a = _empresa('EMPRESA A', '11.222.333/0001-81',
                             EstadoCertificado.PRONTO)
        empresa_b = _empresa('EMPRESA B', '22.333.444/0001-92',
                             EstadoCertificado.PRONTO)
        chave_a = _chave(empresa_a, CHAVE_A)
        chave_b = _chave(empresa_b, CHAVE_B)
        empresa_a_id = empresa_a.id
        chave_a_id = chave_a.id
        chave_b_id = chave_b.id

    payloads = [
        {
            'modo': 'carteira',
            'tipo_evento': manifestador_service.DESCONHECIMENTO,
        },
        {
            'modo': 'empresa',
            'tipo_evento': manifestador_service.CONFIRMACAO,
            'empresa_id': empresa_a_id,
            'competencia': '2017-01',
        },
    ]
    respostas = []
    chegada_admissao = Barrier(2)
    workers = []

    original_init = batch_engine.init_batch_run

    def init_sincronizado(*args, **kwargs):
        chegada_admissao.wait(timeout=5)
        return original_init(*args, **kwargs)

    def worker_falso(worker_fn, app_factory, on_finished=None):
        workers.append((worker_fn, app_factory, on_finished))

    monkeypatch.setattr(batch_engine, 'init_batch_run', init_sincronizado)
    monkeypatch.setattr(batch_engine, 'run_worker', worker_falso)
    monkeypatch.setattr(
        'app.services.manifestador_cofre.estado_da_carteira',
        lambda: {'prontas': 2},
    )

    def enviar(payload):
        with app.test_client() as cliente:
            cliente.post('/login', data={
                'username': 'admin_test',
                'senha': 'senha-admin-1',
            })
            resposta = cliente.post('/manifestador/lote/iniciar', json=payload)
            respostas.append((payload, resposta.status_code, resposta.get_json()))

    threads = [Thread(target=enviar, args=(payload,)) for payload in payloads]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        assert all(not thread.is_alive() for thread in threads)
        assert sorted(status for _payload, status, _corpo in respostas) == [200, 409]
        assert len(workers) == 1

        vencedor, _status_vencedor, corpo_vencedor = next(
            item for item in respostas if item[1] == 200
        )
        assert corpo_vencedor['tipo_evento'] == vencedor['tipo_evento']

        with app.app_context():
            assert batch_state.manif_batch_opcoes()['tipo_evento'] == (
                vencedor['tipo_evento'])
            assert MANIF_BATCH_STATE['scope'] == vencedor['modo']
            ids_aceitos = list(MANIF_BATCH_STATE['ids'])
            esperado = ([chave_a_id, chave_b_id]
                        if vencedor['modo'] == 'carteira'
                        else [chave_a_id])
            assert ids_aceitos == esperado

            chamadas = []

            def manifestar_falso(chave_id, tipo_evento=None, **_kwargs):
                chamadas.append((chave_id, tipo_evento))
                return manifestador_service.Resultado(True, 'Manifestada.')

            monkeypatch.setattr(manifestador_lote, 'manifestar', manifestar_falso)
            for chave_id in ids_aceitos:
                manifestador_lote._manifestar_item(
                    chave_id, None, MANIF_BATCH_STATE['execution_id'])

            assert [tipo for _chave_id, tipo in chamadas] == [
                vencedor['tipo_evento'] for _ in ids_aceitos]
    finally:
        batch_engine.reset_batch_state(MANIF_BATCH_STATE)
