"""Testes de caracterizacao da auto-selecao de certificado do RS (NFSE-10 / T1).

Fixa o comportamento ATUAL antes da extracao do nucleo generico (ND-002d): os
mesmos testes precisam continuar verdes, sem nenhuma alteracao, depois do
refactor. Por isso as asserts ficam sobre a superficie que o refactor promete
preservar (`_montar_politica_autoselect_rs`, `_ativar_...`, `_desativar_...`) e
sobre o EFEITO no registro, nunca sobre o modulo/funcao interna que grava.

O `winreg` e dublado em todos os modulos de `app.` que o referenciam: o teste
nao escreve no registro real da maquina e continua valendo quando a gravacao
mudar de modulo.
"""
import json
import sys

import pytest

from app.automation import cert_policy, driver

CHAVE_REGISTRO = r"Software\Policies\Google\Chrome\AutoSelectCertificateForUrls"

ISSUER_RS = 'AC DIGITALSIGN RFB G3'
SUBJECT_RS = 'JURACI DA ROSA OLIVEIRA:34560971072'
PATTERN_RS = 'https://www.sefaz.rs.gov.br'

POLITICA_RS = {
    'pattern': PATTERN_RS,
    'filter': {
        'ISSUER': {'CN': ISSUER_RS},
        'SUBJECT': {'CN': SUBJECT_RS},
    },
}


class _ChaveFalsa:
    """Handle de chave: o codigo usa `with winreg.CreateKey(...) as chave`."""

    def __init__(self, caminho):
        self.caminho = caminho

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class WinregFalso:
    """Duble minimo do winreg, com os valores num dict em memoria."""

    HKEY_CURRENT_USER = 'HKCU'
    REG_SZ = 1

    def __init__(self):
        self.valores = {}

    def CreateKey(self, raiz, caminho):
        self.valores.setdefault(caminho, {})
        return _ChaveFalsa(caminho)

    def SetValueEx(self, chave, nome, reservado, tipo, valor):
        self.valores[chave.caminho][nome] = valor

    def DeleteValue(self, chave, nome):
        if nome not in self.valores.get(chave.caminho, {}):
            raise FileNotFoundError(nome)
        del self.valores[chave.caminho][nome]

    def politica(self, indice='1'):
        """Valor gravado no indice, ou None quando a policy nao esta no registro."""
        return self.valores.get(CHAVE_REGISTRO, {}).get(indice)


def _drenar_ativacoes():
    # Zera o refcount sem depender de onde ele mora: desativar sem ativar e no-op.
    for _ in range(5):
        driver._desativar_politica_autoselect_rs_temporaria()


def _config_rs(monkeypatch, **override):
    """Envs `RS_CERT_AUTOSELECT_*` completas; sem app context, cai em os.environ."""
    valores = {
        'RS_CERT_AUTOSELECT_ENABLED': 'true',
        'RS_CERT_AUTOSELECT_PATTERN': PATTERN_RS,
        'RS_CERT_AUTOSELECT_ISSUER_CN': ISSUER_RS,
        'RS_CERT_AUTOSELECT_SUBJECT_CN': SUBJECT_RS,
    }
    valores.update(override)
    for chave, valor in valores.items():
        monkeypatch.setenv(chave, valor)


@pytest.fixture()
def registro(monkeypatch):
    falso = WinregFalso()
    monkeypatch.setattr(driver.os, 'name', 'nt')
    for modulo in list(sys.modules.values()):
        nome = getattr(modulo, '__name__', '') or ''
        if nome.startswith('app.') and hasattr(modulo, 'winreg'):
            monkeypatch.setattr(modulo, 'winreg', falso)
    _drenar_ativacoes()
    falso.valores.clear()
    yield falso
    _drenar_ativacoes()


def test_montar_com_autoselect_desligado_devolve_none(monkeypatch):
    _config_rs(monkeypatch, RS_CERT_AUTOSELECT_ENABLED='false')
    assert driver._montar_politica_autoselect_rs() is None


def test_montar_sem_pattern_devolve_none(monkeypatch):
    _config_rs(monkeypatch, RS_CERT_AUTOSELECT_PATTERN='   ')
    assert driver._montar_politica_autoselect_rs() is None


