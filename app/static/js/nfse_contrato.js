// Central de alterações do contrato adaptativo da NFS-e.
//
// Este módulo só consulta o estado persistido e reage a ações explícitas do
// operador. Ele nunca chama a rota de recon, preenche a sessão ou emite nota
// durante a inicialização.

import { showToast } from './toasts.js';
import { limparInvalido, marcarInvalido } from './campos.js';

const ORIGENS = new Set([
  'fixo',
  'nota',
  'derivado',
  'configuracao',
  'padrao_portal',
  'intocavel',
]);

const ORIGENS_SEM_FONTE = new Set(['fixo', 'padrao_portal', 'intocavel']);
const rotulosEstado = {
  compativel: {
    titulo: 'Contrato compatível',
    texto: 'Não há incidentes no contrato ativo.',
  },
  aviso: {
    titulo: 'Contrato com avisos',
    texto: 'Há alterações que precisam de atenção do operador.',
  },
  bloqueado: {
    titulo: 'Automação bloqueada',
    texto: 'Resolva os incidentes críticos antes do modo automático.',
  },
  desconhecido: {
    titulo: 'Estado desconhecido',
    texto: 'Não foi possível confirmar o estado do contrato.',
  },
};

function porId(root, id) {
  return root?.getElementById?.(id) || root?.querySelector?.(`#${id}`) || null;
}

function criarElemento(tag, texto) {
  const elemento = document.createElement(tag);
  if (texto != null) elemento.textContent = String(texto);
  return elemento;
}

function limpar(elemento) {
  if (elemento) elemento.replaceChildren();
}

function dadosEstado(payload) {
  if (payload && payload.ativo) return payload;
  if (payload?.contrato_estado?.ativo) return payload.contrato_estado;
  return null;
}

function incidentesPendentes(estado) {
  return Array.isArray(estado?.incidentes)
    ? estado.incidentes.filter((item) => ['aberto', 'configurado'].includes(item?.estado))
    : [];
}

function estadoVisual(estado) {
  if (!estado?.ativo || !Array.isArray(estado.incidentes)) return 'desconhecido';
  // O servidor manda: `estado_visual` traduz o mesmo fato que fecha o gate do
  // automatico. A regra vivia em quatro copias e elas ja discordavam — a faixa
  // dizia "aviso" ao lado do radio desabilitado. O fallback so cobre payload
  // antigo, e segue a mesma regra do servidor.
  if (typeof estado.estado_visual === 'string' && estado.estado_visual) {
    return estado.estado_visual;
  }
  const abertos = incidentesPendentes(estado);
  if (estado.ativo.elegivel_automatico === false || abertos.length) return 'bloqueado';
  return 'compativel';
}

function preencherStatus(estado, root) {
  const faixa = porId(root, 'nfseContratoStatus');
  const titulo = porId(root, 'nfseContratoStatusTitulo');
  const texto = porId(root, 'nfseContratoStatusTexto');
  const visual = estadoVisual(estado);
  if (faixa) faixa.dataset.estado = visual;
  if (titulo) titulo.textContent = rotulosEstado[visual].titulo;
  if (texto) texto.textContent = rotulosEstado[visual].texto;
  return visual;
}


const ROTULOS_ORIGEM = [
  ['fixo', 'Valor fixo'],
  ['nota', 'Fonte da nota'],
  ['derivado', 'Valor derivado'],
  ['configuracao', 'Configuração'],
  ['padrao_portal', 'Padrão do portal'],
  ['intocavel', 'Não tocar'],
];

// Quantas opções cabem antes de o `<details>` valer a pena. O select de país
// tem 200+; despejá-las na linha enterra os incidentes seguintes.
const OPCOES_VISIVEIS = 4;

const CAMPOS_DA_LINHA = ['origem', 'fonte', 'valor_fixo', 'chave_observada'];

function montarSelect(classe, placeholder, itens) {
  const select = criarElemento('select');
  select.className = `form-select form-select-sm ${classe}`.trim();
  const vazio = criarElemento('option', placeholder);
  vazio.value = '';
  vazio.selected = true;
  select.appendChild(vazio);
  itens.forEach(([valor, rotulo]) => {
    const item = criarElemento('option', rotulo);
    item.value = valor;
    select.appendChild(item);
  });
  return select;
}

function campoDaLinha(linha, nome) {
  return linha?.querySelector(`[data-campo="${nome}"]`) || null;
}

function exibirCampo(elemento, visivel) {
  if (!elemento) return;
  elemento.hidden = !visivel;
  elemento.classList.toggle('d-none', !visivel);
}

/**
 * Mostra na linha só os campos que a origem escolhida exige.
 *
 * @param {HTMLElement} linha
 * @param {Array<object>} fontes
 * @param {string} origem
 */
