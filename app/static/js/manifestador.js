/* Manifestador de NF-e — "a fila é a página".
 *
 * As barras reusam as classes .zl-comp-* do design language: tanto os estados
 * do cofre (somam as empresas) quanto os do lote (somam as chaves) são
 * composição, não fatos independentes.
 */
import { showToast as toast } from './toasts.js';
import {
  agrupar_arquivos_xml as daEntradaDeArquivos,
  balanco_vazio as balancoVazio,
  chave_segmentada as chaveSegmentada,
  escapar_html as escapar,
  somar_balanco as somarBalanco,
} from './manifestador_dados.js';

const $ = (id) => document.getElementById(id);

/* Mapa de estado -> token de cor. É a ÚNICA cor da tela (design language:
 * "a única cor é o status"), e a escala é a mesma da tabela de certidões. */
const COR_COFRE = {
  pronto: 'ok',
  vencido: 'danger',
  sem_arquivo: 'muted',
  sem_pasta: 'muted',
  senha_pendente: 'warn',
  cnpj_divergente: 'pend',
};

const ROTULO_COFRE = {
  pronto: 'prontas',
  vencido: 'vencidos',
  sem_arquivo: 'sem arquivo',
  sem_pasta: 'sem pasta',
  senha_pendente: 'falta a senha',
  cnpj_divergente: 'CNPJ não confere',
};

const COR_CHAVE = {
  manifestada: 'ok',
  indefinida: 'warn',
  rejeitada: 'danger',
  duplicata: 'pend',
  pendente: 'muted',
  enviando: 'muted',
};

/* `ja_existia` distingue "manifestamos agora" de "a SEFAZ disse que já
 * constava" (cStat 573). Os dois são desfecho BOM e ficam verdes — mas sem essa
 * distinção a linha mostrava a pílula "Manifestada" com "Rejeição: Duplicidade
 * de evento" logo abaixo, que lê como duas afirmações opostas.
 *
 * Cuidado com a palavra: o estado `duplicata` daqui é OUTRA coisa — a mesma
 * chave importada para duas empresas da carteira (conflito local, "Duas
 * empresas"), sem relação com a duplicidade de evento da SEFAZ. */
const ROTULO_CHAVE = {
  manifestada: 'Manifestada',
  indefinida: 'Sem desfecho',
  rejeitada: 'Rejeitada',
  duplicata: 'Duas empresas',
  pendente: 'Pendente',
  enviando: 'Enviando…',
};

/* Ordem fixa na barra: do desfecho bom ao "nem começou". Mudar a ordem entre
 * uma renderização e outra faria os segmentos pularem de lugar a cada poll. */
const ORDEM_COFRE = ['pronto', 'vencido', 'sem_arquivo', 'sem_pasta',
  'senha_pendente', 'cnpj_divergente'];

let chaves = [];
let empresas = [];
let pollLote = null;
/* Pastas e arquivos escolhidos para importar, acumulados ate o operador clicar
 * em Importar. Guardamos `{nome, itens:[{caminho, arquivo}]}` em vez de uma
 * FileList porque a FileList e imutavel: nao da para somar a segunda pasta nem
 * tirar a que entrou por engano. */
let fontes = [];

// --- rede -------------------------------------------------------------------

/**
 * Executa uma chamada JSON e converte o envelope de erro em exceção.
 *
 * @param {string} url
 * @param {RequestInit=} opcoes
 * @returns {Promise<Record<string, unknown>>}
 */
async function pedir(url, opcoes) {
  const resposta = await fetch(url, opcoes);
  const dados = await resposta.json().catch(() => ({}));
  if (!resposta.ok) {
    const erro = new Error(dados.message || 'Não consegui falar com o servidor.');
    erro.dados = dados;
    erro.status = resposta.status;
    throw erro;
  }
  return dados;
}

const comoJson = (corpo) => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(corpo),
});

// --- a assinatura: chave segmentada pelo significado ------------------------

// --- barra de composição (reusa .zl-comp-*) ---------------------------------

