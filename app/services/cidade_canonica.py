"""Grafia canonica de cidades (COV-05): display com acento, matching sem acento.

O banco guarda o nome de exibicao (ex.: 'Imbé'); todo casamento no backend
(dispatch da automacao, resolver de municipio, filtro do dashboard, export)
normaliza via `utils.normalizar_cidade` (remove acento + upper), entao a grafia
acentuada nao quebra nada. Este modulo e a fonte unica do mapa
chave-normalizada -> grafia correta, reusado pela migration de padronizacao e
por quem precisar canonicalizar uma cidade.
"""
import sqlalchemy as sa

from app.utils import normalizar_cidade

# Chave (normalizar_cidade: sem acento + maiuscula) -> grafia canonica de exibicao.
CANONICO = {
    'IMBE': 'Imbé',
    'OSORIO': 'Osório',
    'SAO PAULO': 'São Paulo',
    'TRAMANDAI': 'Tramandaí',
    'CAPAO DA CANOA': 'Capão da Canoa',
    'GRAVATAI': 'Gravataí',
    'XANGRI-LA': 'Xangri-Lá',
    'PONTA PORA': 'Ponta Porã',
    # Ja corretas (sem acento): entram para padronizar tambem a CAIXA.
    'CANOAS': 'Canoas',
    'CIDREIRA': 'Cidreira',
    'NOVO HAMBURGO': 'Novo Hamburgo',
    'PORTO ALEGRE': 'Porto Alegre',
    'SAPUCAIA DO SUL': 'Sapucaia do Sul',
    'SORRISO': 'Sorriso',
}

# Colunas de cidade padronizadas pela migration COV-05.
_ALVOS = (('municipio', 'nome'), ('empresa', 'cidade'))


def canonicalizar(valor):
    """Grafia canonica (acento/caixa) da cidade, se conhecida; senao o valor
    trimado. Chave = `normalizar_cidade` (mesma usada no matching). Cidades fora
    do mapa ficam inalteradas. `None`/'' passam direto."""
    if not valor:
        return valor
    return CANONICO.get(normalizar_cidade(valor), valor.strip())


def padronizar_colunas(conn):
    """Aplica `canonicalizar` nas colunas de cidade de `municipio`/`empresa` via a
    conexao dada. Usado pela migration COV-05 (e testavel isoladamente). Idempotente
    (so escreve quando muda). Retorna quantas linhas foram atualizadas."""
    total = 0
    for tabela, coluna in _ALVOS:
        rows = conn.execute(
            sa.text(f'SELECT id, {coluna} AS valor FROM {tabela}')  # noqa: S608 (identificadores fixos)
        ).fetchall()
        for linha in rows:
            novo = canonicalizar(linha.valor)
            if novo and novo != linha.valor:
                conn.execute(
                    sa.text(f'UPDATE {tabela} SET {coluna} = :v WHERE id = :i'),  # noqa: S608
                    {'v': novo, 'i': linha.id},
                )
                total += 1
    return total
