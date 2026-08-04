# NFS-e de honorários

> Pilar de **faturamento do escritório**: emite no **Emissor Nacional** (`nfse.gov.br`) as notas
> de honorários do próprio escritório, partindo do extrato do banco.
> O outro pilar (regularidade fiscal dos clientes) está em [CERTIDOES.md](CERTIDOES.md).

É o único fluxo do sistema que produz **documento fiscal**, e por isso o desenho é
deliberadamente conservador: errar não é rollback, é cancelamento de nota junto à prefeitura.

## Do extrato à fila de notas

- **Dois formatos entram pela mesma porta**, escolhidos pelo **conteúdo** do arquivo e nunca pela extensão: o **CSV de cobranças do Banrisul** e o **PDF do extrato do Banco Inter** (onde os honorários chegam por Pix). Uma seleção pode misturar os dois; dali para baixo o código é o mesmo.
- **A competência tem duas regras, de propósito.** No CSV ela é *derivada* (mês anterior ao vencimento do título, tratando a virada de ano, porque lá a data é de vencimento); no Inter ela vem *escrita* na descrição do Pix e é essa que vale.
- **Leitura do PDF por coordenada de coluna**, não por texto corrido: o extrato do Inter imprime `1.806,00 3.862,63` sem dizer qual é Entrada e qual é Saldo. As colunas são alinhadas à direita em `x` fixo e as coordenadas saem da própria linha de cabeçalho. Nada é *hardcoded* em pixel. Uma palavra só vira valor se parecer dinheiro **e** terminar na borda da coluna.
- **Cada nome vira um cliente**: o banco manda a razão social truncada em 35 caracteres, o cadastro guarda o apelido curto. O casamento é por similaridade, mas só vincula sozinho quando o match é **bom e folgado** em relação ao segundo colocado. Na dúvida, manda para conferência manual em vez de arriscar. Errar aqui emitiria uma nota com o CNPJ de outro cliente. A escolha manual vira **apelido salvo**: no mês seguinte aquele nome já entra resolvido.
- **Tomador pessoa física**: nem todo cliente é empresa. CPF é aceito e memorizado (com validação de dígito verificador), sem virar cadastro de empresa.
- **Trava de duplicidade** por documento + competência + serviço, contando inclusive notas emitidas fora do sistema. Avisa e pede liberação explícita, em vez de bloquear, porque uma alteração contratual e uma baixa da mesma empresa caem no mesmo mês e são duas notas legítimas.
- **Conferência de valor**: se a soma das parcelas do extrato não bate com o valor final, a linha é marcada com divergência (e mantém o valor final do banco).

## Agrupamento: o sistema propõe, o operador confirma

Várias entradas do mesmo tomador no mês, ou um estorno abatendo entradas, viram uma **proposta de
agrupamento**. Enquanto a proposta espera resposta, as notas do grupo ficam **fora da fila**:
emitir a entrada bruta de R$ 2.000,00 com um estorno de R$ 1.784,00 pendurado nela é exatamente
o erro que a proposta evita.

Confirmar é **reversível**: confirmar e desfazer são simétricos e nada é destruído no caminho.
O valor volta do valor original do extrato (imutável, o número que está no PDF) e descrição e
pendência voltam do retrato guardado no momento do agrupamento.

## Emissão: três modos, escolhidos a cada lote

- **Uma por vez**: preenche as três etapas do assistente DPS, para na tela de revisão e fecha o navegador quando a nota sai.
- **Lista inteira**: o mesmo laço, mantendo a janela autenticada de ponta a ponta.
- **Automático**: emite sozinho, depois de uma **auto-revisão** que confere documento, valor e descrição na tela antes de clicar.

Nos dois primeiros modos, **a automação preenche e o operador emite**: quem clica em "Emitir NFS-e"
é sempre a pessoa. O modo automático é uma terceira opção escolhida explicitamente a cada lote,
nunca um padrão. Pausar, retomar, pular e parar funcionam durante a espera.

- **Uma sessão de navegador para o dia todo**: o login por certificado digital e a conferência da alíquota do Simples acontecem uma vez, não a cada nota.
- **Ninguém chuta se a nota saiu**: a confirmação é lida do portal, não da interface. Se o navegador for fechado no meio da revisão, a nota fica *aguardando confirmação* e o operador marca à mão. Os dois chutes erram em direções opostas (marcar emitida perde uma nota que não existe; marcar pendente reemite no mês seguinte uma nota que já existe).
- **Ação em massa parcial por desenho**: o que der certo é aplicado e o que não der volta nomeado. Só entram ações reversíveis por um clique (cancelar, restaurar, marcar/desmarcar emitida manualmente). **Emitir e preencher ficam de fora**, por definição.

## O outro lado da conta: conferência do portal

Um espelho da tela de NFS-e emitidas do Emissor Nacional responde "o que a Receita registra que eu
emiti", contra o "o que eu preciso emitir" vindo do extrato. Daí sai o total do mês, que antes era somado
à mão, e as duas divergências que interessam: **quem pagou e ficou sem nota**, e **que nota saiu sem
pagamento**.

- Filtro e paginação do portal são **querystring**, então a automação monta a URL e navega, o que elimina máscara de data, datepicker e botão que só existe após render assíncrono. O módulo **só lê**: a tela tem botões de cancelar e substituir NFS-e e ele nunca os toca.
- As colunas são lidas pela **classe** do `<td>`, nunca pelo índice, porque uma coluna nova no portal deslocaria tudo em silêncio. A varredura confere a contagem contra o "Total de N registros" da própria tela e **recusa** o resultado se não bater: um total fiscal a menos passa despercebido por parecer plausível.
- **Duas coisas se chamam "competência" e quase nunca são o mesmo mês**: a competência do honorário (mês de referência) e a competência do DPS (mês da emissão). O cliente paga em julho o honorário de junho, e casar os dois campos acusava "pagou e ficou sem nota" para quase todo cliente. A conciliação usa **documento + valor**, com desempate por proximidade de data.

## Como usar

Acesse `/nfse` e siga os passos da página:

1. **Importar** o extrato do banco (CSV do Banrisul e/ou PDF do Inter, um ou vários de uma vez).
2. **Resolver** o que ficou pendente: vínculo empresa→CNPJ, descrição do serviço e propostas de agrupamento.
3. **Abrir o portal** e conferir a alíquota (uma vez por sessão).
4. **Escolher o modo** e emitir. Nos modos assistidos, o sistema para na tela de revisão de cada nota. **O clique em emitir é seu**.

## Limitações atuais

- O vínculo automático nome→CNPJ cobre a maior parte do extrato, mas não tudo; o restante exige uma escolha do operador, que fica memorizada para os meses seguintes.
- A leitura do PDF do Inter é acoplada ao layout atual do extrato; mudança de layout do banco exige revisão das colunas.
- A conferência do portal soma apenas as notas com código de nota gerada; outros códigos são contados à parte e mostrados, nunca somados nem descartados por adivinhação.
