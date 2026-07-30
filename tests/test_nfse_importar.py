"""Importacao transacional do extrato (NFSE-04..07).

Duas garantias que estes testes existem para provar:

1. Arquivo invalido nao deixa lote parcial no banco — a contagem tem de ficar
   em zero depois da recusa.
2. A chave de duplicidade e (empresa, competencia), nao (empresa, arquivo).
   No extrato real o mesmo tomador aparece duas vezes com vencimentos de meses
   diferentes: sao duas notas legitimas e as duas precisam emitir (ND-004).
"""
from decimal import Decimal

import pytest

from app import db
from app.models import (
    ApelidoNfse,
    Empresa,
    LoteNfse,
    NotaNfse,
    OrigemVinculoNfse,
    StatusNotaNfse,
)
from app.services import nfse_import as imp

BASE = ('"13/07/2026";"{nome}";"0001443038";"062623";"{venc}";'
        '"{f}";"{g}";"{h}";"{i}";"COBRANCA SIMPLES"')


@pytest.fixture()
def banco(app):
    """Schema limpo por teste, sem os dados semeados pelo fixture `ids`.

    O fixture `app` do conftest e session-scoped e nao cria schema; quem cria e
    o `ids`, que semeia uma Empresa — o que contaminaria a resolucao de nome
    exercitada aqui. `db.session.remove()` antes do `drop_all()` e obrigatorio:
    sem isso o DROP TABLE trava no MySQL por metadata lock (AD-020).
    """
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _linha(nome='ACME TRANSPORTES LTDA', venc='05/07/2026',
           f='811,00', g='16,22', h='1,13', i='826,09'):
    return BASE.format(nome=nome, venc=venc, f=f, g=g, h=h, i=i)


def _empresa(nome='ACME TRANSPORTES', cnpj='11.111.111/0001-11'):
    empresa = Empresa(nome=nome, cnpj=cnpj, cidade='Imbé', estado='RS')
    db.session.add(empresa)
    db.session.commit()
    return empresa


def _notas(lote):
    return NotaNfse.query.filter_by(lote_id=lote.id).order_by(NotaNfse.id).all()


# --- recusa nao persiste nada (NFSE-07) ------------------------------------

@pytest.mark.parametrize('conteudo', ['', '   ', 'nome,valor\nFulano,10\n'])
def test_arquivo_invalido_nao_cria_lote_nem_nota(banco, conteudo):
    with pytest.raises(imp.ArquivoInvalidoError):
        imp.importar(conteudo, nome_arquivo='ruim.csv')
    assert LoteNfse.query.count() == 0
    assert NotaNfse.query.count() == 0


# --- vinculo, competencia e trilha de auditoria ----------------------------

def test_nota_resolvida_recebe_cnpj_competencia_e_trilha(banco):
    empresa = _empresa()
    lote = imp.importar(_linha(), nome_arquivo='extrato.csv')
    nota = _notas(lote)[0]
    assert nota.empresa_id == empresa.id
    assert nota.documento == '11.111.111/0001-11'
    assert nota.competencia == '06/2026'
    assert nota.valor_final == Decimal('826.09')
    assert nota.status == StatusNotaNfse.PRONTA
    assert nota.origem_vinculo in (OrigemVinculoNfse.EXATO, OrigemVinculoNfse.FUZZY)
    assert nota.score_match is not None


def test_nome_sem_cadastro_fica_pendente_de_empresa(banco):
    _empresa()
    lote = imp.importar(_linha(nome='EMPRESA QUE NAO EXISTE SA'))
    nota = _notas(lote)[0]
    assert nota.status == StatusNotaNfse.EMPRESA_PENDENTE
    assert nota.empresa_id is None
    assert nota.documento is None


def test_apelido_salvo_resolve_na_importacao(banco):
    empresa = _empresa(nome='JM ADMINISTRACOES')
    db.session.add(ApelidoNfse(
        nome_norm='JM ADM DE CONDOMINIOS LTDA', empresa_id=empresa.id))
    db.session.commit()
    lote = imp.importar(_linha(nome='JM ADM DE CONDOMINIOS LTDA'))
    assert _notas(lote)[0].origem_vinculo == OrigemVinculoNfse.APELIDO


def test_linha_invalida_e_persistida_sem_abortar_o_lote(banco):
    _empresa()
    bruto = _linha() + '\n' + _linha(i='A COMBINAR')
    lote = imp.importar(bruto)
    notas = _notas(lote)
    assert len(notas) == 2
    assert notas[0].status == StatusNotaNfse.PRONTA
    assert notas[1].status == StatusNotaNfse.INVALIDA
    assert notas[1].erro


