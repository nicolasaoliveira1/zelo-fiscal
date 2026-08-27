"""incidente da NFS-e guarda a posicao do controle na etapa

Revision ID: a2c7f4b18d93
Revises: f8b4c2d6e9a1
"""
import sqlalchemy as sa
from alembic import op

revision = 'a2c7f4b18d93'
down_revision = 'f8b4c2d6e9a1'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'incidente_contrato_nfse',
        sa.Column('ordem_pagina', sa.Integer(), nullable=True),
    )


def downgrade():
    op.drop_column('incidente_contrato_nfse', 'ordem_pagina')
