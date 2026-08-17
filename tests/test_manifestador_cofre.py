"""Cofre de certificados: leitura do .pfx, casamento por CNPJ e cifra da senha
(MANIF-02, MANIF-05).

Os casos de casamento nao sao inventados: reproduzem os tres achados da
varredura real da carteira (`.specs/features/manifestador-nfe/recon.md`) que
tornaram o nome indefensavel como chave.
"""
import datetime as dt

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from app.services import manifestador_cofre as cofre


# --- fabrica de .pfx de teste ----------------------------------------------

def _fazer_pfx(cn, senha=b'123456', dias_validade=365, emissor='AC DE TESTE'):
    """Um .pfx real (bytes), autoassinado, para exercitar o caminho de verdade."""
    chave = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    agora = dt.datetime.now(dt.timezone.utc)
    vence = agora + dt.timedelta(days=dias_validade)
    # `dias_validade` negativo pede um certificado JA VENCIDO; nesse caso o
    # inicio da validade tem de recuar antes do vencimento, senao o proprio
    # builder recusa.
    inicia = min(agora, vence) - dt.timedelta(days=1)
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    emissor_nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, emissor)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(nome)
        .issuer_name(emissor_nome)
        .public_key(chave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(inicia)
        .not_valid_after(vence)
        .sign(chave, hashes.SHA256())
    )
    cifra = (serialization.BestAvailableEncryption(senha) if senha
             else serialization.NoEncryption())
    return pkcs12.serialize_key_and_certificates(
        name=b'teste', key=chave, cert=cert, cas=None, encryption_algorithm=cifra)


def _gravar_pfx(tmp_path, nome_arquivo, **kwargs):
    caminho = tmp_path / nome_arquivo
    caminho.write_bytes(_fazer_pfx(**kwargs))
    return caminho


# --- cnpj_do_cn: a chave do casamento (MANIF-02) ----------------------------

def test_cnpj_sai_do_final_do_cn():
    assert cofre.cnpj_do_cn('LOURDES M PESCE LTDA:54214676000107') == '54214676000107'


def test_cn_que_nao_e_o_nome_da_empresa_ainda_casa():
    """Achado real: existe certificado cujo CN e um aviso, nao a razao social.
    Casar por nome perderia este; casar por CNPJ acerta."""
    cn = 'CONSULTA RFB A REALIZAR NA HORA DA VALIDACAO:54214676000107'
    assert cofre.cnpj_do_cn(cn) == '54214676000107'


def test_grafias_diferentes_do_mesmo_cnpj_casam_igual():
    """Achado real: 'SOARES & LEAL' e 'SOARES E LEAL' sao o mesmo CNPJ."""
    a = cofre.cnpj_do_cn('SOARES & LEAL LTDA:07210971000105')
    b = cofre.cnpj_do_cn('SOARES E LEAL LTDA:07210971000105')
    assert a == b == '07210971000105'


def test_mesma_razao_social_em_cnpjs_diferentes_nao_se_confunde():
    """Achado real: 'E E C PEREIRA' aparece em 5 CNPJs (matriz + filiais).
    Por nome seriam indistinguiveis; por CNPJ sao 5 empresas distintas."""
    cnpjs = {cofre.cnpj_do_cn(f'E E C PEREIRA:{doc}')
             for doc in ('09041741000195', '09041741000276', '09041741000438',
                         '09041741000519', '09041741000608')}
    assert len(cnpjs) == 5


def test_e_cpf_nao_vira_cnpj():
    """e-CPF tem 11 digitos. Manifestar exige e-CNPJ do destinatario, entao um
    e-CPF de socio NAO pode casar como se fosse a empresa."""
    assert cofre.cnpj_do_cn('JURACI DA ROSA OLIVEIRA:34560971072') is None


def test_cn_sem_documento_devolve_none():
    assert cofre.cnpj_do_cn('AC SyngularID Multipla') is None
    assert cofre.cnpj_do_cn('') is None
    assert cofre.cnpj_do_cn(None) is None


# --- carregar_pfx -----------------------------------------------------------

def test_carregar_pfx_le_cn_issuer_e_validade(tmp_path):
    caminho = _gravar_pfx(tmp_path, 'a.pfx', cn='EMPRESA X LTDA:11222333000181',
                          emissor='AC SyngularID Multipla')
    info = cofre.carregar_pfx(caminho, b'123456')

    assert info.subject_cn == 'EMPRESA X LTDA:11222333000181'
    assert info.issuer_cn == 'AC SyngularID Multipla'
    assert info.cnpj == '11222333000181'
    assert info.not_after > dt.datetime.now()
    assert info.chave_privada is not None


def test_carregar_pfx_com_senha_errada_devolve_none(tmp_path):
    caminho = _gravar_pfx(tmp_path, 'a.pfx', cn='EMPRESA X LTDA:11222333000181',
                          senha=b'outra-senha')
    assert cofre.carregar_pfx(caminho, b'123456') is None


def test_abrir_com_senhas_conhecidas_tenta_123456_primeiro(tmp_path):
    caminho = _gravar_pfx(tmp_path, 'a.pfx', cn='X:11222333000181', senha=b'123456')
    senha, info = cofre.abrir_com_senhas_conhecidas(caminho)
    assert senha == '123456'
    assert info.cnpj == '11222333000181'


def test_abrir_com_senhas_conhecidas_aceita_senha_vazia(tmp_path):
    caminho = _gravar_pfx(tmp_path, 'a.pfx', cn='X:11222333000181', senha=None)
    senha, info = cofre.abrir_com_senhas_conhecidas(caminho)
    assert senha == ''
    assert info is not None


