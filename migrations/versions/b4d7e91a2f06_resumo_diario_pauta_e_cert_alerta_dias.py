"""Resumo diario: tabela pauta_notificacao + configuracao.cert_alerta_dias

Revision ID: b4d7e91a2f06
Revises: c8e2b4f70a13
Create Date: 2026-08-21

AD-029. Duas mudancas do mesmo pedido: (1) os alertas param de sair um a um e
passam a ser anotados numa pauta ate o resumo diario levar todos juntos; (2) a
janela de aviso de vencimento de certificado sai do env e vira campo editavel.

O `server_default` do `cert_alerta_dias` existe so para a linha ja gravada no
banco (a coluna e NOT NULL); e removido logo em seguida para que o default valha
no modelo, e nao no schema.
"""
import sqlalchemy as sa
from alembic import op

revision = 'b4d7e91a2f06'
down_revision = 'c8e2b4f70a13'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'pauta_notificacao',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('chave', sa.String(length=120), nullable=False),
        sa.Column('tipo', sa.String(length=40), nullable=False),
        sa.Column('titulo', sa.String(length=200), nullable=False),
        sa.Column('corpo', sa.Text(), nullable=True),
        sa.Column('criada_em', sa.DateTime(), nullable=False),
        sa.Column('enviada_em', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_pauta_notificacao_chave', 'pauta_notificacao',
                    ['chave'], unique=False)
    op.create_index('ix_pauta_notificacao_enviada_em', 'pauta_notificacao',
                    ['enviada_em'], unique=False)

    with op.batch_alter_table('configuracao_sistema') as batch:
        batch.add_column(sa.Column('cert_alerta_dias', sa.Integer(),
                                   nullable=False, server_default='10'))
    with op.batch_alter_table('configuracao_sistema') as batch:
        batch.alter_column('cert_alerta_dias', existing_type=sa.Integer(),
                           nullable=False, server_default=None)


def downgrade():
    with op.batch_alter_table('configuracao_sistema') as batch:
        batch.drop_column('cert_alerta_dias')
    # Tabela criada por esta migration: o downgrade e so o drop (os indices vao
    # junto). Derrubar indice antes do drop_table e o que o InnoDB recusa quando
    # ha FK; aqui nao ha, mas a regra do projeto e a mesma.
    op.drop_table('pauta_notificacao')