function aplicarOrigemNaLinha(linha, fontes, origem) {
  const fonte = campoDaLinha(linha, 'fonte');
  const valor = campoDaLinha(linha, 'valor_fixo');
  exibirCampo(fonte, Boolean(origem) && !ORIGENS_SEM_FONTE.has(origem));
  exibirCampo(valor, origem === 'fixo');
  limparInvalido(fonte);
  limparInvalido(valor);
  const artigo = linha?.closest?.('.nfse-contrato-incidente');
  const erro = artigo?.querySelector('.nfse-contrato-erro');
  if (erro) erro.textContent = '';
  if (!fonte) return;
  limpar(fonte);
  const vazio = criarElemento('option', 'Fonte…');
  vazio.value = '';
  vazio.selected = true;
  fonte.appendChild(vazio);
  opcoesDaOrigem(fontes, origem).forEach((opcao) => {
    const item = criarElemento('option', opcao.rotulo || opcao.fonte || 'Fonte');
    item.value = opcao.fonte || '';
    fonte.appendChild(item);
  });
  if (valor && origem !== 'fixo') valor.value = '';
}

function montarOpcoes(incidente) {
  const opcoes = Array.isArray(incidente.opcoes) ? incidente.opcoes : [];
  if (!opcoes.length) return null;
  const bloco = criarElemento('details');
  bloco.className = 'nfse-contrato-opcoes';
  const resumo = criarElemento('summary');
  const visiveis = opcoes.slice(0, OPCOES_VISIVEIS)
    .map((opcao) => opcao.rotulo || opcao.valor || '')
    .join(' · ');
  const restante = opcoes.length - OPCOES_VISIVEIS;
  resumo.textContent = restante > 0
    ? `${opcoes.length} opções: ${visiveis} +${restante}`
    : `${opcoes.length} opções: ${visiveis}`;
  bloco.appendChild(resumo);
  const lista = criarElemento('ul');
  opcoes.forEach((opcao) => {
    const item = criarElemento('li');
    item.appendChild(document.createTextNode(`${opcao.rotulo || ''} `));
    item.appendChild(criarElemento('code', opcao.valor || ''));
    lista.appendChild(item);
  });
  bloco.appendChild(lista);
  return bloco;
}

function montarConfiguracao(incidente, estado) {
  const forma = criarElemento('form');
  forma.className = 'nfse-contrato-config';
  forma.dataset.configIncidente = String(incidente.id ?? '');

  const origem = montarSelect('', 'Origem…', ROTULOS_ORIGEM);
  origem.dataset.campo = 'origem';
  origem.required = true;
  origem.setAttribute('aria-label', 'Origem do valor');
  forma.appendChild(origem);

  const fonte = montarSelect('', 'Fonte…', []);
  fonte.dataset.campo = 'fonte';
  fonte.setAttribute('aria-label', 'Fonte do valor');
  forma.appendChild(fonte);

  // Quando o controle declara opções, o valor fixo é uma escolha entre elas —
  // não um código para o operador decorar. O rótulo é o que ele lê na tela; o
  // código aparece junto só para conferência.
  const declaradas = Array.isArray(incidente.opcoes) ? incidente.opcoes : [];
  const valor = declaradas.length
    ? montarSelect('', 'Escolha a opção…', declaradas.map((opcao) => [
      opcao.valor || '',
      opcao.valor ? `${opcao.rotulo || opcao.valor} (${opcao.valor})` : (opcao.rotulo || ''),
    ]))
    : criarElemento('input');
  if (!declaradas.length) {
    valor.className = 'form-control form-control-sm';
    valor.maxLength = 500;
    valor.autocomplete = 'off';
    valor.placeholder = 'Valor fixo';
  }
  valor.dataset.campo = 'valor_fixo';
  valor.setAttribute('aria-label', 'Valor fixo');
  forma.appendChild(valor);

  // A recomendação de remapeamento é rara; quando existe, o operador precisa
  // escolher o controle e confirmar na mesma linha, sem sair para um modal.
  const recomendacao = recomendacaoDoIncidente(incidente);
  if (recomendacao) {
    const escolha = montarSelect('', 'Controle…',
      (recomendacao.candidatos || []).map((chave) => [chave, chave]));
    escolha.dataset.campo = 'chave_observada';
    escolha.setAttribute('aria-label', 'Controle correspondente');
    if (!recomendacao.ambigua && recomendacao.chave_observada) {
      escolha.value = recomendacao.chave_observada;
    }
    forma.appendChild(escolha);

    const confirmar = criarElemento('label');
    confirmar.className = 'nfse-contrato-confirmar';
    const caixa = criarElemento('input');
    caixa.type = 'checkbox';
    caixa.className = 'form-check-input';
    caixa.dataset.campo = 'confirmar_recomendacao';
    confirmar.appendChild(caixa);
    confirmar.appendChild(document.createTextNode(
      recomendacao.ambigua ? ' confirmo (ambígua)' : ' confirmo',
    ));
    confirmar.title = (recomendacao.evidencias || []).join('; ');
    forma.appendChild(confirmar);
  }

  const salvar = criarElemento('button', 'Salvar');
  salvar.type = 'submit';
  salvar.className = 'btn btn-soft-primary btn-sm';
  forma.appendChild(salvar);

  aplicarOrigemNaLinha(forma, estado?.fontes || [], '');
  return forma;
}