def test_montar_sem_issuer_e_sem_subject_devolve_none(monkeypatch):
    _config_rs(monkeypatch, RS_CERT_AUTOSELECT_ISSUER_CN='', RS_CERT_AUTOSELECT_SUBJECT_CN='')
    assert driver._montar_politica_autoselect_rs() is None


def test_montar_completo_devolve_pattern_e_filtro(monkeypatch):
    _config_rs(monkeypatch)
    assert driver._montar_politica_autoselect_rs() == POLITICA_RS


def test_ativar_grava_a_politica_no_indice_padrao(monkeypatch, registro):
    _config_rs(monkeypatch)
    assert driver._ativar_politica_autoselect_rs_temporaria() is True
    assert json.loads(registro.politica('1')) == POLITICA_RS


def test_refcount_mantem_politica_ate_a_ultima_desativacao(monkeypatch, registro):
    _config_rs(monkeypatch)
    driver._ativar_politica_autoselect_rs_temporaria()
    driver._ativar_politica_autoselect_rs_temporaria()

    driver._desativar_politica_autoselect_rs_temporaria()
    assert json.loads(registro.politica('1')) == POLITICA_RS

    driver._desativar_politica_autoselect_rs_temporaria()
    assert registro.politica('1') is None


def test_desativar_sem_ativar_nao_levanta_e_registro_segue_sem_politica(monkeypatch, registro):
    _config_rs(monkeypatch)
    driver._desativar_politica_autoselect_rs_temporaria()
    assert registro.politica('1') is None


def test_ativar_com_autoselect_desligado_devolve_false_e_nao_grava(monkeypatch, registro):
    _config_rs(monkeypatch, RS_CERT_AUTOSELECT_ENABLED='false')
    assert driver._ativar_politica_autoselect_rs_temporaria() is False
    assert registro.politica('1') is None


# --- T2: nucleo generico com refcount por indice (NFSE-10 / ND-002c / ND-006) ---

NFSE_PATTERN = 'https://certificado.nfse.gov.br'
NFSE_ISSUER = 'AC SyngularID Multipla'
NFSE_SUBJECT = 'JURACI DA ROSA OLIVEIRA:94645405000120'

POLITICA_NFSE = {
    'pattern': NFSE_PATTERN,
    'filter': {
        'ISSUER': {'CN': NFSE_ISSUER},
        'SUBJECT': {'CN': NFSE_SUBJECT},
    },
}


def _politica_nfse():
    return cert_policy.PoliticaCertificado(
        pattern=NFSE_PATTERN,
        indice='2',
        issuer_cn=NFSE_ISSUER,
        subject_cn=NFSE_SUBJECT,
    )


@pytest.fixture()
def registro_por_indice(registro):
    """Como `registro`, mas zerando tambem o refcount do indice da NFSe."""
    def _drenar():
        for _ in range(5):
            cert_policy.desativar(_politica_nfse())

    _drenar()
    registro.valores.clear()
    yield registro
    _drenar()


def test_desativar_a_politica_da_nfse_nao_apaga_a_do_rs(monkeypatch, registro_por_indice):
    _config_rs(monkeypatch)
    driver._ativar_politica_autoselect_rs_temporaria()
    cert_policy.ativar(_politica_nfse())
    # as duas convivem: indices distintos, cada uma com seu certificado (ND-006)
    assert json.loads(registro_por_indice.politica('1')) == POLITICA_RS
    assert json.loads(registro_por_indice.politica('2')) == POLITICA_NFSE

    cert_policy.desativar(_politica_nfse())

    assert registro_por_indice.politica('2') is None
    # regressao que o contador global causaria: o RS perderia a policy no meio do lote
    assert json.loads(registro_por_indice.politica('1')) == POLITICA_RS


def test_refcount_do_indice_conta_as_proprias_ativacoes(registro_por_indice):
    politica = _politica_nfse()
    cert_policy.ativar(politica)
    cert_policy.ativar(politica)

    cert_policy.desativar(politica)
    assert json.loads(registro_por_indice.politica('2')) == POLITICA_NFSE

    cert_policy.desativar(politica)
    assert registro_por_indice.politica('2') is None


