"""Resolucao nome do banco -> Empresa com limiar duplo (NFSE-03 / ND-003).

O cadastro guarda apelido curto ('ALUMAP') e o banco manda a razao social
truncada em 35 caracteres ('ALUMAP COMERCIO DE ALUMINIOS LTDA'), com
abreviacoes proprias ('PROD', 'ADM', 'CONST'). Por isso o scorer e
`token_set_ratio`, que pontua 100 quando um conjunto de tokens e subconjunto
do outro.

O limiar e DUPLO de proposito: score >= 90 E distancia >= 10 para o segundo
colocado. Um limiar so de score nao distingue "match bom" de "match bom mas
ambiguo", e o custo de errar e uma nota fiscal com o CNPJ de outro cliente.
Estes testes existem principalmente para provar que o caso ambiguo NAO vincula.
"""
import os
from types import SimpleNamespace

from app.models import OrigemVinculoNfse
from app.services import nfse_import as imp

FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures', 'extrato_banco.csv')


def _empresa(id_, nome):
    return SimpleNamespace(id=id_, nome=nome, cnpj=f'00.000.000/{id_:04d}-00')


def _apelido(nome_norm, empresa_id=None, documento=None, tipo_documento=None):
    """Espelha os campos reais de ApelidoNfse: o vinculo pode ser para uma
    Empresa cadastrada OU para um documento avulso (CPF/CNPJ)."""
    return SimpleNamespace(nome_norm=nome_norm, empresa_id=empresa_id,
                           documento=documento, tipo_documento=tipo_documento)


# --- 1) exato --------------------------------------------------------------

def test_match_exato_normalizado_vence():
    empresas = [_empresa(1, 'Restaurante Galetão'), _empresa(2, 'Outra Coisa')]
    v = imp.resolver_empresa('  RESTAURANTE   GALETAO  ', empresas)
    assert v.empresa.id == 1
    assert v.origem == OrigemVinculoNfse.EXATO
    assert v.score == 100


def test_nome_repetido_no_cadastro_nao_resolve_por_exato():
    # dois cadastros com o mesmo nome: qualquer escolha seria arbitraria
    empresas = [_empresa(1, 'ALUMAP'), _empresa(2, 'ALUMAP')]
    assert not imp.resolver_empresa('ALUMAP', empresas).resolvido


def test_nome_vazio_nao_resolve():
    assert not imp.resolver_empresa('', [_empresa(1, 'ALUMAP')]).resolvido
    assert not imp.resolver_empresa('   ', [_empresa(1, 'ALUMAP')]).resolvido


def test_cadastro_vazio_nao_resolve():
    assert not imp.resolver_empresa('ALUMAP COMERCIO LTDA', []).resolvido


# --- 2) apelido ------------------------------------------------------------

def test_apelido_salvo_resolve_o_que_o_fuzzy_nao_resolveria():
    # 'JM ADM DE CONDOMINIOS LTDA' vs cadastro 'JM ADMINISTRACOES': tokens
    # diferentes demais para o fuzzy, mas um humano ja confirmou uma vez
    empresas = [_empresa(1, 'JM ADMINISTRACOES'), _empresa(2, 'CONNECTA')]
    nome = 'JM ADM DE CONDOMINIOS LTDA'
    assert not imp.resolver_empresa(nome, empresas).resolvido

    apelidos = [_apelido(imp.normalizar_nome(nome), 1)]
    v = imp.resolver_empresa(nome, empresas, apelidos)
    assert v.empresa.id == 1
    assert v.origem == OrigemVinculoNfse.APELIDO


def test_apelido_de_empresa_removida_nao_resolve():
    apelidos = [_apelido('EMPRESA SUMIDA', 99)]
    assert not imp.resolver_empresa('EMPRESA SUMIDA', [_empresa(1, 'OUTRA')], apelidos).resolvido


# --- 3) fuzzy: o que o limiar duplo protege --------------------------------

def test_truncamento_do_banco_resolve_pelo_fuzzy():
    # o cadastro guarda o apelido curto; o banco manda a razao social inteira
    empresas = [_empresa(1, 'ALUMAP'), _empresa(2, 'ALUMINIOS DO SUL'), _empresa(3, 'MADEIRAS TAPIA')]
    v = imp.resolver_empresa('ALUMAP COMERCIO DE ALUMINIOS LTDA', empresas)
    assert v.empresa.id == 1
    assert v.origem == OrigemVinculoNfse.FUZZY
    assert v.score >= imp.LIMIAR_SCORE


def test_abreviacao_do_banco_resolve_quando_inequivoca():
    empresas = [_empresa(1, 'BOLL REPRESENTACOES'), _empresa(2, 'ANELISE BOLL')]
    v = imp.resolver_empresa('BOLL REPRESENTACOES COMERCIAIS LTDA', empresas)
    assert v.empresa.id == 1


def test_score_baixo_nao_vincula():
    empresas = [_empresa(1, 'MADEIRAS TAPIA'), _empresa(2, 'VALERIA CABREIRA')]
    v = imp.resolver_empresa('ROTA 786 BEBIDAS LTDA', empresas)
    assert not v.resolvido
    assert v.origem is None


