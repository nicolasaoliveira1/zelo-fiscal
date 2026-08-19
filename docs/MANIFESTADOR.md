# Manifestador de NF-e

> Manifestação do destinatário das NF-e recebidas pelos clientes, **direto no webservice da
> SEFAZ** — sem navegador, sem `.jnlp` e sem o assinador Java.
> Os outros dois pilares: [certidões dos clientes](CERTIDOES.md) e
> [NFS-e de honorários](NFSE.md).

Toda NF-e emitida contra um cliente precisa ser manifestada. Sem manifestação em **90 dias**
(prazo do Ajuste SINIEF 14/2026, em vigor desde 01/06/2026), a SEFAZ registra Confirmação
automática e o cliente perde o direito de recusar uma nota que não reconhece. Antes, cada nota
passava pelo portal uma a uma, à mão, com o assinador Java no meio.

Como a NFS-e, este fluxo produz **ato fiscal irreversível**: Confirmação da Operação não tem
desfazer junto à SEFAZ. O desenho é conservador pelo mesmo motivo.

## Sem navegador: a canalização é stdlib

O risco que mandava no projeto era um só — a SEFAZ aceitaria nossa assinatura? Foi **medido antes
de construir**. Contra 3 NF-e reais já assinadas, a canonicalização da biblioteca padrão do Python
reproduziu o `DigestValue` byte a byte e validou o `SignatureValue` RSA-SHA1. Só então a
implementação começou.

O resultado é que não há `lxml`, `signxml` nem `zeep` no projeto: a única dependência nova é
`cryptography`, para ler o `.pfx` e assinar.

- **Perfil de assinatura obrigatório**: SHA-1, RSA-SHA1, C14N 1.0, transforms *enveloped-signature* + C14N. Trocar por SHA-256 faz a SEFAZ rejeitar.
- **Cada bloco sai no seu próprio namespace default**, porque a canonicalização preserva prefixos e a SEFAZ recalcula o digest a partir dos bytes recebidos. Prefixo errado é rejeição sem nenhum sinal do nosso lado.
- **Endereços medidos, não supostos**: o serviço de recepção de evento responde em dois hosts, mas o de distribuição responde em **um só** — o outro devolve 404. Homologação tem host próprio.
- **Um evento por lote de envio**, bem abaixo do limite de 20 da SEFAZ.

## O cofre de certificados

As empresas são casadas com o `.pfx` do drive de rede pelo **CNPJ que está dentro do CN do
certificado** — nunca pelo nome do arquivo, da pasta ou pela razão social. Nenhum dos três é
confiável na prática: há certificado cujo CN é um recado do emissor em vez de um nome, a mesma
empresa grafada com `&` num arquivo e com `E` noutro, e nomes iguais pertencendo a CNPJs
diferentes. O CNPJ dentro do certificado é o único identificador que a autoridade certificadora
garante.

- **O `.pfx` nunca é copiado.** O cofre guarda caminho + senha cifrada, e lê o arquivo do drive no momento do uso — assim a renovação anual na pasta é herdada sem recadastro.
- **A senha padrão do emissor não é gravada cifrada**: guardá-la só criaria dependência da chave do cofre sem proteger nada. Senha divergente é **sugerida** a partir do nome da pasta e aplicada só por clique do operador.
- **Seis estados por empresa** (pronto, vencido, sem arquivo, sem pasta, falta a senha, CNPJ não confere), somados numa régua de uma linha no topo da página. Empresa sem certificado utilizável é **estado de negócio**, exposto antes do lote — nunca uma falha no meio dele.

## Da chave à manifestação