def test_desativar_a_politica_do_rs_nao_apaga_a_da_nfse(monkeypatch, registro_por_indice):
    _config_rs(monkeypatch)
    driver._ativar_politica_autoselect_rs_temporaria()
    cert_policy.ativar(_politica_nfse())

    driver._desativar_politica_autoselect_rs_temporaria()

    # o isolamento vale nos dois sentidos (ND-002c: "nem vice-versa")
    assert registro_por_indice.politica('1') is None
    assert json.loads(registro_por_indice.politica('2')) == POLITICA_NFSE


# --- Issuer descoberto no store: sobreviver a renovacao do certificado ---
#
# O bug real: renovado o e-CPF em 30/07/2026, o titular (subject) seguiu igual
# mas a AC emissora mudou de "AC DIGITALSIGN RFB G3" para "AC SyngularID
# Multipla". Com o issuer antigo fixado no .env a policy nao casava com
# certificado nenhum, e o Chrome voltava a abrir o dialogo de selecao no meio do
# lote do RS — sem erro, so parado.

ISSUER_RENOVADO = 'AC SyngularID Multipla'


def _store_devolve(monkeypatch, issuer, esperado_subject=None):
    def _fake(subject_cn):
        if esperado_subject is not None:
            assert subject_cn == esperado_subject
        return issuer

    monkeypatch.setattr(cert_policy.cert_store, 'encontrar_issuer', _fake)


def test_issuer_vem_do_store_e_nao_do_env_quando_o_certificado_foi_renovado(monkeypatch, registro):
    _config_rs(monkeypatch)  # env ainda aponta para a AC antiga
    _store_devolve(monkeypatch, ISSUER_RENOVADO, esperado_subject=SUBJECT_RS)

    driver._ativar_politica_autoselect_rs_temporaria()

    gravada = json.loads(registro.politica('1'))
    assert gravada['filter']['ISSUER'] == {'CN': ISSUER_RENOVADO}
    # o subject e a chave estavel da busca: continua o do .env, intocado
    assert gravada['filter']['SUBJECT'] == {'CN': SUBJECT_RS}


def test_sem_resposta_do_store_mantem_o_issuer_configurado(monkeypatch, registro):
    _config_rs(monkeypatch)
    _store_devolve(monkeypatch, None)

    driver._ativar_politica_autoselect_rs_temporaria()

    # "nao sei" (fora do Windows, store vazia, crypt32 indisponivel) nao pode
    # virar policy sem ISSUER: o fallback e o valor configurado
    assert json.loads(registro.politica('1')) == POLITICA_RS


def test_a_politica_da_nfse_tambem_resolve_o_issuer_no_store(monkeypatch, registro_por_indice):
    _store_devolve(monkeypatch, 'AC NOVA', esperado_subject=NFSE_SUBJECT)

    cert_policy.ativar(_politica_nfse())

    gravada = json.loads(registro_por_indice.politica('2'))
    assert gravada['filter']['ISSUER'] == {'CN': 'AC NOVA'}


def test_o_issuer_resolvido_e_o_mesmo_na_checagem_e_na_gravacao(monkeypatch, registro):
    """Uma consulta por instancia: `ativar` e a gravacao nao podem divergir."""
    _config_rs(monkeypatch)
    chamadas = []

    def _fake(subject_cn):
        chamadas.append(subject_cn)
        return ISSUER_RENOVADO

    monkeypatch.setattr(cert_policy.cert_store, 'encontrar_issuer', _fake)

    politica = driver._politica_autoselect_rs()
    cert_policy.ativar(politica)

    assert len(chamadas) == 1
    assert json.loads(registro.politica('1'))['filter']['ISSUER'] == {'CN': ISSUER_RENOVADO}


def test_sem_subject_configurado_nao_consulta_o_store(monkeypatch, registro):
    _config_rs(monkeypatch, RS_CERT_AUTOSELECT_SUBJECT_CN='')
    chamadas = []
    monkeypatch.setattr(cert_policy.cert_store, 'encontrar_issuer',
                        lambda subject_cn: chamadas.append(subject_cn))

    driver._ativar_politica_autoselect_rs_temporaria()

    # sem subject nao ha o que buscar; o filtro fica so com o ISSUER do .env
    assert chamadas == []
    assert json.loads(registro.politica('1'))['filter'] == {'ISSUER': {'CN': ISSUER_RS}}
