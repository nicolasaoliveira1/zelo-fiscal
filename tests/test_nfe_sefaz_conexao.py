"""Transporte mTLS com a SEFAZ (MANIF-13).

A prova antecipada do plano: responde "a SEFAZ aceita nosso certificado?" antes
de existir uma linha de XML de evento. Aqui so o transporte — montagem do
envelope e leitura da resposta ficam no T10.

O ponto sensivel e o arquivo temporario: `ssl.SSLContext.load_cert_chain` so
aceita CAMINHO, nunca bytes, entao a chave privada decifrada toca o disco local
durante o handshake. Ela tem de sumir mesmo quando o corpo levanta.
"""
import os
from pathlib import Path

import pytest
import requests

from app.services import manifestador_cofre as cofre
from app.services import nfe_sefaz
from tests.test_manifestador_cofre import _fazer_pfx


@pytest.fixture
def credencial(tmp_path):
    caminho = tmp_path / 'cert.pfx'
    caminho.write_bytes(_fazer_pfx(cn='EMPRESA X LTDA:11222333000181'))
    return nfe_sefaz.Credencial(caminho=str(caminho), senha='123456')


# --- material de chave em disco --------------------------------------------

def test_sessao_traz_certificado_de_cliente(credencial):
    with nfe_sefaz.sessao_mtls(credencial) as sessao:
        assert isinstance(sessao, requests.Session)
        assert sessao.cert is not None
        assert os.path.exists(sessao.cert)


def test_pem_temporario_tem_chave_e_certificado(credencial):
    with nfe_sefaz.sessao_mtls(credencial) as sessao:
        conteudo = Path(sessao.cert).read_text(encoding='ascii')
    assert 'PRIVATE KEY' in conteudo
    assert 'BEGIN CERTIFICATE' in conteudo


def test_temporario_some_ao_sair_normalmente(credencial):
    with nfe_sefaz.sessao_mtls(credencial) as sessao:
        caminho = sessao.cert
    assert not os.path.exists(caminho)


def test_temporario_some_quando_o_corpo_levanta(credencial):
    """O caso que importa: uma falha no meio do envio nao pode deixar chave
    privada esquecida no disco."""
    caminho = None
    with pytest.raises(RuntimeError):
        with nfe_sefaz.sessao_mtls(credencial) as sessao:
            caminho = sessao.cert
            raise RuntimeError('falha no meio do envio')

    assert caminho is not None
    assert not os.path.exists(caminho)


def test_temporario_nao_fica_no_projeto_nem_no_drive(credencial):
    """O temp e local. Escrever chave privada no `Z:` a exporia a todo o
    escritorio; escrever no diretorio do projeto arriscaria o commit."""
    with nfe_sefaz.sessao_mtls(credencial) as sessao:
        caminho = Path(sessao.cert).resolve()

    projeto = Path(__file__).resolve().parent.parent
    assert projeto not in caminho.parents
    assert not str(caminho).upper().startswith('Z:')


def test_credencial_que_nao_abre_da_erro_acionavel(tmp_path):
    caminho = tmp_path / 'cert.pfx'
    caminho.write_bytes(_fazer_pfx(cn='X:11222333000181', senha=b'outra'))
    credencial_ruim = nfe_sefaz.Credencial(caminho=str(caminho), senha='123456')

    with pytest.raises(nfe_sefaz.SefazError) as erro:
        with nfe_sefaz.sessao_mtls(credencial_ruim):
            pass
    assert 'certificado' in str(erro.value).lower()


def test_senha_nao_aparece_na_mensagem_de_erro(tmp_path):
    caminho = tmp_path / 'cert.pfx'
    caminho.write_bytes(_fazer_pfx(cn='X:11222333000181', senha=b'outra'))
    ruim = nfe_sefaz.Credencial(caminho=str(caminho), senha='senha-do-cliente')

    with pytest.raises(nfe_sefaz.SefazError) as erro:
        with nfe_sefaz.sessao_mtls(ruim):
            pass
    assert 'senha-do-cliente' not in str(erro.value)


# --- cabecalho e endereco ---------------------------------------------------

