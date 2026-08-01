"""nfse: espelho das notas emitidas no portal

Tabela separada da `nota_nfse` de proposito: uma e a fila de trabalho montada a
partir do extrato do banco ("o que eu preciso emitir"), a outra e o que a
Receita registra ("o que eu de fato emiti"). Guardar junto perderia justamente a
diferenca entre as duas, que e o dado interessante — quem pagou e ficou sem
nota, e que nota saiu sem pagamento.

A chave de acesso (50 digitos) e unica: reconsultar o mesmo mes atualiza as
linhas em vez de duplicar o total.

Revision ID: d5f8b3c210ae
Revises: c3e7a9f14b62
"""
import sqlalchemy as sa
from alembic import op

revision = 'd5f8b3c210ae'
down_revision = 'c3e7a9f14b62'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'nota_emitida_nfse',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('chave', sa.String(length=60), nullable=False),
        sa.Column('data_geracao', sa.Date(), nullable=True),
        sa.Column('competencia', sa.String(length=7), nullable=True),
        sa.Column('documento', sa.String(length=18), nullable=True),
        sa.Column('nome_tomador', sa.String(length=140), nullable=True),
        sa.Column('municipio', sa.String(length=60), nullable=True),
        sa.Column('valor', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('situacao', sa.String(length=30), nullable=True),
        sa.Column('consultado_em', sa.DateTime(), nullable=False),
        sa.Column('nota_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['nota_id'], ['nota_nfse.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('chave'),
    )
    with op.batch_alter_table('nota_emitida_nfse', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_nota_emitida_nfse_competencia'),
                              ['competencia'], unique=False)
        batch_op.create_index(batch_op.f('ix_nota_emitida_nfse_data_geracao'),
                              ['data_geracao'], unique=False)
        batch_op.create_index(batch_op.f('ix_nota_emitida_nfse_documento'),
                              ['documento'], unique=False)
        batch_op.create_index(batch_op.f('ix_nota_emitida_nfse_situacao'),
                              ['situacao'], unique=False)
        batch_op.create_index(batch_op.f('ix_nota_emitida_nfse_nota_id'),
                              ['nota_id'], unique=False)


def downgrade():
    with op.batch_alter_table('nota_emitida_nfse', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_nota_emitida_nfse_nota_id'))
        batch_op.drop_index(batch_op.f('ix_nota_emitida_nfse_situacao'))
        batch_op.drop_index(batch_op.f('ix_nota_emitida_nfse_documento'))
        batch_op.drop_index(batch_op.f('ix_nota_emitida_nfse_data_geracao'))
        batch_op.drop_index(batch_op.f('ix_nota_emitida_nfse_competencia'))
    op.drop_table('nota_emitida_nfse')
