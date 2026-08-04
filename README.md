# Zelo — Automação de Rotinas Fiscais Contábeis

> Regularidade sob controle.

Aplicação web em Python/Flask que automatiza as rotinas recorrentes de um escritório contábil: o controle e a emissão das **certidões fiscais dos clientes** (Federal, FGTS, Estadual, Municipal e Trabalhista) e a emissão das **NFS-e de honorários do próprio escritório**. **Zelo** é o nome do sistema de uso interno; a identidade é monocromática (grafite sobre papel), reservando cor apenas para o status das certidões.

![Dashboard](docs/image.png)

O foco do projeto é reduzir trabalho manual naquilo que **se repete todo mês e tem prazo**. Do lado dos clientes: manter controle visual de vencimentos, organizar automaticamente os PDFs emitidos e apoiar o controle de débitos, já que uma certidão pendente normalmente sinaliza pendência ou débito na respectiva esfera fiscal/trabalhista. Do lado do escritório: transformar o extrato do banco nas notas de honorários do mês, sem redigitar cliente por cliente.

## Visão geral

- Dashboard único com o status das certidões de toda a carteira.
- Automação Selenium sobre portais públicos reais (RFB, Caixa, SEFAZ RS, prefeituras, TST), individual e em lote, com pausa, retomada e parada.
- Ciclo completo do arquivo: download, estabilização, classificação do PDF, movimentação para a pasta da empresa e vínculo ao registro.
- Agendador diário que emite o que está vencendo e avisa por e-mail, sem serviço externo.
- Emissão das NFS-e de honorários no Emissor Nacional a partir do extrato bancário (CSV do Banrisul e PDF do Inter).

## Documentação

- [Certidões fiscais](docs/CERTIDOES.md): automação por tipo, lotes, agendador e relatórios
- [NFS-e de honorários](docs/NFSE.md): do extrato bancário à nota emitida
- [Instalação e configuração](docs/OPERACAO.md): requisitos, `.env`, Docker, observabilidade e testes

## Destaques técnicos

- **Automação com freio onde o erro é caro.** Na emissão de NFS-e o sistema preenche as três etapas do assistente e **para na tela de revisão**: quem clica em emitir é o operador. O artefato é documento fiscal: o desfazer não é um rollback, é o cancelamento de uma nota junto à prefeitura. O modo totalmente automático existe, mas é escolha explícita a cada lote e só emite depois de uma auto-revisão que confere documento, valor e descrição na tela.
- **Casamento aproximado que prefere não decidir.** O banco manda a razão social truncada em 35 caracteres. O vínculo nome→CNPJ só acontece sozinho quando o match é **bom e folgado** em relação ao segundo colocado; score alto porém ambíguo vai para conferência manual, porque errar aqui é emitir nota com o CNPJ de outro cliente.
- **Manutenção preventiva das automações.** Um **dry-run** percorre o fluxo real de cada município até a fronteira da emissão e reporta qual seletor deixou de resolver, sem emitir nada e sem gastar solver. Roda sozinho todo dia, então a quebra de portal aparece antes de o operador esbarrar nela.
- **Observabilidade de verdade.** Logs com saída dupla (console legível + `app.jsonl`), `request_id` por requisição e `execution_id` por lote, taxonomia de erros traduzida em mensagens acionáveis (título + causa + ação), preflight antes de emitir e detector de erros recorrentes com hipótese de causa.
- **CI com paridade de banco.** Dois jobs em paralelo: lint + suíte em SQLite (gate rápido) e a suíte inteira contra **MySQL 8.0**, para pegar divergência de enum, colação e tipo antes da produção. Há ainda um teste de migração idempotente (`upgrade → downgrade → upgrade`).
- **Segurança aplicada ao uso diário.** Autenticação *deny-by-default* por um `before_request` global (imune a esquecer de decorar rota nova), três papéis com hierarquia, CSRF, trilha de auditoria, PDF servido por token assinado e expirável, credenciais sensíveis só via ambiente.
- **Reuso deliberado no lugar de cópias.** Um motor de lotes compartilhado por cinco fluxos distintos e núcleos únicos para captcha de imagem, política de certificado do Chrome e classificação de PDF. Cada duplicata seria uma divergência silenciosa entre dois portais.

## Stack

| Camada | Tecnologias |
| --- | --- |
| Backend | Python 3.10+, Flask, SQLAlchemy 2.0 / Flask-Migrate, Flask-Login, Flask-WTF, APScheduler |
| Automação | Selenium, undetected-chromedriver, 2captcha, pdfplumber, certificado digital A1/A3 via política do Chrome |
| Frontend | Jinja2, Bootstrap 5.3 com identidade própria (design tokens, IBM Plex, dark/light), JS vanilla (ES modules, sem bundler) |
| Dados | MySQL 8.0 (produção), SQLite (desenvolvimento) |
| Documentos | openpyxl (XLSX), pypdf + fpdf2 (dossiê PDF), thefuzz (casamento de nomes) |

## Licença

Software proprietário: **todos os direitos reservados** (veja [LICENSE](LICENSE)).

O repositório é público apenas para fins de **estudo, demonstração e portfólio**: o código pode ser visualizado e lido, mas **não** há permissão para usar, executar, copiar, modificar ou redistribuir. Não é um projeto open-source. Para qualquer uso além da visualização, é necessária autorização prévia e por escrito do autor.
