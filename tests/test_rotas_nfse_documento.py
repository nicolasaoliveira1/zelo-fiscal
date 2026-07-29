"""Tomador por CPF, memoria de documento avulso e emissao manual.

Parte dos clientes do escritorio e pessoa fisica (produtor rural, autonomo) e
nunca vai virar cadastro de Empresa. Sem memoria do documento, o operador
redigitaria o CPF todo mes; e sem separar CPF de CNPJ, a linha ficaria
oferecendo "cadastrar" para sempre.

Os CPFs usados sao numeros validos por digito verificador, sem vinculo com
pessoa real.
"""
import io

from app import db
from app.models import ApelidoNfse, Empresa, NotaNfse, StatusNotaNfse

CPF_OK = '529.982.247-25'
CNPJ_OK = '33.684.001/0001-51'
LINHA = ('"13/07/2026";"{nome}";"0001443038";"062623";"{venc}";'
         '"811,00";"16,22";"1,13";"826,09";"COBRANCA SIMPLES"')


def _importar(client, nome, venc='05/07/2026'):
    corpo = LINHA.format(nome=nome, venc=venc)
    return client.post('/nfse/importar',
                       data={'arquivo': (io.BytesIO(corpo.encode('utf-8')), 'extrato.csv')},
                       content_type='multipart/form-data')


def _ultima_nota(app):
    with app.app_context():
        return NotaNfse.query.order_by(NotaNfse.id.desc()).first().id


# --- CPF do tomador ---------------------------------------------------------

def test_cpf_valido_vira_pessoa_fisica(client, app):
    _importar(client, 'RODRIGO FERREIRA FARIA')
    nota_id = _ultima_nota(app)

    resposta = client.post(f'/nfse/nota/{nota_id}/resolver', json={'documento': CPF_OK})
    assert resposta.status_code == 200
    nota = resposta.get_json()['nota']
    assert nota['status'] == StatusNotaNfse.PESSOA_FISICA
    assert nota['tipo_documento'] == 'cpf'
    assert nota['documento'] == CPF_OK
    assert nota['empresa_id'] is None


def test_cpf_invalido_e_recusado_citando_cpf(client, app):
    _importar(client, 'MARIO BOLL PRODUTOR RURAL')
    nota_id = _ultima_nota(app)
    resposta = client.post(f'/nfse/nota/{nota_id}/resolver',
                           json={'documento': '529.982.247-26'})
    assert resposta.status_code == 400
    assert 'CPF' in resposta.get_json()['message']


def test_cnpj_invalido_e_recusado_citando_cnpj(client, app):
    _importar(client, 'ALGUMA COISA LTDA')
    nota_id = _ultima_nota(app)
    resposta = client.post(f'/nfse/nota/{nota_id}/resolver',
                           json={'documento': '33.684.001/0001-52'})
    assert resposta.status_code == 400
    assert 'CNPJ' in resposta.get_json()['message']


def test_documento_com_tamanho_invalido_e_recusado(client, app):
    _importar(client, 'ALGUMA COISA LTDA')
    nota_id = _ultima_nota(app)
    resposta = client.post(f'/nfse/nota/{nota_id}/resolver', json={'documento': '12345'})
    assert resposta.status_code == 400


def test_cnpj_de_empresa_nao_cadastrada_fica_ainda_nao_cadastrada(client, app):
    _importar(client, 'CONSTRUTORA INEXISTENTE LTDA')
    nota_id = _ultima_nota(app)
    resposta = client.post(f'/nfse/nota/{nota_id}/resolver', json={'documento': CNPJ_OK})
    nota = resposta.get_json()['nota']
    assert nota['status'] == StatusNotaNfse.CADASTRO_PENDENTE
    assert nota['tipo_documento'] == 'cnpj'


# --- memoria entre importacoes ---------------------------------------------