def test_empate_perfeito_nao_vincula():
    # ambos pontuam 100 (os dois sao subconjunto do nome do banco): gap = 0
    empresas = [_empresa(1, 'CASA DAS TINTAS'), _empresa(2, 'CASA DAS TINTAS SUL')]
    assert not imp.resolver_empresa('CASA DAS TINTAS SUL LTDA ME', empresas).resolvido


def test_score_maximo_com_gap_pequeno_NAO_vincula():
    """O caso que justifica o limiar duplo (ND-003).

    O melhor candidato pontua 100 — um limiar so de score vincularia na hora.
    Mas o segundo pontua 93, e escolher entre os dois seria um chute que emite
    nota fiscal com o CNPJ da empresa errada. Vai para conferencia humana.
    """
    empresas = [_empresa(1, 'SUPERMERCADO ECONOMIA'), _empresa(2, 'SUPERMERCADO ECONOMIA II')]
    v = imp.resolver_empresa('SUPERMERCADO ECONOMIA LTDA', empresas)
    assert not v.resolvido, 'score 100 com segundo colocado a 7 pontos nao pode vincular sozinho'
    assert v.empresa is None


def test_gap_suficiente_vincula_mesmo_com_segundo_parecido():
    empresas = [_empresa(1, 'E E C PEREIRA'), _empresa(2, 'E E C PEREIRA FILIAL')]
    v = imp.resolver_empresa('E E C PEREIRA LTDA', empresas)
    assert v.empresa.id == 1


def test_limiares_sao_os_do_nd_003():
    # se alguem afrouxar isso, os testes acima perdem o sentido
    assert imp.LIMIAR_SCORE == 90
    assert imp.LIMIAR_GAP == 10


# --- comportamento agregado sobre o arquivo inteiro ------------------------

def _empresas_da_fixture():
    """Cadastro no estilo real: apelido curto, subconjunto do nome do banco."""
    nomes = [
        'ALUMAP', 'BOA VISTA TRANSPORTES', 'VALE VERDE AGROPECUARIA',
        'TECNOFRIO', 'LAVANDERIA CRISTAL', 'HORIZONTE CONSTRUCOES',
        'NORTEC', 'SIGMA', 'RENATO FIGUEIRA', 'MADEIREIRA TRES PINHEIROS',
    ]
    return [_empresa(i, nome) for i, nome in enumerate(nomes, start=1)]


def test_nenhum_vinculo_incorreto_na_fixture():
    """Criterio de sucesso da spec: zero vinculos incorretos.

    Toda linha resolvida automaticamente precisa apontar para uma empresa cujo
    nome cadastrado seja de fato subconjunto do nome vindo do banco. Um vinculo
    que nao satisfaz isso e um match errado — nota fiscal com CNPJ de terceiro.
    """
    empresas = _empresas_da_fixture()
    with open(FIXTURE, 'rb') as fh:
        linhas = imp.parse_csv(fh.read())

    resolvidas = 0
    for linha in linhas:
        if linha.invalida or not linha.nome:
            continue
        v = imp.resolver_empresa(linha.nome, empresas)
        if not v.resolvido:
            continue
        resolvidas += 1
        tokens_cadastro = set(imp.normalizar_nome(v.empresa.nome).split())
        tokens_banco = set(linha.nome_norm.split())
        assert tokens_cadastro <= tokens_banco, (
            f'vinculo incorreto: "{linha.nome}" -> "{v.empresa.nome}"')
    assert resolvidas > 0, 'a fixture precisa ter casos que resolvem sozinhos'


# --- memoria de documento avulso (CPF ou CNPJ nao cadastrado) --------------

def test_apelido_com_documento_avulso_resolve_sem_empresa():
    """Parte dos tomadores e pessoa fisica e nunca vai virar cadastro. Sem essa
    memoria o operador redigitaria o CPF todo mes."""
    empresas = [_empresa(1, 'OUTRA COISA')]
    apelidos = [_apelido('RODRIGO FERREIRA FARIA', documento='529.982.247-25',
                         tipo_documento='cpf')]
    v = imp.resolver_empresa('RODRIGO FERREIRA FARIA', empresas, apelidos)
    assert v.resolvido
    assert v.empresa is None
    assert v.documento == '529.982.247-25'
    assert v.tipo_documento == 'cpf'
    assert v.origem == OrigemVinculoNfse.APELIDO


def test_apelido_com_cnpj_avulso_tambem_resolve():
    apelidos = [_apelido('CONSTRUTORA NOVA LTDA', documento='33.684.001/0001-51',
                         tipo_documento='cnpj')]
    v = imp.resolver_empresa('CONSTRUTORA NOVA LTDA', [], apelidos)
    assert v.documento == '33.684.001/0001-51'
    assert v.empresa is None


def test_empresa_cadastrada_tem_precedencia_sobre_documento_avulso():
    """Se a empresa foi cadastrada depois, o cadastro passa a mandar: ele e a
    fonte de verdade do CNPJ."""
    empresas = [_empresa(1, 'CONSTRUTORA NOVA')]
    apelidos = [_apelido('CONSTRUTORA NOVA LTDA', empresa_id=1,
                         documento='99.999.999/9999-99', tipo_documento='cnpj')]
    v = imp.resolver_empresa('CONSTRUTORA NOVA LTDA', empresas, apelidos)
    assert v.empresa.id == 1
    assert v.documento is None
