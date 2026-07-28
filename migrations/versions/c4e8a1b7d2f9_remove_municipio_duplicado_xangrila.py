"""Remove o municipio duplicado 'Xangrila' (mesma cidade de 'Xangri-La')

Revision ID: c4e8a1b7d2f9
Revises: b2d4f6a8c0e1
Create Date: 2026-07-27 10:00:00.000000

COV-05: as duas linhas eram identicas (mesma url/seletores/config/validade) e so
existiam porque a chave de cidade mantinha o hifen — 'XANGRI-LA' e 'XANGRILA' nao
se encontravam, entao o cadastro precisava de uma linha por grafia. Agora
`utils.normalizar_cidade` descarta separadores e uma linha atende as duas
grafias; a duplicata vira ambiguidade na busca de municipio.
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'c4e8a1b7d2f9'
down_revision = 'b2d4f6a8c0e1'
branch_labels = None
depends_on = None

_DUPLICADO = 'Xangrila'
_MANTIDO = 'Xangri-Lá'

_COLUNAS = (
    'url_certidao', 'automacao_ativa', 'validade_dias', 'usar_slow_typing',
    'config_automacao', 'cnpj_field_id', 'by', 'inscricao_field_id',
    'inscricao_field_by', 'pre_fill_click_id', 'pre_fill_click_by',
    'shadow_host_selector', 'inner_input_selector',
)


def _citar(conn, colunas):
    """Cita identificadores conforme o dialeto (`by` e palavra RESERVADA no MySQL:
    sem crase o SELECT/INSERT vira erro de sintaxe la, embora passe no SQLite)."""
    preparer = conn.dialect.identifier_preparer
    return [preparer.quote(coluna) for coluna in colunas]


def upgrade():
    conn = op.get_bind()
    # So remove se a linha canonica existir (nao deixa a cidade sem municipio).
    mantido = conn.execute(
        sa.text('SELECT id FROM municipio WHERE nome = :n'), {'n': _MANTIDO}
    ).fetchone()
    if mantido:
        conn.execute(sa.text('DELETE FROM municipio WHERE nome = :n'), {'n': _DUPLICADO})


def downgrade():
    conn = op.get_bind()
    ja_existe = conn.execute(
        sa.text('SELECT id FROM municipio WHERE nome = :n'), {'n': _DUPLICADO}
    ).fetchone()
    colunas_citadas = _citar(conn, _COLUNAS)
    origem = conn.execute(
        sa.text(f"SELECT {', '.join(colunas_citadas)} FROM municipio WHERE nome = :n"),
        {'n': _MANTIDO}
    ).fetchone()
    if ja_existe or not origem:
        return
    # Recria a duplicata copiando a linha canonica (eram identicas).
    dados = dict(zip(_COLUNAS, origem))
    dados['nome'] = _DUPLICADO
    campos = ', '.join(_citar(conn, dados))
    valores = ', '.join(f':{c}' for c in dados)
    conn.execute(sa.text(f'INSERT INTO municipio ({campos}) VALUES ({valores})'), dados)