function pintarBarra(elemento, partes, total) {
  elemento.innerHTML = partes.map(({ valor, cor }) => {
    const largura = total ? (valor / total * 100).toFixed(3) : 0;
    return `<div class="zl-comp-seg is-${cor}${valor ? '' : ' is-vazio'}"
                 style="width:${largura}%"></div>`;
  }).join('');
}

function pintarLegenda(elemento, partes) {
  elemento.innerHTML = partes.map(({ valor, cor, rotulo }) => `
    <span class="zl-comp-stat is-${cor}" data-zero="${valor ? '0' : '1'}">
      <strong class="zl-comp-v">${valor}</strong>${escapar(rotulo)}
    </span>`).join('');
}

// --- cofre ------------------------------------------------------------------

async function carregarCofre() {
  const dados = await pedir('/manifestador/cofre');
  const contagem = dados.contagem || {};
  const total = Object.values(contagem).reduce((s, n) => s + n, 0);

  $('manifCofrePronto').textContent = dados.prontas ?? 0;
  $('manifCofreTotal').textContent = total;

  const partes = ORDEM_COFRE
    .filter((estado) => contagem[estado])
    .map((estado) => ({
      valor: contagem[estado],
      cor: COR_COFRE[estado] || 'muted',
      rotulo: ROTULO_COFRE[estado] || estado,
    }));

  pintarBarra($('manifCofreBarra'), partes, total);
  pintarLegenda($('manifCofreLegenda'), partes);

  const aviso = $('manifCofreAviso');
  const pendentes = total - (dados.prontas || 0);
  if (!dados.inventariado) {
    aviso.hidden = false;
    aviso.className = 'manif-st is-warn';
    aviso.textContent = 'Nunca inventariado';
  } else if (pendentes) {
    aviso.hidden = false;
    aviso.className = 'manif-st is-warn';
    aviso.textContent = `${pendentes} a resolver`;
  } else {
    aviso.hidden = true;
  }

  pintarPendencias(dados.problemas || []);
}

function pintarPendencias(problemas) {
  const corpo = $('manifCofreLista');
  if (!problemas.length) {
    corpo.innerHTML = '<tr><td class="text-secondary small py-3">'
      + 'Nenhuma pendência: todos os certificados abrem e conferem.</td></tr>';
    return;
  }
  corpo.innerHTML = problemas.map((p) => {
    /* A senha sugerida vem do NOME DA PASTA e é só proposta: quem grava é o
     * clique do operador. Deduzir credencial de metadado em silêncio seria
     * adivinhação. */
    const acao = p.sugestao_senha
      ? `<button type="button" class="btn btn-soft-primary btn-sm py-0 px-2"
                 data-senha="${escapar(p.sugestao_senha)}" data-empresa="${p.empresa_id}">
           Usar ${escapar(p.sugestao_senha)}
         </button>`
      : `<span class="manif-st is-${COR_COFRE[p.estado] || 'muted'}">
           ${escapar(ROTULO_COFRE[p.estado] || p.estado)}</span>`;
    return `<tr>
      <td class="manif-emp">${escapar(p.empresa)}</td>
      <td class="manif-motivo">${escapar(p.detalhe || p.caminho || '')}</td>
      <td class="text-end">${acao}</td>
    </tr>`;
  }).join('');
}

// --- lista ------------------------------------------------------------------

async function carregarChaves() {
  const filtro = new URLSearchParams();
  if ($('manifEmpresa').value) filtro.set('empresa_id', $('manifEmpresa').value);
  if ($('manifCompetencia').value) filtro.set('competencia', $('manifCompetencia').value);
  if ($('manifEstado').value) filtro.set('status', $('manifEstado').value);

  const dados = await pedir(`/manifestador/chaves?${filtro}`);
  chaves = dados.chaves || [];
  pintarLista();
  atualizarFiltros();
}

