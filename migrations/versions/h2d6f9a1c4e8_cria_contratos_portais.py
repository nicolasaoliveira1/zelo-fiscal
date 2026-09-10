"""cria contratos versionados dos portais de certidões

Revision ID: h2d6f9a1c4e8
Revises: g1c4e8b2d6f0
"""
import sqlalchemy as sa
from alembic import op


revision = 'h2d6f9a1c4e8'
down_revision = 'g1c4e8b2d6f0'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'contrato_portal',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('fluxo', sa.String(length=40), nullable=False),
        sa.Column('alvo', sa.String(length=190), nullable=False),
        sa.Column('versao', sa.Integer(), nullable=False),
        sa.Column('estado', sa.String(length=30), nullable=False),
        sa.Column('ativa_unica', sa.String(length=64), nullable=True),
        sa.Column('host', sa.String(length=255), nullable=False),
        sa.Column('rota', sa.String(length=500), nullable=False),
        sa.Column('fingerprint', sa.String(length=64), nullable=False),
        sa.Column('base_id', sa.Integer(), nullable=True),
        sa.Column('origem', sa.String(length=20), nullable=False),
        sa.Column('criado_em', sa.DateTime(), nullable=False),
        sa.Column('classificado_em', sa.DateTime(), nullable=True),
        sa.Column('ativado_em', sa.DateTime(), nullable=True),
        sa.Column('criado_por_id', sa.Integer(), nullable=True),
        sa.Column('ativado_por_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ['base_id'], ['contrato_portal.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(
            ['criado_por_id'], ['usuario.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(
            ['ativado_por_id'], ['usuario.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'fluxo', 'alvo', 'versao',
            name='uq_contrato_portal_fluxo_alvo_versao'),
        sa.UniqueConstraint(
            'ativa_unica', name='uq_contrato_portal_ativa_unica'),
    )
    with op.batch_alter_table('contrato_portal', schema=None) as batch_op:
        for coluna in (
            'fluxo', 'alvo', 'estado', 'fingerprint', 'base_id',
            'criado_por_id', 'ativado_por_id',
        ):
            batch_op.create_index(
                batch_op.f(f'ix_contrato_portal_{coluna}'),
                [coluna], unique=False)

    op.create_table(
        'elemento_contrato_portal',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('contrato_id', sa.Integer(), nullable=False),
        sa.Column('chave', sa.String(length=100), nullable=False),
        sa.Column('etapa', sa.String(length=50), nullable=False),
        sa.Column('papel', sa.String(length=40), nullable=False),
        sa.Column('acao', sa.String(length=40), nullable=False),
        sa.Column('seletor_tipo', sa.String(length=20), nullable=False),
        sa.Column('seletor', sa.String(length=500), nullable=False),
        sa.Column('tag', sa.String(length=30), nullable=False),
        sa.Column('tipo', sa.String(length=50), nullable=False),
        sa.Column('rotulo', sa.String(length=500), nullable=False),
        sa.Column('assinatura_formulario', sa.String(length=64), nullable=False),
        sa.Column('ordem_relativa', sa.Integer(), nullable=False),
        sa.Column('obrigatorio', sa.Boolean(), nullable=False),
        sa.Column('visivel', sa.Boolean(), nullable=False),
        sa.Column('somente_leitura', sa.Boolean(), nullable=False),
        sa.Column('autoajuste_seletor', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ['contrato_id'], ['contrato_portal.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'contrato_id', 'chave',
            name='uq_elemento_contrato_portal_chave'),
    )
    with op.batch_alter_table('elemento_contrato_portal', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_elemento_contrato_portal_contrato_id'),
            ['contrato_id'], unique=False)
        batch_op.create_index(
            batch_op.f('ix_elemento_contrato_portal_etapa'),
            ['etapa'], unique=False)

    op.create_table(
        'incidente_contrato_portal',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('contrato_base_id', sa.Integer(), nullable=False),
        sa.Column('contrato_candidato_id', sa.Integer(), nullable=True),
        sa.Column('assinatura', sa.String(length=64), nullable=False),
        sa.Column('estado', sa.String(length=20), nullable=False),
        sa.Column('classificacao', sa.String(length=30), nullable=False),
        sa.Column('severidade', sa.String(length=20), nullable=False),
        sa.Column('etapa', sa.String(length=50), nullable=True),
        sa.Column('elemento_chave', sa.String(length=100), nullable=True),
        sa.Column('mensagem', sa.String(length=500), nullable=False),
        sa.Column('artefato_sanitizado', sa.String(length=2000), nullable=True),
        sa.Column('primeira_observacao_em', sa.DateTime(), nullable=False),
        sa.Column('ultima_observacao_em', sa.DateTime(), nullable=False),
        sa.Column('observacoes', sa.Integer(), nullable=False),
        sa.Column('resolvido_em', sa.DateTime(), nullable=True),
        sa.Column('resolvido_por_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ['contrato_base_id'], ['contrato_portal.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(
            ['contrato_candidato_id'], ['contrato_portal.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(
            ['resolvido_por_id'], ['usuario.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'contrato_base_id', 'assinatura',
            name='uq_incidente_contrato_portal_assinatura'),
    )
    with op.batch_alter_table('incidente_contrato_portal', schema=None) as batch_op:
        for coluna in (
            'contrato_base_id', 'contrato_candidato_id', 'estado',
            'classificacao', 'resolvido_por_id',
        ):
            batch_op.create_index(
                batch_op.f(f'ix_incidente_contrato_portal_{coluna}'),
                [coluna], unique=False)

    op.create_table(
        'diferenca_contrato_portal',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('incidente_id', sa.Integer(), nullable=False),
        sa.Column('ordem', sa.Integer(), nullable=False),
        sa.Column('dimensao', sa.String(length=40), nullable=False),
        sa.Column('esperado', sa.String(length=1000), nullable=True),
        sa.Column('observado', sa.String(length=1000), nullable=True),
        sa.Column('evidencia_sanitizada', sa.String(length=1000), nullable=True),
        sa.ForeignKeyConstraint(
            ['incidente_id'], ['incidente_contrato_portal.id'],
            ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('diferenca_contrato_portal', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_diferenca_contrato_portal_incidente_id'),
            ['incidente_id'], unique=False)


def downgrade():
    op.drop_table('diferenca_contrato_portal')
    op.drop_table('incidente_contrato_portal')
    op.drop_table('elemento_contrato_portal')
    op.drop_table('contrato_portal')