def test_cpf_informado_e_lembrado_no_import_seguinte(client, app):
    """O ponto central: no mes que vem o CPF ja vem preenchido sozinho."""
    _importar(client, 'RODRIGO FERREIRA FARIA')
    nota_id = _ultima_nota(app)
    client.post(f'/nfse/nota/{nota_id}/resolver', json={'documento': CPF_OK})

    resposta = _importar(client, 'RODRIGO FERREIRA FARIA', venc='05/08/2026')
    nota = resposta.get_json()['notas'][0]
    assert nota['documento'] == CPF_OK
    assert nota['status'] == StatusNotaNfse.PESSOA_FISICA
    assert nota['competencia'] == '07/2026'


def test_cnpj_avulso_lembrado_mantem_o_convite_para_cadastrar(client, app):
    """Puxa o CNPJ do mes anterior, mas continua 'ainda nao cadastrada' — o
    botao de cadastrar segue disponivel ate a empresa existir de fato."""
    _importar(client, 'CONSTRUTORA INEXISTENTE LTDA')
    nota_id = _ultima_nota(app)
    client.post(f'/nfse/nota/{nota_id}/resolver', json={'documento': CNPJ_OK})

    resposta = _importar(client, 'CONSTRUTORA INEXISTENTE LTDA', venc='05/08/2026')
    nota = resposta.get_json()['notas'][0]
    assert nota['documento'] == CNPJ_OK
    assert nota['status'] == StatusNotaNfse.CADASTRO_PENDENTE


def test_memoria_de_documento_nao_cria_empresa(client, app):
    """CPF nunca vira cadastro: se criasse, poluiria a carteira de certidoes."""
    with app.app_context():
        antes = Empresa.query.count()
    _importar(client, 'RODRIGO FERREIRA FARIA')
    client.post(f'/nfse/nota/{_ultima_nota(app)}/resolver', json={'documento': CPF_OK})
    with app.app_context():
        assert Empresa.query.count() == antes
        apelido = ApelidoNfse.query.filter_by(nome_norm='RODRIGO FERREIRA FARIA').first()
        assert apelido is not None
        assert apelido.empresa_id is None
        assert apelido.documento == CPF_OK


def test_cadastrar_a_empresa_depois_faz_o_cadastro_mandar(client, app):
    """Se a empresa passa a existir, o cadastro vira a fonte de verdade."""
    _importar(client, 'CONSTRUTORA INEXISTENTE LTDA')
    client.post(f'/nfse/nota/{_ultima_nota(app)}/resolver', json={'documento': CNPJ_OK})
    with app.app_context():
        db.session.add(Empresa(nome='CONSTRUTORA INEXISTENTE', cnpj=CNPJ_OK,
                               cidade='Imbé', estado='RS'))
        db.session.commit()
        empresa_id = Empresa.query.filter_by(cnpj=CNPJ_OK).first().id

    nota_id = _ultima_nota(app)
    client.post(f'/nfse/nota/{nota_id}/resolver', json={'empresa_id': empresa_id})
    resposta = _importar(client, 'CONSTRUTORA INEXISTENTE LTDA', venc='05/08/2026')
    nota = resposta.get_json()['notas'][0]
    assert nota['empresa_id'] == empresa_id
    assert nota['status'] == StatusNotaNfse.PRONTA


# --- nota emitida fora do sistema ------------------------------------------

def test_marcar_como_emitida_na_mao(client, app):
    _importar(client, 'EMPRESA TESTE LTDA')
    nota_id = _ultima_nota(app)
    resposta = client.post(f'/nfse/nota/{nota_id}/emitida-manual', json={'marcar': True})
    assert resposta.status_code == 200
    nota = resposta.get_json()['nota']
    assert nota['status'] == StatusNotaNfse.EMITIDA
    assert nota['origem_emissao'] == 'manual'


