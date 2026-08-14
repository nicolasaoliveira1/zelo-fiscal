# Zelo — Automação de Rotinas Fiscais Contábeis

> Regularidade sob controle.

Aplicação web em Python/Flask que automatiza as rotinas recorrentes de um escritório contábil: o controle e a emissão das **certidões fiscais dos clientes** (Federal, FGTS, Estadual, Municipal e Trabalhista) e a emissão das **NFS-e de honorários do próprio escritório**. **Zelo** é o nome do sistema de uso interno; a identidade é monocromática (grafite sobre papel), reservando cor apenas para o status das certidões.

![Dashboard](docs/image.png)

O foco do projeto é reduzir trabalho manual naquilo que **se repete todo mês e tem prazo**. Do lado dos clientes: manter controle visual de vencimentos, organizar automaticamente os PDFs emitidos e apoiar o controle de débitos, já que uma certidão pendente normalmente sinaliza pendência ou débito na respectiva esfera fiscal/trabalhista. Do lado do escritório: transformar o extrato do banco nas notas de honorários do mês, sem redigitar cliente por cliente.

## Visão geral

- Dashboard único com o status das certidões de toda a carteira.
- Automação Selenium sobre portais públicos reais (RFB, Caixa, SEFAZ RS, prefeituras, TST), individual e em lote, com pausa, retomada e parada.
- Ciclo completo do arquivo: download, estabilização, classificação do PDF, movimentação para a pasta da empresa e vínculo ao registro.
- Agendador diário que emite o que está vencendo, recheca a situação cadastral dos clientes na Receita e avisa por e-mail, sem serviço externo.
- Emissão das NFS-e de honorários no Emissor Nacional a partir do extrato bancário (CSV do Banrisul e PDF do Inter).

## Documentação

- [Certidões fiscais](docs/CERTIDOES.md): automação por tipo, lotes, agendador e relatórios
- [NFS-e de honorários](docs/NFSE.md): do extrato bancário à nota emitida
- [Instalação e configuração](docs/OPERACAO.md): requisitos, `.env`, Docker, observabilidade e testes

## Destaques técnicos

Cada decisão abaixo existe porque o erro correspondente é caro de desfazer.

| Destaque | O que o sistema faz |
| --- | --- |
| **Freio onde o erro é caro** | Na NFS-e a automação preenche as três etapas e **para na tela de revisão** — quem emite é o operador, porque o desfazer não é rollback, é cancelamento de nota. O modo automático existe, mas é escolha explícita a cada lote e só emite após auto-revisão de documento, valor e descrição. |
| **Casamento que prefere não decidir** | O banco manda a razão social truncada em 35 caracteres. O vínculo nome→CNPJ só é automático quando o match é bom **e** folgado sobre o segundo colocado; ambíguo vai para conferência manual, porque errar é emitir nota com o CNPJ de outro cliente. |
| **Manutenção preventiva** | Um **dry-run** diário percorre o fluxo real de cada município até a fronteira da emissão e aponta o seletor que quebrou, sem emitir nada e sem gastar solver. |
| **Freio automático de portal fora** | Três falhas seguidas abrem um *circuit breaker*: o lote para em estado retomável e sai alerta por e-mail, em vez de queimar créditos de solver. Portal municipal para sozinho, os outros seguem; o bloqueio expira sem intervenção. O que esgotou tentativas volta à fila com um clique, agrupado por motivo. |
| **PDF salvo precisa ser certidão** | Antes de gravar validade, um gate confere texto e vocabulário de certidão: página de erro do portal é descartada e vira falha com retry, não certidão "válida" no painel. Reprova só por evidência negativa, para não quebrar fluxos que funcionam. |
| **Cadastro que se defende sozinho** | CNPJ validado por dígito verificador, dados da Receita via BrasilAPI com fallback ReceitaWS. Campo vazio é preenchido; campo divergente é **sinalizado, nunca sobrescrito** (a automação municipal casa cidade por string). CNPJ baixado sai do lote automático. |
| **Observabilidade de verdade** | Saída dupla (console + `app.jsonl`), `request_id` por requisição e `execution_id` por lote, erros traduzidos em título + causa + ação, preflight antes de emitir e detector de recorrência com hipótese de causa. |
| **Segurança no uso diário** | Autenticação *deny-by-default* por `before_request` global (rota nova nasce protegida), três papéis hierárquicos, CSRF, trilha de auditoria, PDF por token assinado e expirável, segredos só via ambiente. |
| **CI com paridade de banco** | Dois jobs paralelos: lint + suíte em SQLite (gate rápido) e a suíte inteira contra **MySQL 8.0**, pegando divergência de enum, colação e tipo antes da produção — mais teste de migração idempotente (`upgrade → downgrade → upgrade`). |
| **Reuso deliberado** | Um motor de lotes para cinco fluxos e núcleos únicos de captcha de imagem, política de certificado do Chrome e classificação de PDF. Cada cópia seria uma divergência silenciosa entre dois portais. |

## Stack

| Camada | Tecnologias |
| --- | --- |
| Backend | Python 3.10+, Flask, SQLAlchemy 2.0 / Flask-Migrate, Flask-Login, Flask-WTF, APScheduler |
| Automação | Selenium, undetected-chromedriver, 2captcha, pdfplumber, certificado digital A1/A3 via política do Chrome |
| Integrações | BrasilAPI e ReceitaWS (consulta de CNPJ) via `requests` |
| Frontend | Jinja2, Bootstrap 5.3 com identidade própria (design tokens, IBM Plex, dark/light), JS vanilla (ES modules, sem bundler) |
| Dados | MySQL 8.0 (produção), SQLite (desenvolvimento) |
| Documentos | openpyxl (XLSX), pypdf + fpdf2 (dossiê PDF), thefuzz (casamento de nomes) |

## Licença

Software proprietário: **todos os direitos reservados** (veja [LICENSE](LICENSE)).

O repositório é público apenas para fins de **estudo, demonstração e portfólio**: o código pode ser visualizado e lido, mas **não** há permissão para usar, executar, copiar, modificar ou redistribuir. Não é um projeto open-source. Para qualquer uso além da visualização, é necessária autorização prévia e por escrito do autor.
