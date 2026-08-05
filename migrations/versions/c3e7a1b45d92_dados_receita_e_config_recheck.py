"""spec 08: tabela dados_receita e config do recheck

Cria o espelho do que a Receita informa sobre a empresa, em tabela separada da
`Empresa` de proposito: uma guarda o que o escritorio decidiu, a outra o que a
Receita diz, e a diferenca entre as duas e justamente o dado que o operador
precisa conferir.

Sem backfill: a tabela nasce vazia e o job de recheck a preenche. `verificado_em`
nulo entra primeiro na fila, por ser "a mais velha".

Revision ID: c3e7a1b45d92
Revises: e7c1a4b98d20
"""
import sqlalchemy as sa
from alembic import op

revision = 'c3e7a1b45d92'
down_revision = 'e7c1a4b98d20'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'dados_receita',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('empresa_id', sa.Integer(), nullable=False),
        # situacao em String, nunca enum nativo (AD-016/AD-020)
        sa.Column('situacao', sa.String(length=40), nullable=True),
        sa.Column('situacao_data', sa.Date(), nullable=True),
        sa.Column('situacao_motivo', sa.String(length=200), nullable=True),
        sa.Column('razao_social', sa.String(length=200), nullable=True),
        sa.Column('nome_fantasia', sa.String(length=200), nullable=True),
        sa.Column('data_inicio_atividade', sa.Date(), nullable=True),
        sa.Column('porte', sa.String(length=40), nullable=True),
        sa.Column('natureza_juridica', sa.String(length=120), nullable=True),
        sa.Column('matriz_filial', sa.String(length=20), nullable=True),
        sa.Column('logradouro', sa.String(length=200), nullable=True),
        sa.Column('numero', sa.String(length=20), nullable=True),
        sa.Column('complemento', sa.String(length=120), nullable=True),
        sa.Column('bairro', sa.String(length=100), nullable=True),
        sa.Column('cep', sa.String(length=9), nullable=True),
        sa.Column('municipio_ibge', sa.String(length=7), nullable=True),
        sa.Column('municipio', sa.String(length=100), nullable=True),
        sa.Column('uf', sa.String(length=2), nullable=True),
        sa.Column('cnae_fiscal', sa.String(length=10), nullable=True),
        sa.Column('cnae_descricao', sa.String(length=200), nullable=True),
        # nullable: None ("a fonte nao informou") != False ("nao optante")
        sa.Column('opcao_simples', sa.Boolean(), nullable=True),
        sa.Column('opcao_simples_data', sa.Date(), nullable=True),
        sa.Column('fonte', sa.String(length=20), nullable=True),
        sa.Column('verificado_em', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['empresa_id'], ['empresa.id'],
                                name=op.f('fk_dados_receita_empresa_id_empresa')),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('empresa_id', name=op.f('uq_dados_receita_empresa_id')),
    )
    # Indice em bloco separado do create_table pelo mesmo motivo das migrations
    # anteriores (no SQLite o batch recria a tabela).
    with op.batch_alter_table('dados_receita', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_dados_receita_verificado_em'),
                              ['verificado_em'], unique=False)

    with op.batch_alter_table('configuracao_sistema', schema=None) as batch_op:
        batch_op.add_column(sa.Column('receita_recheck_ativo', sa.Boolean(),
                                      nullable=False, server_default=sa.true()))
        batch_op.add_column(sa.Column('receita_recheck_idade_dias', sa.Integer(),
                                      nullable=False, server_default='30'))
        batch_op.add_column(sa.Column('receita_recheck_limite', sa.Integer(),
                                      nullable=False, server_default='50'))


def downgrade():
    with op.batch_alter_table('configuracao_sistema', schema=None) as batch_op:
        batch_op.drop_column('receita_recheck_limite')
        batch_op.drop_column('receita_recheck_idade_dias')
        batch_op.drop_column('receita_recheck_ativo')

    with op.batch_alter_table('dados_receita', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_dados_receita_verificado_em'))

    op.drop_table('dados_receita')