/**
 * Fotografa o que o operador já escolheu e ainda não salvou.
 *
 * Salvar uma linha recarrega o estado e redesenha a lista inteira. Sem esta
 * foto, marcar dez incidentes e ir salvando um a um é impossível: o primeiro
 * Salvar apaga as outras nove escolhas.
 *
 * @param {HTMLElement} lista
 * @returns {Map<string, object>}
 */
function fotografarEscolhas(lista) {
  const foto = new Map();
  lista.querySelectorAll('.nfse-contrato-incidente').forEach((artigo) => {
    const forma = artigo.querySelector('.nfse-contrato-config');
    if (!forma) return;
    const valores = {};
    CAMPOS_DA_LINHA.forEach((nome) => {
      const campo = campoDaLinha(forma, nome);
      if (campo && campo.value) valores[nome] = campo.value;
    });
    const confirmar = campoDaLinha(forma, 'confirmar_recomendacao');
    if (confirmar?.checked) valores.confirmar_recomendacao = true;
    if (Object.keys(valores).length) foto.set(artigo.dataset.incidenteId, valores);
  });
  return foto;
}

function restaurarEscolhas(forma, valores, fontes) {
  if (!valores) return;
  const origem = campoDaLinha(forma, 'origem');
  if (valores.origem && origem) {
    origem.value = valores.origem;
    // Repovoa fonte e troca fonte↔valor antes de devolver os demais valores.
    aplicarOrigemNaLinha(forma, fontes, valores.origem);
  }
  CAMPOS_DA_LINHA.filter((nome) => nome !== 'origem').forEach((nome) => {
    const campo = campoDaLinha(forma, nome);
    if (campo && valores[nome] !== undefined) campo.value = valores[nome];
  });
  const confirmar = campoDaLinha(forma, 'confirmar_recomendacao');
  if (confirmar && valores.confirmar_recomendacao) confirmar.checked = true;
}

function renderizarIncidentes(estado, root) {
  const lista = porId(root, 'nfseContratoIncidentes');
  if (!lista) return;
  const escolhas = fotografarEscolhas(lista);
  limpar(lista);
  const incidentes = incidentesPendentes(estado);
  if (!incidentes.length) {
    const vazio = criarElemento('p', 'Não há incidentes no contrato ativo.');
    vazio.id = 'nfseContratoVazio';
    vazio.className = 'nfse-hint mb-0';
    lista.appendChild(vazio);
    return;
  }

  incidentes.forEach((incidente) => {
    const artigo = criarElemento('article');
    artigo.className = 'nfse-contrato-incidente';
    artigo.dataset.incidenteId = String(incidente.id ?? '');

    const linha = criarElemento('div');
    linha.className = 'nfse-contrato-linha';

    const identidade = criarElemento('div');
    identidade.className = 'nfse-contrato-campo';
    const nome = criarElemento('strong', incidente.campo?.rotulo || 'Campo sem rótulo');
    nome.title = incidente.campo?.chave_observada || incidente.campo?.chave_esperada || '';
    identidade.appendChild(nome);
    const meta = criarElemento('span', [
      incidente.etapa || 'etapa desconhecida',
      incidente.tipo || 'diferença',
      incidente.campo?.obrigatorio ? 'obrigatório' : 'opcional',
      `${incidente.observacoes ?? 0}×`,
    ].join(' · '));
    meta.className = 'nfse-contrato-meta';
    identidade.appendChild(meta);
    linha.appendChild(identidade);

    const selo = criarElemento('span', incidente.severidade || 'informativa');
    selo.className = 'nfse-status';
    selo.dataset.status = incidente.severidade || 'informativa';
    linha.appendChild(selo);

    if (incidente.estado === 'aberto') {
      const forma = montarConfiguracao(incidente, estado);
      restaurarEscolhas(forma, escolhas.get(String(incidente.id ?? '')), estado?.fontes || []);
      linha.appendChild(forma);
    } else {
      const feito = criarElemento('span', 'configurado');
      feito.className = 'nfse-contrato-meta';
      linha.appendChild(feito);
      // Configurar não pode ser via de mão única: o desfazer devolve o
      // incidente a `aberto` para ser reconfigurado na mesma linha.
      if (incidente.contrato_candidato_id) {
        const desfazer = criarElemento('button', 'Desfazer');
        desfazer.type = 'button';
        desfazer.className = 'btn btn-ghost btn-sm';
        desfazer.dataset.desfazerCandidata = String(incidente.contrato_candidato_id);
        linha.appendChild(desfazer);
      }
    }
    artigo.appendChild(linha);

    // Com o select de opções na linha, a lista expansível vira ruído: ela
    // existia só para o operador descobrir qual código era qual.
    if (incidente.estado !== 'aberto') {
      const opcoes = montarOpcoes(incidente);
      if (opcoes) artigo.appendChild(opcoes);
    }

    const erro = criarElemento('div');
    erro.className = 'nfse-contrato-erro text-danger';
    erro.setAttribute('role', 'alert');
    artigo.appendChild(erro);

    lista.appendChild(artigo);
  });
}

