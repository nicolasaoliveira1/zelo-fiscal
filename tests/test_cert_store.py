"""Escolha do certificado por subject no store do Windows.

A varredura em si (`_varrer`) e ctypes/crypt32 e so existe no Windows; o que
esta testado aqui e a **decisao** tomada sobre o que a varredura devolve, que e
onde mora a regra: descartar vencido, preferir o de validade mais longa e nunca
inventar um issuer quando nao ha resposta.

O duble substitui `_varrer`, entao nenhum teste le certificado da maquina.
"""
import pytest

from app.automation import cert_store

SUBJECT = 'FULANO DE TAL:12345678909'

# O conftest dubla `encontrar_issuer` para toda a suite (nenhum teste le o store
# da maquina). Aqui a funcao e justamente o alvo, entao guardamos a original
# antes do duble e a devolvemos por teste — o isolamento vem de `_varrer`.
_ENCONTRAR_ISSUER_REAL = cert_store.encontrar_issuer


@pytest.fixture(autouse=True)
def _funcao_real(monkeypatch):
    monkeypatch.setattr(cert_store, 'encontrar_issuer', _ENCONTRAR_ISSUER_REAL)

# FILETIMEs ficticios, so a ordem relativa importa
VENC_ANTIGO = 1_000
VENC_NOVO = 2_000


@pytest.fixture()
def store(monkeypatch):
    """Store dublada: `encontrar_issuer` roda como se estivesse no Windows."""
    achados = {'por_store': []}

    monkeypatch.setattr(cert_store, '_carregar_crypt32', lambda: object())

    def _fake_varrer(_crypt32, flag, subject_alvo):
        assert subject_alvo == subject_alvo.casefold()  # comparacao case-insensitive
        return achados['por_store'].pop(0) if achados['por_store'] else []

    monkeypatch.setattr(cert_store, '_varrer', _fake_varrer)

    def _definir(*por_store):
        achados['por_store'] = list(por_store)

    return _definir


def test_sem_subject_nao_consulta(monkeypatch):
    chamou = []
    monkeypatch.setattr(cert_store, '_carregar_crypt32', lambda: chamou.append(1))

    assert cert_store.encontrar_issuer('') is None
    assert cert_store.encontrar_issuer('   ') is None
    assert cert_store.encontrar_issuer(None) is None
    assert chamou == []


def test_fora_do_windows_devolve_none(monkeypatch):
    monkeypatch.setattr(cert_store.os, 'name', 'posix')
    assert cert_store.encontrar_issuer(SUBJECT) is None


def test_devolve_o_issuer_do_unico_certificado_valido(store):
    store([(VENC_NOVO, 'AC SyngularID Multipla')], [])
    assert cert_store.encontrar_issuer(SUBJECT) == 'AC SyngularID Multipla'


def test_com_dois_validos_vence_o_de_validade_mais_longa(store):
    # o caso da renovacao: o recem-emitido e o que tem o vencimento mais distante
    store([(VENC_ANTIGO, 'AC DIGITALSIGN RFB G3'), (VENC_NOVO, 'AC SyngularID Multipla')], [])
    assert cert_store.encontrar_issuer(SUBJECT) == 'AC SyngularID Multipla'


def test_soma_as_duas_stores(store):
    # instalacao machine-wide: o certificado pode nao estar na store do usuario
    store([], [(VENC_NOVO, 'AC SyngularID Multipla')])
    assert cert_store.encontrar_issuer(SUBJECT) == 'AC SyngularID Multipla'


def test_sem_candidato_devolve_none_em_vez_de_chutar(store):
    # certificado vencido e ainda nao reinstalado: quem chama mantem o configurado
    store([], [])
    assert cert_store.encontrar_issuer(SUBJECT) is None


def test_erro_na_varredura_nao_levanta(monkeypatch):
    monkeypatch.setattr(cert_store, '_carregar_crypt32', lambda: object())

    def _explode(*_args):
        raise OSError('store indisponivel')

    monkeypatch.setattr(cert_store, '_varrer', _explode)

    assert cert_store.encontrar_issuer(SUBJECT) is None
