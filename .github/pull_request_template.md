<!--
Template de PR do Zelo. Preencha as seções que se aplicam e apague as que não.
A regra que este template serve: nada entra em `main` direto — toda mudança vem
de uma branch própria, a partir de `main` atualizada, e chega por PR.
-->

## O que muda

<!-- Uma ou duas frases em linguagem de negócio: o que passa a ser possível, ou
     o que deixa de quebrar. Não descreva os arquivos — descreva o efeito. -->

## Por quê

<!-- O problema real que motivou. Se houver medição (tempo, contagem, taxa de
     erro), traga o número: este projeto decide por evidência, não por intuição. -->

## Escopo

- **Spec / tarefas**: <!-- ex.: `.specs/features/manifestador-nfe/` — T1..T18 (P1) -->
- **Decisões registradas**: <!-- ex.: AD-027 em `.specs/STATE.md`, ou "nenhuma" -->
- **O que deliberadamente NÃO entrou**: <!-- o corte de escopo e a razão -->

## Como foi verificado

<!-- Evidência, não promessa. Cole os números. -->

- [ ] `ruff check .` limpo
- [ ] Suíte em SQLite: `python -m pytest -q -n auto` → **N passed**
- [ ] Suíte em MySQL (obrigatório se tocou `app/models.py`, migration ou `tests/conftest.py`):
      **N passed**
- [ ] Migration reversível: `upgrade → downgrade → upgrade` <!-- ou: não há migration -->
- [ ] Verificação manual: <!-- o que foi exercitado no app de verdade, e o resultado -->

## Riscos e reversão

<!-- O que pode dar errado em produção, como perceber, e como voltar atrás.
     "Nenhum risco" é uma resposta válida quando for verdade — mas justifique. -->

## UI (apague se não houver)

- [ ] Segue `docs/DESIGN_LANGUAGE.md`; somente tokens `--zelo-*`, nenhum hex fixo
- [ ] Conferido nos temas **claro e escuro**
- [ ] Padrão novo entrou em **Componentes** e no **Histórico de decisões**
- [ ] JS em `app/static/js/`, não embutido no HTML (AD-015)

## Rastro documental

- [ ] `docs/context.json` atualizado (rotas, modelos, diretórios novos)
- [ ] `CLAUDE.md` atualizado, se a mudança altera como se trabalha no projeto
- [ ] Dependência nova declarada em `requirements.txt` **com o motivo em comentário**
