"""Importacao de chaves a partir dos XML das NF-e (MANIF-10).

A diferenca para a colagem: aqui a empresa NAO e escolhida pelo operador — ela
sai do `<dest><CNPJ>` de dentro do arquivo. E o unico caminho de entrada que
dispensa separar as chaves por empresa antes.
"""
from pathlib import Path

from app import db
from app.models import ChaveManifestacao, Empresa
from app.services import manifestador_import as imp

FIXTURES = Path(__file__).parent / 'fixtures'
XML_COM_DEST = FIXTURES / 'nfe_mod55_com_dest.xml'
XML_SEM_DEST = FIXTURES / 'nfce_mod65_sem_dest.xml'

CHAVE_55 = '43250707461248000107550010000012341000012340'


def _empresa(nome='EMPRESA DESTINATARIA', cnpj='11.222.333/0001-81'):
    emp = Empresa(nome=nome, cnpj=cnpj, estado='RS', cidade='Imbé')
    db.session.add(emp)
    db.session.commit()
    return emp


def _bytes(caminho):
    return caminho.read_bytes()


# --- caminho feliz ----------------------------------------------------------

def test_le_a_chave_do_id_do_infnfe(app, ids):
    with app.app_context():
        _empresa()
        balanco = imp.importar_xmls([(XML_COM_DEST.name, _bytes(XML_COM_DEST))])

        assert balanco.aceitas == [CHAVE_55]


def test_resolve_a_empresa_pelo_cnpj_do_destinatario(app, ids):
    """O operador nao escolhe a empresa: ela vem de dentro do arquivo."""
    with app.app_context():
        outra = _empresa('OUTRA EMPRESA', '99.888.777/0001-66')
        alvo = _empresa('EMPRESA DESTINATARIA', '11.222.333/0001-81')

        imp.importar_xmls([(XML_COM_DEST.name, _bytes(XML_COM_DEST))])

        linha = ChaveManifestacao.query.filter_by(chave=CHAVE_55).first()
        assert linha.empresa_id == alvo.id
        assert linha.empresa_id != outra.id


def test_grava_origem_xml_e_competencia_da_chave(app, ids):
    with app.app_context():
        _empresa()
        imp.importar_xmls([(XML_COM_DEST.name, _bytes(XML_COM_DEST))])

        linha = ChaveManifestacao.query.filter_by(chave=CHAVE_55).first()
        assert linha.origem == 'xml'
        assert linha.competencia == '2025-07'
        assert linha.cnpj_emitente == '07461248000107'


# --- recusas nomeando o ARQUIVO ---------------------------------------------

def test_destinatario_fora_da_carteira_e_recusado_pelo_nome(app, ids):
    with app.app_context():
        _empresa('EMPRESA QUALQUER', '99.888.777/0001-66')

        balanco = imp.importar_xmls([(XML_COM_DEST.name, _bytes(XML_COM_DEST))])

        assert balanco.aceitas == []
        assert balanco.sem_empresa == [XML_COM_DEST.name]
        assert ChaveManifestacao.query.count() == 0


def test_nfce_sem_destinatario_e_recusada_pelo_nome(app, ids):
    """NFC-e (modelo 65) e venda no balcao: sem destinatario identificado nao ha
    o que manifestar. Todas as notas guardadas no drive sao deste tipo."""
    with app.app_context():
        _empresa()

        balanco = imp.importar_xmls([(XML_SEM_DEST.name, _bytes(XML_SEM_DEST))])

        assert balanco.aceitas == []
        assert balanco.sem_empresa == [XML_SEM_DEST.name]


def test_xml_malformado_e_recusado_sem_derrubar_o_lote(app, ids):
    """Um arquivo ruim no meio de 200 nao pode obrigar a reimportar tudo."""
    with app.app_context():
        _empresa()

        balanco = imp.importar_xmls([
            ('quebrado.xml', b'<isto nao fecha'),
            (XML_COM_DEST.name, _bytes(XML_COM_DEST)),
        ])

        assert balanco.aceitas == [CHAVE_55]
        assert 'quebrado.xml' in balanco.sem_empresa


def test_arquivo_vazio_e_recusado(app, ids):
    with app.app_context():
        _empresa()
        balanco = imp.importar_xmls([('vazio.xml', b'')])

        assert balanco.aceitas == []
        assert balanco.sem_empresa == ['vazio.xml']


def test_xml_sem_id_no_infnfe_e_recusado(app, ids):
    with app.app_context():
        _empresa()
        sem_id = (b'<?xml version="1.0"?>'
                  b'<NFe xmlns="http://www.portalfiscal.inf.br/nfe">'
                  b'<infNFe versao="4.00"><dest><CNPJ>11222333000181</CNPJ>'
                  b'</dest></infNFe></NFe>')

        balanco = imp.importar_xmls([('sem_id.xml', sem_id)])

        assert balanco.aceitas == []
        assert balanco.sem_empresa == ['sem_id.xml']


def test_id_com_dv_invalido_cai_no_grupo_de_dv(app, ids):
    """DV errado dentro do XML e problema DIFERENTE de destinatario ausente, e
    os dois grupos existem para o operador saber o que corrigir."""
    with app.app_context():
        _empresa()
        errado = CHAVE_55[:43] + str((int(CHAVE_55[43]) + 1) % 10)
        xml = (f'<?xml version="1.0"?>'
               f'<NFe xmlns="http://www.portalfiscal.inf.br/nfe">'
               f'<infNFe versao="4.00" Id="NFe{errado}">'
               f'<dest><CNPJ>11222333000181</CNPJ></dest>'
               f'</infNFe></NFe>').encode()

        balanco = imp.importar_xmls([('dv_ruim.xml', xml)])

        assert balanco.dv_invalido == [errado]
        assert balanco.sem_empresa == []


# --- destinatario pessoa fisica e duplicata ---------------------------------

def test_destinatario_com_cpf_e_recusado(app, ids):
    """`<dest>` com CPF: o destinatario e pessoa fisica, nao empresa da
    carteira."""
    with app.app_context():
        _empresa()
        xml = (f'<?xml version="1.0"?>'
               f'<NFe xmlns="http://www.portalfiscal.inf.br/nfe">'
               f'<infNFe versao="4.00" Id="NFe{CHAVE_55}">'
               f'<dest><CPF>34560971072</CPF></dest>'
               f'</infNFe></NFe>').encode()

        balanco = imp.importar_xmls([('cpf.xml', xml)])

        assert balanco.aceitas == []
        assert balanco.sem_empresa == ['cpf.xml']


def test_reimportar_o_mesmo_xml_vira_duplicata(app, ids):
    with app.app_context():
        _empresa()
        arquivo = (XML_COM_DEST.name, _bytes(XML_COM_DEST))
        imp.importar_xmls([arquivo])

        balanco = imp.importar_xmls([arquivo])

        assert balanco.aceitas == []
        assert balanco.duplicatas[0]['chave'] == CHAVE_55
        assert ChaveManifestacao.query.count() == 1


def test_balanco_soma_todos_os_grupos_no_xml(app, ids):
    with app.app_context():
        _empresa()
        balanco = imp.importar_xmls([
            (XML_COM_DEST.name, _bytes(XML_COM_DEST)),
            (XML_SEM_DEST.name, _bytes(XML_SEM_DEST)),
            ('quebrado.xml', b'<nao fecha'),
        ])

        assert balanco.total_lidas == 3
        assert (len(balanco.aceitas) + len(balanco.dv_invalido)
                + len(balanco.duplicatas) + len(balanco.competencia_invalida)
                + len(balanco.sem_empresa)) == balanco.total_lidas
