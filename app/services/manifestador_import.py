r"""Camada conferivel do manifestador: texto e XML viram fila (MANIF-07..11).

**Zero rede, zero Selenium** — mesmo papel que o `nfse_import` tem na NFSe. E
aqui que o erro e barato: chave mal colada descoberta nesta camada nao custa
nada; descoberta na SEFAZ custa uma rejeicao e a duvida sobre o que aconteceu.

Layout da chave de 44 digitos:

    cUF(2) AAMM(4) CNPJ-emitente(14) mod(2) serie(3) nNF(9) tpEmis(1) cNF(8) DV(1)

Duas consequencias que mandam no desenho:

1. A **competencia** sai de `AAMM` — e o mes de emissao da NF-e, que e o que o
   escritorio chama de competencia ("no mes 08 a competencia e 07").
2. O CNPJ ali dentro e do **emitente**, nao do destinatario. Por isso a chave
   sozinha NAO diz de qual empresa da carteira ela e: a colagem e por empresa, e
   so o XML (que traz `dest/CNPJ`) resolve isso sozinho.
"""
import re
from collections import namedtuple

ChaveDecomposta = namedtuple(
    'ChaveDecomposta',
    'cuf aamm cnpj_emitente modelo serie numero tipo_emissao codigo dv')

TAMANHO_CHAVE = 44

# Grupos de digitos da linha. Todo o resto (espaco, ponto, hifen, letra) e
# separador — e as fronteiras que ele deixa sao o unico lugar por onde
# `_chaves_da_linha` tem permissao de recortar.
_BLOCO_DE_DIGITOS = re.compile(r'\d+')


def dv_valido(chave):
    """Digito verificador pelo modulo 11, pesos 2..9 ciclando da direita.

    Conferido contra 3 NF-e reais (`recon.md` §3). E a segunda rede de seguranca
    da extracao: um bloco que virou chave por acidente quase certamente cai
    aqui."""
    if not chave or len(str(chave)) != TAMANHO_CHAVE or not str(chave).isdigit():
        return False

    chave = str(chave)
    peso = 2
    soma = 0
    for digito in reversed(chave[:43]):
        soma += int(digito) * peso
        peso = 2 if peso == 9 else peso + 1

    resto = soma % 11
    esperado = 0 if resto in (0, 1) else 11 - resto
    return esperado == int(chave[43])


def decompor(chave):
    """Os 9 campos da chave. Nao valida — use `dv_valido` antes."""
    chave = str(chave)
    return ChaveDecomposta(
        cuf=chave[0:2], aamm=chave[2:6], cnpj_emitente=chave[6:20],
        modelo=chave[20:22], serie=chave[22:25], numero=chave[25:34],
        tipo_emissao=chave[34:35], codigo=chave[35:43], dv=chave[43:44])


def competencia_da_chave(chave):
    """'AAAA-MM' a partir de `AAMM`, ou None se o mes nao existe.

    O ano de 2 digitos e sempre 20xx: a NF-e comecou em 2006 e a chave nao
    representa datas anteriores."""
    if not chave or len(str(chave)) < 6:
        return None
    aamm = str(chave)[2:6]
    if not aamm.isdigit():
        return None
    ano, mes = int(aamm[:2]), int(aamm[2:])
    if not 1 <= mes <= 12:
        return None
    return f'20{ano:02d}-{mes:02d}'


def _fatiar(digitos):
    """Divide um bloco de 44*n digitos em n chaves."""
    return [digitos[i:i + TAMANHO_CHAVE]
            for i in range(0, len(digitos), TAMANHO_CHAVE)]


def _chaves_da_linha(linha):
    """As chaves de UMA linha, ou [] se a linha nao contem chave.

    O recorte so acontece em fronteira de separador que ja existia no texto —
    nunca numa janela arbitraria. Essa propriedade e o que impede o erro mais
    caro desta camada: uma chave DESALINHADA aponta para a NF-e de outra
    pessoa, e manifestar a nota errada nao tem desfazer.

    Duas passagens:

    1. Junta todos os grupos de digitos da linha. Se o total e multiplo de 44, e
       isso — sem exigir DV, para que uma chave com digito trocado ainda seja
       EXTRAIDA e possa ser recusada com nome na lista (MANIF-08). Aceitar tudo
       nao descarta informacao, entao nao precisa de prova.
    2. Se nao fecha, tenta descartar grupos pela esquerda ('NFe numero 4540
       <chave>', 'Chave: <chave>'). Aqui ha descarte de dados, entao exige-se
       prova: so vale a fatia cujos DVs todos conferem.
    """
    grupos = _BLOCO_DE_DIGITOS.findall(linha)
    if not grupos:
        return []

    tudo = ''.join(grupos)
    if len(tudo) % TAMANHO_CHAVE == 0:
        return _fatiar(tudo)

    acumulado = tudo
    for grupo in grupos[:-1]:
        acumulado = acumulado[len(grupo):]
        if len(acumulado) % TAMANHO_CHAVE:
            continue
        fatias = _fatiar(acumulado)
        if all(dv_valido(c) for c in fatias):
            return fatias
    return []


def extrair_chaves(texto):
    """Toda chave de 44 digitos do texto, na ordem, com repetidas preservadas.

    Trabalha LINHA A LINHA: a colagem do operador vem do scanner de codigo de
    barras ou de copiar-colar de PDF, e nos dois a unidade e a linha. Juntar
    linhas deixaria o final de uma emendar no comeco da outra.

    Repetidas NAO sao deduplicadas aqui: quem decide o que fazer com duplicata e
    a importacao (MANIF-11), e sumir com elas silenciosamente esconderia do
    operador que ele colou o mesmo bloco duas vezes."""
    if not texto:
        return []

    achadas = []
    for linha in str(texto).splitlines():
        achadas.extend(_chaves_da_linha(linha))
    return achadas
