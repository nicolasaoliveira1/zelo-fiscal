"""Testes da padronizacao de nomes de cidade (COV-05).

Cobre a funcao pura `canonicalizar` (acento/caixa/desconhecida/idempotencia) e
o aplicador `padronizar_colunas` contra um SQLite em memoria (plumbing do SQL +
o duplicado 'Xangrila' que deve ficar intocado).
"""
import sqlalchemy as sa

from app.services.cidade_canonica import canonicalizar, padronizar_colunas


def test_canonicalizar_acento_e_caixa():
    assert canonicalizar('Imbe') == 'Imbé'
    assert canonicalizar('IMBE') == 'Imbé'
    assert canonicalizar('imbe') == 'Imbé'
    assert canonicalizar('Tramandai') == 'Tramandaí'
    assert canonicalizar('SAO PAULO') == 'São Paulo'
    assert canonicalizar('Capao da Canoa') == 'Capão da Canoa'
    assert canonicalizar('Osorio') == 'Osório'


def test_canonicalizar_ja_correto_idempotente():
    assert canonicalizar('Imbé') == 'Imbé'
    assert canonicalizar('Gravataí') == 'Gravataí'
    assert canonicalizar('Xangri-Lá') == 'Xangri-Lá'


def test_canonicalizar_desconhecida_e_vazia():
    assert canonicalizar('Curitiba') == 'Curitiba'   # fora do mapa: inalterada
    assert canonicalizar('  Imbe  ') == 'Imbé'        # trim
    assert canonicalizar('') == ''
    assert canonicalizar(None) is None


def test_separadores_nao_criam_cidade_diferente():
    """COV-05: hifen/espaco deixaram de gerar chaves distintas — as tres grafias
    de Xangri-Lá convergem para a mesma forma canonica (era o motivo de existir
    uma linha de municipio duplicada)."""
    from app.utils import normalizar_cidade
    assert normalizar_cidade('Xangri-Lá') == normalizar_cidade('Xangrila') \
        == normalizar_cidade('Xangri La') == 'XANGRILA'
    for grafia in ('Xangri-Lá', 'Xangrila', 'Xangri La', 'XANGRI-LA'):
        assert canonicalizar(grafia) == 'Xangri-Lá'
    # cidades compostas continuam distintas entre si
    assert normalizar_cidade('Capão da Canoa') != normalizar_cidade('Canoas')


def test_padronizar_colunas_transforma_linhas():
    eng = sa.create_engine('sqlite://')
    with eng.begin() as conn:
        conn.execute(sa.text('CREATE TABLE municipio (id INTEGER PRIMARY KEY, nome TEXT)'))
        conn.execute(sa.text('CREATE TABLE empresa (id INTEGER PRIMARY KEY, cidade TEXT)'))
        conn.execute(sa.text(
            "INSERT INTO municipio (id, nome) VALUES "
            "(1, 'Imbe'), (2, 'Xangri-Lá'), (3, 'Xangrila'), (4, 'Osorio')"))
        conn.execute(sa.text(
            "INSERT INTO empresa (id, cidade) VALUES "
            "(1, 'Imbe'), (2, 'Tramandai'), (3, 'Curitiba')"))

        atualizadas = padronizar_colunas(conn)

        muni = dict(conn.execute(sa.text('SELECT id, nome FROM municipio')).fetchall())
        emp = dict(conn.execute(sa.text('SELECT id, cidade FROM empresa')).fetchall())

    assert muni[1] == 'Imbé'
    assert muni[2] == 'Xangri-Lá'      # ja correto: intocado
    # 'Xangrila' viraria 'Xangri-Lá', mas essa linha ja existe e `nome` e UNIQUE:
    # a colisao e evitada (a duplicata some na migration de deduplicacao).
    assert muni[3] == 'Xangrila'
    assert muni[4] == 'Osório'
    assert emp[1] == 'Imbé'
    assert emp[2] == 'Tramandaí'
    assert emp[3] == 'Curitiba'        # fora do mapa: intocado
    # imbe(muni) + osorio(muni) + imbe(emp) + tramandai(emp) = 4
    assert atualizadas == 4
