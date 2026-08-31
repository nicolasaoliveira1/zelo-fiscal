"""Rotas do extrato do Inter: import de PDF, descricao, agrupamento e cancelar.

O PDF da fixture e gerado (`tests/fixtures/extrato_inter.py`) com as coordenadas
medidas no extrato real do banco.
"""
import io
import os
import sys

from app import db
from app.models import Empresa, NotaNfse, ServicoNfse, StatusNotaNfse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'fixtures'))
import extrato_inter as fixture_pdf  # noqa: E402

PDF = fixture_pdf.gerar(None)


def _importar_pdf(client, nome='Contas & Extratos.pdf'):
    return client.post(
        '/nfse/importar',
        data={'arquivo': (io.BytesIO(PDF), nome)},
        content_type='multipart/form-data')


def _notas(resposta):
    return resposta.get_json()['notas']


def _acha(notas, trecho):
    return next(n for n in notas if trecho.upper() in (n['nome_csv'] or ''))


def _empresa(app, nome, cnpj):
    with app.app_context():
        empresa = Empresa(nome=nome, cnpj=cnpj, cidade='Imbé', estado='RS')
        db.session.add(empresa)
        db.session.commit()
        return empresa.id


# --- import ----------------------------------------------------------------

def test_importa_pdf_pela_mesma_rota_do_csv(client):
    resposta = _importar_pdf(client)
    assert resposta.status_code == 200

    corpo = resposta.get_json()
    assert corpo['status'] == 'ok'
    assert len(corpo['notas']) == 6
    assert all(n['origem_extrato'] == 'inter' for n in corpo['notas'])


def test_resumo_traz_as_categorias(client):
    corpo = _importar_pdf(client).get_json()
    categorias = corpo['resumo']['por_categoria']

    assert categorias['honorarios'] == 3      # Alfa, Beta, Delta
    assert categorias['servico'] == 2         # ALT. CONTRATO e BAIXA
    assert categorias['indefinida'] == 1      # so o nome, sem competencia
    assert corpo['resumo']['grupos_pendentes'] == 1


def test_nota_traz_a_descricao_que_ira_para_o_portal(client):
    notas = _notas(_importar_pdf(client))

    alfa = _acha(notas, 'ALFA')
    assert alfa['categoria'] == 'honorarios'
    assert '06/2026' in alfa['descricao_prevista']

    epsilon = _acha(notas, 'EPSILON')
    assert epsilon['categoria'] == 'servico'
    assert epsilon['descricao_prevista'] == 'BAIXA DE EMPRESA'
    # a descricao crua do Pix continua visivel para conferencia
    assert 'baixa Epsilon' in epsilon['descricao_extrato']


def test_arquivo_que_nao_e_extrato_e_recusado_com_o_nome(client):
    resposta = client.post(
        '/nfse/importar',
        data={'arquivo': (io.BytesIO(b'%PDF-1.4 nao e extrato'), 'boleto.pdf')},
        content_type='multipart/form-data')
    assert resposta.status_code == 400
    assert 'boleto.pdf' in resposta.get_json()['message']


# --- resolver a descricao --------------------------------------------------

def test_resolve_descricao_como_servico_e_memoriza(client, app):
    notas = _notas(_importar_pdf(client))
    pendente = _acha(notas, 'GAMA SAUDE PRODUTOS')
    assert pendente['categoria'] == 'indefinida'
    assert pendente['descricao_prevista'] is None

    resposta = client.post(f'/nfse/nota/{pendente["id"]}/descricao',
                           json={'descricao_servico': 'CONSULTORIA TRIBUTARIA'})
    assert resposta.status_code == 200

    nota = resposta.get_json()['nota']
    assert nota['categoria'] == 'servico'
    assert nota['descricao_prevista'] == 'CONSULTORIA TRIBUTARIA'

    with app.app_context():
        memoria = ServicoNfse.query.one()
        assert memoria.descricao == 'CONSULTORIA TRIBUTARIA'
        # chave sem o prefixo 'Pix': a mesma forma que a busca do import usa
        assert memoria.termo_norm == 'GAMA SAUDE PRODUTOS LTDA'


