"""Rotas de importacao, resolucao e configuracao da NFSe (NFSE-01..09, 17, 22).

O fixture `ids` (que o `client`/`login_as` puxam) ja cria o schema e semeia uma
Empresa 'Empresa Teste' em Tramandai — usada aqui como alvo de vinculo manual.
"""
import io
from datetime import datetime
from decimal import Decimal

import pytest

from app import db
from app.models import ApelidoNfse, Empresa, LoteNfse, NotaNfse, StatusNotaNfse

LINHA = ('"13/07/2026";"{nome}";"0001443038";"062623";"05/07/2026";'
         '"811,00";"16,22";"1,13";"826,09";"COBRANCA SIMPLES"')


def _csv(*nomes):
    corpo = '\n'.join(LINHA.format(nome=n) for n in nomes)
    return {'arquivo': (io.BytesIO(corpo.encode('utf-8')), 'extrato.csv')}


def _importar(client, *nomes):
    return client.post('/nfse/importar', data=_csv(*nomes),
                       content_type='multipart/form-data')


# --- autorizacao (AD-005) --------------------------------------------------

@pytest.mark.parametrize('rota,metodo', [
    ('/nfse', 'get'),
    ('/nfse/importar', 'post'),
    ('/nfse/configuracao', 'post'),
])
def test_papel_leitura_nao_acessa(login_as, rota, metodo):
    resposta = getattr(login_as('leitura'), metodo)(rota)
    assert resposta.status_code == 403


def test_anonimo_e_barrado(client_anon):
    resposta = client_anon.get('/nfse')
    assert resposta.status_code in (302, 401, 403)


def test_operador_abre_a_pagina(login_as):
    assert login_as('operador').get('/nfse').status_code == 200


# --- importacao ------------------------------------------------------------

def test_importa_e_devolve_notas_e_resumo(client):
    resposta = _importar(client, 'EMPRESA TESTE LTDA')
    assert resposta.status_code == 200
    dados = resposta.get_json()
    assert dados['status'] == 'ok'
    assert len(dados['notas']) == 1
    assert dados['resumo']['total'] == 1
    assert dados['notas'][0]['competencia'] == '06/2026'
    assert dados['notas'][0]['valor'] == '826,09'


def test_import_sem_arquivo_devolve_400_com_envelope(client):
    resposta = client.post('/nfse/importar', data={},
                           content_type='multipart/form-data')
    assert resposta.status_code == 400
    dados = resposta.get_json()
    assert dados['status'] == 'error'
    assert dados['request_id'] is not None
    assert dados['message']


def test_import_de_arquivo_que_nao_e_o_extrato_devolve_400(client):
    dados = {'arquivo': (io.BytesIO(b'nome,valor\nFulano,10\n'), 'outro.csv')}
    resposta = client.post('/nfse/importar', data=dados,
                           content_type='multipart/form-data')
    assert resposta.status_code == 400
    assert 'colunas' in resposta.get_json()['message'].lower()


def test_import_de_arquivo_vazio_devolve_400(client):
    dados = {'arquivo': (io.BytesIO(b''), 'vazio.csv')}
    resposta = client.post('/nfse/importar', data=dados,
                           content_type='multipart/form-data')
    assert resposta.status_code == 400


def test_nome_desconhecido_volta_como_pendente(client):
    resposta = _importar(client, 'NINGUEM CONHECE ESSA SA')
    assert resposta.get_json()['notas'][0]['status'] == StatusNotaNfse.EMPRESA_PENDENTE


# --- resolucao manual da empresa (NFSE-22) ---------------------------------

def _primeira_nota():
    return NotaNfse.query.order_by(NotaNfse.id).first()


def test_vincular_por_empresa_salva_apelido(client, app):
    _importar(client, 'NOME ESTRANHO DO BANCO LTDA')
    with app.app_context():
        nota = _primeira_nota()
        empresa = Empresa.query.first()
        nota_id, empresa_id, norm = nota.id, empresa.id, nota.nome_csv_norm

    resposta = client.post(f'/nfse/nota/{nota_id}/resolver',
                           json={'empresa_id': empresa_id})
    assert resposta.status_code == 200
    assert resposta.get_json()['nota']['status'] == StatusNotaNfse.PRONTA

    with app.app_context():
        assert ApelidoNfse.query.filter_by(nome_norm=norm).first() is not None


def test_cnpj_invalido_e_recusado(client, app):
    _importar(client, 'OUTRO NOME QUALQUER LTDA')
    with app.app_context():
        nota_id = _primeira_nota().id
    resposta = client.post(f'/nfse/nota/{nota_id}/resolver',
                           json={'cnpj': '11.111.111/1111-11'})
    assert resposta.status_code == 400
    assert 'digito' in resposta.get_json()['message'].lower()


