"""nfse: remove o flag emissao_automatica da configuracao

O modo de emissao (assistido individual, assistido em lote ou automatico) passou
a ser escolhido no momento de iniciar a fila, na propria pagina. Manter tambem um
flag persistido daria DOIS controles para a mesma coisa — e o pior tipo de dois:
um visivel e outro escondido, com o operador sem saber qual venceu.

A coluna nunca chegou a ser lida por ninguem.

Revision ID: a1c4e9f27b03
Revises: f7a3c9e2b845
"""
import sqlalchemy as sa
from alembic import op

revision = 'a1c4e9f27b03'
down_revision = 'f7a3c9e2b845'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('configuracao_nfse', schema=None) as batch_op:
        batch_op.drop_column('emissao_automatica')


def downgrade():
    # Volta com o default do original (desligado): reverter nao pode ligar
    # emissao automatica em lugar nenhum.
    with op.batch_alter_table('configuracao_nfse', schema=None) as batch_op:
        batch_op.add_column(sa.Column('emissao_automatica', sa.Boolean(),
                                      nullable=False, server_default=sa.false()))