def test_resolve_descricao_como_honorarios_de_uma_competencia(client):
    notas = _notas(_importar_pdf(client))
    pendente = _acha(notas, 'GAMA SAUDE PRODUTOS')

    resposta = client.post(f'/nfse/nota/{pendente["id"]}/descricao',
                           json={'competencia': '05/2026'})
    nota = resposta.get_json()['nota']

    assert nota['categoria'] == 'honorarios'
    assert nota['competencia'] == '05/2026'
    assert '05/2026' in nota['descricao_prevista']


def test_servico_e_competencia_juntos_valem_os_dois(client):
    """Os dois campos sao eixos independentes: o servico e o TEXTO, a
    competencia e o MES. Travar um contra o outro obrigava a salvar duas vezes,
    e no meio do caminho a nota exibia uma descricao que ninguem queria."""
    notas = _notas(_importar_pdf(client))
    pendente = _acha(notas, 'GAMA SAUDE PRODUTOS')

    resposta = client.post(f'/nfse/nota/{pendente["id"]}/descricao',
                           json={'competencia': '05/2026',
                                 'descricao_servico': 'ALTERAÇÃO CONTRATUAL'})
    assert resposta.status_code == 200

    nota = resposta.get_json()['nota']
    assert nota['competencia'] == '05/2026'
    # o texto e o servico, e NAO recebe o mes: quem descreve uma alteracao
    # contratual nao diz "referente ao mes de"
    assert nota['descricao_prevista'] == 'ALTERAÇÃO CONTRATUAL'


def test_so_servico_nao_mexe_na_competencia(client):
    notas = _notas(_importar_pdf(client))
    alfa = _acha(notas, 'ALFA')
    assert alfa['competencia'] == '06/2026'

    nota = client.post(f'/nfse/nota/{alfa["id"]}/descricao',
                       json={'descricao_servico': 'BAIXA DE EMPRESA'}).get_json()['nota']
    assert nota['competencia'] == '06/2026'
    assert nota['descricao_prevista'] == 'BAIXA DE EMPRESA'


def test_limpar_o_servico_devolve_a_nota_a_honorarios(client):
    """Campo de servico em branco significa 'nao e servico avulso'."""
    notas = _notas(_importar_pdf(client))
    epsilon = _acha(notas, 'EPSILON')
    assert epsilon['categoria'] == 'servico'

    nota = client.post(f'/nfse/nota/{epsilon["id"]}/descricao',
                       json={'competencia': '05/2026'}).get_json()['nota']
    assert nota['categoria'] == 'honorarios'
    assert nota['competencia'] == '05/2026'
    assert '05/2026' in nota['descricao_prevista']


def test_ida_e_volta_entre_servico_e_honorarios_nao_exige_dois_saves(client):
    """O vaivem que a regra antiga criava: trocar de honorarios para servico
    obrigava a salvar so o mes, depois reabrir e salvar so o texto."""
    notas = _notas(_importar_pdf(client))
    pendente = _acha(notas, 'GAMA SAUDE PRODUTOS')
    url = f'/nfse/nota/{pendente["id"]}/descricao'

    nota = client.post(url, json={'competencia': '05/2026'}).get_json()['nota']
    assert nota['categoria'] == 'honorarios'

    # UMA chamada troca as duas coisas de uma vez
    nota = client.post(url, json={'competencia': '06/2026',
                                  'descricao_servico': 'ALTERAÇÃO CONTRATUAL'}).get_json()['nota']
    assert nota['categoria'] == 'servico'
    assert nota['competencia'] == '06/2026'
    assert nota['descricao_prevista'] == 'ALTERAÇÃO CONTRATUAL'


def test_descricao_exige_ao_menos_um_dos_campos(client):
    notas = _notas(_importar_pdf(client))
    pendente = _acha(notas, 'GAMA SAUDE PRODUTOS')
    resposta = client.post(f'/nfse/nota/{pendente["id"]}/descricao', json={})
    assert resposta.status_code == 400