def test_abrir_com_senha_desconhecida_devolve_par_vazio(tmp_path):
    caminho = _gravar_pfx(tmp_path, 'a.pfx', cn='X:11222333000181',
                          senha=b'Isa@2110')
    senha, info = cofre.abrir_com_senhas_conhecidas(caminho)
    assert senha is None
    assert info is None


def test_arquivo_ilegivel_nao_levanta(tmp_path):
    caminho = tmp_path / 'quebrado.pfx'
    caminho.write_bytes(b'isto nao e um pkcs12')
    assert cofre.abrir_com_senhas_conhecidas(caminho) == (None, None)


# --- escolher_melhor: desempate por vencimento (ND-006) ---------------------

def test_escolher_melhor_pega_o_de_vencimento_mais_distante(tmp_path):
    """Mesma regra do cert_store: o recem-renovado vence o antigo."""
    antigo = cofre.carregar_pfx(
        _gravar_pfx(tmp_path, 'velho.pfx', cn='X:11222333000181', dias_validade=30),
        b'123456')
    novo = cofre.carregar_pfx(
        _gravar_pfx(tmp_path, 'novo.pfx', cn='X:11222333000181', dias_validade=400),
        b'123456')

    assert cofre.escolher_melhor([antigo, novo]) is novo
    assert cofre.escolher_melhor([novo, antigo]) is novo


def test_escolher_melhor_ignora_vencido_quando_ha_valido(tmp_path):
    vencido = cofre.carregar_pfx(
        _gravar_pfx(tmp_path, 'v.pfx', cn='X:11222333000181', dias_validade=-1),
        b'123456')
    valido = cofre.carregar_pfx(
        _gravar_pfx(tmp_path, 'ok.pfx', cn='X:11222333000181', dias_validade=100),
        b'123456')

    assert cofre.escolher_melhor([vencido, valido]) is valido


def test_escolher_melhor_sem_candidatos_devolve_none():
    assert cofre.escolher_melhor([]) is None


# --- cifra da senha (MANIF-05) ---------------------------------------------

def test_senha_faz_round_trip_cifrada(monkeypatch):
    monkeypatch.setenv(cofre.VAULT_KEY_ENV, cofre.gerar_chave_cofre())
    token = cofre.cifrar_senha('Isa@2110')
    assert token != 'Isa@2110'
    assert cofre.decifrar_senha(token) == 'Isa@2110'


def test_sem_chave_do_cofre_erro_e_acionavel(monkeypatch):
    """Sem MANIF_VAULT_KEY nao se grava nem se le senha — e a mensagem tem de
    dizer o que fazer, nao vazar um traceback de biblioteca."""
    monkeypatch.delenv(cofre.VAULT_KEY_ENV, raising=False)
    with pytest.raises(cofre.CofreError) as erro:
        cofre.cifrar_senha('123456')
    assert cofre.VAULT_KEY_ENV in str(erro.value)


def test_chave_do_cofre_invalida_erro_e_acionavel(monkeypatch):
    monkeypatch.setenv(cofre.VAULT_KEY_ENV, 'isto-nao-e-uma-chave-fernet')
    with pytest.raises(cofre.CofreError) as erro:
        cofre.cifrar_senha('123456')
    assert cofre.VAULT_KEY_ENV in str(erro.value)


def test_token_de_outra_chave_nao_abre(monkeypatch):
    monkeypatch.setenv(cofre.VAULT_KEY_ENV, cofre.gerar_chave_cofre())
    token = cofre.cifrar_senha('123456')

    monkeypatch.setenv(cofre.VAULT_KEY_ENV, cofre.gerar_chave_cofre())
    with pytest.raises(cofre.CofreError):
        cofre.decifrar_senha(token)


def test_chave_do_cofre_chega_pelo_app_config(app):
    """DENTRO do app context, `get_config_value` le de `app.config`, nao do
    ambiente. Se `MANIF_VAULT_KEY` nao estiver declarada no `config.py`, todo
    uso real do cofre falharia — e os demais testes deste arquivo nao pegariam,
    porque rodam fora de contexto e caem no `os.environ`."""
    with app.app_context():
        app.config['MANIF_VAULT_KEY'] = cofre.gerar_chave_cofre()
        assert cofre.decifrar_senha(cofre.cifrar_senha('Isa@2110')) == 'Isa@2110'

    assert 'MANIF_VAULT_KEY' in app.config


def test_senha_nunca_aparece_no_repr_da_info(tmp_path):
    """A senha em claro circula em memoria, mas nao pode vazar por repr — que e
    o que acaba num log de excecao."""
    caminho = _gravar_pfx(tmp_path, 'a.pfx', cn='X:11222333000181')
    _senha, info = cofre.abrir_com_senhas_conhecidas(caminho)
    assert '123456' not in repr(info)


def test_erro_de_leitura_nao_carrega_a_senha(tmp_path, monkeypatch):
    """A mensagem de erro do cofre nunca cita a senha tentada."""
    monkeypatch.setenv(cofre.VAULT_KEY_ENV, cofre.gerar_chave_cofre())
    token = cofre.cifrar_senha('senha-secreta-do-cliente')
    monkeypatch.setenv(cofre.VAULT_KEY_ENV, cofre.gerar_chave_cofre())
    try:
        cofre.decifrar_senha(token)
    except cofre.CofreError as exc:
        assert 'senha-secreta-do-cliente' not in str(exc)