def test_user_agent_e_explicito(credencial):
    """Portal do governo recusa cliente anonimo — mesma licao do
    `portal_health` e da BrasilAPI (spec 08/09)."""
    with nfe_sefaz.sessao_mtls(credencial) as sessao:
        assert sessao.headers.get('User-Agent')
        assert 'python-requests' not in sessao.headers['User-Agent']


def test_distribuicao_dfe_usa_www1_e_nao_www():
    """Medido no recon: `www.nfe.fazenda.gov.br/NFeDistribuicaoDFe` responde
    404. Assumir a simetria obvia com o RecepcaoEvento quebraria o P2 com uma
    pagina HTML em vez de erro SOAP."""
    assert 'www1.' in nfe_sefaz.URLS['producao']['distribuicao']
    assert nfe_sefaz.URLS['producao']['distribuicao'].startswith(
        'https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe')


def test_homologacao_e_producao_sao_enderecos_distintos():
    assert nfe_sefaz.URLS['homologacao']['evento'] != \
        nfe_sefaz.URLS['producao']['evento']
    assert 'hom' in nfe_sefaz.URLS['homologacao']['evento']


def test_ambiente_desconhecido_e_recusado(credencial):
    with pytest.raises(nfe_sefaz.SefazError):
        nfe_sefaz.url_de('evento', ambiente='inventado')


# --- testar_conexao ---------------------------------------------------------

def test_testar_conexao_relata_o_status_sem_enviar_evento(credencial, monkeypatch):
    enviados = []

    class _Resposta:
        status_code = 200
        text = '<definitions xmlns="http://schemas.xmlsoap.org/wsdl/"/>'

    def _get(self, url, **kwargs):
        enviados.append(('GET', url))
        return _Resposta()

    def _post(self, url, **kwargs):
        raise AssertionError('testar_conexao nao pode fazer POST')

    monkeypatch.setattr(requests.Session, 'get', _get)
    monkeypatch.setattr(requests.Session, 'post', _post)

    resultado = nfe_sefaz.testar_conexao(credencial)

    assert resultado['status'] == 200
    assert resultado['autenticado'] is True
    assert len(enviados) == 1


def test_testar_conexao_reconhece_403_como_nao_autenticado(credencial,
                                                           monkeypatch):
    """403 sem certificado aceito e o sintoma exato medido no recon."""
    class _Resposta:
        status_code = 403
        text = '<html>Forbidden</html>'

    monkeypatch.setattr(requests.Session, 'get',
                        lambda self, url, **kw: _Resposta())

    resultado = nfe_sefaz.testar_conexao(credencial)

    assert resultado['status'] == 403
    assert resultado['autenticado'] is False


def test_testar_conexao_devolve_erro_de_rede_sem_levantar(credencial,
                                                          monkeypatch):
    def _get(self, url, **kwargs):
        raise requests.exceptions.ConnectionError('sem rota para o host')

    monkeypatch.setattr(requests.Session, 'get', _get)

    resultado = nfe_sefaz.testar_conexao(credencial)

    assert resultado['status'] is None
    assert resultado['autenticado'] is False
    assert 'rota' in resultado['erro']


def test_credencial_sai_do_cofre(app, ids, tmp_path, monkeypatch):
    """A ponte entre o cofre e o transporte: so empresa PRONTA vira credencial."""
    from app import db
    from app.models import CertificadoEmpresa, Empresa, EstadoCertificado

    with app.app_context():
        caminho = tmp_path / 'c.pfx'
        caminho.write_bytes(_fazer_pfx(cn='X:11222333000181'))
        emp = Empresa(nome='EMP', cnpj='11.222.333/0001-81', estado='RS',
                      cidade='Imbé')
        db.session.add(emp)
        db.session.commit()
        emp.certificado = CertificadoEmpresa(
            caminho=str(caminho), estado=EstadoCertificado.PRONTO)
        db.session.commit()

        assert cofre.credencial(emp) == (str(caminho), '123456')

        emp.certificado.estado = EstadoCertificado.VENCIDO
        db.session.commit()
        assert cofre.credencial(emp) is None
