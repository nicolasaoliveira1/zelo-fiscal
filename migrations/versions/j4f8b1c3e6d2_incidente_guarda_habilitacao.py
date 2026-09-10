"""elemento do contrato de portal guarda a habilitacao do controle

O comparador compara `habilitacao_alterada`, mas a coluna nao existia: ao
recarregar do banco o valor voltava sempre como False, e um controle que nasce
desabilitado na baseline gerava drift espurio em todo preflight seguinte.

Revision ID: j4f8b1c3e6d2
Revises: i3e7a0b2d5c9
"""
import sqlalchemy as sa
from alembic import op


revision = 'j4f8b1c3e6d2'
down_revision = 'i3e7a0b2d5c9'
branch_labels = None
depends_on = None


def upgrade():
    # server_default e obrigatorio aqui: a coluna e NOT NULL e a tabela pode ter
    # linhas: sem ele o InnoDB recusa o ALTER. Fica na coluna de proposito —
    # remover exigiria um segundo ALTER que nao paga o que custa.
    with op.batch_alter_table('elemento_contrato_portal', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'desabilitado', sa.Boolean(), nullable=False,
            server_default=sa.text('0')))


def downgrade():
    with op.batch_alter_table('elemento_contrato_portal', schema=None) as batch_op:
        batch_op.drop_column('desabilitado')