def test_cnpj_valido_de_empresa_nao_cadastrada_fica_cadastro_pendente(client, app):
    _importar(client, 'NOME QUE NAO EXISTE LTDA')
    with app.app_context():
        nota_id = _primeira_nota().id
    resposta = client.post(f'/nfse/nota/{nota_id}/resolver',
                           json={'cnpj': '44.556.677/0001-86'})
    assert resposta.status_code == 200
    assert resposta.get_json()['nota']['status'] == StatusNotaNfse.CADASTRO_PENDENTE


def test_resolver_sem_empresa_nem_cnpj_devolve_400(client, app):
    _importar(client, 'QUALQUER COISA LTDA')
    with app.app_context():
        nota_id = _primeira_nota().id
    assert client.post(f'/nfse/nota/{nota_id}/resolver', json={}).status_code == 400


def test_resolver_nota_inexistente_devolve_404(client):
    assert client.post('/nfse/nota/99999/resolver', json={'cnpj': 'x'}).status_code == 404


# --- duplicata --------------------------------------------------------------

def test_liberar_duplicata(client, app):
    _importar(client, 'EMPRESA TESTE LTDA')
    with app.app_context():
        nota = _primeira_nota()
        nota.status = StatusNotaNfse.DUPLICATA
        db.session.commit()
        nota_id = nota.id

    resposta = client.post(f'/nfse/nota/{nota_id}/liberar-duplicata')
    assert resposta.status_code == 200
    assert resposta.get_json()['nota']['duplicata_liberada'] is True


def test_liberar_o_que_nao_e_duplicata_devolve_400(client, app):
    _importar(client, 'EMPRESA TESTE LTDA')
    with app.app_context():
        nota_id = _primeira_nota().id
    assert client.post(f'/nfse/nota/{nota_id}/liberar-duplicata').status_code == 400


# --- configuracao -----------------------------------------------------------

def test_salvar_configuracao(client):
    resposta = client.post('/nfse/configuracao', json={'codigo_tributacao': '17.19.02'})
    assert resposta.status_code == 200
    assert resposta.get_json()['config']['codigo_tributacao'] == '17.19.02'


def test_template_sem_placeholder_devolve_400_apontando_o_campo(client):
    resposta = client.post('/nfse/configuracao',
                           json={'descricao_template': 'SEM PLACEHOLDER'})
    assert resposta.status_code == 400
    assert resposta.get_json()['campo'] == 'descricao_template'


# --- cadastro pre-preenchido (NFSE-23) -------------------------------------

def test_nova_empresa_aceita_nome_e_cnpj_da_querystring(login_as):
    resposta = login_as('operador').get(
        '/empresa/nova?nome=ACME+TRANSPORTES&cnpj=44.556.677/0001-86')
    assert resposta.status_code == 200
    corpo = resposta.get_data(as_text=True)
    assert 'ACME TRANSPORTES' in corpo
    assert '44.556.677/0001-86' in corpo


def test_nova_empresa_sem_querystring_continua_igual(login_as):
    """Regressao: a rota existia antes e nao pode mudar de comportamento."""
    resposta = login_as('operador').get('/empresa/nova')
    assert resposta.status_code == 200
    corpo = resposta.get_data(as_text=True)
    assert 'id="nome"' in corpo and 'value=""' in corpo


def test_querystring_e_escapada_no_template(login_as):
    resposta = login_as('operador').get('/empresa/nova?nome=%3Cscript%3Ex%3C/script%3E')
    corpo = resposta.get_data(as_text=True)
    assert '<script>x</script>' not in corpo


# --- varios arquivos de uma vez --------------------------------------------

def _csv_multi(*grupos):
    """grupos = sequencia de (nome_arquivo, [nomes de empresa])."""
    campos = []
    for nome_arquivo, nomes in grupos:
        corpo = '\n'.join(LINHA.format(nome=n) for n in nomes)
        campos.append((io.BytesIO(corpo.encode('utf-8')), nome_arquivo))
    return {'arquivo': campos}


def test_importa_varios_arquivos_de_uma_vez(client):
    resposta = client.post(
        '/nfse/importar',
        data=_csv_multi(('julho.csv', ['EMPRESA TESTE LTDA']),
                        ('extra.csv', ['OUTRA EMPRESA LTDA'])),
        content_type='multipart/form-data')
    assert resposta.status_code == 200
    dados = resposta.get_json()
    assert dados['arquivos'] == 2
    assert dados['resumo']['total'] == 2


def test_linha_repetida_entre_arquivos_entra_uma_vez(client):
    resposta = client.post(
        '/nfse/importar',
        data=_csv_multi(('a.csv', ['EMPRESA TESTE LTDA']),
                        ('b.csv', ['EMPRESA TESTE LTDA'])),
        content_type='multipart/form-data')
    dados = resposta.get_json()
    assert dados['resumo']['total'] == 1
    assert dados['ignoradas_duplicadas'] == 1


