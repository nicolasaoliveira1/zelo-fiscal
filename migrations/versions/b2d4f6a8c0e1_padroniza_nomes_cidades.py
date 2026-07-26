"""Padroniza nomes de cidades (acento/caixa) em municipio e empresa

Revision ID: b2d4f6a8c0e1
Revises: d7e1a4c9b3f6
Create Date: 2026-07-26 12:00:00.000000

COV-05: display com acento correto (Imbé, Tramandaí, Osório, São Paulo, ...).
O backend segue casando por nome normalizado (sem acento), entao a mudanca e
so de exibicao. Aplica em municipio.nome E empresa.cidade de forma consistente
(o "Abrir Site" municipal casa por string exata, entao os dois precisam bater).
Cidades fora do mapa (ex.: 'Xangrila' duplicado) ficam intocadas.
"""
from alembic import op

from app.services.cidade_canonica import padronizar_colunas

# revision identifiers, used by Alembic.
revision = 'b2d4f6a8c0e1'
down_revision = 'd7e1a4c9b3f6'
branch_labels = None
depends_on = None


def upgrade():
    padronizar_colunas(op.get_bind())


def downgrade():
    # Normalizacao de nomes e lossy: nao ha como restaurar a caixa/grafia
    # originais por linha. No-op — na base fresca do CI (upgrade->downgrade->
    # upgrade) nao ha dados, e em producao a padronizacao nao deve ser revertida.
    # upgrade e idempotente (canonico->canonico nao muda nada).
    pass