function renderizarHistorico(estado, root) {
  const historico = porId(root, 'nfseContratoHistorico');
  if (!historico) return;
  limpar(historico);
  historico.appendChild(criarElemento('h3', 'Histórico de versões'));
  const candidatas = Array.isArray(estado?.candidatas) ? estado.candidatas : [];
  if (!candidatas.length) {
    const vazio = criarElemento('p', 'Nenhuma versão candidata foi criada.');
    vazio.className = 'nfse-hint mb-0';
    historico.appendChild(vazio);
    return;
  }
  const envoltorio = criarElemento('div');
  envoltorio.className = 'table-responsive';
  const tabela = criarElemento('table');
  tabela.className = 'table table-sm align-middle';
  const cabecalho = criarElemento('thead');
  const linhaCabecalho = criarElemento('tr');
  ['Versão', 'Estado', 'Ação'].forEach((rotulo, indice) => {
    const th = criarElemento('th', rotulo);
    th.scope = 'col';
    if (indice === 2) th.className = 'text-end';
    linhaCabecalho.appendChild(th);
  });
  cabecalho.appendChild(linhaCabecalho);
  tabela.appendChild(cabecalho);
  const corpo = criarElemento('tbody');
  candidatas.forEach((candidata) => {
    const linha = criarElemento('tr');
    linha.dataset.contratoCandidato = String(candidata.id ?? '');
    const versao = criarElemento('td', candidata.versao ?? '—');
    versao.className = 'nfse-mono';
    linha.appendChild(versao);
    linha.appendChild(criarElemento('td', candidata.estado || 'desconhecida'));
    const acao = criarElemento('td');
    acao.className = 'text-end';
    const botao = criarElemento(
      'button',
      candidata.estado === 'validada' ? 'Ativar versão' : 'Validar candidata',
    );
    botao.type = 'button';
    botao.className = candidata.estado === 'validada'
      ? 'btn btn-primary btn-sm'
      : 'btn btn-soft-primary btn-sm';
    if (candidata.estado === 'validada') {
      botao.dataset.ativarContrato = String(candidata.id ?? '');
    } else {
      botao.dataset.validarContrato = String(candidata.id ?? '');
      botao.setAttribute('data-bs-toggle', 'modal');
      botao.setAttribute('data-bs-target', '#modalValidarContrato');
    }
    const descartar = criarElemento('button', 'Descartar');
    descartar.type = 'button';
    descartar.className = 'btn btn-ghost btn-sm ms-1';
    descartar.dataset.desfazerCandidata = String(candidata.id ?? '');
    acao.appendChild(botao);
    acao.appendChild(descartar);
    linha.appendChild(acao);
    corpo.appendChild(linha);
  });
  tabela.appendChild(corpo);
  envoltorio.appendChild(tabela);
  historico.appendChild(envoltorio);
}

/**
 * Renderiza o estado persistido da central sem interpretar HTML do servidor.
 *
 * @param {object} payload
 * @param {Document|HTMLElement} [root=document]
 * @returns {string} estado visual calculado
 */
export function renderizarEstadoContrato(payload, root = document) {
  const estado = dadosEstado(payload);
  const central = porId(root, 'nfseContratoCentral');
  if (central && estado?.ativo?.id != null) central.dataset.contratoId = String(estado.ativo.id);
  preencherStatus(estado, root);
  renderizarIncidentes(estado, root);
  renderizarHistorico(estado, root);
  return estadoVisual(estado);
}

/**
 * Devolve as fontes do catálogo para uma origem, mantendo a ordem do servidor.
 *
 * @param {Array<object>} fontes
 * @param {string} origem
 * @returns {Array<object>}
 */
export function opcoesDaOrigem(fontes, origem) {
  if (!Array.isArray(fontes)) return [];
  return fontes
    .filter((item) => item && item.origem === origem)
    .map((item) => ({ ...item }));
}

function valorDe(dados, camel, snake) {
  return dados[camel] ?? dados[snake];
}

/**
 * Valida a escolha do operador e monta somente o payload aceito pela rota.
 *
 * @param {object} dados
 * @returns {object}
 */
