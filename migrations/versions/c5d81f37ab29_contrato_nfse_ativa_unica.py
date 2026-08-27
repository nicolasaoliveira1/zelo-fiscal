"""Sentinela que torna "só uma versão ativa" uma regra do banco.

Antes desta migration o invariante existia apenas no serviço, apoiado no
`with_for_update()` de `nfse_contrato.ativar()`. O dialeto SQLite do SQLAlchemy
descarta `FOR UPDATE` em silêncio, e SQLite é o banco padrão quando
`DATABASE_URL` não está definido — ou seja, em dev/CI o guarda era no-op e uma
segunda linha `estado='ativa'` passaria sem acusar nada.

`ativa_unica` vale 1 quando `estado='ativa'` e NULL nos demais casos. NULL não
colide em índice único nem no MySQL nem no SQLite, então a constraint deixa
passar quantas arquivadas existirem e barra a segunda ativa.

Revision ID: c5d81f37ab29
Revises: a2c7f4b18d93
"""
from alembic import op
import sqlalchemy as sa


revision = 'c5d81f37ab29'
down_revision = 'a2c7f4b18d93'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('contrato_nfse') as lote:
        lote.add_column(sa.Column('ativa_unica', sa.Integer(), nullable=True))
    # Backfill antes da constraint: uma base ja em uso tem a ativa corrente, e
    # ela precisa carregar a sentinela para que a proxima ativacao seja barrada
    # em vez de conviver com a antiga.
    op.execute(
        "UPDATE contrato_nfse SET ativa_unica = 1 WHERE estado = 'ativa'"
    )
    with op.batch_alter_table('contrato_nfse') as lote:
        lote.create_unique_constraint('uq_contrato_nfse_ativa', ['ativa_unica'])


def downgrade():
    with op.batch_alter_table('contrato_nfse') as lote:
        lote.drop_constraint('uq_contrato_nfse_ativa', type_='unique')
        lote.drop_column('ativa_unica')
