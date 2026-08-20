"""Testes de `utils.normalizar_cidade` (spec 04, EXPORT-02).

A chave de cidade e a fonte unica compartilhada pelo filtro de Certidões e pela
exportacao da carteira. Variacoes de acento/caixa DEVEM colapsar na mesma chave
para o recorte do export bater 1:1 com o painel.
"""
from app.utils import normalizar_cidade


def test_acento_e_caixa_colapsam_na_mesma_chave():
    # 'Imbe'/'IMBE'/'imbé' representam a mesma cidade no painel -> mesma chave.
    chave = normalizar_cidade('Imbé')
    assert chave == 'IMBE'
    assert normalizar_cidade('IMBE') == chave
    assert normalizar_cidade('imbé') == chave
    assert normalizar_cidade('ImBe') == chave


def test_vazio_e_none_viram_string_vazia():
    assert normalizar_cidade('') == ''
    assert normalizar_cidade(None) == ''
    assert normalizar_cidade('   ') == ''


def test_espacos_sao_removidos():
    # COV-05: alem do trim, os separadores internos (espaco/hifen) tambem saem —
    # e o que faz 'Xangri-La' e 'Xangrila' caírem na mesma chave.
    assert normalizar_cidade('  Porto Alegre  ') == 'PORTOALEGRE'
    assert normalizar_cidade('Xangri-Lá') == normalizar_cidade('Xangrila')


def test_paridade_com_o_alias_de_certidoes():
    # O alias de Certidões deve delegar exatamente a esta função (mesma chave).
    from app.routes import _normalizar_cidade_certidoes
    for valor in ('Tramandaí', 'SANTO ANTÔNIO', '', None, '  Osório '):
        assert _normalizar_cidade_certidoes(valor) == normalizar_cidade(valor)