export function montarDadosCandidato(dados = {}) {
  const origem = String(dados.origem || '').trim();
  const fonteCrua = valorDe(dados, 'fonte', 'fonte');
  const fonte = fonteCrua == null || String(fonteCrua).trim() === ''
    ? null
    : String(fonteCrua).trim();
  const valorFixoCru = valorDe(dados, 'valorFixo', 'valor_fixo');
  const valorFixo = valorFixoCru == null ? '' : String(valorFixoCru);
  const fontes = Array.isArray(dados.fontes) ? dados.fontes : null;
  const recomendacao = dados.recomendacao || null;
  const confirmou = valorDe(dados, 'confirmarRecomendacao', 'confirmar_recomendacao');
  const chaveCrua = valorDe(dados, 'chaveObservada', 'chave_observada');
  let chaveObservada = chaveCrua == null ? null : String(chaveCrua).trim();

  if (!ORIGENS.has(origem)) throw new Error('Escolha uma origem válida.');
  if (recomendacao) {
    chaveObservada ||= recomendacao.chave_observada || null;
    if (!chaveObservada) {
      throw new Error('A recomendação é ambígua; escolha o campo manualmente.');
    }
    if (!(recomendacao.candidatos || []).includes(chaveObservada)) {
      throw new Error('Escolha um dos campos recomendados.');
    }
  }
  if (recomendacao && confirmou !== true) {
    throw new Error('Confirme explicitamente a recomendação antes de salvar.');
  }

  if (ORIGENS_SEM_FONTE.has(origem)) {
    if (fonte !== null) throw new Error('Esta origem não aceita fonte.');
  } else {
    const opcoes = opcoesDaOrigem(fontes, origem);
    if (fonte === null || (fontes && !opcoes.some((item) => item.fonte === fonte))) {
      throw new Error('Escolha uma fonte válida para esta origem.');
    }
  }

  if (origem === 'fixo') {
    if (!valorFixo.trim()) throw new Error('Informe o valor fixo.');
    if (valorFixo.length > 500) throw new Error('O valor fixo excede o limite permitido.');
  } else if (valorFixo.trim()) {
    throw new Error('Valor fixo só pode ser usado com a origem fixa.');
  }

  const payload = { origem };
  if (fonte !== null) payload.fonte = fonte;
  if (origem === 'fixo') payload.valor_fixo = valorFixo;
  if (recomendacao && confirmou === true) {
    payload.confirmar_recomendacao = true;
    payload.chave_observada = chaveObservada;
  }
  return payload;
}

async function lerResposta(resposta) {
  const dados = await resposta.json().catch(() => ({}));
  if (!resposta.ok) {
    throw new Error(dados.message || 'Não foi possível concluir a ação.');
  }
  return dados;
}

