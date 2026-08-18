/* Manifestador de NF-e — "a fila é a página".
 *
 * As barras reusam as classes .zl-comp-* do design language: tanto os estados
 * do cofre (somam as 93 empresas) quanto os do lote (somam as chaves) são
 * composição, não fatos independentes.
 */
import { showToast as toast } from './toasts.js';

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

// --- rede -------------------------------------------------------------------

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

/* Os 44 dígitos são 9 campos. A segmentação semântica mostra de onde a
 * competência foi derivada — o AAMM sublinhado É a explicação. */
const CAMPOS_CHAVE = [
  [0, 2, 'f-cuf'], [2, 6, 'f-aamm'], [6, 20, 'f-cnpj'], [20, 22, 'f-mod'],
  [22, 25, 'f-serie'], [25, 34, 'f-nnf'], [34, 35, 'f-tp'], [35, 43, 'f-cnf'],
  [43, 44, 'f-dv'],
];

function chaveSegmentada(chave) {
  if (!chave || chave.length !== 44) return escapar(chave || '');
  return CAMPOS_CHAVE
    .map(([ini, fim, classe]) => `<span class="${classe}">${chave.slice(ini, fim)}</span>`)
    .join('');
}

function escapar(texto) {
  const no = document.createElement('span');
  no.textContent = texto ?? '';
  return no.innerHTML;
}

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
    }
    /* O `AAMM` sublinhado e o mes de EMISSAO — rotula-lo "competência" era
     * mentira, porque a competencia e a da ENTRADA e vem do XML ou do operador.
     * Quando as duas divergem a linha diz as duas, que e onde o operador
     * percebe a nota que virou o mes. */
    const emissao = c.chave && c.chave.length === 44
      ? `20${c.chave.slice(2, 4)}-${c.chave.slice(4, 6)}` : null;
    const divergiu = c.competencia && emissao && c.competencia !== emissao;
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
      <td><span class="manif-st is-${cor}">${escapar(rotulo)}</span></td>
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
   * painel de andamento do dashboard). */
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

async function importarXml() {
  const arquivos = $('manifXml').files;
  if (!arquivos.length) return;
  const corpo = new FormData();
  [...arquivos].forEach((a) => corpo.append('arquivo', a));
  try {
    const dados = await pedir('/manifestador/importar/xml', { method: 'POST', body: corpo });
    mostrarBalanco(dados.balanco);
    $('manifXml').value = '';
    $('manifImportarXml').disabled = true;
    carregarChaves();
  } catch (erro) {
    toast(erro.message, 'error');
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
      await pedir(`/manifestador/cofre/senha/${botao.dataset.empresa}`,
        comoJson({ senha: botao.dataset.senha }));
      toast('Senha guardada.', 'success');
      carregarCofre();
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
      carregarCofre();
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
  $('manifXml').addEventListener('change', (e) => {
    $('manifImportarXml').disabled = !e.target.files.length;
  });
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
