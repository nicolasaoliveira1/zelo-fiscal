"""Visao Geral — a pagina inicial que responde "por onde eu comeco hoje".

Comeca pela contagem da carteira (OVER-02), que virou nucleo compartilhado: o
digest por e-mail e a tela fazem a MESMA pergunta, e um numero que diverge entre
os dois nao tem como o operador saber em qual acreditar.
"""
from datetime import date, timedelta

from app import db
from app.models import (Certidao, Empresa, StatusEspecial, TipoCertidao)
from app.services import snapshot_service


def _empresa(nome='EMPRESA VG'):
    emp = Empresa(nome=nome, cnpj=f'00.000.000/000{Empresa.query.count()}-00',
                  estado='RS', cidade='Tramandai')
    db.session.add(emp)
    db.session.commit()
    return emp


def _cert(emp, tipo, *, validade=None, pendente=False):
    # `status` e propriedade derivada da validade (verde/amarelo/vermelho): quem
    # decide a categoria e a DATA, nao um campo que se possa fixar no fixture.
    c = Certidao(tipo=tipo, empresa=emp, data_validade=validade,
                 status_especial=(StatusEspecial.PENDENTE if pendente else None))
    db.session.add(c)
    db.session.commit()
    return c


def test_contagem_separa_vencidas_a_vencer_e_pendentes(app, ids):
    with app.app_context():
        emp = _empresa()
        hoje = date.today()
        _cert(emp, TipoCertidao.FEDERAL, validade=hoje - timedelta(days=2))
        _cert(emp, TipoCertidao.FGTS, validade=hoje + timedelta(days=5))
        _cert(emp, TipoCertidao.ESTADUAL, pendente=True)

        assert snapshot_service.contagem_carteira() == {
            'vencidas': 1, 'a_vencer': 1, 'pendentes': 1}


def test_valida_e_sem_data_ficam_fora_da_contagem(app, ids):
    """A contagem responde "o que pede atencao", nao "quantas certidoes existem":
    valida nao pede nada, e sem data nao e afirmacao sobre validade."""
    with app.app_context():
        emp = _empresa()
        _cert(emp, TipoCertidao.FEDERAL,
              validade=date.today() + timedelta(days=200))
        _cert(emp, TipoCertidao.FGTS, validade=None)

        assert snapshot_service.contagem_carteira() == {
            'vencidas': 0, 'a_vencer': 0, 'pendentes': 0}


def test_carteira_vazia_nao_quebra(app, ids):
    with app.app_context():
        assert snapshot_service.contagem_carteira() == {
            'vencidas': 0, 'a_vencer': 0, 'pendentes': 0}


def test_contagem_aceita_a_data_de_referencia(app, ids):
    """A data e parametro para o chamador nao precisar viajar no tempo: a mesma
    certidao e "a vencer" hoje e "vencida" depois do vencimento."""
    with app.app_context():
        emp = _empresa()
        vence = date.today() + timedelta(days=3)
        _cert(emp, TipoCertidao.FEDERAL, validade=vence)

        assert snapshot_service.contagem_carteira(hoje=date.today())['a_vencer'] == 1
        assert snapshot_service.contagem_carteira(
            hoje=vence + timedelta(days=1))['vencidas'] == 1


def test_digest_e_tela_leem_a_mesma_funcao(app, ids):
    """A prova da extracao: o digest nao tem mais laco proprio de contagem."""
    import inspect

    from app.services import notificacoes

    assert not hasattr(notificacoes, '_contagem_carteira')
    assert 'contagem_carteira' in inspect.getsource(notificacoes.montar_digest)