function visiveis() {
  const busca = ($('manifBusca').value || '').trim().toLowerCase();
  if (!busca) return chaves;
  return chaves.filter((c) => (c.empresa || '').toLowerCase().includes(busca)
    || c.chave.includes(busca.replace(/\D/g, '')));
}

function pintarLista() {
  const linhas = visiveis();
  const corpo = $('manifLista');
  $('manifVazio').classList.toggle('d-none', linhas.length > 0);

  corpo.innerHTML = linhas.map((c) => {
    const cor = COR_CHAVE[c.status] || 'muted';
    const rotulo = (c.status === 'manifestada' && c.ja_existia)
      ? 'Já estava manifestada'
      : (ROTULO_CHAVE[c.status] || c.status);
    const marcavel = ['pendente', 'rejeitada', 'indefinida'].includes(c.status);
    /* O texto da SEFAZ vai cru (é o registro oficial que o operador pesquisa),
     * mas quando ele contradiz a pílula a linha diz por quê. */
    let motivo = c.cstat ? `${c.cstat} — ${c.xmotivo || ''}` : '';
    if (c.status === 'indefinida') {
      motivo = 'Enviei e não recebi resposta. Confira no portal antes de repetir.';
    } else if (c.status === 'manifestada' && c.ja_existia) {
      motivo = `A SEFAZ respondeu que o evento já constava — ${motivo}`;
    } else if (c.no_teto) {
      /* A SEFAZ bloqueia o CNPJ por 1h quando a mesma rejeição volta 20 vezes;
       * paramos em 3 porque insistir na mesma recusa nunca mudou nada. */
      motivo = `${motivo} · Saiu da fila após ${c.tentativas} tentativas com a `
        + 'mesma recusa. Insistir arrisca bloquear o CNPJ na SEFAZ.';
    }
    /* O `AAMM` sublinhado e o mes de EMISSAO — rotula-lo "competência" era
     * mentira, porque a competencia e a da ENTRADA e vem do XML ou do operador.
     * Quando as duas divergem a linha diz as duas, que e onde o operador
     * percebe a nota que virou o mes. */
    const emissao = c.chave && c.chave.length === 44
      ? `20${c.chave.slice(2, 4)}-${c.chave.slice(4, 6)}` : null;
    const divergiu = c.competencia && emissao && c.competencia !== emissao;
    /* Prazo de 90 dias contados da autorização (Ajuste SINIEF 14/2026). Passado
     * ele a SEFAZ registra Confirmação automática, e manifestar vira rejeição. */
    const prazo = c.fora_do_prazo && c.status === 'pendente'
      ? '<span class="manif-st is-warn ms-2">Fora do prazo</span>' : '';
    const legenda = `<div class="manif-chave-leg"><span>UF</span>
         <span><b>emissão ${escapar(emissao || '—')}</b></span>
         <span>emitente</span><span>nº</span>
         ${c.competencia ? `<span>competência <b>${escapar(c.competencia)}</b>`
           + `${divergiu ? ' (entrada em outro mês)' : ''}</span>` : ''}
       </div>`;
    return `<tr data-id="${c.id}">
      <td>${marcavel ? `<input type="checkbox" class="manif-check" value="${c.id}"
              aria-label="Marcar ${escapar(c.empresa || '')}">` : ''}</td>
      <td class="manif-emp">${escapar(c.empresa || '—')}</td>
      <td>
        <span class="manif-chave">${chaveSegmentada(c.chave)}</span>
        ${legenda}
        ${motivo ? `<div class="manif-motivo">${escapar(motivo)}</div>` : ''}
      </td>
      <td><span class="manif-st is-${cor}">${escapar(rotulo)}</span>${prazo}</td>
    </tr>`;
  }).join('');

  $('manifContagem').innerHTML =
    `<strong class="manif-num">${linhas.length}</strong> na lista`;
  avaliarBotao();
}

function atualizarFiltros() {
  const competencias = [...new Set(chaves.map((c) => c.competencia).filter(Boolean))].sort();
  const alvo = $('manifCompetencia');
  const escolhida = alvo.value;
  alvo.innerHTML = '<option value="">Todas as competências</option>'
    + competencias.map((c) => `<option value="${c}">${c}</option>`).join('');
  alvo.value = escolhida;
}

