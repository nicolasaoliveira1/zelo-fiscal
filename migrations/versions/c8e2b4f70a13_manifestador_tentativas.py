"""Manifestador: contador de reenvios com a mesma rejeicao

Revision ID: c8e2b4f70a13
Revises: a7f3d5c9e214
Create Date: 2026-08-18 17:30:00.000000

A SEFAZ bloqueia o CNPJ por 1 hora quando o MESMO evento volta com a MESMA
rejeicao mais de 20 vezes (NT 2018.002 — consumo indevido, cStat 656). Pior:
continuar enviando durante o bloqueio reinicia o cronometro, e 50 bloqueios
consecutivos viram bloqueio PERMANENTE, que so a SEFAZ destrava.

Sem contador nao havia como recusar o 21o reenvio — o botao "reprocessar" nao
tinha teto nenhum.
"""
import sqlalchemy as sa
from alembic import op

revision = 'c8e2b4f70a13'
down_revision = 'a7f3d5c9e214'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('chave_manifestacao', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('tentativas', sa.Integer(), nullable=False,
                      server_default='0'))


def downgrade():
    with op.batch_alter_table('chave_manifestacao', schema=None) as batch_op:
        batch_op.drop_column('tentativas')
