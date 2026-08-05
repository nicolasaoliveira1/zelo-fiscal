"""Modelo DadosReceita e config do recheck (spec 08, DATA-02.1/02.7).

Tabela 1:1 com Empresa, separada de proposito: Empresa guarda o que o
escritorio decidiu, DadosReceita o que a Receita informa.
"""
from datetime import date, datetime

import sqlalchemy as sa

from app import db
from app.models import ConfiguracaoSistema, DadosReceita, Empresa


def _empresa(app, nome='Empresa Receita', cnpj='33.000.167/0001-01'):
    emp = Empresa(nome=nome, cnpj=cnpj, estado='RS', cidade='Porto Alegre')
    db.session.add(emp)
    db.session.commit()
    return emp


def test_relacionamento_um_para_um(app, ids):
    with app.app_context():
        emp = _empresa(app)
        emp.dados_receita = DadosReceita(situacao='ATIVA', razao_social='RAZAO LTDA')
        db.session.commit()

        recarregada = db.session.get(Empresa, emp.id)
        assert recarregada.dados_receita.situacao == 'ATIVA'
        assert recarregada.dados_receita.razao_social == 'RAZAO LTDA'
        assert recarregada.dados_receita.empresa.id == emp.id


def test_remover_empresa_remove_dados_receita(app, ids):
    """cascade delete-orphan: sem isso sobra linha orfa com FK pendurada."""
    with app.app_context():
        emp = _empresa(app)
        emp.dados_receita = DadosReceita(situacao='ATIVA')
        db.session.commit()
        empresa_id = emp.id

        db.session.delete(emp)
        db.session.commit()

        assert DadosReceita.query.filter_by(empresa_id=empresa_id).count() == 0


def test_uma_empresa_nao_aceita_dois_dados_receita(app, ids):
    """empresa_id e UNIQUE: o espelho da Receita e um por empresa."""
    with app.app_context():
        emp = _empresa(app)
        db.session.add(DadosReceita(empresa_id=emp.id, situacao='ATIVA'))
        db.session.commit()

        db.session.add(DadosReceita(empresa_id=emp.id, situacao='BAIXADA'))
        try:
            db.session.commit()
            duplicou = True
        except Exception:
            db.session.rollback()
            duplicou = False
        assert duplicou is False


def test_opcao_simples_e_tri_estado(app, ids):
    """None ("a fonte nao informou") tem de ser distinguivel de False
    ("nao optante") — sao respostas diferentes e a UI mostra diferente."""
    with app.app_context():
        emp_a = _empresa(app, 'Sem Info', '33.000.167/0001-01')
        emp_b = _empresa(app, 'Nao Optante', '11.222.333/0001-81')
        emp_a.dados_receita = DadosReceita(opcao_simples=None)
        emp_b.dados_receita = DadosReceita(opcao_simples=False)
        db.session.commit()

        assert db.session.get(Empresa, emp_a.id).dados_receita.opcao_simples is None
        assert db.session.get(Empresa, emp_b.id).dados_receita.opcao_simples is False


def test_situacao_e_string_nao_enum_nativo(app, ids):
    """AD-016/AD-020: enum nativo diverge entre SQLite e MySQL. Uma situacao
    fora da lista conhecida tem de ser gravada, nao rejeitada."""
    with app.app_context():
        emp = _empresa(app)
        emp.dados_receita = DadosReceita(situacao='SITUACAO NOVA DA RECEITA')
        db.session.commit()
        assert db.session.get(Empresa, emp.id).dados_receita.situacao == \
            'SITUACAO NOVA DA RECEITA'

    assert isinstance(DadosReceita.__table__.c.situacao.type, sa.String)


def test_verificado_em_tem_indice():
    """O job de recheck ordena por verificado_em a cada execucao."""
    indexados = {tuple(c.name for c in idx.columns)
                 for idx in DadosReceita.__table__.indexes}
    assert ('verificado_em',) in indexados


def test_campos_dos_quatro_grupos_persistem(app, ids):
    """DATA-02.1: os 4 grupos escolhidos na discuss (situacao, identificacao,
    endereco, fiscal) sobrevivem a um round-trip."""
    with app.app_context():
        emp = _empresa(app)
        emp.dados_receita = DadosReceita(
            situacao='BAIXADA', situacao_data=date(2020, 5, 1),
            situacao_motivo='EXTINCAO POR ENCERRAMENTO LIQUIDACAO VOLUNTARIA',
            razao_social='RAZAO SOCIAL LTDA', nome_fantasia='FANTASIA',
            data_inicio_atividade=date(1999, 3, 15), porte='DEMAIS',
            natureza_juridica='Sociedade Empresária Limitada', matriz_filial='MATRIZ',
            logradouro='AVENIDA REPUBLICA DO CHILE', numero='65',
            complemento='SALA 1', bairro='CENTRO', cep='20031170',
            municipio_ibge='3304557', municipio='RIO DE JANEIRO', uf='RJ',
            cnae_fiscal='6920601', cnae_descricao='Atividades de contabilidade',
            opcao_simples=True, opcao_simples_data=date(2010, 1, 1),
            fonte='brasilapi', verificado_em=datetime.now())
        db.session.commit()

        d = db.session.get(Empresa, emp.id).dados_receita
        assert d.situacao == 'BAIXADA'
        assert d.situacao_data == date(2020, 5, 1)
        assert d.razao_social == 'RAZAO SOCIAL LTDA'
        assert d.municipio_ibge == '3304557'
        assert d.cnae_fiscal == '6920601'
        assert d.opcao_simples is True
        assert d.fonte == 'brasilapi'
        assert d.verificado_em is not None


def test_razao_social_nao_toca_no_nome_da_empresa(app, ids):
    """DATA-01.8: Empresa.nome e a chave da pasta no drive de rede. A razao
    social da Receita vive em coluna propria, nunca sobrescrevendo o nome."""
    with app.app_context():
        emp = _empresa(app, nome='NOME DA PASTA')
        emp.dados_receita = DadosReceita(razao_social='RAZAO SOCIAL COMPLETAMENTE OUTRA')
        db.session.commit()

        recarregada = db.session.get(Empresa, emp.id)
        assert recarregada.nome == 'NOME DA PASTA'
        assert recarregada.dados_receita.razao_social == 'RAZAO SOCIAL COMPLETAMENTE OUTRA'


# --- ConfiguracaoSistema: parametros do recheck (DATA-02.7) ------------------

def test_config_recheck_defaults(app, ids):
    with app.app_context():
        cfg = ConfiguracaoSistema()
        db.session.add(cfg)
        db.session.commit()

        assert cfg.receita_recheck_ativo is True
        assert cfg.receita_recheck_idade_dias == 30
        assert cfg.receita_recheck_limite == 50


def test_config_recheck_editavel(app, ids):
    with app.app_context():
        cfg = ConfiguracaoSistema()
        db.session.add(cfg)
        db.session.commit()

        cfg.receita_recheck_ativo = False
        cfg.receita_recheck_idade_dias = 7
        cfg.receita_recheck_limite = 10
        db.session.commit()

        recarregada = db.session.get(ConfiguracaoSistema, cfg.id)
        assert recarregada.receita_recheck_ativo is False
        assert recarregada.receita_recheck_idade_dias == 7
        assert recarregada.receita_recheck_limite == 10