def test_descricao_recusa_competencia_fora_do_formato(client):
    notas = _notas(_importar_pdf(client))
    pendente = _acha(notas, 'GAMA SAUDE PRODUTOS')

    resposta = client.post(f'/nfse/nota/{pendente["id"]}/descricao',
                           json={'competencia': '13/2026'})
    assert resposta.status_code == 400
    assert 'MM/AAAA' in resposta.get_json()['message']


def test_descricao_nao_muda_depois_de_ir_ao_portal(client, app):
    notas = _notas(_importar_pdf(client))
    pendente = _acha(notas, 'GAMA SAUDE PRODUTOS')
    with app.app_context():
        nota = db.session.get(NotaNfse, pendente['id'])
        nota.status = StatusNotaNfse.EMITIDA
        db.session.commit()

    resposta = client.post(f'/nfse/nota/{pendente["id"]}/descricao',
                           json={'descricao_servico': 'QUALQUER'})
    assert resposta.status_code == 409


# --- proposta de agrupamento -----------------------------------------------

def test_confirmar_o_grupo_pela_rota(client, app):
    _empresa(app, 'GAMA SAUDE', '22.222.222/0001-22')
    notas = _notas(_importar_pdf(client))

    lider = next(n for n in notas if n['grupo'] and n['grupo']['lider'])
    assert lider['grupo']['valor_liquido'] == '900,00'
    assert '1.784,00' in lider['grupo']['detalhe']

    resposta = client.post(f'/nfse/grupo/{lider["grupo"]["token"]}/confirmar')
    assert resposta.status_code == 200

    nota = resposta.get_json()['nota']
    assert nota['valor'] == '900,00'
    assert nota['descricao_prevista'] == 'ALTERAÇÃO CONTRATUAL'
    # o grupo SOBREVIVE ao confirmar — e o que torna o desfazer possivel —,
    # mas sai do estado de proposta pendente
    assert nota['grupo']['confirmado'] is True
    assert nota['grupo']['pendente'] is False


def test_confirmar_com_descricao_escolhida(client, app):
    _empresa(app, 'GAMA SAUDE', '22.222.222/0001-22')
    notas = _notas(_importar_pdf(client))
    grupo = next(n['grupo'] for n in notas if n['grupo'] and n['grupo']['lider'])
    # a sugestao vem do servico escrito em alguma das linhas do grupo
    assert grupo['descricao'] == 'ALTERAÇÃO CONTRATUAL'

    resposta = client.post(f'/nfse/grupo/{grupo["token"]}/confirmar',
                           json={'descricao': 'ALTERAÇÃO CONTRATUAL E ADITIVO'})
    nota = resposta.get_json()['nota']
    assert nota['descricao_prevista'] == 'ALTERAÇÃO CONTRATUAL E ADITIVO'


def test_desfazer_devolve_tudo_ao_que_era(client, app):
    _empresa(app, 'GAMA SAUDE', '22.222.222/0001-22')
    notas = _notas(_importar_pdf(client))
    antes = {n['id']: n for n in notas if n['grupo']}
    token = next(n['grupo']['token'] for n in antes.values())

    client.post(f'/nfse/grupo/{token}/confirmar', json={'valor': '950,00'})
    resposta = client.post(f'/nfse/grupo/{token}/desfazer')
    assert resposta.status_code == 200

    lider = resposta.get_json()['nota']
    # valor volta ao do EXTRATO, nao ao liquido nem ao ajustado
    assert lider['valor'] == antes[lider['id']]['valor']
    assert lider['valor_ajustado'] is False
    # e a proposta volta a esperar resposta, nao a descartada: desfazer nao e recusar
    assert lider['grupo']['pendente'] is True
    assert lider['grupo']['confirmado'] is False

    depois = {n['id']: n for n in _notas_de(client)}
    for nota_id, era in antes.items():
        assert depois[nota_id]['status'] == era['status']
        assert depois[nota_id]['valor'] == era['valor']


