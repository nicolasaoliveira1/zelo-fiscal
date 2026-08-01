"""Total do mes e conciliacao extrato x portal.

**Os dois lados usam meses diferentes, e confundi-los foi o bug da ND-027.**
`NotaNfse.competencia` e o mes de REFERENCIA do honorario; a nota correspondente
sai no portal no mes SEGUINTE (o cliente paga em julho o honorario de junho).
Por isso, aqui, o padrao dos ajudantes e: nota de referencia 06/2026, paga em
10/07, emitida em 15/07.

O que estes testes protegem:

1. o **total do mes** — o numero que o operador somava a mao. E por data de
   GERACAO, e soma so o que esta comprovadamente emitido;
2. a conciliacao por **documento + valor**, nao por competencia;
3. as **duas direcoes** da divergencia, ancoradas no mes de referencia;
4. a **idempotencia**: reconsultar nao pode dobrar nem desfazer ligacao.
"""
from datetime import date, datetime
from decimal import Decimal

import pytest

from app import db
from app.models import (
    LoteNfse,
    NotaEmitidaNfse,
    NotaNfse,
    SituacaoNotaEmitida,
    StatusNotaNfse,
)
from app.services import nfse_emitidas as emit

CNPJ_A = '11.111.111/0001-11'
CNPJ_B = '22.222.222/0001-22'
CNPJ_C = '33.333.333/0001-33'

REFERENCIA = '06/2026'          # mes do honorario
PAGAMENTO = date(2026, 7, 10)   # o cliente paga no mes seguinte
GERACAO = date(2026, 7, 15)     # e a nota sai depois do pagamento
MES_GERACAO = '07/2026'


@pytest.fixture()
def banco(app):
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _emitida(chave, documento, valor, geracao=GERACAO,
             situacao=SituacaoNotaEmitida.GERADA):
    registro = NotaEmitidaNfse(
        chave=chave, documento=documento,
        # o portal informa a competencia do DPS = mes da emissao
        competencia_dps=f'{geracao.month:02d}/{geracao.year}',
        valor=Decimal(valor), situacao=situacao, data_geracao=geracao,
        nome_tomador='TOMADOR ' + documento[:2], consultado_em=datetime.now())
    db.session.add(registro)
    return registro


def _nota(documento, valor, competencia=REFERENCIA, status=StatusNotaNfse.PRONTA,
          pagamento=PAGAMENTO):
    lote = LoteNfse.query.first()
    if lote is None:
        lote = LoteNfse(nome_arquivo='extrato.pdf', total=0)
        db.session.add(lote)
        db.session.flush()
    nota = NotaNfse(lote_id=lote.id, documento=documento, competencia=competencia,
                    valor_final=Decimal(valor), status=status,
                    data_pagamento=pagamento, nome_csv='CLIENTE ' + documento[:2])
    db.session.add(nota)
    return nota


# --- a regressao da ND-027 --------------------------------------------------

def test_nota_de_junho_emitida_em_julho_e_reconhecida(banco):
    """O CASO NORMAL, e o que a versao anterior errava.

    O honorario de junho e pago em julho e a nota sai em julho. Casar
    `NotaNfse.competencia` (06/2026) com a competencia do portal (07/2026)
    acusava "pagou e ficou sem nota" para praticamente todo cliente."""
    nota = _nota(CNPJ_A, '827.22', competencia='06/2026',
                 pagamento=date(2026, 7, 29))
    emitida = _emitida('1' * 50, CNPJ_A, '827.22', geracao=date(2026, 7, 31))
    db.session.commit()

    emit.conciliar()

    assert emitida.nota_id == nota.id
    assert emit.divergencias('06/2026')['sem_nota'] == []


def test_tres_notas_do_mesmo_cliente_no_mesmo_mes_de_emissao(banco):
    """Caso real: mesmo tomador, tres competencias, todas emitidas em julho.

    A competencia do portal e a MESMA nas tres (07/2026) — so o valor as
    distingue."""
    maio = _nota(CNPJ_A, '495.61', competencia='05/2026', pagamento=date(2026, 6, 5))
    junho = _nota(CNPJ_A, '771.01', competencia='06/2026', pagamento=date(2026, 7, 5))
    julho = _nota(CNPJ_A, '486.45', competencia='07/2026', pagamento=date(2026, 7, 28))
    e_maio = _emitida('1' * 50, CNPJ_A, '495.61', geracao=date(2026, 7, 28))
    e_junho = _emitida('2' * 50, CNPJ_A, '771.01', geracao=date(2026, 7, 28))
    e_julho = _emitida('3' * 50, CNPJ_A, '486.45', geracao=date(2026, 7, 31))
    db.session.commit()

    emit.conciliar()

    assert e_maio.nota_id == maio.id
    assert e_junho.nota_id == junho.id
    assert e_julho.nota_id == julho.id


