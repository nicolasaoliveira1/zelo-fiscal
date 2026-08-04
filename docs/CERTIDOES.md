# Certidões fiscais

> Pilar de **regularidade fiscal dos clientes**: controle de vencimentos e emissão
> automatizada das certidões Federal, FGTS, Estadual, Municipal e Trabalhista.
> O outro pilar (faturamento do escritório) está em [NFSE.md](NFSE.md).

## Acesso e segurança (login e papéis)

- Login por sessão: **nenhuma tela funciona sem estar autenticado** (negação por padrão; só login, health e estáticos são públicos).
- Três papéis com hierarquia (**leitura** < **operador** < **admin**): leitura só consulta e exporta; operador emite/edita; admin gerencia usuários e vê a auditoria.
- Proteção CSRF em todos os formulários; senhas com hash forte.
- **Trilha de auditoria**: ações sensíveis (login, criação/alteração de usuário, etc.) ficam registradas e são consultáveis em `/admin/auditoria` (admin).
- Gestão de usuários em `/admin/usuarios` (admin). O primeiro administrador é criado por linha de comando (não há auto-cadastro público).

## Dashboard e operação

- Cadastro de empresa com criação automática de 5 certidões.
- Filtros por status e busca por nome em tempo real.
- Status visual de certidões (a única cor da interface):
  - Verde: válida
  - Âmbar: a vencer (limite configurável, global ou por tipo de certidão)
  - Vermelho: vencida
  - Laranja-tijolo: pendente (provável débito), em tom próprio e distinto de vencida
  - Cinza: sem data definida
- Cadastro de nova empresa com seleção de cidade via dropdown (apenas municípios cadastrados) e inscrição mobiliária condicional (Imbé).
- Tela de Empresas com listagem, filtros, edição e remoção com confirmação.
- Sidebar responsiva com estado persistente.
- Tema claro/escuro com persistência local.

## Automação de emissão

- **Federal**: fluxo assistido melhorado (o gate é hCaptcha invisível Enterprise, inviável sem operador).
  - "Abrir Site" abre o portal da RFB, copia o CNPJ e monitora o download do PDF que o operador baixa.
  - O PDF é classificado ao chegar: positiva vira PENDENTE (arquivo removido); negativa/positiva-com-efeitos-de-negativa grava a validade lida do PDF ("Válida até"), com fallback de 180 dias quando a data não é legível. Assim a Federal entra no ciclo de alertas.
  - Alternativa por **upload**: o operador pode anexar um PDF federal que já tem em mãos (botão "Registrar PDF"), sem reabrir o portal; a mesma classificação/validade é aplicada.
- **FGTS**:
  - Emissão individual com geração de PDF via Chrome DevTools.
  - Emissão em lote com pausa, retomada, parada e resumo final.
  - Detecção de PDF positiva no lote: arquivo removido e certidão marcada como PENDENTE automaticamente.
- **Estadual RS**:
  - Unitário mantido manual para evitar consumo indevido de solver.
  - Lote com ALTCHA automático via API 2captcha.
  - Processo robusto: só avança para o próximo CNPJ após baixar, estabilizar, mover e classificar o arquivo.
- **Municipal**: automação orientada por dados, com URL, seletores e steps de cada cidade guardados na tabela Município.
  - Tramandaí: fluxo condicional com detecção de link NEGATIVA na página final; suporte a lote.
  - Gravataí: classificação de status via conteúdo do PDF (positiva/negativa), com tratamento automático de pendência quando positiva.
  - Imbé: resolução automática de captcha de imagem via 2captcha; emissão de geral e mobiliário separadamente; suporte a lote por subtipo.
  - Portais **IPM Atende.Net** (Gravataí, Osório, Novo Hamburgo): a emissão individual usa **undetected-chromedriver** com um perfil persistente dedicado para não ser bloqueada pelo score anti-bot do portal (tela "validação automática de segurança / baixa pontuação"). A detecção é automática pela URL (`*.atende.net`), então qualquer novo município com esse domínio entra no fluxo sem mudança de código. O captcha em si continua resolvido manualmente pelo operador. Falhas de pré-condição (driver indisponível ou perfil em uso) retornam mensagem acionável (HTTP 409) sem cair para o navegador comum.
- **Trabalhista (CNDT/TST)**:
  - Unitário mantido assistido/manual para evitar consumo indevido de solver (o operador resolve o captcha).
  - Lote com captcha de imagem resolvido automaticamente via 2captcha; disponível também na emissão proativa do agendador.
  - Classificação do PDF: positiva vira PENDENTE (arquivo removido); negativa/positiva-com-efeitos-de-negativa grava validade de 180 dias.

## Verificação preventiva dos municípios

