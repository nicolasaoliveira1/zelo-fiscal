"""Lote municipal de Imbe: as DUAS certidoes (geral e mobiliario) no mesmo lote.

Imbe e o unico municipio com duas certidoes municipais por empresa (telas
diferentes no portal). O lote antes filtrava por subtipo, obrigando o operador a
rodar dois lotes; hoje as duas entram juntas e saem uma depois da outra. Sem
Selenium: exercita so o calculo dos alvos.
"""
from datetime import date, timedelta

import pytest

from app import db
from app.models import (
    Certidao,
    Empresa,
    StatusEspecial,
    SubtipoCertidao,
    TipoCertidao,
)
from app.routes import lotes


@pytest.fixture()
def ctx(app):
    with app.app_context():
        db.create_all()
        yield app
        db.session.rollback()
        db.session.remove()
        db.drop_all()


def _empresa(nome, cnpj, cidade='Imbé'):
    empresa = Empresa(nome=nome, cnpj=cnpj, cidade=cidade, estado='RS')
    db.session.add(empresa)
    db.session.commit()
    return empresa


def _par_imbe(empresa, *, validade=None, pendente=False):
    """As duas municipais de Imbe da empresa, na ordem (geral, mobiliario)."""
    criadas = []
    for subtipo in (SubtipoCertidao.GERAL, SubtipoCertidao.MOBILIARIO):
        certidao = Certidao(
            empresa_id=empresa.id,
            tipo=TipoCertidao.MUNICIPAL,
            subtipo=subtipo,
            data_validade=validade,
            status_especial=StatusEspecial.PENDENTE if pendente else None,
        )
        db.session.add(certidao)
        criadas.append(certidao)
    db.session.commit()
    return criadas


def test_lote_vencidas_pega_geral_e_mobiliario(ctx):
    empresa = _empresa('Alfa', '00.000.000/0001-00')
    ontem = date.today() - timedelta(days=1)
    geral, mobiliario = _par_imbe(empresa, validade=ontem)

    dados = lotes._calc_municipal_targets_by_scope(geral.id, scope='default')

    assert dados['ids'] == [geral.id, mobiliario.id], dados['ids']
    assert dados['total'] == 2
    assert dados['vencidas'] == 2


def test_lote_pendentes_pega_geral_e_mobiliario(ctx):
    empresa = _empresa('Beta', '00.000.000/0002-00')
    geral, mobiliario = _par_imbe(empresa, pendente=True)

    dados = lotes._calc_municipal_targets_by_scope(geral.id, scope='pendentes')

    assert dados['scope'] == 'pendentes'
    assert set(dados['ids']) == {geral.id, mobiliario.id}, dados['ids']
    assert dados['pendentes'] == 2


def test_partindo_do_mobiliario_traz_a_geral_junto(ctx):
    """A certidao clicada abre o lote — a irma vem junto, nao importa qual seja."""
    empresa = _empresa('Gama', '00.000.000/0003-00')
    ontem = date.today() - timedelta(days=1)
    geral, mobiliario = _par_imbe(empresa, validade=ontem)

    dados = lotes._calc_municipal_targets_by_scope(mobiliario.id, scope='default')

    assert dados['ids'] == [mobiliario.id, geral.id], dados['ids']
    assert dados['start_incluida'] is True


def test_par_da_mesma_empresa_sai_junto_e_a_inicial_primeiro(ctx):
    """Ids intercalados entre empresas: o lote agrupa por empresa para as duas de
    Imbe sairem uma depois da outra, e o grupo da certidao clicada vai na frente."""
    ontem = date.today() - timedelta(days=1)
    a = _empresa('Alfa', '00.000.000/0001-00')
    b = _empresa('Beta', '00.000.000/0002-00')
    # criacao intercalada -> ids em zigue-zague entre as duas empresas
    a_geral = Certidao(empresa_id=a.id, tipo=TipoCertidao.MUNICIPAL,
                       subtipo=SubtipoCertidao.GERAL, data_validade=ontem)
    b_geral = Certidao(empresa_id=b.id, tipo=TipoCertidao.MUNICIPAL,
                       subtipo=SubtipoCertidao.GERAL, data_validade=ontem)
    a_mob = Certidao(empresa_id=a.id, tipo=TipoCertidao.MUNICIPAL,
                     subtipo=SubtipoCertidao.MOBILIARIO, data_validade=ontem)
    b_mob = Certidao(empresa_id=b.id, tipo=TipoCertidao.MUNICIPAL,
                     subtipo=SubtipoCertidao.MOBILIARIO, data_validade=ontem)
    for c in (a_geral, b_geral, a_mob, b_mob):
        db.session.add(c)
        db.session.commit()

    dados = lotes._calc_municipal_targets_by_scope(b_mob.id, scope='default')

    assert dados['ids'] == [b_mob.id, b_geral.id, a_geral.id, a_mob.id], dados['ids']


def test_outra_cidade_nao_entra_no_lote_de_imbe(ctx):
    """O recorte por cidade continua valendo — o que caiu foi so o de subtipo."""
    ontem = date.today() - timedelta(days=1)
    imbe = _empresa('Alfa', '00.000.000/0001-00', cidade='Imbé')
    tramandai = _empresa('Beta', '00.000.000/0002-00', cidade='Tramandai')
    geral, mobiliario = _par_imbe(imbe, validade=ontem)
    outra = Certidao(empresa_id=tramandai.id, tipo=TipoCertidao.MUNICIPAL,
                     data_validade=ontem)
    db.session.add(outra)
    db.session.commit()

    dados = lotes._calc_municipal_targets_by_scope(geral.id, scope='default')

    assert dados['ids'] == [geral.id, mobiliario.id], dados['ids']
