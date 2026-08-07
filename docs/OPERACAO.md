# Instalação, configuração e operação

> Como subir o Zelo, o que configurar e onde olhar quando algo quebra.
> As funcionalidades estão em [CERTIDOES.md](CERTIDOES.md) e [NFSE.md](NFSE.md).

## Requisitos

- Python 3.10+
- Google Chrome
- MySQL (recomendado para produção) ou SQLite (desenvolvimento)

## Instalação

1. Clone o repositório:

```powershell
git clone https://github.com/nicolasaoliveira1/zelo-certidoes.git
cd zelo-certidoes
```

2. Crie e ative o ambiente virtual:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

3. Instale as dependências:

```powershell
pip install -r requirements.txt
```

4. Copie `.env.example` para `.env` e ajuste os valores (ver [Variáveis de ambiente](#variáveis-de-ambiente)).

5. Rode as migrations:

```powershell
flask db upgrade
```

6. Crie o primeiro administrador (só é possível criar um admin por CLI; a senha é solicitada de forma interativa):

```powershell
flask criar-admin --username chefe
```

> Depois, novos usuários podem ser criados pela CLI (`flask criar-usuario --username ana --papel operador`) ou pela tela `/admin/usuarios`.

7. Inicie a aplicação:

```powershell
python run.py
```

Acesso local: http://localhost:5000 (faça login com o admin criado acima)

> **Atalho no Windows:** dê um duplo clique em `iniciar.bat` na pasta do projeto. Ele ativa o `venv`, garante as dependências (`pip install -r requirements.txt`, idempotente) e sobe o app. Se faltar alguma dependência crítica (ex.: `undetected-chromedriver`), o `run.py` aborta o boot com uma mensagem clara em vez de subir quebrado.

## Variáveis de ambiente

```env
# Obrigatória
SECRET_KEY=uma_chave_segura

# Banco (escolha um)
# DATABASE_URL=mysql+pymysql://usuario:senha@host/banco
# DATABASE_URL=sqlite:///instance/database.db

# Caminho de rede (opcional; também configurável na tela de Configurações,
# que tem precedência sobre esta variável)
# CAMINHO_REDE=Z:\\PASTAS EMPRESAS

# Perfil do Chrome (opcional)
# CHROME_PROFILE_DIR=C:\CertidoesPython\chrome-profile
# CHROME_PROFILE_NAME=Certidoes

# Perfil dedicado dos municípios IPM Atende.Net (undetected-chromedriver, opcional)
# CHROME_PROFILE_MUNICIPAL_DIR=C:\CertidoesPython\chrome-profile-municipal
# Força o major do Chrome para o undetected-chromedriver (opcional; por padrão é
# detectado automaticamente do Chrome instalado). Use se a auto-detecção falhar.
# CHROME_UC_VERSION_MAIN=149

# Certificado Estadual RS (opcional)
# SUBJECT é a chave (não muda na renovação); ISSUER é apenas o fallback.
# RS_CERT_AUTOSELECT_ENABLED=true
# RS_CERT_AUTOSELECT_PATTERN=https://www.sefaz.rs.gov.br
# RS_CERT_AUTOSELECT_POLICY_INDEX=1
# RS_CERT_AUTOSELECT_ISSUER_CN=AC emissora
# RS_CERT_AUTOSELECT_SUBJECT_CN=Titular CPF

# Certificado da NFSe / Emissor Nacional (opcional; sem isso o Chrome abre o
# diálogo de certificado e o operador escolhe à mão).
# Use um POLICY_INDEX diferente do RS: são certificados distintos e as duas
# políticas precisam conviver. ISSUER e SUBJECT são ambos obrigatórios.
# NFSE_CERT_AUTOSELECT_ENABLED=true
# NFSE_CERT_AUTOSELECT_PATTERN=https://certificado.nfse.gov.br
# NFSE_CERT_AUTOSELECT_POLICY_INDEX=2
# NFSE_CERT_AUTOSELECT_ISSUER_CN=AC emissora
# NFSE_CERT_AUTOSELECT_SUBJECT_CN=Titular CNPJ

# ALTCHA RS em lote (opcional)
# RS_ALTCHA_AUTOSOLVE_ENABLED=true
# RS_ALTCHA_MANUAL_FALLBACK=true
# CAPTCHA_2_API_KEY=sua_chave
# CAPTCHA_2_DEFAULT_TIMEOUT=180
# CAPTCHA_2_POLLING_INTERVAL=10
# CAPTCHA_2_SERVER=2captcha.com
# CAPTCHA_2_SALDO_MINIMO=2.0

# Agendador da emissão proativa (opcional; liga/desliga e hora também no painel)
# AGENDADOR_ENABLED=true

# Notificações por e-mail (opcional; sem SMTP_HOST/SMTP_FROM o envio é ignorado com aviso)
# SMTP_HOST=smtp.seuprovedor.com
# SMTP_PORT=587
# SMTP_USER=usuario
# SMTP_PASSWORD=senha
# SMTP_FROM=certidoes@seuescritorio.com
# SMTP_USE_TLS=true
# SMTP_TIMEOUT=20
# NOTIF_DIGEST_ENVIAR_VAZIO=true
# NOTIF_ALERTA_JANELA_HORAS=24

# Captura de contexto na falha Selenium (screenshot + HTML em logs/selenium)
# SELENIUM_CAPTURE_ENABLED=true
# SELENIUM_CAPTURE_DIR=logs/selenium
# SELENIUM_CAPTURE_RETENCAO_DIAS=14
```

## Rodar com Docker (dev/reprodutibilidade)

Ambiente de desenvolvimento reprodutível com **app + MySQL** em um comando, independente do Windows/`iniciar.bat`. A automação Selenium/Chrome **não** roda no container (fica no host, com certificado e unidade de rede `Z:`); o compose serve a UI e os dados sobre um MySQL igual ao de produção (8.0, `utf8mb4`/`utf8mb4_0900_ai_ci`).

```bash
cp .env.docker.example .env.docker   # ajuste SECRET_KEY / senha de dev (sem segredo real versionado)
docker compose --env-file .env.docker up
```

- `db`: MySQL 8.0 com volume nomeado `mysql_data` (dados persistem entre `up`/`down`).
- `web`: build do `Dockerfile` (`python:3.12-slim`); o schema é criado pelas **migrations** no boot (`AUTO_DB_UPGRADE=1`), não por `create_all`. App em http://localhost:5000.
- O `.env.docker` real é ignorado pelo git; só o `.env.docker.example` é versionado.

## Configurações importantes

### Caminho de rede para salvar certidões

O caminho base onde os PDFs das empresas são organizados pode ser definido de duas formas (nesta ordem de precedência): pela tela de **Configurações** (campo "Caminho de rede", salvo no banco) ou pela variável de ambiente `CAMINHO_REDE`. Sem nenhum dos dois, usa o padrão `Z:\PASTAS EMPRESAS`.

### Certificado digital (Estadual RS e NFSe)

Os dois fluxos que exigem certificado usam a política `AutoSelectCertificateForUrls` do Chrome para escolher o certificado sem exibir o diálogo. Cada um declara o seu conjunto completo (padrão de URL, índice no registro, issuer e subject) e **nada é herdado do outro**: o RS usa um e-CPF e a NFSe um e-CNPJ.

- Use um `POLICY_INDEX` **diferente** para cada fluxo. Eles convivem, e reutilizar o mesmo índice faria um sobrescrever a política do outro.
- O **`SUBJECT_CN` é a variável que importa**: é a chave de busca no repositório de certificados do Windows e é o dado que **não muda** na renovação (nome do titular + CPF/CNPJ). Copie-o exatamente como aparece no CN do certificado, incluindo o número depois dos dois-pontos.
- O **`ISSUER_CN` é só o fallback**, usado quando o certificado não está instalado na máquina. Na renovação a AC emissora costuma mudar, então o valor do `.env` envelhece sozinho — por isso o issuer é descoberto a cada ativação, e não lido do `.env` quando há certificado instalado.
- A escolha, quando há mais de um certificado com o mesmo titular, é: **dentro da validade**, **com chave privada**, e entre os que sobram vence o de **vencimento mais distante** (o recém-renovado). O vencido fica de fora, e a política gravada continua com issuer + subject — o filtro não é afrouxado.
- Sem essas variáveis o fluxo continua funcionando: o Chrome passa a pedir o certificado na tela, e o operador escolhe.

**"O lote parou na tela 'Selecione um certificado'"** — é o sintoma de a política não casar com nenhum certificado, e não gera erro no log da automação (o lote fica só esperando um clique). Procure no log por:

- `cert_store_issuer_resolvido` — achou; o campo `issuer_cn` mostra qual AC foi usada.
- `cert_store_sem_certificado_valido` — **nenhum** certificado válido para aquele subject: o certificado venceu e o novo ainda não foi instalado, ou o `SUBJECT_CN` está escrito diferente do CN real. Confira com o PowerShell:

  ```powershell
  Get-ChildItem Cert:\CurrentUser\My | Select-Object Subject, Issuer, NotAfter
  ```

- `cert_store_issuer_ambiguo` — mais de um certificado válido para o mesmo titular, com emissores diferentes; o log mostra o escolhido e os descartados.

### Estadual RS e 2captcha

- A integração usa API backend, sem extensão no Chrome.
- Se a chave estiver inválida, o lote RS encerra com erro explícito para evitar tentativas improdutivas.
- Se alterar variáveis no `.env`, reinicie a aplicação.

### Limite de "a vencer"

Na tela de Configurações, é possível ajustar o limite de dias para uma certidão ficar "a vencer" (1 a 90 dias). Há um valor **padrão** (aplicado a todos os tipos) e limites **opcionais por tipo** (Federal, FGTS, Estadual, Municipal e Trabalhista) que sobrepõem o padrão quando preenchidos. O limite efetivo afeta dashboard, relatórios e lotes.

### Municípios

As automações municipais dependem da configuração de seletores e steps na tabela Município. Para novas cidades, é necessário mapear o portal e registrar a configuração correspondente (URL, seletores e `config_automacao`).

Portais **IPM Atende.Net** (URL `*.atende.net`, como Gravataí/Osório/Novo Hamburgo) são roteados automaticamente para o `undetected-chromedriver` com perfil persistente próprio (`CHROME_PROFILE_MUNICIPAL_DIR`, padrão `chrome-profile-municipal/`, isolado do perfil do RS/Federal). No primeiro acesso com o perfil "frio", o bloqueio do portal pode aparecer uma vez até o operador desbloquear manualmente; depois o cookie de confiança persiste no perfil e os próximos acessos fluem.

### Captura de contexto na falha Selenium

Quando uma automação Selenium quebra (tipicamente porque um portal mudou de estrutura), o sistema salva automaticamente um screenshot e o HTML da página em `logs/selenium/` para acelerar o diagnóstico. Controlado por `SELENIUM_CAPTURE_ENABLED` (padrão ligado), com limpeza por retenção (`SELENIUM_CAPTURE_RETENCAO_DIAS`, padrão 14 dias).

## Observabilidade e diagnóstico

- Logs com **saída dupla**: console legível para humano (hora, nível, domínio, evento, campos-chave e `req_id`, com cor por nível) e arquivo `logs/app.jsonl` rotativo com o JSON cru.
- `request_id` por requisição HTTP e `execution_id` por execução de lote; as respostas HTTP incluem o header `X-Request-Id` para correlacionar logs e requisições.
- Taxonomia de erros (`TIMEOUT`, `CAPTCHA`, `PORTAL`, `SELECTOR`, `NETWORK_PATH`, `PERMISSION`, `DB`, `UNKNOWN`) traduzida em **mensagens acionáveis** (título + causa + ação) que chegam ao usuário no toast e carregam `error_type`/`acao` no JSON.
- **Pré-checagens (preflight)** antes de emitir/lote: valida rede, perfil do Chrome e solver, falhando cedo com mensagem clara em vez de quebrar no meio do Selenium.
- **Detector de padrões recorrentes**: o mesmo erro repetido no mesmo alvo abre um alerta com hipótese (provável seletor quebrado/portal fora).
- **Painel de diagnóstico** em `GET /diagnostico`: últimos erros/avisos (histórico persistido em banco via `DIAGNOSTICO_PERSISTIR`, retenção por `DIAGNOSTICO_RETENCAO_DIAS`) e alertas de recorrência.
- **Painel de municípios** em `GET /diagnostico/municipios`: estado da automação de cada município, com dry-run sob demanda.
- Retry com limite e backoff em pontos recuperáveis (ex.: timeout de carregamento e leitura de caminho de rede).
- **Health check** em `GET /health`: retorna `ok` ou `degraded` com detalhes de banco de dados, caminho de rede (incluindo leitura e escrita), profile do Chrome e configuração do solver.
- Para ajustar verbosidade/saída, use `LOG_LEVEL`, `QUIET_WERKZEUG_LOGS`, `LOG_CONSOLE_FORMAT` (`human`/`json`) e `LOG_JSON_FILE` no `.env`. Para reduzir ruído local, logs HTTP de estáticos/polling são filtrados e o log padrão fica em nível `WARNING`.

## Testes e CI

- Suíte `pytest` (`pip install -r requirements-dev.txt` + `pytest -q`).
- **CI com paridade de banco** (GitHub Actions, dois jobs em paralelo):
  - `testes-sqlite`: lint (`ruff`) + suíte em SQLite (gate rápido).
  - `testes-mysql`: suíte inteira contra **MySQL 8.0** (service container, `utf8mb4`/`utf8mb4_0900_ai_ci`) para pegar divergência de enum nativo/colação/tipo antes de produção, mais um teste de **migração idempotente** (`upgrade → downgrade → upgrade`).
- Localmente, aponte a suíte para outro banco com `TEST_DATABASE_URL` (sem a variável, usa SQLite).
- Os fluxos Selenium não são exercitados pelos testes automatizados (o navegador é substituído por mocks); para eles existe um roteiro de verificação manual.

## Estrutura do projeto

```text
.
  config.py
  run.py                     # Entrypoint; aborta o boot se faltar dependência crítica
  iniciar.bat                # Atalho Windows: venv + deps + run.py
  requirements.txt
  docs/                      # Documentação (context.json é a fonte de verdade da estrutura)
  migrations/                # Alembic
app/
  __init__.py                # Inicialização Flask (factory create_app)
  routes/                    # Rotas por domínio, todas no blueprint 'main'
    __init__.py              #   core: bp, hooks, dashboard, /api/pendencias, /health, /diagnostico*
    empresas.py              #   rotas de empresa
    certidoes.py             #   /certidao/* (baixar delega a emissao_service)
    lotes.py                 #   factory de rotas de lote + fluxos do agendador
    relatorios.py            #   /relatorios, /configuracoes, exportação
    nfse.py                  #   /nfse/* (importação, resolução, sessão e lote assistido)
  auth.py                    # Login/papéis (deny-by-default) + painéis admin
  cli.py                     # Comandos CLI (criar-admin / criar-usuario)
  models.py                  # Modelos do banco
  captcha_solver.py          # Integração 2captcha (ALTCHA e captcha de imagem)
  file_manager.py            # Detecção/movimentação de PDFs
  errors.py                  # Taxonomia de erros + mensagens acionáveis
  utils.py                   # Utilitários compartilhados (inclui validação de CPF/CNPJ)
  automation/                # Pacote de automação
    sites.py                 #   URLs, seletores e validades padrão
    driver.py                #   WebDriver Chrome/undetected-chromedriver
    cert_policy.py           #   Núcleo da auto-seleção de certificado (RS e NFSe)
    steps.py                 #   Steps municipais data-driven
    pdf.py                   #   Leitura/classificação de PDF
    emissao.py               #   Emissão por tipo (FGTS/Estadual RS/Municipal/Trabalhista)
    captcha_img.py           #   Núcleo de captcha de imagem (Imbé e CNDT)
    trabalhista.py           #   Fluxo CNDT/TST
    nfse.py                  #   Emissor Nacional de NFS-e (login, etapas do DPS, detectores)
    nfse_emitidas.py         #   Leitura da tela de NFS-e emitidas do portal
    capture.py               #   Screenshot + HTML na falha Selenium
    batch_state.py           #   Estado e locks compartilhados dos lotes
  services/                  # Camada de serviços (motor de lotes, agendador, notificações,
                             #   exportação, observabilidade, importação e emissão de NFS-e)
  static/                    # CSS, imagens e JS por página (ES modules, sem bundler)
  templates/                 # Jinja2
```