# --- divergencia F+G-H vs I (NFSE-04) --------------------------------------

def test_soma_que_bate_nao_marca_divergencia(banco):
    _empresa()
    lote = imp.importar(_linha(f='811,00', g='16,22', h='1,13', i='826,09'))
    assert _notas(lote)[0].divergencia_valor is False


def test_soma_que_nao_bate_marca_divergencia_mas_mantem_o_valor_final(banco):
    _empresa()
    lote = imp.importar(_linha(f='811,00', g='0,00', h='0,00', i='999,99'))
    nota = _notas(lote)[0]
    assert nota.divergencia_valor is True
    # a coluna I continua sendo o valor a emitir: a conta e so rede de seguranca
    assert nota.valor_final == Decimal('999.99')


def test_diferenca_de_um_centavo_fica_dentro_da_tolerancia(banco):
    _empresa()
    lote = imp.importar(_linha(f='811,00', g='0,00', h='0,00', i='811,01'))
    assert _notas(lote)[0].divergencia_valor is False


# --- duplicidade por (empresa, competencia) — ND-004 -----------------------

def test_mesmo_tomador_com_competencias_diferentes_nao_e_duplicata(banco):
    """O caso real do extrato: pagamento atrasado do mes anterior junto com o
    do mes corrente. Vencimentos 05/06 e 05/07 -> competencias 05/2026 e
    06/2026. Sao duas notas legitimas; travar aqui quebraria o fluxo real."""
    _empresa()
    bruto = _linha(venc='05/06/2026') + '\n' + _linha(venc='05/07/2026')
    notas = _notas(imp.importar(bruto))
    assert [n.competencia for n in notas] == ['05/2026', '06/2026']
    assert all(n.status == StatusNotaNfse.PRONTA for n in notas)


def test_mesma_competencia_no_mesmo_arquivo_marca_a_segunda_como_duplicata(banco):
    """Duas cobrancas DIFERENTES do mesmo tomador na mesma competencia.

    Linhas identicas nao servem para este caso: elas sao descartadas na leitura
    (dedupe entre arquivos). Aqui os valores diferem, entao sao duas linhas
    legitimas do extrato — e cabe ao operador decidir se emite as duas.
    """
    _empresa()
    bruto = _linha(i='826,09') + '\n' + _linha(i='500,00')
    notas = _notas(imp.importar(bruto))
    assert notas[0].status == StatusNotaNfse.PRONTA
    assert notas[1].status == StatusNotaNfse.DUPLICATA


def test_competencia_ja_emitida_marca_duplicata_na_reimportacao(banco):
    empresa = _empresa()
    primeira = _notas(imp.importar(_linha()))[0]
    primeira.status = StatusNotaNfse.EMITIDA
    db.session.commit()

    notas = _notas(imp.importar(_linha()))
    assert notas[0].status == StatusNotaNfse.DUPLICATA
    assert notas[0].empresa_id == empresa.id


def test_nota_apenas_pronta_nao_bloqueia_reimportacao(banco):
    """Só o que foi EMITIDO trava. Uma importacao anterior que nunca virou nota
    fiscal nao pode impedir a nova — senao um import errado travaria o mes."""
    _empresa()
    imp.importar(_linha())
    notas = _notas(imp.importar(_linha()))
    assert notas[0].status == StatusNotaNfse.PRONTA


def test_pendente_de_empresa_nao_vira_duplicata(banco):
    # sem empresa vinculada nao ha chave (empresa, competencia) para comparar
    _empresa()
    bruto = _linha(nome='SEM CADASTRO SA') + '\n' + _linha(nome='SEM CADASTRO SA')
    notas = _notas(imp.importar(bruto))
    assert all(n.status == StatusNotaNfse.EMPRESA_PENDENTE for n in notas)


# --- varios arquivos de uma vez --------------------------------------------

def test_importa_varios_arquivos_num_lote_so(banco):
    _empresa()
    a = _linha(nome='ACME TRANSPORTES LTDA')
    b = _linha(nome='ACME TRANSPORTES LTDA', venc='05/08/2026', i='500,00')
    lote = imp.importar([('julho.csv', a), ('agosto.csv', b)])
    notas = _notas(lote)
    assert len(notas) == 2
    assert [n.competencia for n in notas] == ['06/2026', '07/2026']
    assert 'julho.csv' in lote.nome_arquivo and 'agosto.csv' in lote.nome_arquivo


