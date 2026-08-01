"""nfse: documento CPF/CNPJ do tomador, memoria avulsa e emissao manual

Nem todo tomador e pessoa juridica (produtor rural, autonomo), entao `cnpj`
vira `documento` + `tipo_documento`. O apelido passa a lembrar tambem um
documento avulso, com `empresa_id` opcional: CPF nunca vira cadastro de
Empresa, e sem essa memoria o operador redigitaria o numero todo mes.
`origem_emissao` marca a nota que o operador emitiu fora do sistema.

Revision ID: f7a3c9e2b845
Revises: e5b2c7d1a3f8
"""
import sqlalchemy as sa
from alembic import op

revision = 'f7a3c9e2b845'
down_revision = 'e5b2c7d1a3f8'
branch_labels = None
depends_on = None


def upgrade():
    # Rename e indice em blocos separados: no SQLite o batch recria a tabela e
    # o indice seria montado contra a definicao antiga, que ainda tem `cnpj`.
    with op.batch_alter_table('nota_nfse', schema=None) as batch_op:
        batch_op.alter_column('cnpj', new_column_name='documento',
                              existing_type=sa.String(length=18), existing_nullable=True)
        batch_op.add_column(sa.Column('tipo_documento', sa.String(length=4), nullable=True))
        batch_op.add_column(sa.Column('origem_emissao', sa.String(length=12), nullable=True))

    with op.batch_alter_table('nota_nfse', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_nota_nfse_documento'), ['documento'], unique=False)

    with op.batch_alter_table('apelido_nfse', schema=None) as batch_op:
        batch_op.add_column(sa.Column('documento', sa.String(length=18), nullable=True))
        batch_op.add_column(sa.Column('tipo_documento', sa.String(length=4), nullable=True))
        # empresa_id passa a ser opcional: o apelido pode guardar so o documento
        batch_op.alter_column('empresa_id', existing_type=sa.Integer(), nullable=True)


def downgrade():
    with op.batch_alter_table('apelido_nfse', schema=None) as batch_op:
        # apelidos sem empresa nao cabem no schema antigo
        batch_op.drop_column('tipo_documento')
        batch_op.drop_column('documento')
        batch_op.alter_column('empresa_id', existing_type=sa.Integer(), nullable=False)

    with op.batch_alter_table('nota_nfse', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_nota_nfse_documento'))

    with op.batch_alter_table('nota_nfse', schema=None) as batch_op:
        batch_op.drop_column('origem_emissao')
        batch_op.drop_column('tipo_documento')
        batch_op.alter_column('documento', new_column_name='cnpj',
                              existing_type=sa.String(length=18), existing_nullable=True)
