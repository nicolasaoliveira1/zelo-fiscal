"""Regressões de arquivamento seguro de certidões (REV-04)."""
from pathlib import Path
from unittest.mock import patch

from app import file_manager


def _preparar_destino(tmp_path):
    destino = tmp_path / 'certidoes'
    destino.mkdir()
    return destino


def _mover_no_destino(destino, origem, monkeypatch):
    monkeypatch.setattr(file_manager, 'encontrar_pasta_empresa', lambda _nome: destino)
    monkeypatch.setattr(file_manager, 'encontrar_caminho_final', lambda _pasta: destino)
    return file_manager.mover_e_renomear(str(origem), 'Empresa Sintética', 'Federal')


def test_falha_ao_preparar_preserva_versao_anterior(tmp_path, monkeypatch):
    destino = _preparar_destino(tmp_path)
    anterior = destino / 'CERTIDAO FEDERAL anterior.pdf'
    anterior.write_bytes(b'pdf anterior')
    origem = tmp_path / 'novo.pdf'
    origem.write_bytes(b'pdf novo')
    monkeypatch.setattr(file_manager.shutil, 'move',
                        lambda *_args: (_ for _ in ()).throw(OSError('rede indisponível')))
    monkeypatch.setattr(file_manager.shutil, 'copy2',
                        lambda *_args: (_ for _ in ()).throw(PermissionError('sem permissão')))

    with patch.object(file_manager, 'limpar_versoes_antigas') as limpar:
        sucesso, _mensagem = _mover_no_destino(destino, origem, monkeypatch)

    assert sucesso is False
    assert anterior.read_bytes() == b'pdf anterior'
    limpar.assert_not_called()


def test_falha_na_troca_preserva_destino_e_temporario(tmp_path, monkeypatch):
    destino = _preparar_destino(tmp_path)
    final = destino / 'CERTIDAO FEDERAL.pdf'
    final.write_bytes(b'pdf anterior')
    origem = tmp_path / 'novo.pdf'
    origem.write_bytes(b'pdf novo')
    monkeypatch.setattr(file_manager.os, 'replace',
                        lambda *_args: (_ for _ in ()).throw(OSError('destino bloqueado')))

    sucesso, _mensagem = _mover_no_destino(destino, origem, monkeypatch)

    assert sucesso is False
    assert final.read_bytes() == b'pdf anterior'
    temporarios = list(destino.glob('CERTIDAO FEDERAL.pdf.novo-*'))
    assert len(temporarios) == 1
    assert temporarios[0].read_bytes() == b'pdf novo'


def test_limpeza_so_ocorre_depois_da_troca_confirmada(tmp_path, monkeypatch):
    destino = _preparar_destino(tmp_path)
    final = destino / 'CERTIDAO FEDERAL.pdf'
    final.write_bytes(b'pdf anterior')
    origem = tmp_path / 'novo.pdf'
    origem.write_bytes(b'pdf novo')

    def conferir_troca(*_args):
        assert final.read_bytes() == b'pdf novo'

    monkeypatch.setattr(file_manager, 'limpar_versoes_antigas', conferir_troca)

    sucesso, caminho = _mover_no_destino(destino, origem, monkeypatch)

    assert sucesso is True
    assert Path(caminho) == final
    assert final.read_bytes() == b'pdf novo'