def test_desfazer_preserva_descricao_que_o_operador_digitou(client, app):
    """Re-deduzir a descricao do extrato descartaria o que ele digitou."""
    _empresa(app, 'GAMA SAUDE', '22.222.222/0001-22')
    notas = _notas(_importar_pdf(client))
    lider = next(n for n in notas if n['grupo'] and n['grupo']['lider'])

    client.post(f'/nfse/nota/{lider["id"]}/descricao',
                json={'descricao_servico': 'ASSESSORIA SOCIETARIA'})
    client.post(f'/nfse/grupo/{lider["grupo"]["token"]}/confirmar',
                json={'descricao': 'OUTRA COISA'})
    resposta = client.post(f'/nfse/grupo/{lider["grupo"]["token"]}/desfazer')

    assert resposta.get_json()['nota']['descricao_prevista'] == 'ASSESSORIA SOCIETARIA'


def test_desfazer_o_que_nao_foi_agrupado_e_recusado(client, app):
    _empresa(app, 'GAMA SAUDE', '22.222.222/0001-22')
    notas = _notas(_importar_pdf(client))
    token = next(n['grupo']['token'] for n in notas if n['grupo'])

    resposta = client.post(f'/nfse/grupo/{token}/desfazer')
    assert resposta.status_code == 409


def test_confirmar_duas_vezes_e_recusado(client, app):
    _empresa(app, 'GAMA SAUDE', '22.222.222/0001-22')
    notas = _notas(_importar_pdf(client))
    token = next(n['grupo']['token'] for n in notas if n['grupo'])

    assert client.post(f'/nfse/grupo/{token}/confirmar').status_code == 200
    assert client.post(f'/nfse/grupo/{token}/confirmar').status_code == 400


def test_falha_no_recalculo_do_grupo_nao_vira_sucesso(client, monkeypatch):
    def falhar(*_args, **_kwargs):
        raise RuntimeError('falha sintética no recálculo')

    monkeypatch.setattr('app.routes.nfse.nfse_grupos.confirmar', falhar)

    resposta = client.post('/nfse/grupo/grupo-sintetico/confirmar')

    assert resposta.status_code == 500
    assert resposta.get_json()['status'] == 'error'


def _notas_de(client):
    return client.get('/nfse/notas').get_json()['notas']


# --- acao em massa ---------------------------------------------------------

def test_cancelar_varias_linhas_de_uma_vez(client):
    notas = _notas(_importar_pdf(client))
    ids = [n['id'] for n in notas[:3]]

    resposta = client.post('/nfse/notas/acao',
                           json={'acao': 'cancelar', 'ids': ids})
    assert resposta.status_code == 200

    corpo = resposta.get_json()
    assert len(corpo['aplicadas']) == 3
    assert corpo['recusadas'] == []
    assert all(n['status'] == StatusNotaNfse.CANCELADA for n in corpo['aplicadas'])


def test_restaurar_varias_linhas_de_uma_vez(client):
    notas = _notas(_importar_pdf(client))
    ids = [n['id'] for n in notas[:3]]
    client.post('/nfse/notas/acao', json={'acao': 'cancelar', 'ids': ids})

    corpo = client.post('/nfse/notas/acao',
                        json={'acao': 'restaurar', 'ids': ids}).get_json()
    assert len(corpo['aplicadas']) == 3
    assert not any(n['status'] == StatusNotaNfse.CANCELADA for n in corpo['aplicadas'])


def test_acao_em_massa_e_parcial_e_nomeia_o_que_recusou(client, app):
    """Uma linha emitida no meio da selecao nao pode abortar as outras."""
    notas = _notas(_importar_pdf(client))
    with app.app_context():
        nota = db.session.get(NotaNfse, notas[0]['id'])
        nota.status = StatusNotaNfse.EMITIDA
        db.session.commit()

    corpo = client.post('/nfse/notas/acao', json={
        'acao': 'cancelar', 'ids': [n['id'] for n in notas[:3]]}).get_json()

    assert len(corpo['aplicadas']) == 2
    assert len(corpo['recusadas']) == 1
    assert corpo['recusadas'][0]['id'] == notas[0]['id']
    assert 'prefeitura' in corpo['recusadas'][0]['motivo']