// --- ação -------------------------------------------------------------------

function marcadas() {
  return [...document.querySelectorAll('.manif-check:checked')].map((i) => Number(i.value));
}

function avaliarBotao() {
  const temEvento = Boolean($('manifEvento').value);
  const temAlvo = marcadas().length > 0 || Boolean($('manifEmpresa').value);
  $('manifManifestar').disabled = !(temEvento && temAlvo);
  const n = marcadas().length;
  $('manifManifestar').textContent = n ? `Manifestar ${n}` : 'Manifestar';
}

async function manifestar() {
  const escolhidas = marcadas();
  const empresaId = $('manifEmpresa').value;
  /* Uma chave marcada = individual; empresa filtrada = a empresa inteira;
   * nada filtrado = a carteira. O modo sai do que está na tela, não de um
   * seletor à parte — assim a fila é exatamente o que o operador vê. */
  const corpo = {
    tipo_evento: $('manifEvento').value,
    competencia: $('manifCompetencia').value || null,
  };
  if (escolhidas.length === 1) {
    corpo.modo = 'individual';
    corpo.chave_id = escolhidas[0];
  } else if (empresaId) {
    corpo.modo = 'empresa';
    corpo.empresa_id = Number(empresaId);
  } else {
    corpo.modo = 'carteira';
  }

  try {
    const dados = await pedir('/manifestador/lote/iniciar', comoJson(corpo));
    const puladas = Object.keys(dados.empresas_puladas || {});
    if (puladas.length) {
      toast(`${puladas.length} empresa(s) ficaram de fora: ${puladas.join(', ')}`, 'warning');
    }
    $('manifAndamento').classList.remove('d-none');
    iniciarPoll();
  } catch (erro) {
    if (erro.dados?.motivo === 'cofre_vazio') {
      toast(erro.message, 'warning');
      $('manifCofreToggle').click();
      return;
    }
    toast(erro.message, 'error');
  }
}

// --- andamento --------------------------------------------------------------

function iniciarPoll() {
  if (pollLote) return;
  pollLote = setInterval(atualizarAndamento, 2000);
  atualizarAndamento();
}

async function atualizarAndamento() {
  let lote;
  try {
    ({ lote } = await pedir('/manifestador/lote/status'));
  } catch {
    return;
  }

  /* Nomes do payload compartilhado com os lotes de certidao: `falhas` e
   * `pendentes_resultado`, nao `failures`/`pending`. */
  $('manifFeitas').textContent = lote.processed || 0;
  $('manifTotalLote').textContent = lote.total || 0;

  const partes = [
    { valor: lote.success || 0, cor: 'ok', rotulo: 'manifestadas' },
    { valor: lote.pendentes_resultado || 0, cor: 'pend', rotulo: 'sem desfecho' },
    { valor: lote.falhas || 0, cor: 'danger', rotulo: 'recusadas' },
  ];
  pintarBarra($('manifBarraLote'), partes, lote.total || 0);
  pintarLegenda($('manifLoteLegenda'), partes);

  /* O nivel vem da ULTIMA mensagem do lote — o payload nao tem `level` no
   * topo. Quando ela e aviso ou erro, a linha viva toma a cor (mesma regra do
   * painel de andamento de Certidões). */
  const ultima = (lote.last_messages || []).slice(-1)[0] || {};
  const linha = $('manifAndamentoLinha');
  linha.textContent = lote.message || 'Manifestando…';
  linha.classList.toggle('is-error', ultima.level === 'error');
  linha.classList.toggle('is-warning', ultima.level === 'warning');

  const pausado = lote.status === 'paused';
  $('manifRetomar').classList.toggle('d-none', !pausado);
  $('manifPausar').classList.toggle('d-none', pausado);

  if (['completed', 'stopped', 'error', 'idle'].includes(lote.status)) {
    clearInterval(pollLote);
    pollLote = null;
    carregarChaves();
    if (lote.status === 'completed') toast('Manifestação concluída.', 'success');
  }
}

