"""Montagem do XML do evento de manifestacao (MANIF-12).

O que vai para a SEFAZ. Cada campo aqui tem um formato exigido pelo schema da
NF-e — e o custo de errar nao e excecao, e rejeicao com codigo obscuro.
"""
import re
import xml.etree.ElementTree as ET

import pytest

from app.services import manifestador_service as svc
from app.services import nfe_assinatura as assin
from tests.test_nfe_assinatura import _par_de_teste

NS_NFE = 'http://www.portalfiscal.inf.br/nfe'
CHAVE = '43170122333444000181650010000045391000045393'
CNPJ = '11222333000181'


def _texto(raiz, tag):
    no = raiz.find(f'.//{{{NS_NFE}}}{tag}')
    return no.text if no is not None else None


def _montar(**kwargs):
    kwargs.setdefault('chave', CHAVE)
    kwargs.setdefault('cnpj_destinatario', CNPJ)
    kwargs.setdefault('tipo_evento', svc.CONFIRMACAO)
    return svc.montar_evento(**kwargs)


# --- campos obrigatorios ----------------------------------------------------

def test_evento_traz_os_campos_do_schema():
    raiz = _montar()

    assert _texto(raiz, 'cOrgao') == '91'
    assert _texto(raiz, 'CNPJ') == CNPJ
    assert _texto(raiz, 'chNFe') == CHAVE
    assert _texto(raiz, 'tpEvento') == '210200'
    assert _texto(raiz, 'nSeqEvento') == '1'
    assert _texto(raiz, 'verEvento') == '1.00'
    assert _texto(raiz, 'descEvento') == 'Confirmacao da Operacao'


def test_corgao_e_91_porque_manifestacao_vai_ao_ambiente_nacional():
    """Nao e a UF da empresa: manifestacao do destinatario e sempre AN."""
    assert _texto(_montar(), 'cOrgao') == '91'


def test_ambiente_producao_e_1_e_homologacao_e_2():
    assert _texto(_montar(ambiente='producao'), 'tpAmb') == '1'
    assert _texto(_montar(ambiente='homologacao'), 'tpAmb') == '2'


def test_nseqevento_e_sempre_1():
    """Na manifestacao do destinatario cabe UM evento de cada tipo por NF-e.
    Reenviar o mesmo tipo devolve cStat 573 (duplicidade), que o fluxo ja trata
    como desfecho de sucesso — entao nao ha sequencia a incrementar."""
    assert _texto(_montar(), 'nSeqEvento') == '1'


# --- o Id (formato medido no recon) -----------------------------------------

def test_id_e_ID_mais_tpevento_mais_chave_mais_sequencia():
    raiz = _montar()
    inf = raiz.find(f'{{{NS_NFE}}}infEvento')

    assert inf.get('Id') == f'ID210200{CHAVE}01'
    assert len(inf.get('Id')) == 54  # 2 + 6 + 44 + 2


def test_id_usa_o_tipo_de_evento_escolhido():
    raiz = _montar(tipo_evento=svc.DESCONHECIMENTO)
    inf = raiz.find(f'{{{NS_NFE}}}infEvento')

    assert inf.get('Id').startswith('ID210220')
    assert len(inf.get('Id')) == 54


def test_sequencia_vai_com_zero_a_esquerda():
    """`01`, nao `1`: o Id tem tamanho fixo de 54 e a SEFAZ o compara literal."""
    inf = _montar().find(f'{{{NS_NFE}}}infEvento')
    assert inf.get('Id').endswith('01')


# --- os quatro tipos de evento ----------------------------------------------

def test_os_quatro_tipos_tem_a_descricao_correspondente():
    """Codigo e descricao vem do select do portal; divergencia entre os dois e
    rejeicao na hora."""
    esperado = {
        svc.CIENCIA: ('210210', 'Ciencia da Operacao'),
        svc.CONFIRMACAO: ('210200', 'Confirmacao da Operacao'),
        svc.DESCONHECIMENTO: ('210220', 'Desconhecimento da Operacao'),
        svc.NAO_REALIZADA: ('210240', 'Operacao nao Realizada'),
    }
    for tipo, (codigo, descricao) in esperado.items():
        kwargs = {'tipo_evento': tipo}
        if tipo == svc.NAO_REALIZADA:
            kwargs['justificativa'] = 'Mercadoria nao entregue pelo fornecedor'
        raiz = _montar(**kwargs)
        assert _texto(raiz, 'tpEvento') == codigo
        assert _texto(raiz, 'descEvento') == descricao


