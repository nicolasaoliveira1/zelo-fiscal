"""Atualiza a URL de Xangri-La (o portal saiu da porta 8443)

Revision ID: e3a7c5d91b48
Revises: d2b6f8a3c105
Create Date: 2026-08-17 10:00:00.000000

O portal SIA de Xangri-La (MS Gestao Publica) deixou de atender em
`xangrila.msgestaopublica.app.br:8443` e passou a responder na porta padrao
(443). A porta antiga nao recusa a conexao: ela fica pendurada ate o timeout,
o que aparecia como emissao travando e nao como "URL errada".

Nada em codigo prende a URL — ela vive na coluna `municipio.url_certidao`
(o baseline `c9f1a2d4e7b3` semeou a variante com porta). Selecionadores e
`config_automacao` seguem iguais e continuam valendo: a home nova e o mesmo SIA
de Ponta Pora, com o atalho `Contribuinte` e o dialogo
`compInformarContribuinte:formNumero` intactos.
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'e3a7c5d91b48'
down_revision = 'd2b6f8a3c105'
branch_labels = None
depends_on = None

_ANTIGA = 'https://xangrila.msgestaopublica.app.br:8443/servicosweb/home.jsf'
_NOVA = 'https://xangrila.msgestaopublica.app.br/servicosweb/home.jsf'

# A grafia sem hifen ('Xangrila') foi removida em c4e8a1b7d2f9, mas bancos que
# nao chegaram la (ou que recriaram a linha) ainda a teriam: casa por URL, nao
# por nome, para nao deixar uma linha para tras.
_SQL = """
    UPDATE municipio
       SET url_certidao = :destino
     WHERE url_certidao = :origem
"""


def _trocar(origem, destino):
    op.get_bind().execute(sa.text(_SQL), {'origem': origem, 'destino': destino})


def upgrade():
    _trocar(_ANTIGA, _NOVA)


def downgrade():
    _trocar(_NOVA, _ANTIGA)