// --- entrada ----------------------------------------------------------------

function mostrarBalanco(balanco) {
  const grupos = [
    ['aceitas', 'entraram na fila'],
    ['duplicatas', 'já estavam no sistema'],
    ['dv_invalido', 'com dígito verificador errado'],
    ['competencia_invalida', 'com competência impossível'],
    ['sem_empresa', 'sem empresa da carteira'],
    ['fora_do_prazo', 'com mais de 90 dias — a SEFAZ já pode ter confirmado sozinha'],
    /* Por último de propósito: numa pasta do mês este é o grupo mais numeroso
     * e o único sem nada a fazer. Some com ele e um arquivo de fato quebrado
     * sumiria junto; ponha-o em cima e ele empurra para baixo o que importa. */
    ['nao_e_nfe', 'ignorados — não são NF-e (evento, cancelamento, outro arquivo)'],
  ];
  $('manifBalanco').innerHTML = grupos.map(([campo, texto]) => {
    const itens = balanco[campo] || [];
    if (!itens.length) return '';
    const nomes = itens.map((i) => (typeof i === 'string' ? i : i.chave)).slice(0, 3);
    const resto = itens.length > nomes.length ? ` e mais ${itens.length - nomes.length}` : '';
    return `<div><strong class="manif-num">${itens.length}</strong> ${texto}
              <span class="text-secondary">— ${escapar(nomes.join(', '))}${resto}</span></div>`;
  }).join('') || '<div class="text-secondary">Nenhuma chave encontrada no texto.</div>';
}

async function importarTexto() {
  const empresaId = $('manifEntradaEmpresa').value;
  if (!empresaId) return toast('Escolha a empresa dona destas notas.', 'warning');
  try {
    const dados = await pedir('/manifestador/importar', comoJson({
      empresa_id: Number(empresaId),
      texto: $('manifTexto').value,
      competencia: $('manifEntradaCompetencia').value || null,
    }));
    mostrarBalanco(dados.balanco);
    $('manifTexto').value = '';
    carregarChaves();
  } catch (erro) {
    toast(erro.message, 'error');
  }
}

// --- pastas de XML ----------------------------------------------------------

/* Nome da fonte para o que nao veio dentro de pasta nenhuma. */
const AVULSOS = 'Arquivos avulsos';

/* O servidor recusa mais de 1.000 partes por requisicao (limite do Werkzeug), e
 * a pasta de um mes passa disso com folga. Enviar em blocos tambem e o que
 * permite mostrar progresso: com 1.200 notas, uma tela parada por dois minutos
 * parece travamento. */
const POR_ENVIO = 200;
const EH_XML = /\.xml$/i;

const totalArquivos = () => fontes.reduce((soma, f) => soma + f.itens.length, 0);

const chaveDoItem = (item) => `${item.caminho}|${item.arquivo.size}`;

function adicionarFonte(nome, itens) {
  /* Mesma fonte escolhida de novo SOMA sem repetir: quem clica duas vezes na
   * mesma pasta quer conferir, nao importar tudo em dobro. E os avulsos, que
   * caem todos sob um nome so, precisam justamente acumular. */
  if (!itens.length) return;
  let fonte = fontes.find((f) => f.nome === nome);
  if (!fonte) {
    fonte = { nome, itens: [] };
    fontes.push(fonte);
  }
  const vistos = new Set(fonte.itens.map(chaveDoItem));
  itens.forEach((item) => {
    if (vistos.has(chaveDoItem(item))) return;
    vistos.add(chaveDoItem(item));
    fonte.itens.push(item);
  });
}

