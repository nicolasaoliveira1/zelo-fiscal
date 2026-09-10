"""alarga artefato e evidencias do incidente de contrato de portal

O artefato sanitizado e o JSON do inventario INTEIRO da tela (ate 300 elementos),
e um portal real passa de 2000 caracteres com folga. O SQLite ignora a largura do
VARCHAR e o InnoDB impoe: gravar o incidente falhava com DataError 1406, e o
bloqueio estrutural virava erro cru na emissao.

Revision ID: i3e7a0b2d5c9
Revises: h2d6f9a1c4e8
"""
import sqlalchemy as sa
from alembic import op


revision = 'i3e7a0b2d5c9'
down_revision = 'h2d6f9a1c4e8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('incidente_contrato_portal', schema=None) as batch_op:
        batch_op.alter_column(
            'artefato_sanitizado',
            existing_type=sa.String(length=2000),
            type_=sa.Text(),
            existing_nullable=True)

    with op.batch_alter_table('diferenca_contrato_portal', schema=None) as batch_op:
        for coluna in ('esperado', 'observado', 'evidencia_sanitizada'):
            batch_op.alter_column(
                coluna,
                existing_type=sa.String(length=1000),
                type_=sa.Text(),
                existing_nullable=True)


def downgrade():
    # `LEFT()` e MySQL/MariaDB — o unico alvo real deste projeto (o SQLite dos
    # testes monta o schema por create_all, nunca desce migration).
    # Reversao encolhe a coluna: linha gravada depois do upgrade pode nao caber
    # de volta. Trunca antes, no proprio SQL, para o downgrade nao morrer com
    # DataError no pior momento possivel.
    op.execute(
        'UPDATE incidente_contrato_portal '
        'SET artefato_sanitizado = LEFT(artefato_sanitizado, 2000) '
        'WHERE artefato_sanitizado IS NOT NULL')
    op.execute(
        'UPDATE diferenca_contrato_portal SET '
        'esperado = LEFT(esperado, 1000), '
        'observado = LEFT(observado, 1000), '
        'evidencia_sanitizada = LEFT(evidencia_sanitizada, 1000)')

    with op.batch_alter_table('diferenca_contrato_portal', schema=None) as batch_op:
        for coluna in ('esperado', 'observado', 'evidencia_sanitizada'):
            batch_op.alter_column(
                coluna,
                existing_type=sa.Text(),
                type_=sa.String(length=1000),
                existing_nullable=True)

    with op.batch_alter_table('incidente_contrato_portal', schema=None) as batch_op:
        batch_op.alter_column(
            'artefato_sanitizado',
            existing_type=sa.Text(),
            type_=sa.String(length=2000),
            existing_nullable=True)