# --- total do mes: por data de GERACAO --------------------------------------

def test_total_soma_o_que_foi_gerado_no_mes(banco):
    _emitida('1' * 50, CNPJ_A, '400.00', geracao=date(2026, 7, 15))
    _emitida('2' * 50, CNPJ_B, '1459.00', geracao=date(2026, 7, 31))
    _emitida('3' * 50, CNPJ_C, '999.00', geracao=date(2026, 6, 30))
    db.session.commit()

    resumo = emit.resumo(MES_GERACAO)
    assert resumo['quantidade'] == 2
    assert resumo['total'] == Decimal('1859.00')
    assert emit.resumo('06/2026')['total'] == Decimal('999.00')


def test_situacao_desconhecida_fica_fora_do_total_e_visivel(banco):
    """Os codigos de cancelada/substituida nunca foram observados na recon.
    Somar ou descartar por adivinhacao erraria um total fiscal."""
    _emitida('1' * 50, CNPJ_A, '400.00')
    _emitida('2' * 50, CNPJ_B, '999.00', situacao='P200_DESCONHECIDA')
    db.session.commit()

    resumo = emit.resumo(MES_GERACAO)
    assert resumo['total'] == Decimal('400.00')
    assert resumo['quantidade'] == 1
    assert resumo['outras_situacoes'] == {'P200_DESCONHECIDA': 1}


def test_total_de_mes_sem_nota_e_zero_e_nao_erro(banco):
    resumo = emit.resumo('01/2020')
    assert resumo['total'] == Decimal(0)
    assert resumo['quantidade'] == 0
    assert resumo['consultado_em'] is None


# --- conciliacao por documento + valor --------------------------------------

def test_par_encontrado_liga_a_nota(banco):
    nota = _nota(CNPJ_A, '400.00')
    emitida = _emitida('1' * 50, CNPJ_A, '400.00')
    db.session.commit()

    assert emit.conciliar() == 1
    assert emitida.nota_id == nota.id


def test_documento_diferente_nao_liga(banco):
    _nota(CNPJ_A, '400.00')
    emitida = _emitida('1' * 50, CNPJ_B, '400.00')
    db.session.commit()

    emit.conciliar()
    assert emitida.nota_id is None


def test_mesmo_valor_em_meses_seguidos_liga_pelo_mais_proximo(banco):
    """Honorario fixo repete o valor todo mes; a data desempata."""
    junho = _nota(CNPJ_A, '487.00', competencia='06/2026', pagamento=date(2026, 7, 6))
    julho = _nota(CNPJ_A, '487.00', competencia='07/2026', pagamento=date(2026, 8, 6))
    e_junho = _emitida('1' * 50, CNPJ_A, '487.00', geracao=date(2026, 7, 10))
    e_julho = _emitida('2' * 50, CNPJ_A, '487.00', geracao=date(2026, 8, 10))
    db.session.commit()

    emit.conciliar()
    assert e_junho.nota_id == junho.id
    assert e_julho.nota_id == julho.id


def test_uma_linha_nao_e_usada_por_duas_notas(banco):
    nota = _nota(CNPJ_A, '400.00')
    primeira = _emitida('1' * 50, CNPJ_A, '400.00', geracao=date(2026, 7, 15))
    segunda = _emitida('2' * 50, CNPJ_A, '400.00', geracao=date(2026, 7, 16))
    db.session.commit()

    emit.conciliar()
    ligadas = [e for e in (primeira, segunda) if e.nota_id == nota.id]
    assert len(ligadas) == 1


def test_conciliar_e_idempotente(banco):
    _nota(CNPJ_A, '400.00')
    _emitida('1' * 50, CNPJ_A, '400.00')
    db.session.commit()

    assert emit.conciliar() == 1
    assert emit.conciliar() == 0


# --- divergencias, ancoradas no mes de REFERENCIA ---------------------------

def test_pagou_e_ficou_sem_nota(banco):
    nota = _nota(CNPJ_A, '400.00')
    db.session.commit()
    emit.conciliar()

    divergentes = emit.divergencias(REFERENCIA)
    assert [n.id for n in divergentes['sem_nota']] == [nota.id]
    assert divergentes['sem_extrato'] == []


def test_nota_no_portal_sem_linha_no_extrato(banco):
    """Precisa haver extrato do periodo para a afirmacao ter base: a linha do
    CNPJ_A cobre a janela, e ai sim a nota do CNPJ_C aparece como orfa."""
    _nota(CNPJ_A, '400.00')
    _emitida('1' * 50, CNPJ_A, '400.00')
    orfa = _emitida('2' * 50, CNPJ_C, '250.00')
    db.session.commit()
    emit.conciliar()

    divergentes = emit.divergencias(REFERENCIA)
    assert [e.id for e in divergentes['sem_extrato']] == [orfa.id]
    assert divergentes['sem_nota'] == []


