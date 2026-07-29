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
    _empresa()
    bruto = _linha() + '\n' + _linha()
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