def test_linha_identica_em_dois_arquivos_entra_uma_vez_so(banco):
    """O operador baixa periodos que se sobrepoem e a mesma cobranca aparece
    nos dois arquivos. Descartar so o que e identico nao perde informacao."""
    _empresa()
    lote = imp.importar([('a.csv', _linha()), ('b.csv', _linha())])
    assert len(_notas(lote)) == 1
    assert lote.ignoradas_duplicadas == 1


def test_linha_repetida_dentro_do_mesmo_arquivo_tambem_e_descartada(banco):
    _empresa()
    lote = imp.importar([('a.csv', _linha() + '\n' + _linha())])
    assert len(_notas(lote)) == 1
    assert lote.ignoradas_duplicadas == 1


def test_linhas_parecidas_mas_diferentes_nao_sao_descartadas(banco):
    """Difere so no valor: sao duas cobrancas de verdade, ambas entram (e a
    segunda vira duplicata para o operador decidir)."""
    _empresa()
    lote = imp.importar([('a.csv', _linha(i='826,09')), ('b.csv', _linha(i='500,00'))])
    assert len(_notas(lote)) == 2
    assert lote.ignoradas_duplicadas == 0


def test_um_arquivo_invalido_recusa_a_importacao_inteira(banco):
    """Aceitar os validos deixaria o operador achando que importou tudo."""
    _empresa()
    with pytest.raises(imp.ArquivoInvalidoError) as exc:
        imp.importar([('bom.csv', _linha()), ('ruim.csv', 'nome,valor\nx,1\n')])
    assert 'ruim.csv' in str(exc.value)
    assert LoteNfse.query.count() == 0
    assert NotaNfse.query.count() == 0


def test_um_arquivo_so_continua_funcionando(banco):
    """Compatibilidade: a assinatura antiga (bytes/str direto) segue valendo."""
    _empresa()
    lote = imp.importar(_linha(), nome_arquivo='extrato.csv')
    assert len(_notas(lote)) == 1
    assert lote.nome_arquivo == 'extrato.csv'


# --- a duplicata aponta para a original ------------------------------------

def test_duplicata_no_mesmo_arquivo_aponta_para_a_original(banco):
    """Sem o vinculo o operador ve "duplicata" e nao sabe de qual — e a linha
    original pode estar em qualquer lugar de uma lista de 50."""
    _empresa()
    notas = _notas(imp.importar(_linha(i='826,09') + '\n' + _linha(i='500,00')))

    assert notas[1].status == StatusNotaNfse.DUPLICATA
    assert notas[1].duplicata_de_id == notas[0].id


def test_duplicata_de_reimportacao_aponta_para_a_nota_ja_emitida(banco):
    _empresa()
    primeira = _notas(imp.importar(_linha()))[0]
    primeira.status = StatusNotaNfse.EMITIDA
    db.session.commit()
    id_emitida = primeira.id

    notas = _notas(imp.importar(_linha()))
    assert notas[0].status == StatusNotaNfse.DUPLICATA
    assert notas[0].duplicata_de_id == id_emitida


def test_linha_normal_nao_ganha_vinculo_de_duplicata(banco):
    _empresa()
    notas = _notas(imp.importar(_linha()))
    assert notas[0].status == StatusNotaNfse.PRONTA
    assert notas[0].duplicata_de_id is None


def test_nota_preenchida_esperando_confirmacao_tambem_bloqueia(banco):
    """Reimportar o extrato nao pode devolver como Pronta uma linha que ja tem
    DPS aberta no portal: preencher de novo abriria uma segunda para o mesmo
    tomador e a mesma competencia."""
    _empresa()
    primeira = _notas(imp.importar(_linha()))[0]
    primeira.status = StatusNotaNfse.AGUARDANDO_CONFIRMACAO
    db.session.commit()
    id_esperando = primeira.id

    notas = _notas(imp.importar(_linha()))
    assert notas[0].status == StatusNotaNfse.DUPLICATA
    assert notas[0].duplicata_de_id == id_esperando


@pytest.mark.parametrize('status', [
    StatusNotaNfse.PRONTA,
    StatusNotaNfse.FALHA,
    StatusNotaNfse.PULADA,
])
def test_status_que_nao_ocupam_a_competencia_nao_bloqueiam(banco, status):
    """Uma tentativa que nao vingou nao pode impedir a proxima importacao."""
    _empresa()
    primeira = _notas(imp.importar(_linha()))[0]
    primeira.status = status
    db.session.commit()

    assert _notas(imp.importar(_linha()))[0].status == StatusNotaNfse.PRONTA