function pintarFontes() {
  const lista = $('manifFontes');
  lista.hidden = !fontes.length;
  lista.innerHTML = fontes.map((f, i) => `
    <li>
      <i class="bi bi-folder2" aria-hidden="true"></i>
      <span class="manif-fonte-nome" title="${escapar(f.nome)}">${escapar(f.nome)}</span>
      <span class="manif-fonte-num">${f.itens.length} XML</span>
      <button type="button" class="btn btn-ghost btn-sm py-0 px-1" data-fonte="${i}"
              aria-label="Tirar ${escapar(f.nome)} da lista">
        <i class="bi bi-x-lg" aria-hidden="true"></i>
      </button>
    </li>`).join('');

  const resumo = $('manifFontesResumo');
  resumo.hidden = !fontes.length;
  resumo.textContent = fontes.length === 1
    ? '1 pasta escolhida' : `${fontes.length} pastas escolhidas`;

  const total = totalArquivos();
  const botao = $('manifImportarXml');
  botao.disabled = !total;
  botao.textContent = total
    ? `Importar ${total.toLocaleString('pt-BR')} XML`
    : 'Importar XML';
}

async function lerEntrada(entrada, prefixo, saida) {
  /* Percurso recursivo do que foi ARRASTADO. Arrastar e o unico caminho que
   * aceita varias pastas de uma vez — o seletor do sistema abre uma so. */
  if (entrada.isFile) {
    if (!EH_XML.test(entrada.name)) return;
    const arquivo = await new Promise((ok, falhou) => entrada.file(ok, falhou));
    saida.push({ caminho: `${prefixo}${entrada.name}`, arquivo });
    return;
  }
  if (!entrada.isDirectory) return;
  const leitor = entrada.createReader();
  /* `readEntries` devolve no MAXIMO 100 por chamada e nao avisa que truncou:
   * sem repetir ate vir vazio, uma pasta de 400 notas entraria com 100. */
  for (;;) {
    // eslint-disable-next-line no-await-in-loop
    const bloco = await new Promise((ok, falhou) => leitor.readEntries(ok, falhou));
    if (!bloco.length) break;
    for (const filho of bloco) {
      // eslint-disable-next-line no-await-in-loop
      await lerEntrada(filho, `${prefixo}${entrada.name}/`, saida);
    }
  }
}

async function receberSoltos(evento) {
  evento.preventDefault();
  $('manifSolta').classList.remove('is-sobre');
  /* As entradas tem de sair do DataTransfer AGORA, sincronamente: depois que o
   * handler devolve o controle o navegador esvazia o objeto, e o `await` de
   * baixo encontraria a lista vazia. */
  const entradas = [...evento.dataTransfer.items]
    .map((item) => (item.webkitGetAsEntry ? item.webkitGetAsEntry() : null))
    .filter(Boolean);
  if (!entradas.length) return;

  const antes = totalArquivos();
  for (const entrada of entradas) {
    const achados = [];
    // eslint-disable-next-line no-await-in-loop
    await lerEntrada(entrada, '', achados);
    adicionarFonte(entrada.isDirectory ? entrada.name : AVULSOS, achados);
  }
  pintarFontes();
  if (totalArquivos() === antes) toast('Nenhum XML dentro do que você soltou.', 'warning');
}

function progresso(texto) {
  const linha = $('manifProgresso');
  linha.textContent = texto;
  linha.hidden = !texto;
}

function descartarEnviados(quantos) {
  /* Tira da fila os que ja foram, na MESMA ordem em que foram achatados. Depois
   * de um erro no meio, e isso que faz o botao reenviar so o que falta: sem
   * isso, tentar de novo devolveria "184 ja estavam no sistema" e esconderia o
   * que realmente ficou de fora. */
  let restam = quantos;
  fontes.forEach((fonte) => {
    const corta = Math.min(restam, fonte.itens.length);
    fonte.itens = fonte.itens.slice(corta);
    restam -= corta;
  });
  fontes = fontes.filter((fonte) => fonte.itens.length);
}

