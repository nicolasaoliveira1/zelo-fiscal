"""notificacao_log.tipo: String(20) -> String(40)

`alerta_empresa_baixada` (22 caracteres) nao cabia em String(20). No MySQL em
strict mode o INSERT falha, e como `notificacoes._registrar_envio` e best-effort
(engole a excecao) o registro nunca entrava: o anti-spam duravel parava de
funcionar em silencio e o alerta de empresa baixada saia a cada execucao do job
de recheck. No SQLite o valor entrava truncado, o que escondia o problema fora
do CI de MySQL.

Revision ID: d2b6f8a3c105
Revises: c3e7a1b45d92
"""
import sqlalchemy as sa
from alembic import op

revision = 'd2b6f8a3c105'
down_revision = 'c3e7a1b45d92'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('notificacao_log', schema=None) as batch_op:
        batch_op.alter_column('tipo',
                              existing_type=sa.String(length=20),
                              type_=sa.String(length=40),
                              existing_nullable=False)


def downgrade():
    # Encurtar de volta truncaria os tipos longos ja gravados (o MySQL recusaria a
    # conversao em strict mode), entao as linhas que so existem por causa da coluna
    # larga saem antes — sao trilha de notificacao, nao dado fiscal.
    op.execute("DELETE FROM notificacao_log WHERE LENGTH(tipo) > 20")
    with op.batch_alter_table('notificacao_log', schema=None) as batch_op:
        batch_op.alter_column('tipo',
                              existing_type=sa.String(length=40),
                              type_=sa.String(length=20),
                              existing_nullable=False)
