import os
from pathlib import Path

import pytest

from app.services.pem_temporario import Credencial, pem_temporario
from tests.test_manifestador_cofre import _fazer_pfx


@pytest.fixture
def credencial(tmp_path):
    caminho = tmp_path / 'cert.pfx'
    caminho.write_bytes(_fazer_pfx(cn='EMPRESA SINTETICA:11222333000181'))
    return Credencial(caminho=str(caminho), senha='123456')


def test_credencial_omite_senha_do_repr(credencial):
    representacao = repr(credencial)

    assert '123456' not in representacao
    assert 'Credencial' in representacao


def test_pem_temporario_cria_chave_e_certificado_e_remove_na_saida(credencial):
    with pem_temporario(credencial) as caminho:
        assert Path(caminho).exists()
        conteudo = Path(caminho).read_text(encoding='ascii')
        assert 'PRIVATE KEY' in conteudo
        assert 'BEGIN CERTIFICATE' in conteudo

    assert not os.path.exists(caminho)


def test_pem_temporario_remove_arquivo_quando_corpo_levanta(credencial):
    caminho = None

    with pytest.raises(RuntimeError):
        with pem_temporario(credencial) as caminho_temporario:
            caminho = caminho_temporario
            raise RuntimeError('falha sintética')

    assert caminho is not None
    assert not os.path.exists(caminho)