async function importarXml() {
  const itens = fontes.flatMap((f) => f.itens);
  if (!itens.length) return;
  $('manifImportarXml').disabled = true;
  /* Cada arquivo ja e gravado individualmente no servidor, entao um bloco que
   * falha no meio nao desfaz os anteriores: mostramos o que ENTROU junto com o
   * erro, em vez de deixar o operador achando que nada aconteceu. */
  const acumulado = balancoVazio();
  let enviados = 0;
  try {
    for (let i = 0; i < itens.length; i += POR_ENVIO) {
      const bloco = itens.slice(i, i + POR_ENVIO);
      progresso(`Lendo ${i + bloco.length} de ${itens.length} arquivos…`);
      const corpo = new FormData();
      /* O caminho vai como nome do arquivo: recusa que diz "Julho/nota.xml" se
       * acha na pasta; uma que diz so "nota.xml" nao. */
      bloco.forEach(({ caminho, arquivo }) => corpo.append('arquivo', arquivo, caminho));
      // eslint-disable-next-line no-await-in-loop
      const dados = await pedir('/manifestador/importar/xml',
        { method: 'POST', body: corpo });
      somarBalanco(acumulado, dados.balanco);
      enviados += bloco.length;
    }
    $('manifXml').value = '';
    $('manifPasta').value = '';
  } catch (erro) {
    toast(erro.message, 'error');
  } finally {
    progresso('');
    descartarEnviados(enviados);
    /* Balanco zerado nao e balanco: com a falha logo no primeiro bloco, exibi-lo
     * imprimiria "nenhuma chave encontrada" ao lado do aviso de erro, duas
     * afirmacoes diferentes sobre a mesma coisa. */
    if (acumulado.total_lidas) mostrarBalanco(acumulado);
    else $('manifBalanco').innerHTML = '';
    pintarFontes();
    carregarChaves();
  }
}

// --- ligação ----------------------------------------------------------------

