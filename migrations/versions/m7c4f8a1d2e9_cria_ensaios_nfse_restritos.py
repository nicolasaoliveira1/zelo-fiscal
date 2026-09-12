"""persiste a numeração e as tentativas do ensaio restrito da NFS-e

Revision ID: m7c4f8a1d2e9
Revises: l6b3e8d1f4a2
"""
import sqlalchemy as sa
from alembic import op


revision = 'm7c4f8a1d2e9'
down_revision = 'l6b3e8d1f4a2'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('configuracao_nfse', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'serie_dps_restrita', sa.String(length=5), nullable=True))

    op.create_table(
        'contador_dps_nfse',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('ambiente', sa.String(length=10), nullable=False),
        sa.Column('prestador', sa.String(length=14), nullable=False),
        sa.Column('serie', sa.String(length=5), nullable=False),
        sa.Column('proximo_numero', sa.BigInteger(), nullable=False),
        sa.Column('atualizado_em', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'ambiente', 'prestador', 'serie',
            name='uq_contador_dps_nfse_ambiente_prestador_serie'),
    )

    op.create_table(
        'ensaio_dps_nfse',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('nota_nfse_id', sa.Integer(), nullable=False),
        sa.Column('operador_id', sa.Integer(), nullable=False),
        sa.Column('ambiente', sa.String(length=10), nullable=False),
        sa.Column('prestador', sa.String(length=14), nullable=False),
        sa.Column('serie', sa.String(length=5), nullable=False),
        sa.Column('numero', sa.BigInteger(), nullable=False),
        sa.Column('identificador_dps', sa.String(length=45), nullable=False),
        sa.Column('estado', sa.String(length=24), nullable=False),
        sa.Column('xml_referencia', sa.Text(), nullable=True),
        sa.Column('xml_dps_assinada', sa.Text(), nullable=True),
        sa.Column('xml_nfse_teste', sa.Text(), nullable=True),
        sa.Column('chave_nfse_teste', sa.String(length=60), nullable=True),
        sa.Column('comparacao_json', sa.Text(), nullable=True),
        sa.Column('codigo_rejeicao', sa.String(length=40), nullable=True),
        sa.Column('motivo_rejeicao', sa.String(length=1000), nullable=True),
        sa.Column('ultima_falha', sa.String(length=1000), nullable=True),
        sa.Column('criado_em', sa.DateTime(), nullable=False),
        sa.Column('atualizado_em', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ['nota_nfse_id'], ['nota_nfse.id'],
            name='fk_ensaio_dps_nfse_nota_nfse_id_nota_nfse'),
        sa.ForeignKeyConstraint(
            ['operador_id'], ['usuario.id'],
            name='fk_ensaio_dps_nfse_operador_id_usuario'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'identificador_dps', name='uq_ensaio_dps_nfse_identificador'),
        sa.UniqueConstraint(
            'ambiente', 'prestador', 'serie', 'numero',
            name='uq_ensaio_dps_nfse_ambiente_prestador_serie_numero'),
    )
    op.create_index(
        'ix_ensaio_dps_nfse_nota_nfse_id', 'ensaio_dps_nfse',
        ['nota_nfse_id'], unique=False)
    op.create_index(
        'ix_ensaio_dps_nfse_estado', 'ensaio_dps_nfse',
        ['estado'], unique=False)


def downgrade():
    with op.batch_alter_table('ensaio_dps_nfse', schema=None) as batch_op:
        # No InnoDB, os índices das colunas de FK não podem sair antes das
        # constraints que os utilizam (errno 1553).
        batch_op.drop_constraint(
            'fk_ensaio_dps_nfse_operador_id_usuario', type_='foreignkey')
        batch_op.drop_constraint(
            'fk_ensaio_dps_nfse_nota_nfse_id_nota_nfse', type_='foreignkey')
        batch_op.drop_index('ix_ensaio_dps_nfse_estado')
        batch_op.drop_index('ix_ensaio_dps_nfse_nota_nfse_id')
    op.drop_table('ensaio_dps_nfse')
    op.drop_table('contador_dps_nfse')

    with op.batch_alter_table('configuracao_nfse', schema=None) as batch_op:
        batch_op.drop_column('serie_dps_restrita')