async function chamar(fetchImpl, url, payload) {
  const resposta = await fetchImpl(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return lerResposta(resposta);
}

function mostrarErro(root, id, mensagem) {
  const elemento = porId(root, id);
  if (elemento) elemento.textContent = mensagem || '';
}




function definirCarregando(botao, carregando, textoOriginal) {
  if (!botao) return;
  botao.disabled = carregando;
  botao.setAttribute('aria-busy', String(carregando));
  if (carregando) {
    botao.dataset.carregando = '1';
    botao.textContent = 'Aguarde…';
  } else {
    delete botao.dataset.carregando;
    botao.textContent = textoOriginal;
  }
}

async function comCarregamento(botao, trabalho) {
  if (!botao || botao.dataset.carregando) return false;
  const textoOriginal = botao.textContent;
  definirCarregando(botao, true, textoOriginal);
  try {
    await trabalho();
  } finally {
    definirCarregando(botao, false, textoOriginal);
  }
  return true;
}

function esconderModal(id) {
  const modal = porId(document, id);
  const bootstrap = globalThis.bootstrap;
  if (modal && bootstrap?.Modal) bootstrap.Modal.getOrCreateInstance(modal).hide();
}

function lerNotasDaPagina(root) {
  const dados = porId(root, 'dadosNotas');
  if (!dados) return [];
  try {
    const notas = JSON.parse(dados.textContent || '[]');
    return Array.isArray(notas) ? notas : [];
  } catch {
    return [];
  }
}

function preencherNotasValidacao(root) {
  const select = porId(root, 'nfseNotaValidacao');
  if (!select) return;
  const notas = lerNotasDaPagina(root);
  const placeholder = criarElemento('option', 'Escolha uma nota…');
  placeholder.value = '';
  placeholder.disabled = true;
  placeholder.selected = true;
  limpar(select);
  select.appendChild(placeholder);
  notas
    // O veredito vem do servidor (`nfse_service.emitivel`): a regra barra
    // proposta de agrupamento pendente e duplicata nao liberada, que uma lista
    // de status nao ve. Recriar a regra aqui ja tinha divergido.
    .filter((nota) => nota?.emitivel === true)
    .forEach((nota) => {
      const identificador = String(nota.id ?? '');
      const texto = [nota.nome_csv || nota.documento || 'Nota', nota.competencia]
        .filter(Boolean).join(' · ');
      const opcao = criarElemento('option', texto);
      opcao.value = identificador;
      select.appendChild(opcao);
    });
}

function incidenteDaChave(estado, chave) {
  if (!chave) return null;
  return (estado?.incidentes || []).find(
    (item) => item?.estado === 'aberto'
      && (item.campo?.chave_observada === chave || item.campo?.chave_esperada === chave),
  ) || null;
}

function incidentePorId(estado, id) {
  return (estado?.incidentes || []).find((item) => String(item.id) === String(id));
}

function recomendacaoDoIncidente(incidente) {
  return incidente?.recomendacao || null;
}



/**
 * Inicializa a central, carregando apenas o estado persistido no servidor.
 *
 * @param {{root?: Document|HTMLElement, fetchImpl?: Function, estadoUrl?: string, onEstado?: Function}} [opcoes]
 * @returns {Promise<{atualizar: Function, estado: Function}>}
 */
export async function inicializarContratoNfse(opcoes = {}) {
  const root = opcoes.root || document;
  const fetchImpl = opcoes.fetchImpl || globalThis.fetch.bind(globalThis);
  const estadoUrl = opcoes.estadoUrl || '/nfse/contrato';
  let estado = null;
  let candidataAtual = null;

  const formValidar = porId(root, 'formValidarContrato');
  const notaValidacao = porId(root, 'nfseNotaValidacao');
  const botaoRecon = porId(root, 'btnReconContrato');
  const botaoDescartar = porId(root, 'btnReconDescartar');
  const botaoConcluir = porId(root, 'btnReconConcluir');
  const botaoIncidentes = porId(root, 'btnDescartarIncidentes');
  const rotuloPasses = porId(root, 'nfseReconPasses');
  // Os handlers delegados moram no container da central, nao no documento:
  // ancorados no documento eles sobrevivem a cada nova inicializacao e passam
  // a responder com o `fetchImpl` de uma sessao antiga.
  const central = porId(root, 'nfseContratoCentral') || root;

  // A etapa de Pessoas revela campos conforme e preenchida: cada clique e um
  // passe, e o que a recon compara e a uniao dos passes desta mesma DPS.
  const mostrarPasses = (passe, controles) => {
    const acumulando = Number(passe) > 0;
    rotuloPasses?.classList.toggle('d-none', !acumulando);
    botaoDescartar?.classList.toggle('d-none', !acumulando);
    botaoConcluir?.classList.toggle('d-none', !acumulando);
    if (rotuloPasses && acumulando) {
      rotuloPasses.textContent = `passe ${passe} · ${controles} controles`;
    }
  };

  // A recon PROPOE; quem decide e o operador. Nada aqui altera contrato.
  const mostrarSugestoes = (sugestoes) => {
    const painel = porId(root, 'nfseReconSugestoes');
    if (!painel) return;
    limpar(painel);
    const itens = Array.isArray(sugestoes) ? sugestoes : [];
    painel.classList.toggle('d-none', itens.length === 0);
    if (!itens.length) return;
    [
      ['intocavel', 'O portal preenche sozinho — candidatos a “não tocar”'],
      ['preencher', 'Continuam vazios e o portal exige'],
    ].forEach(([chave, titulo]) => {
      const doGrupo = itens.filter((item) => item.sugestao === chave);
      if (!doGrupo.length) return;
      painel.appendChild(criarElemento('h4', titulo));
      const lista = criarElemento('ul');
      doGrupo.forEach((item) => {
        const linha = criarElemento('li');
        linha.appendChild(criarElemento('strong', item.rotulo || item.chave));
        const motivo = criarElemento('span', ` — ${item.motivo || ''}`);
        motivo.className = 'motivo';
        linha.appendChild(motivo);
        // A sugestao so vale se houver incidente aberto para configurar.
        const incidente = incidenteDaChave(estado, item.chave);
        if (incidente) {
          const ir = criarElemento(
            'button', chave === 'intocavel' ? 'Aplicar “não tocar”' : 'Ir ao campo',
          );
          ir.type = 'button';
          ir.className = 'btn btn-ghost btn-sm ms-2';
          ir.dataset.sugestaoIncidente = String(incidente.id ?? '');
          if (chave === 'intocavel') ir.dataset.sugestaoOrigem = 'intocavel';
          linha.appendChild(ir);
        } else {
          const sem = criarElemento('span', ' — sem incidente aberto para configurar');
          sem.className = 'motivo';
          linha.appendChild(sem);
        }
        lista.appendChild(linha);
      });
      painel.appendChild(lista);
    });
  };

  const carregar = async () => {
    try {
      const resposta = await fetchImpl(estadoUrl);
      const dados = await lerResposta(resposta);
      estado = dadosEstado(dados);
      renderizarEstadoContrato(estado, root);
      if (typeof opcoes.onEstado === 'function') opcoes.onEstado(estado);
      preencherNotasValidacao(root);
      mostrarErro(root, 'nfseReconEstado', '');
    } catch (erro) {
      estado = null;
      renderizarEstadoContrato(null, root);
      if (typeof opcoes.onEstado === 'function') opcoes.onEstado(null);
      const mensagem = erro instanceof Error ? erro.message : 'Não foi possível carregar o contrato.';
      mostrarErro(root, 'nfseReconEstado', mensagem);
      showToast(mensagem, 'error');
    }
  };

  // `final` e o operador dizendo "percorri a etapa inteira". So nesse passe a
  // ausencia de um campo vira incidente — e so um `controle_removido` abre o
  // fluxo de remapeamento.
  const executarRecon = (botao, final) => comCarregamento(botao, async () => {
    try {
      const dados = await chamar(fetchImpl, '/nfse/contrato/recon', { final });
      await carregar();
      mostrarPasses(dados.passe, dados.controles_acumulados);
      mostrarSugestoes(dados.sugestoes);
      const observacao = dados.observacao || {};
      // A evidencia carrega o motivo tecnico: sem ela, 'desconhecida' nao
      // diz ao operador o que conferir na tela.
      const evidencia = (observacao.evidencias || [])[0] || '';
      if (observacao.compatibilidade === 'desconhecida') {
        const faixa = porId(root, 'nfseContratoStatus');
        if (faixa) faixa.dataset.estado = 'desconhecido';
        const titulo = porId(root, 'nfseContratoStatusTitulo');
        const texto = porId(root, 'nfseContratoStatusTexto');
        if (titulo) titulo.textContent = rotulosEstado.desconhecido.titulo;
        if (texto) texto.textContent = evidencia || rotulosEstado.desconhecido.texto;
      }
      mostrarErro(
        root,
        'nfseReconEstado',
        observacao.compatibilidade === 'compativel'
          ? 'Recon concluída: a tela atual é compatível.'
          : `Recon concluída: ${observacao.compatibilidade || 'estado desconhecido'}.`
            + (evidencia ? ` ${evidencia}` : ''),
      );
    } catch (erro) {
      const mensagem = erro instanceof Error ? erro.message : 'Não foi possível executar a recon.';
      mostrarErro(root, 'nfseReconEstado', mensagem);
      showToast(mensagem, 'error');
    }
  });

  botaoRecon?.addEventListener('click', () => {
    void executarRecon(botaoRecon, false);
  });

  botaoConcluir?.addEventListener('click', () => {
    void executarRecon(botaoConcluir, true);
  });

  botaoDescartar?.addEventListener('click', () => {
    void comCarregamento(botaoDescartar, async () => {
      try {
        await chamar(fetchImpl, '/nfse/contrato/recon/descartar', {});
        mostrarPasses(0, 0);
        mostrarSugestoes([]);
        mostrarErro(root, 'nfseReconEstado', 'Passes acumulados descartados.');
      } catch (erro) {
        const mensagem = erro instanceof Error ? erro.message : 'Não foi possível descartar.';
        mostrarErro(root, 'nfseReconEstado', mensagem);
        showToast(mensagem, 'error');
      }
    });
  });

  botaoIncidentes?.addEventListener('click', () => {
    // Incidente persiste por upsert de assinatura e nada o expira: uma recon
    // defeituosa entulha a Central para sempre sem esta saida.
    if (!globalThis.confirm('Descartar todos os incidentes abertos do contrato ativo?')) return;
    void comCarregamento(botaoIncidentes, async () => {
      try {
        const dados = await chamar(fetchImpl, '/nfse/contrato/incidentes/descartar', {});
        await carregar();
        mostrarErro(root, 'nfseReconEstado', `${dados.descartados} incidente(s) descartado(s).`);
      } catch (erro) {
        const mensagem = erro instanceof Error ? erro.message : 'Não foi possível descartar.';
        mostrarErro(root, 'nfseReconEstado', mensagem);
        showToast(mensagem, 'error');
      }
    });
  });

  // A origem escolhida decide quais campos da linha aparecem; sem isto o
  // operador ve fonte e valor fixo ao mesmo tempo, e um deles sempre sobra.
  central.addEventListener('change', (evento) => {
    const campo = evento.target;
    if (campo?.dataset?.campo !== 'origem') return;
    const forma = campo.closest('.nfse-contrato-config');
    if (forma) aplicarOrigemNaLinha(forma, estado?.fontes || [], campo.value);
  });

  central.addEventListener('submit', (evento) => {
    const forma = evento.target?.closest?.('.nfse-contrato-config');
    if (!forma) return;
    evento.preventDefault();
    const incidenteId = forma.dataset.configIncidente;
    const incidente = incidentePorId(estado, incidenteId);
    const artigo = forma.closest('.nfse-contrato-incidente');
    const erro = artigo?.querySelector('.nfse-contrato-erro');
    const origemCampo = campoDaLinha(forma, 'origem');
    const fonteCampo = campoDaLinha(forma, 'fonte');
    const valorCampo = campoDaLinha(forma, 'valor_fixo');
    let payload;
    try {
      payload = montarDadosCandidato({
        origem: origemCampo?.value,
        fonte: fonteCampo?.value,
        valorFixo: valorCampo?.value,
        fontes: estado?.fontes || [],
        recomendacao: recomendacaoDoIncidente(incidente),
        chaveObservada: campoDaLinha(forma, 'chave_observada')?.value,
        confirmarRecomendacao:
          campoDaLinha(forma, 'confirmar_recomendacao')?.checked === true,
      });
    } catch (falha) {
      const mensagem = falha instanceof Error ? falha.message : 'Revise a configuração.';
      marcarInvalido(
        (origemCampo?.value === 'fixo' ? valorCampo : fonteCampo) || origemCampo, mensagem,
      );
      if (erro) erro.textContent = mensagem;
      return;
    }
    void comCarregamento(forma.querySelector('button[type="submit"]'), async () => {
      try {
        await chamar(fetchImpl, `/nfse/contrato/incidente/${incidenteId}/configurar`, payload);
        showToast('Configuração salva como candidata.', 'success');
        await carregar();
      } catch (falha) {
        const mensagem = falha instanceof Error ? falha.message : 'Não foi possível salvar.';
        if (erro) erro.textContent = mensagem;
        showToast(mensagem, 'error');
      }
    });
  });

  central.addEventListener('click', (evento) => {
    const alvo = evento.target?.closest?.(
      '[data-sugestao-incidente], [data-desfazer-candidata],'
      + ' [data-validar-contrato], [data-ativar-contrato]',
    );
    if (!alvo) return;
    if (alvo.dataset.sugestaoIncidente) {
      const artigo = central.querySelector(
        `.nfse-contrato-incidente[data-incidente-id="${alvo.dataset.sugestaoIncidente}"]`,
      );
      const origemCampo = artigo?.querySelector('[data-campo="origem"]');
      if (alvo.dataset.sugestaoOrigem && origemCampo) {
        // Pre-seleciona, nao salva: a decisao continua sendo um Salvar explicito.
        origemCampo.value = alvo.dataset.sugestaoOrigem;
        origemCampo.dispatchEvent(new Event('change', { bubbles: true }));
      }
      artigo?.scrollIntoView({ block: 'center' });
      origemCampo?.focus();
      return;
    }
    if (alvo.dataset.desfazerCandidata) {
      const botao = alvo;
      if (!globalThis.confirm(
        'Descartar a versão candidata? Os incidentes dela voltam a ficar abertos '
        + 'para reconfiguração.',
      )) return;
      void comCarregamento(botao, async () => {
        try {
          const dados = await chamar(
            fetchImpl, `/nfse/contrato/${botao.dataset.desfazerCandidata}/descartar`, {},
          );
          showToast(`${dados.reabertos} incidente(s) voltaram a ficar abertos.`, 'success');
          await carregar();
        } catch (falha) {
          const mensagem = falha instanceof Error ? falha.message : 'Não foi possível descartar.';
          showToast(mensagem, 'error');
        }
      });
    } else if (alvo.dataset.validarContrato) {
      candidataAtual = alvo.dataset.validarContrato;
      if (notaValidacao) notaValidacao.value = '';
      mostrarErro(root, 'nfseValidacaoErro', '');
    } else if (alvo.dataset.ativarContrato) {
      const botao = alvo;
      void comCarregamento(botao, async () => {
        try {
          await chamar(fetchImpl, `/nfse/contrato/${botao.dataset.ativarContrato}/ativar`, {});
          showToast('Versão do contrato ativada.', 'success');
          await carregar();
        } catch (erro) {
          const mensagem = erro instanceof Error ? erro.message : 'Não foi possível ativar a versão.';
          showToast(mensagem, 'error');
        }
      });
    }
  });

  formValidar?.addEventListener('submit', (evento) => {
    evento.preventDefault();
    const notaId = Number(notaValidacao?.value || 0);
    if (!candidataAtual || !notaId) {
      mostrarErro(root, 'nfseValidacaoErro', 'Escolha uma nota emitível para validar o contrato.');
      return;
    }
    const botao = porId(root, 'btnValidarContrato');
    void comCarregamento(botao, async () => {
      try {
        await chamar(fetchImpl, `/nfse/contrato/${candidataAtual}/validar`, { nota_id: notaId });
        esconderModal('modalValidarContrato');
        showToast('Validação iniciada na sessão assistida.', 'success');
        await carregar();
      } catch (erro) {
        const mensagem = erro instanceof Error ? erro.message : 'Não foi possível iniciar a validação.';
        mostrarErro(root, 'nfseValidacaoErro', mensagem);
        showToast(mensagem, 'error');
      }
    });
  });

  preencherNotasValidacao(root);
  await carregar();
  return { atualizar: carregar, estado: () => estado };
}