def test_tipo_de_evento_desconhecido_e_recusado():
    with pytest.raises(svc.EventoError):
        _montar(tipo_evento='999999')


def test_operacao_nao_realizada_exige_justificativa():
    """O unico dos quatro que carrega texto livre — e sem ele a SEFAZ rejeita."""
    with pytest.raises(svc.EventoError) as erro:
        _montar(tipo_evento=svc.NAO_REALIZADA)
    assert 'justificativa' in str(erro.value).lower()


def test_justificativa_entra_no_detevento():
    raiz = _montar(tipo_evento=svc.NAO_REALIZADA,
                   justificativa='Mercadoria devolvida ao fornecedor')
    assert _texto(raiz, 'xJust') == 'Mercadoria devolvida ao fornecedor'


def test_confirmacao_nao_leva_justificativa():
    """Campo a mais no detEvento e rejeicao de schema."""
    raiz = _montar(tipo_evento=svc.CONFIRMACAO,
                   justificativa='nao deveria aparecer')
    assert _texto(raiz, 'xJust') is None


# --- data com fuso ----------------------------------------------------------

def test_dhevento_tem_offset_explicito():
    """A SEFAZ exige data com fuso. Sem offset o evento e rejeitado, e um
    offset errado desloca a hora do registro."""
    valor = _texto(_montar(), 'dhEvento')
    assert re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$',
                    valor), valor


def test_dhevento_usa_o_momento_informado():
    from datetime import datetime, timedelta, timezone

    quando = datetime(2026, 8, 17, 14, 25, 33,
                      tzinfo=timezone(timedelta(hours=-3)))
    assert _texto(_montar(quando=quando), 'dhEvento') == \
        '2026-08-17T14:25:33-03:00'


# --- validacao de entrada ---------------------------------------------------

def test_chave_invalida_e_recusada():
    with pytest.raises(svc.EventoError):
        _montar(chave=CHAVE[:43])
    with pytest.raises(svc.EventoError):
        _montar(chave=CHAVE[:43] + str((int(CHAVE[43]) + 1) % 10))


def test_cnpj_do_destinatario_e_obrigatorio_e_so_digitos():
    with pytest.raises(svc.EventoError):
        _montar(cnpj_destinatario='')
    raiz = _montar(cnpj_destinatario='11.222.333/0001-81')
    assert _texto(raiz, 'CNPJ') == CNPJ


# --- integracao com a assinatura --------------------------------------------

def test_evento_montado_pode_ser_assinado_e_verificado():
    chave_rsa, cert = _par_de_teste()
    raiz = _montar()
    inf = raiz.find(f'{{{NS_NFE}}}infEvento')

    assin.assinar(raiz, inf, chave_rsa, cert)

    assert assin.verificar(raiz) is True


def test_evento_assinado_sobrevive_a_serializacao():
    """O caminho que a SEFAZ enxerga: bytes, nao objetos."""
    chave_rsa, cert = _par_de_teste()
    raiz = _montar()
    inf = raiz.find(f'{{{NS_NFE}}}infEvento')
    assin.assinar(raiz, inf, chave_rsa, cert)

    texto = assin.serializar_documento(raiz)

    assert assin.verificar(ET.fromstring(texto.encode('utf-8'))) is True
    assert f'<evento xmlns="{NS_NFE}"' in texto


def test_ordem_dos_campos_segue_o_schema():
    """XSD de NF-e e `sequence`: campo fora de ordem e rejeicao de schema, nao
    aviso."""
    inf = _montar().find(f'{{{NS_NFE}}}infEvento')
    tags = [f.tag.split('}')[-1] for f in inf]

    assert tags == ['cOrgao', 'tpAmb', 'CNPJ', 'chNFe', 'dhEvento', 'tpEvento',
                    'nSeqEvento', 'verEvento', 'detEvento']