- **Dry-run por município**: percorre o fluxo real do portal até a fronteira da emissão e reporta qual passo/seletor deixou de resolver. O passo que gera o PDF é apenas localizado, **nunca clicado**: nenhuma certidão é emitida e nenhum download acontece.
- Honesto sobre o que não deu para ver: passos que dependeriam do clique de emissão são reportados como não verificados, em vez de contados como aprovados. Portal com captcha vira "parcial" (não gasta crédito de solver).
- **Roda sozinho todo dia**, em horário deslocado do lote de emissão, e dispara alerta quando um município passa a falhar.
- **Painel em `/diagnostico/municipios`** com o estado de cada município e botão para rodar o dry-run sob demanda.
- Grafia das cidades padronizada (acento e caixa) com fonte única de canonicalização. O casamento no backend segue normalizado, então acento não quebra nada.

## Gestão de arquivos

- Detecta PDF novo/alterado na pasta Downloads.
- Move e renomeia para a pasta final da empresa.
- Salva caminho do arquivo no banco.
- Visualização de PDF com token assinado e expirável.
- Download automático no Chrome (incluindo fluxos em modo anônimo), reduzindo necessidade de interação manual no diálogo de salvar.

## Emissão proativa (agendador)

- Agendador embutido (sem serviço externo) que roda **uma vez por dia**, na hora configurada no painel.
- Todo dia tira uma "foto" das contagens (para o gráfico de evolução) e, quando ligado, **enfileira e emite automaticamente** as certidões vencidas/a vencer, reaproveitando os mesmos lotes da operação manual.
- Fila durável: o que ficou pendente sobrevive a reinício do sistema e pode ser retentado por item.
- Liga/desliga e hora ficam na tela de **Configurações**.

## Notificações por e-mail

- **Digest periódico** (semanal por padrão, ou diário) com o resumo da carteira: quantas a vencer, vencidas e pendentes.
- **Alertas** de falha recorrente de automação e de **saldo baixo do 2captcha** antes de um lote parar no meio.
- Anti-spam durável (não repete o mesmo alerta dentro da janela, mesmo após reiniciar) e envio que nunca derruba a automação se o e-mail falhar.
- Destinatários e cadência configuráveis no painel; credenciais SMTP só por variável de ambiente.

## Exportação e relatórios

- **Exportar carteira (Excel):** botão no dashboard baixa uma planilha `.xlsx` **respeitando os filtros ativos** (status, tipo, estado, cidade). Sai exatamente o que está na tela.
- **Dossiê (PDF) por empresa:** um único PDF com capa + as certidões **válidas** concatenadas, pronto para licitação/cliente (papel operador). PDF ausente/corrompido é pulado com aviso.
- **Produtividade:** página `/produtividade` com emissões/dia, taxa de sucesso por tipo e tempo médio de lote (30/90 dias), com exportação em Excel.
- **Relatórios:** página `/relatorios` com indicadores e distribuição por status/tipo, pendências detalhadas com rankings por tipo e município, as últimas 100 certidões emitidas, o último lote por tipo × escopo (com modal de rendimento) e o gráfico de evolução por status.

## Como usar

1. Faça login com um usuário existente (o primeiro admin é criado por `flask criar-admin`). Sem sessão, todas as páginas redirecionam para o login.
2. Acesse a tela de nova empresa em `/empresa/nova` e cadastre a empresa com CNPJ, cidade e estado.
3. No dashboard: **Emitir** para automações suportadas, **Abrir Site** quando o fluxo for assistido, **Visualizar** para abrir o PDF salvo.
4. Acesse `/empresas` para gerenciar cadastro, edição e remoção com confirmação.
5. Para lotes:
   - FGTS: fluxo de lote quando houver mais de 1 item elegível.
   - Estadual RS: lote com controles de pausar, retomar e parar.
   - Municipal (Imbé e Tramandaí): lote com as mesmas ações; resolve captcha de imagem via 2captcha no Imbé.
   - Trabalhista: lote quando houver mais de 1 item elegível; resolve captcha de imagem via 2captcha.
6. Em `/diagnostico/municipios`, rode o dry-run quando desconfiar que um portal municipal mudou. Ele valida os seletores sem emitir nada.

## Limitações atuais

- Automações dependem da estabilidade dos portais públicos; mudanças de HTML nos sites podem exigir ajuste de seletores.
- Captchas fora do lote RS, Municipal (Imbé) e Trabalhista continuam majoritariamente manuais.
- A Federal permanece assistida por limitação do portal (hCaptcha invisível Enterprise), não por escolha de escopo.
- Ainda não existe cobertura completa de testes automatizados para os fluxos Selenium; a verificação deles é feita por um roteiro manual.