function ligar() {
  $('manifCofreToggle').addEventListener('click', (e) => {
    const detalhe = $('manifCofreDetalhe');
    const aberto = detalhe.classList.toggle('d-none') === false;
    e.currentTarget.setAttribute('aria-expanded', String(aberto));
    e.currentTarget.innerHTML = `${aberto ? 'Fechar' : 'Abrir'} `
      + `<i class="bi bi-chevron-${aberto ? 'up' : 'down'}" aria-hidden="true"></i>`;
  });

  $('manifCofreLista').addEventListener('click', async (e) => {
    const botao = e.target.closest('[data-senha]');
    if (!botao) return;
    try {
      /* A rota devolve o DESFECHO e o JS descartava. Senha CERTA em certificado
       * VENCIDO grava a senha e mantem a pendencia (`gravar_senha`): a linha
       * continua ali com razao, mas o "Senha guardada." sozinho fazia isso
       * parecer defeito da tela. Dizer qual dos dois aconteceu custa uma
       * linha e e a diferenca entre "nao funcionou" e "falta renovar". */
      const { estado } = await pedir(`/manifestador/cofre/senha/${botao.dataset.empresa}`,
        comoJson({ senha: botao.dataset.senha }));
      if (estado === 'vencido') {
        toast('Senha guardada, mas o certificado está vencido — renove antes de manifestar.',
          'warning');
      } else {
        toast('Senha guardada. Certificado pronto.', 'success');
      }
      /* `await`: sem ele a rejeicao escapa do try/catch, que ja saiu de cena —
       * a lista nao repinta E nao aparece erro nenhum, o pior dos dois mundos. */
      await carregarCofre();
    } catch (erro) {
      toast(erro.message, 'error');
    }
  });

  $('manifReler').addEventListener('click', async () => {
    $('manifRelerAviso').textContent = 'Lendo o drive… isso leva um par de minutos.';
    try {
      await pedir('/manifestador/cofre/inventariar', { method: 'POST' });
      $('manifRelerAviso').textContent = '';
      toast('Cofre atualizado.', 'success');
      await carregarCofre();
    } catch (erro) {
      $('manifRelerAviso').textContent = '';
      toast(erro.message, 'error');
    }
  });

  ['manifEmpresa', 'manifCompetencia', 'manifEstado'].forEach((id) => {
    $(id).addEventListener('change', carregarChaves);
  });
  $('manifBusca').addEventListener('input', pintarLista);
  $('manifEvento').addEventListener('change', avaliarBotao);
  $('manifLista').addEventListener('change', avaliarBotao);
  $('manifMarcarTudo').addEventListener('change', (e) => {
    document.querySelectorAll('.manif-check').forEach((i) => { i.checked = e.target.checked; });
    avaliarBotao();
  });

  $('manifManifestar').addEventListener('click', manifestar);
  $('manifPausar').addEventListener('click', () => pedir('/manifestador/lote/pausar', { method: 'POST' }));
  $('manifParar').addEventListener('click', () => pedir('/manifestador/lote/parar', { method: 'POST' }));
  $('manifRetomar').addEventListener('click', async () => {
    await pedir('/manifestador/lote/retomar', { method: 'POST' });
    iniciarPoll();
  });

  $('manifAbrirEntrada').addEventListener('click', () => {
    $('manifEntrada').hidden = false;
    /* Padrao = mes anterior, que e o ritmo do escritorio ("no mes 08 a
     * competencia e 07"). E so um ponto de partida: o campo fica editavel. */
    if (!$('manifEntradaCompetencia').value) {
      const d = new Date();
      d.setDate(1);
      d.setMonth(d.getMonth() - 1);
      $('manifEntradaCompetencia').value =
        `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
    }
  });
  $('manifFecharEntrada').addEventListener('click', () => { $('manifEntrada').hidden = true; });
  $('manifImportar').addEventListener('click', importarTexto);
  $('manifImportarXml').addEventListener('click', importarXml);
  $('manifEscolherPasta').addEventListener('click', () => $('manifPasta').click());
  $('manifEscolherArquivos').addEventListener('click', () => $('manifXml').click());

  ['manifPasta', 'manifXml'].forEach((id) => {
    $(id).addEventListener('change', (e) => {
      daEntradaDeArquivos(e.target.files).forEach((itens, nome) => {
        adicionarFonte(nome, itens);
      });
      pintarFontes();
      /* Zerar o input e o que faz escolher a MESMA pasta de novo disparar
       * `change` outra vez — sem isso, tirar a pasta da lista e reescolhe-la
       * nao teria efeito nenhum. */
      e.target.value = '';
    });
  });

  $('manifFontes').addEventListener('click', (e) => {
    const botao = e.target.closest('[data-fonte]');
    if (!botao) return;
    fontes.splice(Number(botao.dataset.fonte), 1);
    pintarFontes();
  });

  const solta = $('manifSolta');
  ['dragenter', 'dragover'].forEach((evento) => {
    solta.addEventListener(evento, (e) => {
      e.preventDefault();
      solta.classList.add('is-sobre');
    });
  });
  solta.addEventListener('dragleave', (e) => {
    if (!solta.contains(e.relatedTarget)) solta.classList.remove('is-sobre');
  });
  solta.addEventListener('drop', receberSoltos);
}

function carregarEmpresas() {
  /* Vem do servidor no proprio HTML (mesmo padrao da NFSe): a lista muda
   * raramente e nao merece uma rota nem uma ida a rede a cada carregamento. */
  empresas = JSON.parse($('manifDados').dataset.empresas || '[]');
  const opcoes = empresas
    .map((e) => `<option value="${e.id}">${escapar(e.nome)}</option>`).join('');
  $('manifEmpresa').insertAdjacentHTML('beforeend', opcoes);
  $('manifEntradaEmpresa').insertAdjacentHTML('beforeend', opcoes);
}

document.addEventListener('DOMContentLoaded', async () => {
  ligar();
  carregarEmpresas();
  await Promise.all([carregarCofre(), carregarChaves()]);
  const { lote } = await pedir('/manifestador/lote/status').catch(() => ({ lote: {} }));
  if (['running', 'paused'].includes(lote?.status)) {
    $('manifAndamento').classList.remove('d-none');
    iniciarPoll();
  }
});