def test_um_arquivo_ruim_recusa_tudo_e_diz_qual(client):
    dados = _csv_multi(('bom.csv', ['EMPRESA TESTE LTDA']))
    dados['arquivo'].append((io.BytesIO(b'nome,valor\nx,1\n'), 'ruim.csv'))
    resposta = client.post('/nfse/importar', data=dados,
                           content_type='multipart/form-data')
    assert resposta.status_code == 400
    assert 'ruim.csv' in resposta.get_json()['message']


def test_nenhum_arquivo_selecionado_devolve_400(client):
    resposta = client.post('/nfse/importar', data={'arquivo': []},
                           content_type='multipart/form-data')
    assert resposta.status_code == 400


# --- escopo do que a pagina mostra (competencia x ultima importacao) --------
#
# A competencia sai do VENCIMENTO menos um mes: vencimento 05/07 -> 06/2026.

LINHA_COM_VENC = ('"13/07/2026";"{nome}";"0001443038";"062623";"{venc}";'
                  '"811,00";"16,22";"1,13";"{valor}";"COBRANCA SIMPLES"')


def _importar_venc(client, nome, venc, valor='826,09'):
    corpo = LINHA_COM_VENC.format(nome=nome, venc=venc, valor=valor)
    return client.post(
        '/nfse/importar',
        data={'arquivo': (io.BytesIO(corpo.encode('utf-8')), 'extrato.csv')},
        content_type='multipart/form-data')


def test_sem_filtro_mostra_so_a_ultima_importacao(client, app):
    _importar_venc(client, 'EMPRESA TESTE LTDA', '05/06/2026')
    _importar_venc(client, 'OUTRA COISA LTDA', '05/07/2026')

    dados = client.get('/nfse/notas').get_json()
    assert [n['competencia'] for n in dados['notas']] == ['06/2026']


def test_filtro_por_competencia_atravessa_importacoes(client, app):
    """Quem emite antes do fim do mes importa o extrato duas ou tres vezes, e
    as notas do mesmo mes ficam espalhadas por varios lotes."""
    _importar_venc(client, 'EMPRESA TESTE LTDA', '05/07/2026')
    _importar_venc(client, 'OUTRA COISA LTDA', '05/07/2026', valor='500,00')
    _importar_venc(client, 'MAIS UMA LTDA', '05/08/2026')

    dados = client.get('/nfse/notas?competencia=06/2026').get_json()
    assert len(dados['notas']) == 2
    assert {n['competencia'] for n in dados['notas']} == {'06/2026'}
    assert dados['resumo']['total'] == 2


def test_pagina_lista_as_competencias_da_mais_recente_para_a_mais_antiga(client, app):
    """Ordenar 'MM/AAAA' como texto poe 12/2026 na frente de 01/2027, porque
    compara o MES primeiro. Estes dois vencimentos existem justamente para
    separar a ordem cronologica da alfabetica — com meses do mesmo ano as duas
    coincidem e o teste passaria sem provar nada."""
    _importar_venc(client, 'EMPRESA TESTE LTDA', '05/02/2027')   # -> 01/2027
    _importar_venc(client, 'OUTRA COISA LTDA', '05/01/2027')     # -> 12/2026

    corpo = client.get('/nfse').get_data(as_text=True)
    assert corpo.index('Competência 01/2027') < corpo.index('Competência 12/2026')


def test_competencia_inexistente_cai_na_ultima_importacao(client, app):
    """Querystring so aceita o que existe no banco — nada a injetar por ali."""
    _importar_venc(client, 'EMPRESA TESTE LTDA', '05/07/2026')

    dados = client.get('/nfse/notas?competencia=13/9999').get_json()
    assert len(dados['notas']) == 1
    assert dados['notas'][0]['competencia'] == '06/2026'


def test_notas_sem_lote_algum_nao_quebram_a_pagina(client, app):
    assert client.get('/nfse').status_code == 200
    assert client.get('/nfse/notas').get_json()['notas'] == []


def test_payload_da_conferencia_expoe_emissao_e_preserva_valor_zero(client, app):
    with app.app_context():
        lote = LoteNfse(nome_arquivo='lote-sintetico.csv', total=1)
        db.session.add(lote)
        db.session.flush()
        nota = NotaNfse(
            lote_id=lote.id,
            nome_csv='TOMADOR SINTÉTICO',
            nome_csv_norm='TOMADOR SINTETICO',
            competencia='08/2026',
            valor_final=Decimal('0.00'),
            status=StatusNotaNfse.EMITIDA,
            emitida_em=datetime(2026, 8, 5, 14, 30),
        )
        db.session.add(nota)
        db.session.commit()

    dados = client.get('/nfse/notas?competencia=08/2026').get_json()

    assert dados['notas'][0]['valor'] == '0,00'
    assert dados['notas'][0]['emitida_em'] == '2026-08-05T14:30:00'
