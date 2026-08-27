"""Recusa dado real de cliente em arquivo versionado.

O repositorio e publico. Ja vazaram por aqui: nome de cliente como fixture,
CNPJ real em assercao de funcao pura, extrato bancario real em
`tests/fixtures/` e numeros internos da operacao no corpo de um PR. Trocar o
valor no arquivo NAO apaga o historico do git — por isso a barreira tem de
estar ANTES do commit.

O que este verificador procura:

- CNPJ e CPF com digito verificador valido que NAO estejam na lista de
  documentos sinteticos conhecidos. Documento de teste tem de ser inventado,
  e a lista abaixo e a unica porta de entrada;
- chave de acesso de NF-e (44 digitos) cujo CNPJ do emitente nao seja
  sintetico.

O que ele NAO tenta fazer: adivinhar se "PADARIA CENTRAL" e nome real. Nome de
cliente nao tem forma reconhecivel, e um verificador que chuta vira ruido que o
time aprende a ignorar. Para nome, a regra continua sendo humana — a revisao do
diff antes do commit.

Uso:
    python tools/verificar_dados_sensiveis.py [caminho ...]

Sem argumentos, varre os arquivos versionados. Sai 1 se achar algo.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Documentos sinteticos permitidos, com DV valido. Para acrescentar um, gere
# com DV correto e registre AQUI — e o unico lugar que autoriza.
DOCUMENTOS_SINTETICOS = {
    "11222333000181",
    "22333444000181",
    "44556677000186",
    "55666777000181",
    "66777888022401",
    "99887766000105",
    "11111111000111",
    "11222333000262",
    "11222333000343",
    "11222333000424",
    "11222333000505",
    "98765432000198",
    "22222222223301",
    "12345678909",
    "00000000000000",
    # Documentos de exemplo consagrados: aparecem em todo tutorial de validacao
    # de CPF/CNPJ e em toda documentacao de API da Receita. Nao identificam
    # cliente nenhum, e trocar cada um deles por outro sintetico so tornaria os
    # testes mais estranhos sem proteger ninguem.
    "33000167000101",  # Petrobras, exemplo canonico da API da Receita
    "11444777000161",
    "00000000000191",
    "52998224725",
    "11144477735",
    "39053344705",
    "83567984004",
}

EXTENSOES = {".py", ".js", ".mjs", ".html", ".xml", ".csv", ".json", ".md", ".txt", ".yml", ".yaml"}
IGNORAR_DIRETORIOS = {".git", "venv", "node_modules", "__pycache__", "logs", ".specs"}

_SO_DIGITOS = re.compile(r"\D")
_CANDIDATO_CNPJ = re.compile(r"(?<!\d)\d{2}[.\s]?\d{3}[.\s]?\d{3}[/\s]?\d{4}[-\s]?\d{2}(?!\d)")
_CANDIDATO_CPF = re.compile(r"(?<!\d)\d{3}[.\s]?\d{3}[.\s]?\d{3}[-\s]?\d{2}(?!\d)")
_CANDIDATO_CHAVE = re.compile(r"(?<!\d)\d{44}(?!\d)")


def _dv_cnpj(numero: str) -> bool:
    if len(numero) != 14 or len(set(numero)) == 1:
        return False

    def digito(parcial: str) -> str:
        pesos = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2][-len(parcial):]
        resto = sum(int(d) * p for d, p in zip(parcial, pesos)) % 11
        return "0" if resto < 2 else str(11 - resto)

    primeiro = digito(numero[:12])
    return numero[12] == primeiro and numero[13] == digito(numero[:12] + primeiro)


def _dv_cpf(numero: str) -> bool:
    if len(numero) != 11 or len(set(numero)) == 1:
        return False

    def digito(parcial: str, peso_inicial: int) -> str:
        soma = sum(int(d) * p for d, p in zip(parcial, range(peso_inicial, 1, -1)))
        resto = (soma * 10) % 11
        return "0" if resto == 10 else str(resto)

    primeiro = digito(numero[:9], 10)
    return numero[9] == primeiro and numero[10] == digito(numero[:9] + primeiro, 11)


def _achados_do_texto(texto: str) -> list[tuple[int, str, str]]:
    achados: list[tuple[int, str, str]] = []
    for numero_linha, linha in enumerate(texto.splitlines(), start=1):
        for bruto in _CANDIDATO_CNPJ.findall(linha):
            documento = _SO_DIGITOS.sub("", bruto)
            if _dv_cnpj(documento) and documento not in DOCUMENTOS_SINTETICOS:
                achados.append((numero_linha, "CNPJ", bruto))
        for bruto in _CANDIDATO_CPF.findall(linha):
            documento = _SO_DIGITOS.sub("", bruto)
            if _dv_cpf(documento) and documento not in DOCUMENTOS_SINTETICOS:
                achados.append((numero_linha, "CPF", bruto))
        for chave in _CANDIDATO_CHAVE.findall(linha):
            if chave[6:20] not in DOCUMENTOS_SINTETICOS:
                achados.append((numero_linha, "chave de NF-e", chave))
    return achados


def _blobs_do_indice() -> list[tuple[str, str]]:
    """Conteúdo EM ÍNDICE dos arquivos staged, como (caminho, texto).

    O que vai no commit é o índice, não a árvore de trabalho: editar o arquivo
    depois do `git add` faria a verificação aprovar bytes que não serão gravados.
    """

    nomes = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "-z", "--diff-filter=ACM"],
        capture_output=True,
        check=True,
    ).stdout.split(b"\0")
    conteudos: list[tuple[str, str]] = []
    for bruto in nomes:
        if not bruto:
            continue
        caminho = bruto.decode("utf-8", "surrogateescape")
        if Path(caminho).suffix.lower() not in EXTENSOES:
            continue
        blob = subprocess.run(["git", "show", f":{caminho}"], capture_output=True)
        if blob.returncode != 0:
            continue
        conteudos.append((caminho, blob.stdout.decode("utf-8", "ignore")))
    return conteudos


def _arquivos_versionados() -> list[Path]:
    saida = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True
    ).stdout
    return [Path(linha) for linha in saida.splitlines() if linha]


def verificar(caminhos: list[Path]) -> int:
    problemas = 0
    for caminho in caminhos:
        if caminho.suffix.lower() not in EXTENSOES:
            continue
        if set(caminho.parts) & IGNORAR_DIRETORIOS:
            continue
        try:
            texto = caminho.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for numero_linha, tipo, valor in _achados_do_texto(texto):
            problemas += 1
            print(f"{caminho}:{numero_linha}: {tipo} com DV valido fora da lista sintetica: {valor}")
    if problemas:
        _resumo(problemas)
    return 1 if problemas else 0


def _resumo(problemas: int) -> None:
    print()
    print(
        f"{problemas} ocorrencia(s). O repositorio e publico e o historico do "
        f"git nao esquece."
    )
    print("Use documento inventado com DV valido e registre-o em DOCUMENTOS_SINTETICOS.")


def verificar_staged() -> int:
    problemas = 0
    for caminho, texto in _blobs_do_indice():
        for numero_linha, tipo, valor in _achados_do_texto(texto):
            problemas += 1
            print(
                f"{caminho}:{numero_linha}: {tipo} com DV valido fora da lista "
                f"sintetica: {valor}"
            )
    if problemas:
        _resumo(problemas)
    return 1 if problemas else 0


def main(argv: list[str]) -> int:
    if "--staged" in argv[1:]:
        return verificar_staged()
    caminhos = [Path(a) for a in argv[1:]] or _arquivos_versionados()
    return verificar(caminhos)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