def test_marcar_varias_como_ja_emitidas(client):
    """Nao e emitir: e registrar o que o operador ja emitiu no portal por fora."""
    notas = _notas(_importar_pdf(client))
    ids = [n['id'] for n in notas[:3]]

    corpo = client.post('/nfse/notas/acao',
                        json={'acao': 'emitida_manual', 'ids': ids}).get_json()

    assert len(corpo['aplicadas']) == 3
    assert corpo['recusadas'] == []
    assert all(n['status'] == StatusNotaNfse.EMITIDA for n in corpo['aplicadas'])
    assert all(n['origem_emissao'] == 'manual' for n in corpo['aplicadas'])


def test_desmarcar_varias_de_uma_vez(client):
    notas = _notas(_importar_pdf(client))
    ids = [n['id'] for n in notas[:3]]
    client.post('/nfse/notas/acao', json={'acao': 'emitida_manual', 'ids': ids})

    corpo = client.post('/nfse/notas/acao',
                        json={'acao': 'desmarcar_emitida', 'ids': ids}).get_json()
    assert len(corpo['aplicadas']) == 3
    assert not any(n['status'] == StatusNotaNfse.EMITIDA for n in corpo['aplicadas'])
    assert all(n['origem_emissao'] is None for n in corpo['aplicadas'])


def test_massa_nao_marca_o_que_a_automacao_emitiu(client, app):
    """A automacao VIU a emissao acontecer; desmarcar afirmaria que a nota
    fiscal nao existe. A acao em massa nao pode furar a regra da individual."""
    notas = _notas(_importar_pdf(client))
    with app.app_context():
        nota = db.session.get(NotaNfse, notas[0]['id'])
        nota.status = StatusNotaNfse.EMITIDA
        nota.origem_emissao = 'automacao'
        db.session.commit()

    corpo = client.post('/nfse/notas/acao', json={
        'acao': 'emitida_manual', 'ids': [n['id'] for n in notas[:2]]}).get_json()
    assert len(corpo['recusadas']) == 1
    assert 'automação' in corpo['recusadas'][0]['motivo']

    corpo = client.post('/nfse/notas/acao', json={
        'acao': 'desmarcar_emitida', 'ids': [notas[0]['id']]}).get_json()
    assert len(corpo['recusadas']) == 1


def test_massa_nao_marca_linha_agrupada_em_outra(client, app):
    _empresa(app, 'GAMA SAUDE', '22.222.222/0001-22')
    notas = _notas(_importar_pdf(client))
    token = next(n['grupo']['token'] for n in notas if n['grupo'])
    client.post(f'/nfse/grupo/{token}/confirmar')

    absorvida = next(n for n in _notas_de(client)
                     if n['status'] == StatusNotaNfse.AGRUPADA)
    corpo = client.post('/nfse/notas/acao', json={
        'acao': 'emitida_manual', 'ids': [absorvida['id']]}).get_json()

    assert corpo['aplicadas'] == []
    assert 'absorveu' in corpo['recusadas'][0]['motivo']


def test_marcar_em_massa_conta_na_trava_de_duplicidade(client, app):
    """Marcar como emitida ocupa a competencia, igual a rota individual."""
    _empresa(app, 'ALFA COMERCIO LTDA', '11.111.111/0001-11')
    notas = _notas(_importar_pdf(client))
    alfa = _acha(notas, 'ALFA')
    client.post('/nfse/notas/acao',
                json={'acao': 'emitida_manual', 'ids': [alfa['id']]})

    de_novo = _acha(_notas(_importar_pdf(client)), 'ALFA')
    assert de_novo['status'] == StatusNotaNfse.DUPLICATA


def test_acao_em_massa_recusa_acao_desconhecida(client):
    notas = _notas(_importar_pdf(client))
    resposta = client.post('/nfse/notas/acao',
                           json={'acao': 'emitir', 'ids': [notas[0]['id']]})
    assert resposta.status_code == 400


def test_acao_em_massa_exige_selecao(client):
    _importar_pdf(client)
    assert client.post('/nfse/notas/acao',
                       json={'acao': 'cancelar', 'ids': []}).status_code == 400