- **Dois caminhos de entrada**: colar as chaves (do scanner de código de barras ou de um PDF) ou importar os **XML do mês**, arrastando as pastas — várias de uma vez, subpastas incluídas. No XML a empresa sai de dentro do arquivo; na colagem o operador informa.
- **O recorte da chave só acontece em fronteira de separador que já existia no texto**, e descartar dígitos exige dígito verificador válido como prova. Um bloco grudado é recusado inteiro, porque ali não há fronteira segura e uma janela desalinhada apontaria para a NF-e de outra pessoa.
- **A competência é a da entrada, não a da emissão.** O XML de uma nota emitida em 30/06 com entrada em 01/07 conta como julho, que é o ritmo do escritório. Derivar do mês embutido na chave erra em toda virada de mês — foi assim que o defeito apareceu, na validação com nota real.
- **Três modos de manifestação**: uma nota, uma empresa ou a carteira inteira. No modo carteira a fila é **agrupada por empresa**, o que faz cada certificado ser usado num bloco contíguo em vez de intercalado.
- **O tipo de evento nunca vem preenchido.** O seletor abre em "Escolha o evento…" e a rota recusa o pedido sem ele: Confirmação da Operação é irreversível, e um campo com padrão faz clicar sem ter decidido.

## Os freios

- **Resposta perdida não vira chute.** Falha *antes* do envio é retentável; falha *depois* (timeout, conexão cortada, resposta inesperada) deixa a chave em **indefinida** — nem manifestada, nem de volta à fila. Os dois chutes erram em direções opostas.
- **Consumo indevido para o lote.** Quando a SEFAZ responde que o acesso está bloqueado, o lote **pausa** em estado retomável em vez de seguir para a próxima chave. Continuar enviando durante o bloqueio reinicia o cronômetro de uma hora, e bloqueios seguidos viram bloqueio permanente do CNPJ. É a única exceção à regra "a SEFAZ respondeu, logo está no ar".
- **Teto de reenvios por rejeição repetida**: três tentativas com a *mesma* rejeição e a chave para de ser reenviada. Rejeição diferente zera a contagem, porque aí o problema passou a ser outro.
- **Rejeição da SEFAZ não abre o *circuit breaker***: se ela devolveu um código, está no ar — o problema é a nota, e parar o lote inteiro por causa de uma nota inválida jogaria fora as outras.
- **Aviso de prazo**: chave com mais de 90 dias entra marcada, medindo do ponto mais tardio possível da autorização para nunca acusar nota que ainda está no prazo. É **aviso, não recusa**.

## Como usar

Acesse `/manifestador` e siga a página:

1. **Conferir o cofre** (uma vez por mês, ou quando um certificado for renovado): a régua do topo mostra quantas empresas estão prontas e o que falta nas demais.
2. **Adicionar as chaves**: colar as do scanner, ou arrastar as pastas de XML do mês. A lista mostra cada pasta com a contagem de arquivos — **confira as contagens antes de importar**.
3. **Escolher o evento** e o modo (uma nota, uma empresa, a carteira).
4. **Manifestar.** Pausar, retomar e parar funcionam durante o lote.

## Limitações atuais

- **Alerta de certificado vencendo ainda não existe** (P3 da spec, MANIF-26). O cofre já mostra o estado `vencido` na tela, mas ninguém é avisado por e-mail — o operador descobre ao abrir a página. O canal SMTP e o anti-spam durável do agendador já existem e seriam reusados; falta ligar um ao outro, com um alerta por empresa.
- **As chaves ainda entram por importação** (P2 da spec, MANIF-22..25). Buscá-las na própria SEFAZ pelo serviço de distribuição é a evolução natural, mas exige resolver o controle de sequência e a janela de consulta. Hoje elas chegam pelo scanner e pelos XML, então não é urgente.
- **A varredura recursiva das pastas roda no navegador** e não tem cobertura de teste automatizado — o projeto não tem runner de JS. O lado servidor está coberto. O sinal de que algo deu errado é a **contagem por pasta vir baixa demais** na tela, antes de importar.
- **A manifestação em massa não tem desfazer.** Nada no sistema cancela um evento já registrado; o que existe é a escolha explícita do evento a cada lote.
