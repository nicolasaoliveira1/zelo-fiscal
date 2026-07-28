"""Cria as tabelas da NFSe: configuracao, lote, nota e apelido

Revision ID: e5b2c7d1a3f8
Revises: c4e8a1b7d2f9
Create Date: 2026-07-28 15:00:00.000000

Sem SQL cru: op.create_table/create_index deixam a citacao de identificadores a
cargo do dialeto (licao do commit 2495962, onde `by` — reservada no MySQL —
quebrou o job testes-mysql). Nenhum nome de coluna aqui e palavra reservada.

Valores monetarios em Numeric(12,2), nunca Float (documento fiscal); status em
String, nao enum nativo (AD-016/AD-020).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e5b2c7d1a3f8'
down_revision = 'c4e8a1b7d2f9'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'configuracao_nfse',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('regime_apuracao_sn', sa.String(length=4), nullable=False,
                  server_default='1'),
        sa.Column('municipio_servico_codigo', sa.String(length=10), nullable=False,
                  server_default='4310330'),
        # sem server_default nos textos com acento (o default Python cobre a
        # criacao do registro unico e evita literal acentuado no DDL)
        sa.Column('municipio_servico_nome', sa.String(length=60), nullable=False),
        sa.Column('codigo_tributacao', sa.String(length=20), nullable=False,
                  server_default='17.19.01'),
        sa.Column('item_nbs', sa.String(length=20), nullable=False,
                  server_default='113022100'),
        sa.Column('descricao_template', sa.String(length=300), nullable=False),
        sa.Column('piscofins_situacao', sa.String(length=4), nullable=False,
                  server_default='0'),
        sa.Column('piscofins_tipo_retencao', sa.String(length=4), nullable=False,
                  server_default='0'),
        sa.Column('emissao_automatica', sa.Boolean(), nullable=False,
                  server_default=sa.text('0')),
    )

    op.create_table(
        'lote_nfse',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('nome_arquivo', sa.String(length=200), nullable=True),
        sa.Column('criado_em', sa.DateTime(), nullable=False),
        sa.Column('total', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('execution_id', sa.String(length=40), nullable=True),
    )
    op.create_index('ix_lote_nfse_criado_em', 'lote_nfse', ['criado_em'])

    op.create_table(
        'nota_nfse',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('lote_id', sa.Integer(), sa.ForeignKey('lote_nfse.id'),
                  nullable=False),
        sa.Column('empresa_id', sa.Integer(), sa.ForeignKey('empresa.id'),
                  nullable=True),
        sa.Column('nome_csv', sa.String(length=140), nullable=True),
        sa.Column('nome_csv_norm', sa.String(length=140), nullable=True),
        sa.Column('cnpj', sa.String(length=18), nullable=True),
        sa.Column('data_pagamento', sa.Date(), nullable=True),
        sa.Column('vencimento', sa.Date(), nullable=True),
        sa.Column('valor_titulo', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('acrescimos', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('deducoes', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('valor_final', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('competencia', sa.String(length=7), nullable=True),
        sa.Column('status', sa.String(length=24), nullable=False,
                  server_default='empresa_pendente'),
        sa.Column('origem_vinculo', sa.String(length=10), nullable=True),
        sa.Column('score_match', sa.Integer(), nullable=True),
        sa.Column('divergencia_valor', sa.Boolean(), nullable=False,
                  server_default=sa.text('0')),
        sa.Column('duplicata_de_id', sa.Integer(), sa.ForeignKey('nota_nfse.id'),
                  nullable=True),
        sa.Column('duplicata_liberada', sa.Boolean(), nullable=False,
                  server_default=sa.text('0')),
        sa.Column('emitida_em', sa.DateTime(), nullable=True),
        sa.Column('erro', sa.String(length=500), nullable=True),
    )
    op.create_index('ix_nota_nfse_lote_id', 'nota_nfse', ['lote_id'])
    op.create_index('ix_nota_nfse_empresa_id', 'nota_nfse', ['empresa_id'])
    op.create_index('ix_nota_nfse_nome_csv_norm', 'nota_nfse', ['nome_csv_norm'])
    op.create_index('ix_nota_nfse_competencia', 'nota_nfse', ['competencia'])
    op.create_index('ix_nota_nfse_status', 'nota_nfse', ['status'])

    op.create_table(
        'apelido_nfse',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('nome_norm', sa.String(length=140), nullable=False, unique=True),
        sa.Column('empresa_id', sa.Integer(), sa.ForeignKey('empresa.id'),
                  nullable=False),
        sa.Column('criado_em', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_apelido_nfse_empresa_id', 'apelido_nfse', ['empresa_id'])


def downgrade():
    # drop_table remove indices/FKs da propria tabela; um drop_index explicito em
    # coluna de FK falharia no MySQL (erro 1553). Ordem: filhas antes das pais.
    op.drop_table('apelido_nfse')
    op.drop_table('nota_nfse')
    op.drop_table('lote_nfse')
    op.drop_table('configuracao_nfse')
