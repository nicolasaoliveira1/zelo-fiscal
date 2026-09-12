"""Modela o cursor, as observações e os eventos da conferência por ADN.

As observações preservam os dois retratos da nota emitida e o cursor mantém o
último NSU confirmado. Os campos novos das tabelas existentes são anuláveis
quando o valor só pode ser derivado pela conferência futura.

Revision ID: l6b3e8d1f4a2
Revises: k5a2d9f1c7e4
"""
import sqlalchemy as sa
from alembic import op


revision = 'l6b3e8d1f4a2'
down_revision = 'k5a2d9f1c7e4'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('configuracao_nfse', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'empresa_escritorio_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column(
            'api_ambiente', sa.String(length=10), nullable=False,
            server_default='restrita'))
        batch_op.add_column(sa.Column(
            'api_habilitada', sa.Boolean(), nullable=False,
            server_default=sa.text('0')))

    with op.batch_alter_table('configuracao_nfse', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_configuracao_nfse_empresa_escritorio_id'),
            ['empresa_escritorio_id'], unique=False)

    with op.batch_alter_table('configuracao_nfse', schema=None) as batch_op:
        batch_op.create_foreign_key(
            'fk_configuracao_nfse_empresa_escritorio_id_empresa',
            'empresa', ['empresa_escritorio_id'], ['id'])

    with op.batch_alter_table('nota_emitida_nfse', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'origem_autoritativa', sa.String(length=10), nullable=True))
        batch_op.add_column(sa.Column(
            'situacao_fiscal', sa.String(length=12), nullable=True))

    with op.batch_alter_table('nota_emitida_nfse', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_nota_emitida_nfse_origem_autoritativa'),
            ['origem_autoritativa'], unique=False)
        batch_op.create_index(
            batch_op.f('ix_nota_emitida_nfse_situacao_fiscal'),
            ['situacao_fiscal'], unique=False)

    op.create_table(
        'sincronizacao_adn_nfse',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('ambiente', sa.String(length=10), nullable=False),
        sa.Column('documento_consulta', sa.String(length=14), nullable=False),
        sa.Column('ultimo_nsu', sa.BigInteger(), nullable=True),
        sa.Column('dono_execucao', sa.String(length=40), nullable=True),
        sa.Column('lease_ate', sa.DateTime(), nullable=True),
        sa.Column('atualizado_em', sa.DateTime(), nullable=False),
        sa.Column('ultima_falha', sa.String(length=500), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'ambiente', 'documento_consulta',
            name='uq_sincronizacao_adn_nfse_ambiente_documento'),
    )

    op.create_table(
        'observacao_emitida_nfse',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('fonte', sa.String(length=10), nullable=False),
        sa.Column('chave', sa.String(length=60), nullable=False),
        sa.Column('data_geracao', sa.Date(), nullable=True),
        sa.Column('competencia_dps', sa.String(length=10), nullable=True),
        sa.Column('documento', sa.String(length=18), nullable=True),
        sa.Column('nome_tomador', sa.String(length=140), nullable=True),
        sa.Column('municipio', sa.String(length=60), nullable=True),
        sa.Column('valor', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('situacao_fonte', sa.String(length=30), nullable=True),
        sa.Column('observado_em', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'fonte', 'chave', name='uq_observacao_emitida_nfse_fonte_chave'),
    )
    with op.batch_alter_table('observacao_emitida_nfse', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_observacao_emitida_nfse_data_geracao'),
            ['data_geracao'], unique=False)
        batch_op.create_index(
            batch_op.f('ix_observacao_emitida_nfse_documento'),
            ['documento'], unique=False)

    op.create_table(
        'evento_emitida_nfse',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('chave', sa.String(length=60), nullable=False),
        sa.Column('tipo', sa.String(length=10), nullable=False),
        sa.Column('num_seq', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('data', sa.DateTime(), nullable=True),
        sa.Column('nsu', sa.BigInteger(), nullable=True),
        sa.Column('recebido_em', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'chave', 'tipo', 'num_seq',
            name='uq_evento_emitida_nfse_chave_tipo_seq'),
    )
    with op.batch_alter_table('evento_emitida_nfse', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_evento_emitida_nfse_chave'),
            ['chave'], unique=False)
        batch_op.create_index(
            batch_op.f('ix_evento_emitida_nfse_nsu'),
            ['nsu'], unique=False)


def downgrade():
    with op.batch_alter_table('evento_emitida_nfse', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_evento_emitida_nfse_nsu'))
        batch_op.drop_index(batch_op.f('ix_evento_emitida_nfse_chave'))
    op.drop_table('evento_emitida_nfse')

    with op.batch_alter_table('observacao_emitida_nfse', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_observacao_emitida_nfse_documento'))
        batch_op.drop_index(batch_op.f('ix_observacao_emitida_nfse_data_geracao'))
    op.drop_table('observacao_emitida_nfse')

    op.drop_table('sincronizacao_adn_nfse')

    with op.batch_alter_table('nota_emitida_nfse', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_nota_emitida_nfse_situacao_fiscal'))
        batch_op.drop_index(batch_op.f('ix_nota_emitida_nfse_origem_autoritativa'))
        batch_op.drop_column('situacao_fiscal')
        batch_op.drop_column('origem_autoritativa')

    with op.batch_alter_table('configuracao_nfse', schema=None) as batch_op:
        batch_op.drop_constraint(
            'fk_configuracao_nfse_empresa_escritorio_id_empresa',
            type_='foreignkey')
        batch_op.drop_index(
            batch_op.f('ix_configuracao_nfse_empresa_escritorio_id'))
        batch_op.drop_column('api_habilitada')
        batch_op.drop_column('api_ambiente')
        batch_op.drop_column('empresa_escritorio_id')
