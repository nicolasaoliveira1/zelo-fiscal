"""Testes do nucleo de contagem de recorrencia (spec 09, RESOP-02.1/02.2).

A regra e a mesma que hoje vive dentro do diagnostics: conta falhas SEGUIDAS por
chave e zera por GRUPO quando algo da certo. Os dois consumidores (diagnostics e
circuit_breaker) dependem deste comportamento.
"""
from app.services.recorrencia import ContadorRecorrencia


def test_falha_incrementa_e_devolve_a_contagem():
    contador = ContadorRecorrencia(limiar=3)
    assert contador.falha('a') == 1
    assert contador.falha('a') == 2
    assert contador.contagem('a') == 2


def test_nao_estoura_abaixo_do_limiar():
    contador = ContadorRecorrencia(limiar=3)
    contador.falha('a')
    contador.falha('a')
    assert contador.estourou('a') is False


def test_estoura_exatamente_no_limiar():
    contador = ContadorRecorrencia(limiar=3)
    for _ in range(3):
        contador.falha('a')
    assert contador.estourou('a') is True


def test_chaves_diferentes_contam_separado():
    contador = ContadorRecorrencia(limiar=3)
    for _ in range(3):
        contador.falha('a')
    contador.falha('b')
    assert contador.estourou('a') is True
    assert contador.estourou('b') is False


def test_sucesso_zera_todas_as_chaves_do_grupo():
    """Reset por grupo: e o que permite ao diagnostics contar por
    (error_type, alvo) e zerar o alvo inteiro num sucesso."""
    contador = ContadorRecorrencia(limiar=3)
    for _ in range(3):
        contador.falha(('TIMEOUT', 'Imbe'), grupo='Imbe')
    contador.falha(('CAPTCHA', 'Imbe'), grupo='Imbe')

    contador.sucesso('Imbe')

    assert contador.estourou(('TIMEOUT', 'Imbe')) is False
    assert contador.contagem(('TIMEOUT', 'Imbe')) == 0
    assert contador.contagem(('CAPTCHA', 'Imbe')) == 0


def test_sucesso_de_um_grupo_nao_afeta_outro():
    contador = ContadorRecorrencia(limiar=3)
    for _ in range(3):
        contador.falha('Imbe', grupo='Imbe')
    for _ in range(3):
        contador.falha('Tramandai', grupo='Tramandai')

    contador.sucesso('Imbe')

    assert contador.estourou('Imbe') is False
    assert contador.estourou('Tramandai') is True


def test_grupo_default_e_a_propria_chave():
    """Sem grupo explicito, sucesso(chave) zera a propria chave — e assim que o
    breaker usa (chave = grupo = portal)."""
    contador = ContadorRecorrencia(limiar=3)
    for _ in range(3):
        contador.falha('FGTS')
    contador.sucesso('FGTS')
    assert contador.estourou('FGTS') is False


def test_limpar_zera_tudo():
    contador = ContadorRecorrencia(limiar=3)
    for _ in range(3):
        contador.falha('a')
    contador.limpar()
    assert contador.contagem('a') == 0
    assert contador.estourou('a') is False
