"""nfse: persiste o intervalo de consultas completas do portal

Revision ID: g1c4e8b2d6f0
Revises: f8b4c2d6e9a1, c5d81f37ab29
"""
from alembic import op
import sqlalchemy as sa


revision = 'g1c4e8b2d6f0'
down_revision = ('f8b4c2d6e9a1', 'c5d81f37ab29')
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'consulta_emitida_nfse',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('inicio', sa.Date(), nullable=False),
        sa.Column('fim', sa.Date(), nullable=False),
        sa.Column('consultado_em', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('consulta_emitida_nfse', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_consulta_emitida_nfse_inicio'),
            ['inicio'], unique=False)
        batch_op.create_index(
            batch_op.f('ix_consulta_emitida_nfse_fim'),
            ['fim'], unique=False)


def downgrade():
    with op.batch_alter_table('consulta_emitida_nfse', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_consulta_emitida_nfse_fim'))
        batch_op.drop_index(batch_op.f('ix_consulta_emitida_nfse_inicio'))
    op.drop_table('consulta_emitida_nfse')