def test_marcada_como_emitida_entra_na_trava_de_duplicidade(client, app):
    """O motivo de a marcacao existir: o mes seguinte precisa saber que essa
    competencia ja saiu, mesmo que a nota nunca tenha passado pela automacao."""
    _importar(client, 'EMPRESA TESTE LTDA')
    client.post(f'/nfse/nota/{_ultima_nota(app)}/emitida-manual', json={'marcar': True})

    resposta = _importar(client, 'EMPRESA TESTE LTDA')
    assert resposta.get_json()['notas'][0]['status'] == StatusNotaNfse.DUPLICATA


def test_trava_funciona_tambem_para_tomador_por_cpf(client, app):
    """Com a chave antiga (empresa_id) esse caso nunca seria detectado, porque
    tomador por CPF nao tem empresa vinculada."""
    _importar(client, 'RODRIGO FERREIRA FARIA')
    client.post(f'/nfse/nota/{_ultima_nota(app)}/resolver', json={'documento': CPF_OK})
    client.post(f'/nfse/nota/{_ultima_nota(app)}/emitida-manual', json={'marcar': True})

    resposta = _importar(client, 'RODRIGO FERREIRA FARIA')
    assert resposta.get_json()['notas'][0]['status'] == StatusNotaNfse.DUPLICATA


def test_desmarcar_devolve_a_linha_ao_estado_anterior(client, app):
    _importar(client, 'EMPRESA TESTE LTDA')
    nota_id = _ultima_nota(app)
    client.post(f'/nfse/nota/{nota_id}/emitida-manual', json={'marcar': True})

    resposta = client.post(f'/nfse/nota/{nota_id}/emitida-manual', json={'marcar': False})
    nota = resposta.get_json()['nota']
    assert nota['status'] == StatusNotaNfse.PRONTA
    assert nota['origem_emissao'] is None


def test_desmarcar_tomador_por_cpf_volta_para_pessoa_fisica(client, app):
    _importar(client, 'RODRIGO FERREIRA FARIA')
    nota_id = _ultima_nota(app)
    client.post(f'/nfse/nota/{nota_id}/resolver', json={'documento': CPF_OK})
    client.post(f'/nfse/nota/{nota_id}/emitida-manual', json={'marcar': True})

    resposta = client.post(f'/nfse/nota/{nota_id}/emitida-manual', json={'marcar': False})
    assert resposta.get_json()['nota']['status'] == StatusNotaNfse.PESSOA_FISICA


def test_nao_da_para_desmarcar_o_que_a_automacao_emitiu(client, app):
    """Emissao de verdade nao volta atras por um clique: a nota fiscal existe
    no portal e desfazer aqui daria uma visao falsa do que ja foi emitido."""
    _importar(client, 'EMPRESA TESTE LTDA')
    nota_id = _ultima_nota(app)
    with app.app_context():
        nota = db.session.get(NotaNfse, nota_id)
        nota.status = StatusNotaNfse.EMITIDA
        nota.origem_emissao = 'automacao'
        db.session.commit()

    resposta = client.post(f'/nfse/nota/{nota_id}/emitida-manual', json={'marcar': False})
    assert resposta.status_code == 409


def test_nao_da_para_marcar_como_manual_o_que_a_automacao_emitiu(client, app):
    _importar(client, 'EMPRESA TESTE LTDA')
    nota_id = _ultima_nota(app)
    with app.app_context():
        nota = db.session.get(NotaNfse, nota_id)
        nota.origem_emissao = 'automacao'
        db.session.commit()
    assert client.post(f'/nfse/nota/{nota_id}/emitida-manual',
                       json={'marcar': True}).status_code == 409


def test_marcar_nota_inexistente_devolve_404(client):
    assert client.post('/nfse/nota/99999/emitida-manual', json={}).status_code == 404


def test_marcar_exige_papel_operador(login_as, client, app):
    _importar(client, 'EMPRESA TESTE LTDA')
    nota_id = _ultima_nota(app)
    assert login_as('leitura').post(
        f'/nfse/nota/{nota_id}/emitida-manual', json={}).status_code == 403
