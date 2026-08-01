"""nfse: desfazer o agrupamento e descricao editavel do grupo

Confirmar um agrupamento reescreve o valor da nota lider — e reescrever valor de
documento fiscal sem caminho de volta e o tipo de coisa que so se descobre
errada depois de emitir. Daí:

- `valor_extrato` guarda o valor como veio do banco, imutavel. `valor_final`
  passa a ser "o valor a emitir", que o agrupamento pode reescrever;
- `grupo_confirmado` marca o agrupamento aplicado SEM apagar o token, que e o
  que permite reencontrar as irmas para desfazer;
- `grupo_descricao_anterior` / `grupo_pendente_anterior` sao o retrato da lider
  antes do merge. Nao dava para re-deduzir do extrato: o operador pode ter
  digitado a descricao a mao antes de juntar, e re-deduzir a descartaria calada;
- `grupo_descricao` e a descricao que a nota juntada leva, editavel na tela.

Revision ID: c3e7a9f14b62
Revises: b8d4f1e07a29
"""
import sqlalchemy as sa
from alembic import op

revision = 'c3e7a9f14b62'
down_revision = 'b8d4f1e07a29'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('nota_nfse', schema=None) as batch_op:
        batch_op.add_column(sa.Column('valor_extrato', sa.Numeric(precision=12, scale=2),
                                      nullable=True))
        batch_op.add_column(sa.Column('grupo_confirmado', sa.Boolean(),
                                      nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('grupo_descricao', sa.String(length=300), nullable=True))
        batch_op.add_column(sa.Column('grupo_descricao_anterior', sa.String(length=300),
                                      nullable=True))
        batch_op.add_column(sa.Column('grupo_pendente_anterior', sa.Boolean(), nullable=True))

    # Backfill: para as notas que ja existem, o valor a emitir E o valor do
    # extrato (nenhuma passou por agrupamento — a feature esta nascendo aqui).
    op.execute('UPDATE nota_nfse SET valor_extrato = valor_final '
               'WHERE valor_extrato IS NULL')

    with op.batch_alter_table('nota_nfse', schema=None) as batch_op:
        batch_op.alter_column('grupo_confirmado', server_default=None,
                              existing_type=sa.Boolean(), existing_nullable=False)


def downgrade():
    with op.batch_alter_table('nota_nfse', schema=None) as batch_op:
        batch_op.drop_column('grupo_pendente_anterior')
        batch_op.drop_column('grupo_descricao_anterior')
        batch_op.drop_column('grupo_descricao')
        batch_op.drop_column('grupo_confirmado')
        batch_op.drop_column('valor_extrato')
