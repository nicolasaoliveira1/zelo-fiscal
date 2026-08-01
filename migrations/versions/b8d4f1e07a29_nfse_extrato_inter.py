"""nfse: importacao do extrato do Banco Inter (PDF)

O extrato do Inter traz o que o CSV de cobrancas nao tem: a descricao do Pix.
Dela sai a competencia (literal, nao derivada de vencimento) e, quando o
pagamento nao e de honorarios, o servico prestado. Dai as colunas novas:

- `descricao_servico` NULL = honorarios, e a descricao continua vindo do
  template da configuracao. E o que faz as notas ja existentes seguirem
  funcionando sem backfill nenhum;
- as colunas `grupo_*` guardam uma PROPOSTA de agrupamento (entradas + estorno
  numa nota so), nunca um agrupamento aplicado — quem aplica e o operador;
- `servico_nfse` espelha `apelido_nfse` para o eixo do servico;
- `categoria_extrato` e o nome da categoria que o operador configurou no app do
  banco; se ele renomear la, o import precisa acompanhar sem deploy.

Revision ID: b8d4f1e07a29
Revises: a1c4e9f27b03
"""
import sqlalchemy as sa
from alembic import op

revision = 'b8d4f1e07a29'
down_revision = 'a1c4e9f27b03'
branch_labels = None
depends_on = None

# Default tambem repetido no modelo. Aqui ele preenche as linhas existentes;
# server_default e removido em seguida para a coluna nao carregar default no
# schema (o modelo e quem define o valor de novas linhas).
CATEGORIA_PADRAO = 'HONORÁRIOS - CLIENTES'


def upgrade():
    op.create_table(
        'servico_nfse',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('termo_norm', sa.String(length=140), nullable=False),
        sa.Column('descricao', sa.String(length=300), nullable=False),
        sa.Column('criado_em', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('termo_norm'),
    )

    with op.batch_alter_table('nota_nfse', schema=None) as batch_op:
        batch_op.add_column(sa.Column('descricao_servico', sa.String(length=300), nullable=True))
        batch_op.add_column(sa.Column('descricao_pendente', sa.Boolean(),
                                      nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('origem_extrato', sa.String(length=10), nullable=True))
        batch_op.add_column(sa.Column('descricao_extrato', sa.String(length=300), nullable=True))
        batch_op.add_column(sa.Column('grupo_sugerido', sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column('grupo_valor_liquido', sa.Numeric(precision=12, scale=2), nullable=True))
        batch_op.add_column(sa.Column('grupo_detalhe', sa.String(length=300), nullable=True))
        # server_default aqui e obrigatorio: a coluna e NOT NULL e a tabela pode
        # ter linhas. Sem ele o ALTER falha em MySQL com dados existentes.
        batch_op.add_column(sa.Column('grupo_descartado', sa.Boolean(),
                                      nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('agrupada_em_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('valor_ajustado', sa.Boolean(),
                                      nullable=False, server_default=sa.false()))

    # Indice e FK em bloco separado: no SQLite o batch recria a tabela, e
    # criar o indice no mesmo bloco o montaria contra a definicao antiga, que
    # ainda nao tem `grupo_sugerido` (mesmo motivo do comentario em
    # f7a3c9e2b845).
    with op.batch_alter_table('nota_nfse', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_nota_nfse_grupo_sugerido'),
                              ['grupo_sugerido'], unique=False)
        batch_op.create_foreign_key('fk_nota_nfse_agrupada_em',
                                    'nota_nfse', ['agrupada_em_id'], ['id'])
        batch_op.alter_column('descricao_pendente', server_default=None,
                              existing_type=sa.Boolean(), existing_nullable=False)
        batch_op.alter_column('grupo_descartado', server_default=None,
                              existing_type=sa.Boolean(), existing_nullable=False)
        batch_op.alter_column('valor_ajustado', server_default=None,
                              existing_type=sa.Boolean(), existing_nullable=False)

    with op.batch_alter_table('configuracao_nfse', schema=None) as batch_op:
        batch_op.add_column(sa.Column('categoria_extrato', sa.String(length=60),
                                      nullable=False,
                                      server_default=CATEGORIA_PADRAO))
        batch_op.alter_column('categoria_extrato', server_default=None,
                              existing_type=sa.String(length=60),
                              existing_nullable=False)


def downgrade():
    with op.batch_alter_table('configuracao_nfse', schema=None) as batch_op:
        batch_op.drop_column('categoria_extrato')

    with op.batch_alter_table('nota_nfse', schema=None) as batch_op:
        batch_op.drop_constraint('fk_nota_nfse_agrupada_em', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_nota_nfse_grupo_sugerido'))

    with op.batch_alter_table('nota_nfse', schema=None) as batch_op:
        batch_op.drop_column('valor_ajustado')
        batch_op.drop_column('agrupada_em_id')
        batch_op.drop_column('grupo_descartado')
        batch_op.drop_column('grupo_detalhe')
        batch_op.drop_column('grupo_valor_liquido')
        batch_op.drop_column('grupo_sugerido')
        batch_op.drop_column('descricao_extrato')
        batch_op.drop_column('origem_extrato')
        batch_op.drop_column('descricao_pendente')
        batch_op.drop_column('descricao_servico')

    op.drop_table('servico_nfse')
