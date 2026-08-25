"""cria contratos, campos, opcoes e incidentes da NFSe

Revision ID: f8b4c2d6e9a1
Revises: b4d7e91a2f06
"""
import sqlalchemy as sa
from alembic import op


revision = 'f8b4c2d6e9a1'
down_revision = 'b4d7e91a2f06'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'contrato_nfse',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('versao', sa.Integer(), nullable=False),
        sa.Column('estado', sa.String(length=20), nullable=False),
        sa.Column('fingerprint', sa.String(length=64), nullable=False),
        sa.Column('elegivel_automatico', sa.Boolean(), nullable=False),
        sa.Column('criado_em', sa.DateTime(), nullable=False),
        sa.Column('validado_em', sa.DateTime(), nullable=True),
        sa.Column('ativado_em', sa.DateTime(), nullable=True),
        sa.Column('criado_por_id', sa.Integer(), nullable=True),
        sa.Column('ativado_por_id', sa.Integer(), nullable=True),
        sa.Column('nota_validacao_id', sa.Integer(), nullable=True),
        sa.Column('erro_validacao', sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(
            ['criado_por_id'], ['usuario.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(
            ['ativado_por_id'], ['usuario.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(
            ['nota_validacao_id'], ['nota_nfse.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('versao', name='uq_contrato_nfse_versao'),
    )
    with op.batch_alter_table('contrato_nfse', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_contrato_nfse_estado'), ['estado'], unique=False)
        batch_op.create_index(
            batch_op.f('ix_contrato_nfse_fingerprint'),
            ['fingerprint'], unique=False)
        batch_op.create_index(
            batch_op.f('ix_contrato_nfse_criado_por_id'),
            ['criado_por_id'], unique=False)
        batch_op.create_index(
            batch_op.f('ix_contrato_nfse_ativado_por_id'),
            ['ativado_por_id'], unique=False)
        batch_op.create_index(
            batch_op.f('ix_contrato_nfse_nota_validacao_id'),
            ['nota_validacao_id'], unique=False)

    op.create_table(
        'campo_contrato_nfse',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('contrato_id', sa.Integer(), nullable=False),
        sa.Column('chave_semantica', sa.String(length=100), nullable=False),
        sa.Column('etapa', sa.String(length=20), nullable=False),
        sa.Column('seletor_tipo', sa.String(length=20), nullable=False),
        sa.Column('seletor', sa.String(length=200), nullable=False),
        sa.Column('rotulo', sa.String(length=500), nullable=False),
        sa.Column('tipo', sa.String(length=30), nullable=False),
        sa.Column('interacao', sa.String(length=30), nullable=False),
        sa.Column('obrigatorio', sa.Boolean(), nullable=False),
        sa.Column('ordem', sa.Integer(), nullable=False),
        sa.Column('condicao_chave', sa.String(length=100), nullable=True),
        sa.Column('condicao_valor', sa.String(length=190), nullable=True),
        sa.Column('origem', sa.String(length=30), nullable=True),
        sa.Column('fonte', sa.String(length=100), nullable=True),
        sa.Column('valor_fixo', sa.String(length=500), nullable=True),
        sa.Column('revisao_secao', sa.String(length=100), nullable=True),
        sa.Column('revisao_rotulo', sa.String(length=500), nullable=True),
        sa.Column('conferivel_automatico', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ['contrato_id'], ['contrato_nfse.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'contrato_id', 'chave_semantica',
            name='uq_campo_contrato_nfse_chave'),
    )
    with op.batch_alter_table('campo_contrato_nfse', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_campo_contrato_nfse_contrato_id'),
            ['contrato_id'], unique=False)
        batch_op.create_index(
            batch_op.f('ix_campo_contrato_nfse_etapa'), ['etapa'], unique=False)

    op.create_table(
        'opcao_campo_contrato_nfse',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('campo_id', sa.Integer(), nullable=False),
        sa.Column('valor', sa.String(length=190), nullable=False),
        sa.Column('rotulo', sa.String(length=500), nullable=False),
        sa.Column('ordem', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ['campo_id'], ['campo_contrato_nfse.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'campo_id', 'valor', name='uq_opcao_campo_contrato_nfse_valor'),
    )
    with op.batch_alter_table('opcao_campo_contrato_nfse', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_opcao_campo_contrato_nfse_campo_id'),
            ['campo_id'], unique=False)

    op.create_table(
        'incidente_contrato_nfse',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('contrato_base_id', sa.Integer(), nullable=False),
        sa.Column('contrato_candidato_id', sa.Integer(), nullable=True),
        sa.Column('assinatura', sa.String(length=64), nullable=False),
        sa.Column('etapa', sa.String(length=20), nullable=False),
        sa.Column('tipo', sa.String(length=30), nullable=False),
        sa.Column('severidade', sa.String(length=20), nullable=False),
        sa.Column('estado', sa.String(length=20), nullable=False),
        sa.Column('chave_esperada', sa.String(length=100), nullable=True),
        sa.Column('chave_observada', sa.String(length=100), nullable=True),
        sa.Column('rotulo', sa.String(length=500), nullable=True),
        sa.Column('tipo_controle', sa.String(length=30), nullable=True),
        sa.Column('interacao', sa.String(length=30), nullable=True),
        sa.Column('obrigatorio', sa.Boolean(), nullable=True),
        sa.Column('primeira_observacao_em', sa.DateTime(), nullable=False),
        sa.Column('ultima_observacao_em', sa.DateTime(), nullable=False),
        sa.Column('observacoes', sa.Integer(), nullable=False),
        sa.Column('resolvido_em', sa.DateTime(), nullable=True),
        sa.Column('resolvido_por_id', sa.Integer(), nullable=True),
        sa.Column('mensagem', sa.String(length=500), nullable=False),
        sa.Column('artefato_sanitizado', sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(
            ['contrato_base_id'], ['contrato_nfse.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(
            ['contrato_candidato_id'], ['contrato_nfse.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(
            ['resolvido_por_id'], ['usuario.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'contrato_base_id', 'assinatura',
            name='uq_incidente_contrato_nfse_assinatura'),
    )
    with op.batch_alter_table('incidente_contrato_nfse', schema=None) as batch_op:
        for coluna in (
            'contrato_base_id', 'contrato_candidato_id', 'etapa', 'estado',
            'resolvido_por_id',
        ):
            batch_op.create_index(
                batch_op.f(f'ix_incidente_contrato_nfse_{coluna}'),
                [coluna], unique=False)

    op.create_table(
        'opcao_incidente_contrato_nfse',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('incidente_id', sa.Integer(), nullable=False),
        sa.Column('valor', sa.String(length=190), nullable=False),
        sa.Column('rotulo', sa.String(length=500), nullable=False),
        sa.Column('ordem', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ['incidente_id'], ['incidente_contrato_nfse.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('opcao_incidente_contrato_nfse', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_opcao_incidente_contrato_nfse_incidente_id'),
            ['incidente_id'], unique=False)


def downgrade():
    op.drop_table('opcao_incidente_contrato_nfse')
    op.drop_table('incidente_contrato_nfse')
    op.drop_table('opcao_campo_contrato_nfse')
    op.drop_table('campo_contrato_nfse')
    op.drop_table('contrato_nfse')