def test_confirmar_com_valor_corrigido(client, app):
    _empresa(app, 'GAMA SAUDE', '22.222.222/0001-22')
    notas = _notas(_importar_pdf(client))
    token = next(n['grupo']['token'] for n in notas if n['grupo'])

    resposta = client.post(f'/nfse/grupo/{token}/confirmar', json={'valor': '950,00'})
    nota = resposta.get_json()['nota']
    assert nota['valor'] == '950,00'
    assert nota['valor_ajustado'] is True


def test_confirmar_recusa_valor_ilegivel(client, app):
    _empresa(app, 'GAMA SAUDE', '22.222.222/0001-22')
    notas = _notas(_importar_pdf(client))
    token = next(n['grupo']['token'] for n in notas if n['grupo'])

    resposta = client.post(f'/nfse/grupo/{token}/confirmar', json={'valor': 'novecentos'})
    assert resposta.status_code == 400


def test_descartar_o_grupo_pela_rota(client, app):
    _empresa(app, 'GAMA SAUDE', '22.222.222/0001-22')
    notas = _notas(_importar_pdf(client))
    token = next(n['grupo']['token'] for n in notas if n['grupo'])

    resposta = client.post(f'/nfse/grupo/{token}/descartar')
    assert resposta.status_code == 200

    devolvidas = resposta.get_json()['notas']
    assert len(devolvidas) == 2
    assert all(n['grupo'] is None for n in devolvidas)
    assert {n['valor'] for n in devolvidas} == {'684,00', '2000,00'}


def test_grupo_inexistente_devolve_404(client):
    assert client.post('/nfse/grupo/naoexiste/confirmar').status_code == 404
    assert client.post('/nfse/grupo/naoexiste/descartar').status_code == 404


# --- cancelar a linha ------------------------------------------------------

def test_cancelar_e_descancelar_a_linha(client, app):
    _empresa(app, 'ALFA COMERCIO LTDA', '11.111.111/0001-11')
    notas = _notas(_importar_pdf(client))
    alfa = _acha(notas, 'ALFA')
    assert alfa['status'] == StatusNotaNfse.PRONTA

    cancelada = client.post(f'/nfse/nota/{alfa["id"]}/cancelar').get_json()['nota']
    assert cancelada['status'] == StatusNotaNfse.CANCELADA

    voltou = client.post(f'/nfse/nota/{alfa["id"]}/cancelar',
                         json={'cancelar': False}).get_json()['nota']
    assert voltou['status'] == StatusNotaNfse.PRONTA


def test_cancelar_nao_pode_desfazer_nota_ja_emitida(client, app):
    notas = _notas(_importar_pdf(client))
    alfa = _acha(notas, 'ALFA')
    with app.app_context():
        nota = db.session.get(NotaNfse, alfa['id'])
        nota.status = StatusNotaNfse.EMITIDA
        db.session.commit()

    resposta = client.post(f'/nfse/nota/{alfa["id"]}/cancelar')
    assert resposta.status_code == 409
    assert 'prefeitura' in resposta.get_json()['message']


def test_descancelar_o_que_nao_esta_cancelado_e_recusado(client):
    notas = _notas(_importar_pdf(client))
    alfa = _acha(notas, 'ALFA')
    resposta = client.post(f'/nfse/nota/{alfa["id"]}/cancelar', json={'cancelar': False})
    assert resposta.status_code == 409


def test_cancelar_tira_a_linha_da_fila(client, app):
    from app.services import nfse_service

    _empresa(app, 'ALFA COMERCIO LTDA', '11.111.111/0001-11')
    notas = _notas(_importar_pdf(client))
    alfa = _acha(notas, 'ALFA')
    client.post(f'/nfse/nota/{alfa["id"]}/cancelar')

    with app.app_context():
        nota = db.session.get(NotaNfse, alfa['id'])
        assert nfse_service.emitivel(nota) is False


# --- autorizacao -----------------------------------------------------------

def test_rotas_novas_exigem_operador(login_as):
    leitura = login_as('leitura')
    assert leitura.post('/nfse/nota/1/descricao', json={}).status_code == 403
    assert leitura.post('/nfse/nota/1/cancelar', json={}).status_code == 403
    assert leitura.post('/nfse/grupo/x/confirmar').status_code == 403
    assert leitura.post('/nfse/grupo/x/descartar').status_code == 403
