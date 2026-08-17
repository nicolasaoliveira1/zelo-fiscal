"""Manifestador de NF-e: cofre de certificados e fila de chaves

Revision ID: a7f3d5c9e214
Revises: e3a7c5d91b48
Create Date: 2026-08-17 15:40:00.000000

Duas tabelas com papeis distintos (AD-027):

- `certificado_empresa` e 1:1 com `empresa` e guarda o que o DRIVE informa sobre
  o certificado A1 — caminho e senha cifrada, **nunca o arquivo**. Tabela
  separada pelo mesmo motivo da `dados_receita` (AD-024): juntar com `empresa`
  perderia a divergencia entre o cadastro e o que existe na pasta.
- `chave_manifestacao` e a fila duravel por item. `chave` e UNIQUE porque
  identifica a NF-e globalmente; reimportar uma existente vira duplicata
  liberavel, nao erro.

Status e estado sao `String`, nunca enum nativo: o enum diverge entre SQLite e
MySQL e a suite roda nos dois (AD-016/AD-020).
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'a7f3d5c9e214'
down_revision = 'e3a7c5d91b48'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'certificado_empresa',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('empresa_id', sa.Integer(), nullable=False),
        sa.Column('caminho', sa.String(length=500), nullable=True),
        sa.Column('senha_cifrada', sa.String(length=500), nullable=True),
        sa.Column('subject_cn', sa.String(length=200), nullable=True),
        sa.Column('issuer_cn', sa.String(length=200), nullable=True),
        sa.Column('cnpj_certificado', sa.String(length=14), nullable=True),
        sa.Column('not_after', sa.DateTime(), nullable=True),
        sa.Column('estado', sa.String(length=20), nullable=False),
        sa.Column('detalhe', sa.String(length=500), nullable=True),
        sa.Column('verificado_em', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['empresa_id'], ['empresa.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('empresa_id'),
    )
    with op.batch_alter_table('certificado_empresa', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_certificado_empresa_cnpj_certificado'),
            ['cnpj_certificado'], unique=False)
        batch_op.create_index(
            batch_op.f('ix_certificado_empresa_estado'), ['estado'], unique=False)

    op.create_table(
        'chave_manifestacao',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('chave', sa.String(length=44), nullable=False),
        sa.Column('empresa_id', sa.Integer(), nullable=False),
        sa.Column('competencia', sa.String(length=7), nullable=True),
        sa.Column('competencia_ajustada', sa.Boolean(), nullable=False),
        sa.Column('cnpj_emitente', sa.String(length=14), nullable=True),
        sa.Column('origem', sa.String(length=10), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('tipo_evento', sa.String(length=6), nullable=True),
        sa.Column('cstat', sa.String(length=3), nullable=True),
        sa.Column('xmotivo', sa.String(length=255), nullable=True),
        sa.Column('protocolo', sa.String(length=20), nullable=True),
        sa.Column('ja_existia', sa.Boolean(), nullable=False),
        sa.Column('manifestado_em', sa.DateTime(), nullable=True),
        sa.Column('importado_em', sa.DateTime(), nullable=False),
        sa.Column('atualizado_em', sa.DateTime(), nullable=False),
        sa.Column('liberado_por_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['empresa_id'], ['empresa.id']),
        sa.ForeignKeyConstraint(['liberado_por_id'], ['usuario.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('chave'),
    )
    with op.batch_alter_table('chave_manifestacao', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_chave_manifestacao_chave'), ['chave'], unique=False)
        batch_op.create_index(
            batch_op.f('ix_chave_manifestacao_competencia'), ['competencia'],
            unique=False)
        batch_op.create_index(
            batch_op.f('ix_chave_manifestacao_empresa_id'), ['empresa_id'],
            unique=False)
        batch_op.create_index(
            batch_op.f('ix_chave_manifestacao_importado_em'), ['importado_em'],
            unique=False)
        batch_op.create_index(
            batch_op.f('ix_chave_manifestacao_status'), ['status'], unique=False)


def downgrade():
    # Sem `drop_index` explicito: `drop_table` ja leva os indices junto, e no
    # MySQL derruba-los ANTES falha com errno 1553 — o InnoDB exige indice na
    # coluna de uma foreign key, entao `ix_chave_manifestacao_empresa_id` nao
    # pode sair enquanto a FK existir. O SQLite aceita, o MySQL nao (AD-016).
    # E o mesmo padrao do `691521add9a0`, que derruba `usuario` direto.
    op.drop_table('chave_manifestacao')
    op.drop_table('certificado_empresa')
