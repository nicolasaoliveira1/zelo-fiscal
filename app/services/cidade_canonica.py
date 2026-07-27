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

# Grafias corretas de exibicao (fonte da verdade). As ultimas nao tem acento e
# entram para padronizar tambem a CAIXA.
GRAFIAS = (
    'Imbé', 'Osório', 'São Paulo', 'Tramandaí', 'Capão da Canoa', 'Gravataí',
    'Xangri-Lá', 'Ponta Porã',
    'Canoas', 'Cidreira', 'Novo Hamburgo', 'Porto Alegre', 'Sapucaia do Sul', 'Sorriso',
)

# Chave canonica -> grafia. Derivado (nao hardcoded) para acompanhar qualquer
# mudanca em `normalizar_cidade` — quando ela passou a ignorar separadores,
# 'Xangri-La'/'Xangrila' viraram a mesma chave sem tocar neste mapa (COV-05).
CANONICO = {normalizar_cidade(grafia): grafia for grafia in GRAFIAS}

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
        existentes = {linha.valor for linha in rows}
        for linha in rows:
            novo = canonicalizar(linha.valor)
            if not novo or novo == linha.valor:
                continue
            # Nao renomeia para um valor que outra linha ja tem: `municipio.nome` e
            # UNIQUE e cidades com grafias-espelho (ex.: 'Xangrila' x 'Xangri-Lá')
            # colidiriam. A duplicata e resolvida pela migration de deduplicacao.
            if novo in existentes:
                continue
            conn.execute(
                sa.text(f'UPDATE {tabela} SET {coluna} = :v WHERE id = :i'),  # noqa: S608
                {'v': novo, 'i': linha.id},
            )
            existentes.discard(linha.valor)
            existentes.add(novo)
            total += 1
    return total
