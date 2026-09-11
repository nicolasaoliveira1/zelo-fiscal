"""adiciona versão revogável à sessão do usuário

Revision ID: k5a2d9f1c7e4
Revises: j4f8b1c3e6d2
"""
import sqlalchemy as sa
from alembic import op


revision = 'k5a2d9f1c7e4'
down_revision = 'j4f8b1c3e6d2'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('usuario', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'sessao_versao', sa.Integer(), nullable=False,
            server_default=sa.text('1')))


def downgrade():
    with op.batch_alter_table('usuario', schema=None) as batch_op:
        batch_op.drop_column('sessao_versao')
