"""nfse: renomeia nota_emitida_nfse.competencia para competencia_dps

O nome enganava e o engano custou: a "Competencia" que o portal mostra e a data
de competencia do DPS, preenchida com HOJE pela nossa propria automacao — o mes
da EMISSAO. `NotaNfse.competencia` e outra coisa: o mes de REFERENCIA do
honorario (mes anterior ao vencimento).

Enquanto os dois campos tinham o mesmo nome, a conciliacao casou um com o outro
e acusou "pagou e ficou sem nota" para toda linha paga num mes e emitida no
seguinte — que e o caso NORMAL (o cliente paga em julho o honorario de junho).
Das 166 notas lidas, so 58 casaram, e por coincidencia.

O `_dps` no nome existe para o proximo leitor nao repetir o erro.

Revision ID: e7c1a4b98d20
Revises: d5f8b3c210ae
"""
import sqlalchemy as sa
from alembic import op

revision = 'e7c1a4b98d20'
down_revision = 'd5f8b3c210ae'
branch_labels = None
depends_on = None


def upgrade():
    # Rename e indice em blocos separados: no SQLite o batch recria a tabela e
    # o indice seria montado contra a definicao antiga (mesmo motivo do
    # comentario em f7a3c9e2b845).
    with op.batch_alter_table('nota_emitida_nfse', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_nota_emitida_nfse_competencia'))

    with op.batch_alter_table('nota_emitida_nfse', schema=None) as batch_op:
        batch_op.alter_column('competencia', new_column_name='competencia_dps',
                              existing_type=sa.String(length=7),
                              existing_nullable=True)

    with op.batch_alter_table('nota_emitida_nfse', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_nota_emitida_nfse_competencia_dps'),
                              ['competencia_dps'], unique=False)

    # As ligacoes feitas pela regra antiga estao erradas por construcao: elas
    # casaram mes de referencia com mes de emissao. Zerar e deixar a conciliacao
    # nova refazer e mais seguro que herdar par que ninguem conferiu.
    op.execute('UPDATE nota_emitida_nfse SET nota_id = NULL')


def downgrade():
    with op.batch_alter_table('nota_emitida_nfse', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_nota_emitida_nfse_competencia_dps'))

    with op.batch_alter_table('nota_emitida_nfse', schema=None) as batch_op:
        batch_op.alter_column('competencia_dps', new_column_name='competencia',
                              existing_type=sa.String(length=7),
                              existing_nullable=True)

    with op.batch_alter_table('nota_emitida_nfse', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_nota_emitida_nfse_competencia'),
                              ['competencia'], unique=False)
