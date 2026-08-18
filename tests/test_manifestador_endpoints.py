"""Rotas do manifestador (MANIF-09/11/14/21).

Rotas finas: o que se prova aqui e autorizacao, validacao de entrada e o
contrato da resposta — nao a regra de negocio, que tem testes proprios.

Um teste em especial vale por muitos: o filtro da lista tem de ser EXATAMENTE o
que o lote enfileira. Divergir os dois manifestaria notas que o operador nao
esta olhando.
"""
from app import db
from app.models import (
    CertificadoEmpresa,
    ChaveManifestacao,
    Empresa,
    EstadoCertificado,
    StatusManifestacao,
)
from app.automation.batch_state import MANIF_BATCH_STATE
from app.services import batch_engine, manifestador_lote

CHAVE_A = '43170107461248000107650010000045391000045390'
CHAVE_B = '43170107461248000107650010000045401000045404'
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
        emp = _empresa('EDOO', '11.222.333/0001-81')
        emp.certificado = CertificadoEmpresa(
            caminho=r'Z:\EDOO\CERTIFICADO SENHA 042026\edoo.pfx',
            estado=EstadoCertificado.SENHA_PENDENTE)
        db.session.commit()

    problemas = client.get('/manifestador/cofre').get_json()['problemas']

    assert problemas[0]['sugestao_senha'] == '042026'


def test_cofre_sem_inventario_diz_que_nao_foi_inventariado(app, ids, client):
    assert client.get('/manifestador/cofre').get_json()['inventariado'] is False


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


def test_reprocessar_recusa_manifestada(app, ids, client):
    """Fato fiscal consumado nao volta a fila por um clique de reprocessar."""
    with app.app_context():
        chave_id = _chave(_empresa(),
                          status=StatusManifestacao.MANIFESTADA).id

    resposta = client.post(f'/manifestador/chave/{chave_id}/reprocessar')
    assert resposta.status_code == 400


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


def test_iniciar_individual_exige_a_chave(client):
    resposta = client.post('/manifestador/lote/iniciar',
                           json={'modo': 'individual', 'tipo_evento': '210200'})
    assert resposta.status_code == 400


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


def test_pausar_e_parar_respondem_ok(client):
    assert client.post('/manifestador/lote/pausar').status_code == 200
    assert client.post('/manifestador/lote/parar').status_code == 200
