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
const STATUS_EMITIVEIS = new Set([
  'pronta',
  'cadastro_pendente',
  'pessoa_fisica',
  'falha',
  'pulada',
]);

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
  const abertos = incidentesPendentes(estado);
  if (estado.ativo.elegivel_automatico === false
      || abertos.some((item) => item.estado === 'configurado'
        || ['critica', 'fiscal'].includes(item.severidade))) {
    return 'bloqueado';
  }
  return abertos.length ? 'aviso' : 'compativel';
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

function adicionarDado(lista, rotulo, valor) {
  const grupo = criarElemento('div');
  grupo.appendChild(criarElemento('dt', rotulo));
  grupo.appendChild(criarElemento('dd', valor));
  lista.appendChild(grupo);
}

function renderizarIncidentes(estado, root) {
  const lista = porId(root, 'nfseContratoIncidentes');
  if (!lista) return;
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

    const cabecalho = criarElemento('div');
    cabecalho.className = 'd-flex justify-content-between align-items-start gap-2 flex-wrap';
    const tituloGrupo = criarElemento('div');
    tituloGrupo.appendChild(criarElemento('h3', incidente.campo?.rotulo || 'Campo sem rótulo'));
    const etapa = criarElemento('div', `Etapa: ${incidente.etapa || 'desconhecida'}`);
    etapa.className = 'nfse-contrato-meta';
    tituloGrupo.appendChild(etapa);
    cabecalho.appendChild(tituloGrupo);

    const selo = criarElemento('span', incidente.tipo || 'diferença');
    selo.className = 'nfse-status';
    selo.dataset.status = incidente.severidade || 'informativa';
    cabecalho.appendChild(selo);
    artigo.appendChild(cabecalho);

    const dados = criarElemento('dl');
    dados.className = 'nfse-contrato-dados';
    adicionarDado(dados, 'Tipo', incidente.tipo || '—');
    adicionarDado(
      dados,
      'Obrigatoriedade',
      incidente.campo?.obrigatorio ? 'Obrigatório' : 'Opcional',
    );
    adicionarDado(dados, 'Observações', incidente.observacoes ?? 0);
    artigo.appendChild(dados);

    if (Array.isArray(incidente.opcoes) && incidente.opcoes.length) {
      const legenda = criarElemento('div', 'Opções apresentadas pelo portal');
      legenda.className = 'nfse-contrato-meta';
      artigo.appendChild(legenda);
      const opcoes = criarElemento('ul');
      opcoes.className = 'nfse-contrato-opcoes';
      incidente.opcoes.forEach((opcao) => {
        const item = criarElemento('li');
        item.appendChild(document.createTextNode(`${opcao.rotulo || ''} `));
        item.appendChild(criarElemento('code', opcao.valor || ''));
        opcoes.appendChild(item);
      });
      artigo.appendChild(opcoes);
    }

    if (incidente.recomendacao) {
      const recomendacao = criarElemento('div');
      recomendacao.className = 'nfse-contrato-recomendacao';
      const tituloRecomendacao = incidente.recomendacao.ambigua
        ? 'Recomendação ambígua'
        : `Remapeamento sugerido · confiança ${incidente.recomendacao.confianca || '—'}`;
      recomendacao.appendChild(criarElemento('strong', tituloRecomendacao));
      const evidencias = criarElemento(
        'p',
        (incidente.recomendacao.evidencias || []).join('; '),
      );
      evidencias.className = 'nfse-hint mb-0';
      recomendacao.appendChild(evidencias);
      artigo.appendChild(recomendacao);
    }

    const acoes = criarElemento('div');
    acoes.className = 'd-flex justify-content-end';
    if (incidente.estado === 'aberto') {
      const configurar = criarElemento('button', 'Configurar alteração');
      configurar.type = 'button';
      configurar.className = 'btn btn-primary btn-sm';
      configurar.dataset.configurarIncidente = String(incidente.id ?? '');
      configurar.setAttribute('data-bs-toggle', 'modal');
      configurar.setAttribute('data-bs-target', '#modalConfigContrato');
      acoes.appendChild(configurar);
    } else {
      acoes.appendChild(criarElemento('span', 'Incluído na versão candidata.'));
    }
    artigo.appendChild(acoes);
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
    acao.appendChild(botao);
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

function limparErrosConfiguracao(root) {
  mostrarErro(root, 'nfseContratoErro', '');
  mostrarErro(root, 'nfseValidacaoErro', '');
  ['nfseContratoOrigem', 'nfseContratoFonte', 'nfseContratoValorFixo']
    .forEach((id) => limparInvalido(porId(root, id)));
}

function exibirGrupo(elemento, visivel) {
  if (!elemento) return;
  elemento.hidden = !visivel;
  elemento.classList.toggle('d-none', !visivel);
  elemento.setAttribute('aria-hidden', String(!visivel));
}

function atualizarCamposOrigem(root, fontes, origem) {
  limparErrosConfiguracao(root);
  const fonteGrupo = porId(root, 'nfseContratoFonteGrupo');
  const valorGrupo = porId(root, 'nfseContratoValorGrupo');
  const fonteSelect = porId(root, 'nfseContratoFonte');
  const valorInput = porId(root, 'nfseContratoValorFixo');
  const opcoes = opcoesDaOrigem(fontes, origem);
  const exigeFonte = Boolean(origem) && !ORIGENS_SEM_FONTE.has(origem);
  exibirGrupo(fonteGrupo, exigeFonte);
  exibirGrupo(valorGrupo, origem === 'fixo');

  if (fonteSelect) {
    limpar(fonteSelect);
    const placeholder = criarElemento('option', 'Escolha uma fonte…');
    placeholder.value = '';
    placeholder.selected = true;
    fonteSelect.appendChild(placeholder);
    opcoes.forEach((opcao) => {
      const item = criarElemento('option', opcao.rotulo || opcao.fonte || 'Fonte');
      item.value = opcao.fonte || '';
      fonteSelect.appendChild(item);
    });
  }
  if (valorInput && origem !== 'fixo') valorInput.value = '';
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
    .filter((nota) => STATUS_EMITIVEIS.has(nota?.status))
    .forEach((nota) => {
      const identificador = String(nota.id ?? '');
      const texto = [nota.nome_csv || nota.documento || 'Nota', nota.competencia]
        .filter(Boolean).join(' · ');
      const opcao = criarElemento('option', texto);
      opcao.value = identificador;
      select.appendChild(opcao);
    });
}

function incidentePorId(estado, id) {
  return (estado?.incidentes || []).find((item) => String(item.id) === String(id));
}

function recomendacaoDoIncidente(incidente) {
  return incidente?.recomendacao || null;
}


function prepararRecomendacao(root, recomendacao, estado) {
  const grupo = porId(root, 'nfseContratoRecomendacaoGrupo');
  const escolhaGrupo = porId(root, 'nfseContratoEscolhaGrupo');
  const escolha = porId(root, 'nfseContratoChaveObservada');
  const evidencia = porId(root, 'nfseContratoRecomendacaoEvidencias');
  exibirGrupo(grupo, Boolean(recomendacao));
  exibirGrupo(escolhaGrupo, Boolean(recomendacao?.ambigua));
  if (evidencia) {
    evidencia.textContent = recomendacao
      ? (recomendacao.evidencias || []).join('; ')
      : '';
  }
  if (escolha) {
    limpar(escolha);
    const placeholder = criarElemento('option', 'Escolha o controle…');
    placeholder.value = '';
    placeholder.selected = true;
    escolha.appendChild(placeholder);
    (recomendacao?.candidatos || []).forEach((chave, indice) => {
      const correspondente = (estado?.incidentes || []).find(
        (item) => item?.campo?.chave_observada === chave,
      );
      const rotulo = correspondente?.campo?.rotulo
        || `Controle observado ${indice + 1}`;
      const opcao = criarElemento('option', rotulo);
      opcao.value = chave;
      if (!recomendacao.ambigua && chave === recomendacao.chave_observada) {
        opcao.selected = true;
      }
      escolha.appendChild(opcao);
    });
  }
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
  let incidenteAtual = null;
  let candidataAtual = null;

  const origem = porId(root, 'nfseContratoOrigem');
  const formConfig = porId(root, 'formConfigContrato');
  const formValidar = porId(root, 'formValidarContrato');
  const notaValidacao = porId(root, 'nfseNotaValidacao');
  const botaoRecon = porId(root, 'btnReconContrato');

  const carregar = async () => {
    try {
      const resposta = await fetchImpl(estadoUrl);
      const dados = await lerResposta(resposta);
      estado = dadosEstado(dados);
      renderizarEstadoContrato(estado, root);
      if (typeof opcoes.onEstado === 'function') opcoes.onEstado(estado);
      preencherNotasValidacao(root);
      atualizarCamposOrigem(root, estado?.fontes || [], origem?.value || '');
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

  origem?.addEventListener('change', () => {
    atualizarCamposOrigem(root, estado?.fontes || [], origem.value);
  });

  botaoRecon?.addEventListener('click', () => {
    void comCarregamento(botaoRecon, async () => {
      try {
        const dados = await chamar(fetchImpl, '/nfse/contrato/recon', {});
        await carregar();
        const observacao = dados.observacao || {};
        if (observacao.compatibilidade === 'desconhecida') {
          const faixa = porId(root, 'nfseContratoStatus');
          if (faixa) faixa.dataset.estado = 'desconhecido';
          const titulo = porId(root, 'nfseContratoStatusTitulo');
          const texto = porId(root, 'nfseContratoStatusTexto');
          if (titulo) titulo.textContent = rotulosEstado.desconhecido.titulo;
          if (texto) texto.textContent = rotulosEstado.desconhecido.texto;
        }
        mostrarErro(
          root,
          'nfseReconEstado',
          observacao.compatibilidade === 'compativel'
            ? 'Recon concluída: a tela atual é compatível.'
            : `Recon concluída: ${observacao.compatibilidade || 'estado desconhecido'}.`,
        );
      } catch (erro) {
        const mensagem = erro instanceof Error ? erro.message : 'Não foi possível executar a recon.';
        mostrarErro(root, 'nfseReconEstado', mensagem);
        showToast(mensagem, 'error');
      }
    });
  });

  root.addEventListener('click', (evento) => {
    const alvo = evento.target?.closest?.('[data-configurar-incidente], [data-validar-contrato], [data-ativar-contrato]');
    if (!alvo) return;
    if (alvo.dataset.configurarIncidente) {
      incidenteAtual = incidentePorId(estado, alvo.dataset.configurarIncidente);
      const id = porId(root, 'nfseContratoIncidenteId');
      if (id) id.value = alvo.dataset.configurarIncidente;
      const campo = porId(root, 'nfseContratoCampoSelecionado');
      if (campo) campo.textContent = incidenteAtual?.campo?.rotulo || 'Campo selecionado';
      if (origem) origem.value = '';
      atualizarCamposOrigem(root, estado?.fontes || [], '');
      const recomendacao = recomendacaoDoIncidente(incidenteAtual);
      prepararRecomendacao(root, recomendacao, estado);
      const checkbox = porId(root, 'nfseContratoConfirmarRecomendacao');
      if (checkbox) checkbox.checked = false;
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

  formConfig?.addEventListener('submit', (evento) => {
    evento.preventDefault();
    const botao = porId(root, 'btnSalvarConfigContrato');
    const recomendacao = recomendacaoDoIncidente(incidenteAtual);
    let payload;
    try {
      payload = montarDadosCandidato({
        origem: origem?.value,
        fonte: porId(root, 'nfseContratoFonte')?.value,
        valorFixo: porId(root, 'nfseContratoValorFixo')?.value,
        fontes: estado?.fontes || [],
        recomendacao,
        chaveObservada: porId(root, 'nfseContratoChaveObservada')?.value,
        confirmarRecomendacao: porId(root, 'nfseContratoConfirmarRecomendacao')?.checked === true,
      });
    } catch (erro) {
      const mensagem = erro instanceof Error ? erro.message : 'Revise a configuração.';
      const campo = origem?.value === 'fixo'
        ? porId(root, 'nfseContratoValorFixo')
        : porId(root, 'nfseContratoFonte');
      marcarInvalido(campo || origem, mensagem);
      mostrarErro(root, 'nfseContratoErro', mensagem);
      return;
    }
    void comCarregamento(botao, async () => {
      try {
        await chamar(
          fetchImpl,
          `/nfse/contrato/incidente/${porId(root, 'nfseContratoIncidenteId')?.value}/configurar`,
          payload,
        );
        esconderModal('modalConfigContrato');
        showToast('Configuração salva como candidata.', 'success');
        await carregar();
      } catch (erro) {
        const mensagem = erro instanceof Error ? erro.message : 'Não foi possível salvar a configuração.';
        mostrarErro(root, 'nfseContratoErro', mensagem);
        showToast(mensagem, 'error');
      }
    });
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