def test_nota_muito_depois_nao_e_orfa_desta_competencia(banco):
    """Uma nota de outubro nao pode ser cobrada da competencia de junho."""
    _emitida('1' * 50, CNPJ_C, '250.00', geracao=date(2026, 10, 20))
    db.session.commit()
    emit.conciliar()

    assert emit.divergencias('06/2026')['sem_extrato'] == []


def test_valor_diferente_entre_extrato_e_portal(banco):
    """Casa por documento na segunda passada e mostra a diferenca — em vez de
    listar a mesma nota como faltando dos dois lados."""
    _nota(CNPJ_A, '400.00')
    _emitida('1' * 50, CNPJ_A, '450.00')
    db.session.commit()
    emit.conciliar()

    divergentes = emit.divergencias(REFERENCIA)
    assert len(divergentes['valor_diferente']) == 1
    nota, emitida = divergentes['valor_diferente'][0]
    assert nota.valor_final == Decimal('400.00')
    assert emitida.valor == Decimal('450.00')
    assert divergentes['sem_nota'] == []
    assert divergentes['sem_extrato'] == []


@pytest.mark.parametrize('status', [
    StatusNotaNfse.CANCELADA,
    StatusNotaNfse.AGRUPADA,
    StatusNotaNfse.INVALIDA,
    StatusNotaNfse.DUPLICATA,
])
def test_linha_que_nao_devia_virar_nota_nao_e_cobrada(banco, status):
    """Cobrar por uma linha cancelada ou agrupada seria falso alarme."""
    _nota(CNPJ_A, '400.00', status=status)
    db.session.commit()
    emit.conciliar()

    assert emit.divergencias(REFERENCIA)['sem_nota'] == []


def test_tudo_batendo_nao_reporta_divergencia(banco):
    _nota(CNPJ_A, '400.00')
    _nota(CNPJ_B, '1459.00')
    _emitida('1' * 50, CNPJ_A, '400.00')
    _emitida('2' * 50, CNPJ_B, '1459.00')
    db.session.commit()
    emit.conciliar()

    divergentes = emit.divergencias(REFERENCIA)
    assert divergentes['sem_nota'] == []
    assert divergentes['sem_extrato'] == []
    assert divergentes['valor_diferente'] == []
    assert emit.resumo(MES_GERACAO)['total'] == Decimal('1859.00')


def test_nota_fora_do_periodo_com_extrato_nao_e_acusada(banco):
    """So da para afirmar "nota sem pagamento" onde HA extrato importado.

    Nos dados reais isso acusava 104 orfas, quase todas de periodos cujo
    extrato nunca foi importado — o sistema chamava de "sem pagamento" o que
    era "nao tenho como saber", e uma lista assim ensina a ignorar o painel."""
    _nota(CNPJ_A, '400.00', pagamento=date(2026, 7, 10))
    _emitida('1' * 50, CNPJ_A, '400.00', geracao=date(2026, 7, 15))
    # nota de um periodo sem extrato nenhum importado
    antiga = _emitida('2' * 50, CNPJ_C, '250.00', geracao=date(2026, 1, 20))
    db.session.commit()
    emit.conciliar()

    divergentes = emit.divergencias(REFERENCIA)
    assert antiga not in divergentes['sem_extrato']
    assert divergentes['nao_conferiveis'] == 1


def test_sem_extrato_algum_nao_ha_o_que_conferir(banco):
    _emitida('1' * 50, CNPJ_C, '250.00')
    db.session.commit()
    emit.conciliar()

    divergentes = emit.divergencias(REFERENCIA)
    assert divergentes['sem_extrato'] == []
    assert divergentes['nao_conferiveis'] == 1


def test_pessoa_fisica_concilia_como_qualquer_outra(banco):
    """Regressao da ND-028: com o CPF nao extraido, o mesmo tomador aparecia
    como "pagou e ficou sem nota" E como "nota sem linha" ao mesmo tempo."""
    CPF = '113.411.570-91'
    nota = _nota(CPF, '487.00')
    emitida = _emitida('1' * 50, CPF, '487.00')
    db.session.commit()

    emit.conciliar()

    assert emitida.nota_id == nota.id
    divergentes = emit.divergencias(REFERENCIA)
    assert divergentes['sem_nota'] == []
    assert divergentes['sem_extrato'] == []


def test_documento_vazio_nao_casa_com_documento_vazio(banco):
    """Duas linhas sem documento nao sao "a mesma pessoa": era assim que o CPF
    nao extraido poderia ter casado com qualquer outra nota sem documento."""
    _nota(CNPJ_A, '487.00')
    emitida = _emitida('1' * 50, CNPJ_A, '487.00')
    emitida.documento = None
    db.session.commit()

    emit.conciliar()
    assert emitida.nota_id is None
