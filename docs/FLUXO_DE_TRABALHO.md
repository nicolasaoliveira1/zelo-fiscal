# Fluxo de trabalho — branch própria e PR

Regra única: **nada entra em `main` por commit direto.** Toda mudança nasce numa
branch própria e volta por Pull Request.

## Ramificar

Sempre a partir de `main` **atualizada** — ramificar de uma `main` velha é a
origem mais comum de conflito bobo e de migration com duas cabeças:

```bash
git checkout main
git pull
git checkout -b feat/<slug>
```

Nomeie pela natureza da mudança:

| Prefixo | Quando |
| ------- | ------ |
| `feat/` | capacidade nova |
| `fix/` | correção de comportamento errado |
| `refactor/` | mesma capacidade, estrutura melhor |
| `chore/` | manutenção, dependência, configuração |
| `docs/` | só documentação |

Specs numeradas do roadmap mantêm `feat/<NN>-<slug>` (ex.: `feat/09-resiliencia-operacional`).

## Commits

Um commit por unidade verificável, em [Conventional Commits](https://www.conventionalcommits.org/):
`<tipo>(<escopo>): <descrição no imperativo, minúscula, sem ponto final>`.

Nunca junte tarefas distintas num commit só — o histórico é a ferramenta de
`git bisect` e de rollback granular.

## Abrir o PR

Preencha `.github/pull_request_template.md`. Ele existe para forçar quatro coisas
que a mensagem de commit é curta demais para carregar juntas:

1. **O porquê** — o problema real, com número quando houver medição.
2. **O escopo** — spec e tarefas cobertas, decisões `AD-NNN` registradas, e o que
   ficou de fora **de propósito**.
3. **A evidência** — contagem de testes que passaram, não a promessa de que passam.
4. **O risco** — como perceber que deu errado em produção e como voltar atrás.

### Evidência mínima

- `ruff check .` limpo
- `python -m pytest -q -n auto` — cole o número de testes
- Tocou `app/models.py`, uma migration ou `tests/conftest.py`? Então **também**
  a suíte em MySQL e o par `upgrade → downgrade → upgrade`. É onde SQLite passa e
  MySQL não (enum nativo, colação, arredondamento de `DateTime` — ver AD-016/AD-020
  e a seção de testes do `CLAUDE.md`).
- Mudou UI? Conferido nos temas **claro e escuro**, seguindo `DESIGN_LANGUAGE.md`.

## Merge

Depois do PR aprovado (e, quando fizer sentido, de um `/code-review`), merge em
`main`. A branch pode ser apagada — o PR fica como o registro.

## A exceção

O responsável pelo projeto pode pedir uma mudança direto em `main` — urgência ou
ajuste trivial. Isso vale **quando ele pede**, nunca por presunção. Na dúvida,
ramifique: o custo de uma branch a mais é zero, o de um commit indevido em `main`
não é.
