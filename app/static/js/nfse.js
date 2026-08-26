// Painel de emissao de NFSe (NFSE-17).
//
// Sem framework, no padrão do certidoes.js (AD-015): módulo ES nativo,
// versionado por static_versionado. O CSRF vai no wrapper global de fetch
// definido no base.html.

import { showToast } from './toasts.js';
import {
  inicializarContratoNfse,
} from './nfse_contrato.js';

/**
 * Resposta JSON das rotas da NFSe. Os campos adicionais variam conforme a
 * ação; os campos abaixo são os contratos comuns usados pela tela.
 *
 * @typedef {Object} RespostaNfse
 * @property {string=} status
 * @property {string=} message
 * @property {string=} motivo
 * @property {string=} request_id
 */

const ROTULO_STATUS = {
  pronta: 'Pronta',
  preenchendo: 'Preenchendo…',
  aguardando_confirmacao: 'Aguardando você emitir',
  emitida: 'Emitida',
  empresa_pendente: 'Sem empresa',
  cadastro_pendente: 'Ainda não cadastrada',
  pessoa_fisica: 'Pessoa física',
  duplicata: 'Possível duplicata',
  invalida: 'Linha inválida',
  pulada: 'Pulada',
  falha: 'Falha',
  descricao_pendente: 'Sem descrição',
  cancelada: 'Cancelada',
  agrupada: 'Agrupada em outra',
};

const ROTULO_CATEGORIA = {
  honorarios: 'Honorários',
  servico: 'Serviço',
  indefinida: 'A definir',
};

const lerJson = (id) => {
  const el = document.getElementById(id);
  try { return JSON.parse(el?.textContent || '[]'); } catch { return []; }
};

// Para payloads que sao objeto (ou null), nao lista.
const lerJsonObjeto = (id) => {
  const el = document.getElementById(id);
  try { return JSON.parse(el?.textContent || 'null'); } catch { return null; }
};

let notas = lerJson('dadosNotas');
const empresas = lerJson('dadosEmpresas');
let aliquotaConfirmada = false;
// linhas cujo vinculo o operador reabriu para corrigir
const editando = new Set();
// linhas cuja descricao o operador reabriu para corrigir (eixo independente do
// vinculo: uma nota pode ter empresa certa e descricao a definir)
const editandoDescricao = new Set();
// linhas marcadas para uma acao em massa
const selecionadas = new Set();

// `textContent` -> `innerHTML` escapa &, < e >, mas NAO aspas: como o resultado
// tambem entra em atributos (title="${esc(...)}"), faltavam justamente as duas
// que quebram o atributo. As mensagens da automacao citam o campo entre aspas
// (`Campo "DataCompetencia" nao existe`), entao isso acontece de verdade.
const esc = (texto) => {
  const div = document.createElement('div');
  div.textContent = texto == null ? '' : String(texto);
  return div.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
};

/**
 * Executa uma ação JSON da NFSe e lança erro com o envelope devolvido pelo
 * servidor quando a resposta não é bem-sucedida.
 *
 * @param {string} url
 * @param {RequestInit=} opcoes
 * @returns {Promise<RespostaNfse>}
 */
async function chamar(url, opcoes = {}) {
  const resposta = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    ...opcoes,
  });
  const dados = await resposta.json().catch(() => ({}));
  if (!resposta.ok) {
    const erro = new Error(dados.message || `Falha na requisição (${resposta.status}).`);
    // o corpo carrega `motivo`, que distingue um aviso confirmável de um erro seco
    erro.dados = dados;
    throw erro;
  }
  return dados;
}

// --- tabela ---------------------------------------------------------------

function opcoesEmpresa(selecionada) {
  const itens = empresas
    .map((e) => `<option value="${e.id}"${e.id === selecionada ? ' selected' : ''}>${esc(e.nome)}</option>`)
    .join('');
  return `<option value="">Escolher empresa…</option>${itens}`;
}

function editorVinculo(nota) {
  // Os dois campos sao mutuamente exclusivos: preencher um limpa o outro (ver
  // trocaEmpresa/trocaDocumento). Antes, com os dois preenchidos, o vinculo
  // saia pela empresa e ignorava o documento digitado — em silencio.
  const cancelar = nota.documento || nota.empresa
    ? `<button class="btn btn-ghost btn-sm" data-cancelar="${nota.id}">Cancelar</button>` : '';
  return `
    <div class="d-flex gap-1 flex-wrap align-items-center">
      <select class="form-select form-select-sm" data-empresa-de="${nota.id}" style="min-width: 12rem;">
        ${opcoesEmpresa(nota.empresa_id)}
      </select>
      <input class="form-control form-control-sm" data-doc-de="${nota.id}"
             placeholder="ou CNPJ/CPF" style="max-width: 11rem;"
             value="${esc(nota.empresa_id ? '' : (nota.documento || ''))}">
      <button class="btn btn-soft-primary btn-sm" data-resolver="${nota.id}">Vincular</button>
      ${cancelar}
    </div>`;
}

function celulaEmpresa(nota) {
  const resolvido = nota.empresa || nota.documento;
  if (!resolvido || editando.has(nota.id)) return editorVinculo(nota);

  const origem = nota.origem_vinculo === 'fuzzy' && nota.score_match
    ? ` <span class="nfse-hint">(aproximado, ${nota.score_match})</span>` : '';
  const rotulo = nota.empresa
    ? `${esc(nota.empresa)}${origem}`
    : `<span class="nfse-hint">sem cadastro</span>`;
  const podeEditar = nota.status !== 'emitida';
  const editar = podeEditar
    ? ` <button class="btn btn-ghost btn-sm py-0 px-1" data-editar="${nota.id}"
               title="Trocar empresa ou documento">Editar</button>` : '';
  return `${rotulo}${editar}`;
}

function acoesDaLinha(nota) {
  const partes = [];

  // Linha absorvida por um agrupamento: nao ha o que fazer com ela, e a celula
  // vazia pareceria quebrada — diz para onde ela foi.
  if (nota.status === 'agrupada') {
    return '<span class="nfse-hint">virou parte de outra nota</span>';
  }

  if (nota.status === 'cancelada') {
    return `<button class="btn btn-ghost btn-sm" data-restaurar="${nota.id}"
             title="Voltar para a lista">Restaurar</button>`;
  }

  if (nota.status === 'descricao_pendente') {
    partes.push(`<button class="btn btn-primary btn-sm" data-editar-descricao="${nota.id}">Definir descrição</button>`);
    partes.push(botaoCancelar(nota));
    return partes.join('');
  }

  if (nota.status === 'emitida') {
    // So o que o operador marcou na mao pode ser desmarcado: o que a automacao
    // emitiu ela VIU acontecer na tela de confirmacao do portal, e desfazer por
    // um clique afirmaria que uma nota fiscal existente nao existe.
    // A celula nao pode ficar vazia, ou a linha parece quebrada — diz o porque.
    if (nota.origem_emissao === 'manual') {
      partes.push(`<button class="btn btn-ghost btn-sm" data-desmarcar="${nota.id}">Desmarcar</button>`);
    } else {
      partes.push('<span class="nfse-hint">emitida pelo sistema</span>');
    }
    return partes.join('');
  }

  if (nota.status === 'duplicata' && !nota.duplicata_liberada) {
    partes.push(`<button class="btn btn-ghost btn-sm" data-liberar="${nota.id}">Emitir mesmo assim</button>`);
  }

  // Cadastrar so faz sentido para CNPJ: pessoa fisica nunca vira cadastro de
  // empresa, e o convite ficaria pendurado para sempre.
  if (nota.status === 'cadastro_pendente' && nota.tipo_documento === 'cnpj') {
    const url = `/empresa/nova?nome=${encodeURIComponent(nota.nome_csv || '')}&cnpj=${encodeURIComponent(nota.documento)}`;
    partes.push(`<a class="btn btn-ghost btn-sm" href="${url}">Cadastrar</a>`);
  }

  const podePreencher = ['pronta', 'cadastro_pendente', 'pessoa_fisica', 'falha', 'pulada']
    .includes(nota.status) || (nota.status === 'duplicata' && nota.duplicata_liberada);
  if (podePreencher) {
    partes.push(botaoCancelar(nota));
    partes.push(`<button class="btn btn-ghost btn-sm" data-jaemitida="${nota.id}" title="Já emitida no portal, por fora">Já emiti</button>`);
    partes.push(`<button class="btn btn-primary btn-sm" data-preencher="${nota.id}">Preencher</button>`);
  }
  if (nota.status === 'aguardando_confirmacao') {
    partes.push(`<button class="btn btn-primary btn-sm" data-jaemitida="${nota.id}">Emiti no portal</button>`);
  }
  if (!partes.length && nota.status === 'empresa_pendente') {
    partes.push(botaoCancelar(nota));
  }
  return partes.join('');
}

function botaoCancelar(nota) {
  // Ghost e nao `btn-danger`: cancelar aqui nao destroi nada nem cancela nota
  // na prefeitura — so tira a linha da lista, e e reversivel pelo "Restaurar".
  return `<button class="btn btn-ghost btn-sm" data-cancelar-nota="${nota.id}"
           title="Tirar da lista">Cancelar</button>`;
}

function celulaDescricao(nota) {
  // Editor aberto: o operador pediu para dizer o que a nota descreve.
  if (editandoDescricao.has(nota.id)) return editorDescricao(nota);

  const rotulo = ROTULO_CATEGORIA[nota.categoria] || nota.categoria || '—';
  const chip = `<span class="nfse-categoria cat-${nota.categoria}">${esc(rotulo)}</span>`;

  if (nota.categoria === 'indefinida') {
    // Sem descricao nao ha nota: o texto cru do Pix e o unico dado que ajuda o
    // operador a decidir, entao ele fica visivel em vez de escondido num title.
    return `${chip}
      <div class="nfse-descricao-prevista">${esc(nota.descricao_extrato || '')}</div>`;
  }

  const editar = nota.status === 'emitida' || nota.status === 'aguardando_confirmacao'
    ? ''
    : ` <button class="btn btn-ghost btn-sm py-0 px-1" data-editar-descricao="${nota.id}"
               title="Mudar a descrição">Editar</button>`;
  const prevista = nota.descricao_prevista
    ? `<div class="nfse-descricao-prevista" title="${esc(nota.descricao_prevista)}">${esc(nota.descricao_prevista)}</div>`
    : '';
  return `${chip}${editar}${prevista}`;
}

function editorDescricao(nota) {
  // Os dois campos sao INDEPENDENTES, nao alternativas: o serviço e o texto da
  // nota, a competencia e o mes. Deixar em branco tem significado em cada um —
  // serviço vazio = honorarios; competencia vazia = mantem o mes atual.
  return `
    <div class="nfse-editor-descricao">
      <input class="form-control form-control-sm" data-servico-de="${nota.id}"
             placeholder="Serviço (ex.: BAIXA DE EMPRESA)"
             value="${esc(nota.descricao_servico || '')}">
      <input class="form-control form-control-sm nfse-editor-competencia"
             data-competencia-de="${nota.id}" placeholder="Competência MM/AAAA"
             value="${esc(nota.competencia || '')}">
      <div class="nfse-editor-acoes">
        <button class="btn btn-ghost btn-sm" data-cancelar-descricao="${nota.id}">Cancelar</button>
        <button class="btn btn-soft-primary btn-sm" data-salvar-descricao="${nota.id}">Salvar</button>
      </div>
      <div class="nfse-hint nfse-editor-ajuda">
        Serviço em branco = honorários do mês.
      </div>
    </div>`;
}

function faixaDoGrupo(nota, colunas) {
  // So a lider carrega a conta; as irmas ficam sob a mesma faixa.
  if (!nota.grupo || !nota.grupo.lider) return '';
  const grupo = nota.grupo;
  const token = esc(grupo.token);

  // Ja aplicado: a faixa vira o recibo do que foi feito, com o desfazer.
  if (grupo.confirmado) {
    return `
    <tr class="nfse-grupo-linha">
      <td colspan="${colunas}">
        <div class="nfse-grupo">
          <span class="nfse-grupo-texto">
            <span class="nfse-grupo-selo">Agrupada</span>
            As linhas abaixo viraram uma nota só.
            <span class="nfse-grupo-conta">${esc(grupo.detalhe || '')}</span>
          </span>
          <span class="nfse-grupo-acoes">
            <button class="btn btn-soft-primary btn-sm" data-desfazer-grupo="${token}">
              Desfazer agrupamento
            </button>
          </span>
        </div>
      </td>
    </tr>`;
  }

  return `
    <tr class="nfse-grupo-linha">
      <td colspan="${colunas}">
        <div class="nfse-grupo">
          <span class="nfse-grupo-texto">
            <span class="nfse-grupo-selo">A agrupar</span>
            <strong>Juntar numa nota só?</strong> Vários lançamentos deste
            tomador no período.
            <span class="nfse-grupo-conta">${esc(grupo.detalhe || '')}</span>
          </span>
          <span class="nfse-grupo-acoes">
            <input class="form-control form-control-sm nfse-grupo-descricao"
                   data-descricao-grupo="${token}"
                   value="${esc(grupo.descricao || '')}"
                   placeholder="Descrição da nota"
                   aria-label="Descrição da nota agrupada">
            <input class="form-control form-control-sm nfse-grupo-valor"
                   data-valor-grupo="${token}"
                   value="${esc(grupo.valor_liquido || '')}"
                   aria-label="Valor da nota agrupada">
            <button class="btn btn-ghost btn-sm" data-descartar-grupo="${token}">
              Manter separadas
            </button>
            <button class="btn btn-primary btn-sm" data-confirmar-grupo="${token}">
              Juntar
            </button>
          </span>
        </div>
      </td>
    </tr>`;
}

function linha(nota, ultimaDoGrupo) {
  // as duas origens ficam esmaecidas: uma linha ja resolvida nao disputa
  // atencao com as que ainda faltam
  const alerta = (nota.divergencia_valor ? ' nfse-linha-alerta' : '')
    + (['emitida', 'cancelada', 'agrupada'].includes(nota.status) ? ' nfse-linha-resolvida' : '')
    + (nota.grupo ? ' nfse-linha-grupo' : '')
    + (ultimaDoGrupo ? ' nfse-linha-grupo-fim' : '');
  const aviso = nota.divergencia_valor
    ? ' <i class="bi bi-exclamation-triangle" title="Soma das parcelas não bate com o valor final"></i>' : '';
  const ajustado = nota.valor_ajustado
    ? ' <i class="bi bi-pencil" title="Valor ajustado à mão ao agrupar"></i>' : '';
  const marcada = selecionadas.has(nota.id) ? ' checked' : '';
  return `
    <tr class="${alerta}" data-linha="${nota.id}">
      <td><input type="checkbox" class="nfse-check" data-selecionar="${nota.id}"${marcada}
                 aria-label="Selecionar ${esc(nota.nome_csv || 'linha')}"></td>
      <td>${esc(nota.nome_csv)}</td>
      <td>${celulaEmpresa(nota)}</td>
      <td class="nfse-mono">${esc(nota.documento || '—')}</td>
      <td>${celulaDescricao(nota)}</td>
      <td class="nfse-mono">${esc(nota.competencia || '—')}</td>
      <td class="nfse-mono text-end">${esc(nota.valor || '—')}${aviso}${ajustado}</td>
      <td><span class="nfse-status st-${nota.status}">${esc(ROTULO_STATUS[nota.status] || nota.status)}</span>
          ${nota.erro ? `<div class="nfse-hint nfse-erro" title="${esc(nota.erro)}">${esc(nota.erro)}</div>` : ''}</td>
      <td><div class="nfse-acoes-linha">${acoesDaLinha(nota)}</div></td>
    </tr>`;
}

function renderizar() {
  const corpo = document.getElementById('corpoNotas');
  const vazio = document.getElementById('nfseVazio');
  if (!corpo) return;
  const colunas = document.querySelectorAll('#tabelaNotas thead th').length;
  corpo.innerHTML = notas.map((nota, i) => {
    // A ultima linha de um grupo fecha o bloco com a borda inferior; sem isso
    // o fundo do grupo vaza para a linha seguinte e o operador nao ve onde ele
    // termina. "Ultima" = a proxima nota nao esta no mesmo grupo.
    const proxima = notas[i + 1];
    const ultimaDoGrupo = !!nota.grupo
      && (!proxima || !proxima.grupo || proxima.grupo.token !== nota.grupo.token);
    return faixaDoGrupo(nota, colunas) + linha(nota, ultimaDoGrupo);
  }).join('');
  if (vazio) vazio.classList.toggle('d-none', notas.length > 0);
  atualizarContadores();
  pintarSelecao();
}

function atualizarContadores() {
  const conta = {};
  notas.forEach((n) => { conta[n.status] = (conta[n.status] || 0) + 1; });
  const valores = {
    total: notas.length,
    divergencias: notas.filter((n) => n.divergencia_valor).length,
    // conta PROPOSTAS EM ABERTO, nao notas: as linhas de um grupo sao uma
    // decisao so, e o grupo ja confirmado nao e mais uma pendencia
    grupos_pendentes: new Set(
      notas.filter((n) => n.grupo && n.grupo.pendente).map((n) => n.grupo.token)).size,
    ...conta,
  };
  document.querySelectorAll('[data-contador]').forEach((el) => {
    el.textContent = valores[el.dataset.contador] || 0;
  });
}

function substituir(nota) {
  const i = notas.findIndex((n) => n.id === nota.id);
  if (i >= 0) notas[i] = nota; else notas.push(nota);
  renderizar();
}

// --- sessao do navegador --------------------------------------------------

function pintarSessao({ aliquota, aliquota_confirmada: confirmada, ativa }) {
  const alvo = document.getElementById('nfseAliquota');
  const btnConfirmar = document.getElementById('btnConfirmarAliquota');
  const estado = document.getElementById('nfseSessaoEstado');
  aliquotaConfirmada = !!confirmada;

  if (alvo) alvo.textContent = aliquota || '—';
  if (btnConfirmar) btnConfirmar.classList.toggle('d-none', !aliquota || aliquotaConfirmada);

  if (estado) {
    if (aliquotaConfirmada) {
      estado.textContent = 'Alíquota confirmada.';
    } else if (aliquota) {
      estado.textContent = 'Confira a alíquota antes de preencher.';
    } else if (ativa) {
      estado.textContent = 'Não consegui ler a alíquota. Confira no navegador e confirme.';
    } else {
      estado.textContent = '';
    }
  }
}

async function prepararSessao(botao) {
  botao.disabled = true;
  botao.textContent = 'Abrindo…';
  try {
    const dados = await chamar('/nfse/sessao/preparar');
    pintarSessao({ ...dados, ativa: true });
    showToast(dados.aliquota
      ? `Portal aberto. Alíquota lida: ${dados.aliquota}`
      : 'Portal aberto, mas não consegui ler a alíquota.', 'info');
  } catch (erro) {
    showToast(erro.message, 'error');
  } finally {
    botao.disabled = false;
    botao.textContent = 'Abrir portal';
  }
}

// --- acoes por linha ------------------------------------------------------

async function resolverEmpresa(id) {
  const select = document.querySelector(`[data-empresa-de="${id}"]`);
  const campo = document.querySelector(`[data-doc-de="${id}"]`);
  const empresaId = select?.value ? Number(select.value) : null;
  const documento = campo?.value.trim() || '';

  if (empresaId && documento) {
    showToast('Escolha uma empresa ou informe um CPF/CNPJ, não os dois.', 'error');
    return;
  }
  const corpo = {};
  if (empresaId) corpo.empresa_id = empresaId;
  else if (documento) corpo.documento = documento;
  else { showToast('Escolha uma empresa ou informe o CNPJ/CPF.', 'error'); return; }

  try {
    const dados = await chamar(`/nfse/nota/${id}/resolver`, { body: JSON.stringify(corpo) });
    editando.delete(id);
    substituir(dados.nota);
    showToast('Vinculada.', 'success');
  } catch (erro) {
    showToast(erro.message, 'error');
  }
}

async function liberarDuplicata(id) {
  try {
    const dados = await chamar(`/nfse/nota/${id}/liberar-duplicata`);
    substituir(dados.nota);
    showToast('Duplicata liberada para emissão.', 'info');
  } catch (erro) {
    showToast(erro.message, 'error');
  }
}

async function marcarEmitidaManual(id, marcar) {
  try {
    const dados = await chamar(`/nfse/nota/${id}/emitida-manual`,
      { body: JSON.stringify({ marcar }) });
    substituir(dados.nota);
    showToast(marcar ? 'Marcada como emitida.' : 'Marcação desfeita.', 'info');
  } catch (erro) {
    showToast(erro.message, 'error');
  }
}

async function cancelarNota(id, cancelar) {
  try {
    const dados = await chamar(`/nfse/nota/${id}/cancelar`,
      { body: JSON.stringify({ cancelar }) });
    substituir(dados.nota);
    showToast(cancelar ? 'Linha cancelada.' : 'Linha restaurada.', 'info');
  } catch (erro) {
    showToast(erro.message, 'error');
  }
}

// --- descricao da nota ----------------------------------------------------

async function resolverDescricao(id) {
  const servico = document.querySelector(`[data-servico-de="${id}"]`)?.value.trim() || '';
  const competencia = document.querySelector(`[data-competencia-de="${id}"]`)?.value.trim() || '';
  if (!servico && !competencia) {
    showToast('Informe o serviço ou a competência dos honorários.', 'error');
    return;
  }
  try {
    const dados = await chamar(`/nfse/nota/${id}/descricao`,
      { body: JSON.stringify({ descricao_servico: servico, competencia }) });
    editandoDescricao.delete(id);
    substituir(dados.nota);
    showToast(servico ? 'Serviço definido.' : 'Competência definida.', 'success');
  } catch (erro) {
    showToast(erro.message, 'error');
  }
}

// --- proposta de agrupamento ----------------------------------------------

async function confirmarGrupo(token) {
  const campo = document.querySelector(`[data-valor-grupo="${token}"]`);
  const valor = campo?.value.trim() || null;
  const descricao = document.querySelector(`[data-descricao-grupo="${token}"]`)?.value.trim() || null;
  try {
    const dados = await chamar(`/nfse/grupo/${encodeURIComponent(token)}/confirmar`,
      { body: JSON.stringify({ valor, descricao }) });
    // O agrupamento muda o status das irmas tambem; recarrega a lista inteira
    // em vez de remendar linha a linha.
    await recarregarNotas({ forcar: true });
    showToast(`Lançamentos juntados numa nota de ${dados.nota.valor}.`, 'success');
  } catch (erro) {
    showToast(erro.message, 'error');
  }
}

async function descartarGrupo(token) {
  try {
    await chamar(`/nfse/grupo/${encodeURIComponent(token)}/descartar`);
    await recarregarNotas({ forcar: true });
    showToast('Cada lançamento segue como nota própria.', 'info');
  } catch (erro) {
    showToast(erro.message, 'error');
  }
}

async function desfazerGrupo(token) {
  try {
    await chamar(`/nfse/grupo/${encodeURIComponent(token)}/desfazer`);
    await recarregarNotas({ forcar: true });
    showToast('Agrupamento desfeito.', 'info');
  } catch (erro) {
    showToast(erro.message, 'error');
  }
}

// --- conferencia com o portal (notas emitidas) ----------------------------

function pintarEmitidas(painel) {
  const alvo = document.getElementById('emitidasPainel');
  if (!alvo) return;
  if (!painel) {
    alvo.innerHTML = '<p class="nfse-hint mb-0">Escolha o período e consulte o portal.</p>';
    return;
  }

  // Nunca consultado não é o mesmo que consultado e sem resultado: mostrar as
  // divergências aqui acusaria "pagou e ficou sem nota" para o mês inteiro só
  // porque ninguém leu o portal ainda.
  alvo.dataset.mes = painel.mes_geracao || '';
  sincronizarCompetencia(painel.competencia);
  if (painel.nunca_consultado) {
    alvo.innerHTML = `<p class="nfse-hint mb-0">Nada lido do portal para `
      + `${esc(painel.mes_geracao)} ainda.</p>`;
    return;
  }

  const quando = painel.consultado_em
    ? ` <span class="nfse-hint">· lido do portal em ${esc(painel.consultado_em)}</span>` : '';
  const outras = Object.entries(painel.outras_situacoes || {});
  // Situacao desconhecida NAO entra no total e NAO some: os codigos de
  // cancelada/substituida nunca foram observados, e tanto somar quanto
  // descartar por conta propria erraria um total fiscal.
  const aviso = outras.length
    ? `<div class="nfse-hint mt-1">Fora do total, por não estarem como emitidas: `
      + outras.map(([s, n]) => `${n} em ${esc(s)}`).join(' · ') + '</div>'
    : '';

  const blocos = [
    `<div class="nfse-total">
       <span class="valor">R$ ${esc(painel.total || '0,00')}</span>
       <span class="rotulo">emitido em ${esc(painel.mes_geracao)}</span>
       <span class="nfse-hint">${painel.quantidade} nota(s)</span>${quando}
     </div>${aviso}`,
  ];

  // As divergencias sao de OUTRO mes (o de referencia). Dizer qual, senao o
  // operador le tudo como se fosse do mes do total logo acima.
  blocos.push(`<p class="nfse-hint mt-3 mb-1">Conferência da competência `
    + `<strong>${esc(painel.competencia)}</strong>, o mês de referência.</p>`);

  blocos.push(listaDivergencia(
    'Pagou e ficou sem nota', painel.sem_nota,
    (n) => `${esc(n.empresa || n.nome_csv || '—')} · R$ ${esc(n.valor || '—')}`,
    'Nada pendente.'));

  const foraDoAlcance = painel.nao_conferiveis
    ? ` <span class="nfse-hint">(${painel.nao_conferiveis} nota(s) fora do `
      + `período com extrato importado não entram nesta conta)</span>` : '';
  blocos.push(listaDivergencia(
    'Nota no portal sem linha no extrato', painel.sem_extrato,
    (e) => `${esc(e.nome_tomador || e.documento || '—')} · R$ ${esc(e.valor || '—')}`,
    'Nada sobrando.', foraDoAlcance));

  blocos.push(listaDivergencia(
    'Valor diferente do extrato', painel.valor_diferente,
    (d) => `${esc(d.emitida.nome_tomador || d.nota.nome_csv || '—')}: extrato `
      + `R$ ${esc(d.nota.valor || '—')} · portal R$ ${esc(d.emitida.valor || '—')}`,
    'Todos os valores batem.'));

  alvo.innerHTML = blocos.join('');
}

function listaDivergencia(titulo, itens, formatar, vazio, nota = '') {
  const lista = itens || [];
  const corpo = lista.length
    ? `<ul>${lista.map((i) => `<li>${formatar(i)}</li>`).join('')}</ul>`
    : `<p class="nfse-hint mb-0">${vazio}</p>`;
  return `<div class="nfse-diverg"><h3>${titulo} (${lista.length})${nota}</h3>${corpo}</div>`;
}

// Reflete no campo a competencia que o painel de fato conferiu — mas so quando
// difere, para nao apagar o que o operador esta digitando.
function sincronizarCompetencia(competencia) {
  const campo = document.getElementById('emitidasCompetencia');
  if (campo && competencia && campo.value.trim() !== competencia) {
    campo.value = competencia;
  }
}

function competenciaConferida() {
  const valor = document.getElementById('emitidasCompetencia')?.value.trim() || '';
  return /^\d{2}\/\d{4}$/.test(valor) ? valor : null;
}

// Trocar a competencia nao precisa reconsultar o portal: o espelho ja esta no
// banco, e so a comparacao muda.
async function recarregarPainelEmitidas() {
  const mes = document.getElementById('emitidasPainel')?.dataset.mes;
  if (!mes) return;
  const competencia = competenciaConferida();
  const url = `/nfse/emitidas?mes=${encodeURIComponent(mes)}`
    + (competencia ? `&competencia=${encodeURIComponent(competencia)}` : '');
  try {
    const resposta = await fetch(url);
    if (!resposta.ok) return;
    pintarEmitidas((await resposta.json()).painel);
  } catch { /* a tela continua util com o que ja tem */ }
}

function preencherPeriodoPadrao() {
  const campoInicio = document.getElementById('emitidasInicio');
  const campoFim = document.getElementById('emitidasFim');
  if (!campoInicio || !campoFim || campoInicio.value) return;

  // Mês da competência que a página está mostrando; sem filtro, o mês corrente.
  // É quase sempre "o mês que estou fechando", e digitar as duas datas toda vez
  // seria trabalho repetido.
  const escopo = escopoAtual();
  let ano;
  let mes;
  const casa = /^(\d{2})\/(\d{4})$/.exec(escopo || '');
  if (casa) {
    mes = Number(casa[1]);
    ano = Number(casa[2]);
  } else {
    const hoje = new Date();
    mes = hoje.getMonth() + 1;
    ano = hoje.getFullYear();
  }
  // dia 0 do mês seguinte = último dia deste mês, sem tabela de dias por mês
  const ultimo = new Date(ano, mes, 0).getDate();
  const dois = (n) => String(n).padStart(2, '0');
  campoInicio.value = `${ano}-${dois(mes)}-01`;
  campoFim.value = `${ano}-${dois(mes)}-${dois(ultimo)}`;
}

async function consultarEmitidas(botao) {
  const inicio = document.getElementById('emitidasInicio')?.value;
  const fim = document.getElementById('emitidasFim')?.value;
  const estado = document.getElementById('emitidasEstado');
  if (!inicio || !fim) {
    showToast('Informe as datas inicial e final do período.', 'error');
    return;
  }

  botao.disabled = true;
  const rotulo = botao.textContent;
  botao.textContent = 'Consultando…';
  if (estado) estado.textContent = 'Abrindo o portal e lendo as páginas…';
  try {
    const dados = await chamar('/nfse/emitidas/consultar',
      { body: JSON.stringify({ inicio, fim, competencia: competenciaConferida() }) });
    pintarEmitidas(dados.painel);
    if (estado) {
      estado.textContent = `${dados.lidas} nota(s) lida(s) em `
        + `${dados.blocos} consulta(s) · ${dados.novas} nova(s)`;
    }
    showToast(`${dados.lidas} nota(s) lida(s) do portal.`, 'success');
  } catch (erro) {
    if (estado) estado.textContent = '';
    showToast(erro.message, 'error');
  } finally {
    botao.disabled = false;
    botao.textContent = rotulo;
  }
}

// --- selecao e acao em massa ----------------------------------------------

function pintarSelecao() {
  const barra = document.getElementById('nfseSelecao');
  const total = document.getElementById('nfseSelecaoTotal');
  const todas = document.getElementById('checkTodas');
  if (!barra) return;

  barra.classList.toggle('d-none', selecionadas.size === 0);
  if (total) {
    total.textContent = selecionadas.size === 1
      ? '1 linha selecionada'
      : `${selecionadas.size} linhas selecionadas`;
  }
  if (todas) {
    todas.checked = notas.length > 0 && selecionadas.size === notas.length;
    // estado intermediario: nem todas, nem nenhuma
    todas.indeterminate = selecionadas.size > 0 && selecionadas.size < notas.length;
  }
}

function alternarSelecao(id, marcada) {
  if (marcada) selecionadas.add(id); else selecionadas.delete(id);
  pintarSelecao();
}

function selecionarTodas(marcada) {
  selecionadas.clear();
  if (marcada) notas.forEach((n) => selecionadas.add(n.id));
  renderizar();
}

async function acaoEmMassa(acao) {
  const ids = [...selecionadas];
  if (!ids.length) return;
  try {
    const dados = await chamar('/nfse/notas/acao',
      { body: JSON.stringify({ acao, ids }) });
    selecionadas.clear();
    await recarregarNotas({ forcar: true });

    // o rotulo vem do servidor, onde a lista de acoes ja vive
    const rotulo = dados.rotulo || 'aplicadas';
    const feitas = dados.aplicadas.length;
    const recusadas = dados.recusadas || [];
    if (!recusadas.length) {
      showToast(`${feitas} ${feitas === 1 ? 'linha' : 'linhas'} ${rotulo}.`, 'success');
      return;
    }
    // A acao e parcial de proposito: dizer so "pronto" esconderia o que ficou
    // de fora, e o operador so descobriria na hora de emitir.
    const motivos = recusadas.map((r) => `${r.nome || `#${r.id}`}: ${r.motivo}`).join(' · ');
    showToast(`${feitas} ${rotulo}; ${recusadas.length} não: ${motivos}`, 'info');
  } catch (erro) {
    showToast(erro.message, 'error');
  }
}

// --- emissao assistida ----------------------------------------------------
//
// Os dois modos entram pela mesma rota; o que muda e o que vai na fila e se o
// navegador fecha no fim. Como a automacao ESPERA voce conferir e emitir, o
// disparo e assincrono e o progresso vem por polling — uma requisicao nao pode
// ficar pendurada pelos minutos de uma revisao.

// Guarda o que fazer se o operador confirmar o aviso da alíquota.
let pendenteAliquota = null;
let timerLote = null;
let atualizarContrato = null;
let contratoAutomaticoElegivel = false;

/**
 * Decide somente a elegibilidade estrutural informada pelo backend.
 * O POST de início continua protegido pelo mesmo gate no servidor.
 *
 * @param {object|null} estado
 * @returns {boolean}
 */
export function contratoPermiteAutomatico(estado) {
  if (estado?.ativo?.elegivel_automatico !== true) return false;
  const incidentes = Array.isArray(estado.incidentes) ? estado.incidentes : [];
  return !incidentes.some((item) => ['aberto', 'configurado'].includes(item?.estado));
}

/**
 * Reflete o gate do contrato nos controles do modo, sem substituir a guarda
 * de autoridade da rota de início.
 *
 * @param {object|null} estado
 * @returns {boolean}
 */
export function aplicarGateContrato(estado) {
  contratoAutomaticoElegivel = contratoPermiteAutomatico(estado);
  const status = document.getElementById('nfseContratoStatus');
  if (status?.dataset.estado === 'bloqueado' || status?.dataset.estado === 'desconhecido') {
    contratoAutomaticoElegivel = false;
  }
  const automatico = document.getElementById('modoAutomatico');
  const mensagem = document.getElementById('nfseContratoStatusTexto')?.textContent
    || 'Resolva os incidentes do contrato antes do modo automático.';
  if (automatico) {
    automatico.disabled = !contratoAutomaticoElegivel;
    if (automatico.disabled) {
      automatico.setAttribute('aria-describedby', 'nfseContratoStatusTexto');
      automatico.title = mensagem;
    } else {
      automatico.removeAttribute('aria-describedby');
      automatico.removeAttribute('title');
    }
  }
  pintarModo();
  return contratoAutomaticoElegivel;
}

function modoAtual() {
  return document.querySelector('input[name="nfseModo"]:checked')?.value || 'individual';
}

const DESCRICAO_MODO = {
  individual: 'Preenche a nota que você escolher e para na revisão. '
    + 'Cada nota pede o certificado de novo.',
  lote: 'Preenche a lista inteira na mesma janela, parando na revisão de cada '
    + 'nota. Certificado uma vez só.',
  automatico: 'Preenche e emite sozinha, conferindo CPF/CNPJ, valor e '
    + 'descrição antes de cada emissão.',
};

function pintarModo() {
  const modo = modoAtual();
  const desc = document.getElementById('nfseModoDesc');
  const bloqueado = modo === 'automatico' && !contratoAutomaticoElegivel;
  const descricao = DESCRICAO_MODO[modo] || '';
  if (desc) {
    desc.textContent = bloqueado
      ? `${descricao} Indisponível: resolva os incidentes do contrato.`
      : descricao;
  }

  const iniciar = document.getElementById('btnIniciarLote');
  if (iniciar) {
    iniciar.classList.toggle('d-none', modo === 'individual');
    iniciar.textContent = modo === 'automatico'
      ? 'Emitir a lista inteira sozinho' : 'Emitir a lista inteira';
    iniciar.disabled = bloqueado;
    if (bloqueado) iniciar.title = 'Resolva os incidentes do contrato antes de iniciar.';
    else iniciar.removeAttribute('title');
  }
}

export async function iniciarEmissao({ notaId = null, ignorarAliquota = false } = {}) {
  const modo = notaId ? 'individual' : modoAtual();
  if (modo === 'automatico' && !contratoAutomaticoElegivel) {
    const mensagem = document.getElementById('nfseContratoStatusTexto')?.textContent
      || 'O contrato da NFS-e não está elegível para o modo automático.';
    showToast(mensagem, 'error');
    return;
  }
  try {
    const dados = await chamar('/nfse/lote/iniciar', {
      body: JSON.stringify({
        modo,
        nota_id: notaId,
        ignorar_aliquota: ignorarAliquota,
        competencia: escopoAtual(),
      }),
    });
    const AVISO_INICIO = {
      individual: 'Preenchendo. Confira no navegador e clique em Emitir NFS-e.',
      lote: `Fila iniciada: ${dados.total} nota(s). Confira e emita uma a uma.`,
      automatico: `Emitindo ${dados.total} nota(s) sozinho. Acompanhe o progresso aqui.`,
    };
    showToast(AVISO_INICIO[modo] || AVISO_INICIO.lote, 'info');
    acompanharLote();
  } catch (erro) {
    // Alíquota não conferida é aviso, não erro: pergunta em vez de recusar, e
    // nada foi tentado no portal — nenhuma linha vira falha.
    if (erro.dados?.motivo === 'aliquota_nao_confirmada') {
      pendenteAliquota = { notaId };
      abrirModalAliquota();
      return;
    }
    showToast(erro.message, 'error');
  }
}

// Enquanto a fila anda a pagina espelha o servidor: cada volta traz o status E
// a lista. A alternativa (recarregar so no fim) deixava o operador emitindo no
// portal sem ver nada mudar na tela por minutos.
const INTERVALO_POLL = 1000;

function acompanharLote() {
  if (timerLote) return;
  timerLote = setInterval(consultarLote, INTERVALO_POLL);
  consultarLote();
}

function pararAcompanhamento() {
  clearInterval(timerLote);
  timerLote = null;
}

async function consultarLote() {
  let lote;
  try {
    lote = (await chamar('/nfse/lote/status', { method: 'GET' })).lote;
  } catch {
    return;   // erro de rede num poll nao merece um toast por segundo
  }

  const ativo = ['running', 'paused'].includes(lote.status);
  // a lista vem junto enquanto ha fila: as transicoes que mais interessam
  // (preenchendo -> aguardando -> emitida) nao mexem em contador nenhum, entao
  // observar so o status do lote nao as revelaria
  if (ativo) await recarregarNotas();

  pintarLote(lote);

  if (!ativo) {
    pararAcompanhamento();
    await recarregarNotas();
    if (atualizarContrato && lote.status !== 'idle') await atualizarContrato();
    if (lote.status === 'completed') {
      showToast(`Fila concluída: ${lote.success} emitida(s).`, 'success');
    }
  }
}

function pintarLote(lote) {
  const rodando = lote.status === 'running';
  const pausado = lote.status === 'paused';
  const painel = document.getElementById('nfseProgresso');
  const percorreLista = modoAtual() !== 'individual';

  document.getElementById('btnIniciarLote')?.classList.toggle(
    'd-none', !percorreLista || rodando || pausado);
  // "Pular" existe para abandonar uma nota que ESPERA voce; no automatico nao
  // ha espera, entao o botao nao teria efeito nenhum
  document.getElementById('btnPularNota')?.classList.toggle(
    'd-none', !rodando || modoAtual() === 'automatico');
  document.getElementById('btnPausarLote')?.classList.toggle(
    'd-none', !rodando || lote.total <= 1);
  document.getElementById('btnRetomarLote')?.classList.toggle('d-none', !pausado);
  document.getElementById('btnPararLote')?.classList.toggle(
    'd-none', !(rodando || pausado));

  if (!painel) return;
  painel.classList.toggle('d-none', lote.status === 'idle');
  if (lote.status === 'idle') return;

  const feitas = Math.min(lote.processed, lote.total);
  const pct = lote.total ? Math.round((feitas / lote.total) * 100) : 0;
  const barra = document.getElementById('nfseProgressoBarra');
  if (barra) barra.style.width = `${pct}%`;

  const contagem = document.getElementById('nfseProgressoContagem');
  if (contagem) contagem.textContent = `${feitas}/${lote.total}`;

  const rotulo = document.getElementById('nfseProgressoRotulo');
  if (rotulo) rotulo.textContent = ROTULO_LOTE[lote.status] || lote.status;

  const mensagem = document.getElementById('nfseProgressoMensagem');
  if (mensagem) mensagem.textContent = lote.message || '';

  destacarNotaAtual(lote.nota_id, rodando);

  // Enquanto a fila anda, clicar em Preencher noutra linha so renderia 409:
  // o navegador esta ocupado com a nota atual.
  document.querySelectorAll('[data-preencher]').forEach((botao) => {
    botao.disabled = rodando || pausado;
  });
}

const ROTULO_LOTE = {
  running: 'Aguardando você conferir e emitir no navegador',
  paused: 'Pausado nesta nota',
  stopped: 'Interrompido',
  completed: 'Concluído',
  error: 'Interrompido por erro',
};

function destacarNotaAtual(notaId, rodando) {
  document.querySelectorAll('#corpoNotas tr[data-linha]').forEach((tr) => {
    tr.classList.toggle('table-active',
      rodando && Number(tr.dataset.linha) === notaId);
  });
}

// Escopo que a pagina esta mostrando. O poll TEM de respeita-lo: sem isso ele
// devolveria as notas da ultima importacao e sobrescreveria o mes escolhido a
// cada segundo.
function escopoAtual() {
  return document.getElementById('filtroCompetencia')?.value || 'ultima';
}

async function recarregarNotas({ forcar = false } = {}) {
  // Redesenhar a tabela apaga o que estiver sendo digitado nela, e este poll
  // roda a cada segundo. Dois guardas: nao redesenha se nada mudou (o caso
  // comum), e nao redesenha enquanto o operador tem uma linha aberta para
  // corrigir o vinculo ou a descricao.
  //
  // `forcar` e para a acao EXPLICITA do operador (confirmar/descartar um
  // agrupamento), que muda varias linhas de uma vez: ali o redesenho e o
  // resultado que ele pediu, e pular calado deixaria a tela mentindo.
  if (!forcar && (editando.size > 0 || editandoDescricao.size > 0)) return;
  // digitar valor/descricao na faixa do grupo tambem precisa sobreviver ao poll
  if (!forcar && document.activeElement?.closest?.('.nfse-grupo')) return;
  try {
    const resposta = await fetch(
      `/nfse/notas?competencia=${encodeURIComponent(escopoAtual())}`);
    if (!resposta.ok) return;
    const dados = await resposta.json();
    const novo = JSON.stringify(dados.notas);
    if (novo === JSON.stringify(notas)) return;
    notas = dados.notas;
    renderizar();
  } catch { /* a pagina continua util com o que ja tem na tela */ }
}

async function comandoLote(url, rotulo) {
  try {
    await chamar(url);
    showToast(rotulo, 'info');
    acompanharLote();
  } catch (erro) {
    showToast(erro.message, 'error');
  }
}

function abrirModalAliquota() {
  const el = document.getElementById('modalAliquota');
  if (!el) { showToast('Confira a alíquota antes de preencher.', 'error'); return; }
  bootstrap.Modal.getOrCreateInstance(el).show();
}

function fecharModalAliquota() {
  const el = document.getElementById('modalAliquota');
  if (el) bootstrap.Modal.getOrCreateInstance(el).hide();
}

// --- ligacao --------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
  renderizar();
  pintarEmitidas(lerJsonObjeto('dadosEmitidas'));

  // Default do periodo: o mes da competencia que a pagina esta mostrando, ou o
  // mes corrente. Digitar as duas datas toda vez seria trabalho repetido para o
  // caso que e quase sempre "o mes que estou fechando".
  preencherPeriodoPadrao();

  document.getElementById('emitidasCompetencia')?.addEventListener('change', () => {
    recarregarPainelEmitidas();
  });

  document.getElementById('formEmitidas')?.addEventListener('submit', (ev) => {
    ev.preventDefault();
    consultarEmitidas(document.getElementById('btnConsultarEmitidas'));
  });

  document.getElementById('formImportar')?.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const campo = document.getElementById('arquivoCsv');
    if (!campo?.files?.length) { showToast('Selecione ao menos um arquivo CSV.', 'error'); return; }
    const corpo = new FormData();
    Array.from(campo.files).forEach((f) => corpo.append('arquivo', f));
    try {
      const resposta = await fetch('/nfse/importar', { method: 'POST', body: corpo });
      const dados = await resposta.json().catch(() => ({}));
      if (!resposta.ok) throw new Error(dados.message || 'Falha ao importar.');
      notas = dados.notas;
      renderizar();
      const deArquivos = dados.arquivos > 1 ? ` de ${dados.arquivos} arquivos` : '';
      const repetidas = dados.ignoradas_duplicadas
        ? ` (${dados.ignoradas_duplicadas} linha(s) repetida(s) ignorada(s))` : '';
      showToast(`${dados.resumo.total} notas importadas${deArquivos}${repetidas}.`, 'success');
    } catch (erro) {
      showToast(erro.message, 'error');
    }
  });

  document.getElementById('formConfig')?.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const corpo = Object.fromEntries(new FormData(ev.target).entries());
    try {
      await chamar('/nfse/configuracao', { body: JSON.stringify(corpo) });
      showToast('Configurações salvas.', 'success');
    } catch (erro) {
      showToast(erro.message, 'error');
    }
  });

  document.getElementById('btnPrepararSessao')?.addEventListener('click', (ev) => {
    prepararSessao(ev.currentTarget);
  });

  document.getElementById('btnConfirmarAliquota')?.addEventListener('click', async () => {
    const aliquota = document.getElementById('nfseAliquota')?.textContent;
    try {
      const dados = await chamar('/nfse/sessao/confirmar-aliquota',
        { body: JSON.stringify({ aliquota }) });
      pintarSessao({ ...dados, ativa: true });
      showToast('Alíquota confirmada.', 'success');
    } catch (erro) {
      showToast(erro.message, 'error');
    }
  });

  document.getElementById('btnPreencherMesmoAssim')?.addEventListener('click', () => {
    const alvo = pendenteAliquota;
    pendenteAliquota = null;
    fecharModalAliquota();
    if (!alvo) return;
    iniciarEmissao({ notaId: alvo.notaId, ignorarAliquota: true });
  });

  document.getElementById('btnConferirAliquota')?.addEventListener('click', (ev) => {
    // não perde a linha: depois de conferir e confirmar, o operador clica em
    // Preencher de novo e a sessão já está liberada
    pendenteAliquota = null;
    fecharModalAliquota();
    prepararSessao(document.getElementById('btnPrepararSessao') || ev.currentTarget);
  });

  document.getElementById('modalAliquota')?.addEventListener('hidden.bs.modal', () => {
    pendenteAliquota = null;
  });

  document.getElementById('btnEncerrarSessao')?.addEventListener('click', async () => {
    try {
      await chamar('/nfse/sessao/encerrar');
      pintarSessao({ aliquota: null, aliquota_confirmada: false, ativa: false });
      showToast('Sessão encerrada.', 'info');
    } catch (erro) {
      showToast(erro.message, 'error');
    }
  });

  // Um campo limpa o outro: assim a ambiguidade nao chega a existir.
  document.getElementById('corpoNotas')?.addEventListener('input', (ev) => {
    const campo = ev.target.closest('[data-doc-de]');
    if (campo && campo.value.trim()) {
      const select = document.querySelector(`[data-empresa-de="${campo.dataset.docDe}"]`);
      if (select) select.value = '';
    }
  });

  document.getElementById('corpoNotas')?.addEventListener('change', (ev) => {
    const select = ev.target.closest('[data-empresa-de]');
    if (select && select.value) {
      const campo = document.querySelector(`[data-doc-de="${select.dataset.empresaDe}"]`);
      if (campo) campo.value = '';
    }
  });

  // Checkbox nao e button: precisa do proprio listener, antes do de clique.
  document.getElementById('corpoNotas')?.addEventListener('change', (ev) => {
    const alvo = ev.target.closest('[data-selecionar]');
    if (alvo) alternarSelecao(Number(alvo.dataset.selecionar), alvo.checked);
  });
  document.getElementById('checkTodas')?.addEventListener('change', (ev) => {
    selecionarTodas(ev.target.checked);
  });
  document.getElementById('btnLimparSelecao')?.addEventListener('click', () => {
    selecionarTodas(false);
  });
  document.getElementById('btnCancelarSelecao')?.addEventListener('click', () => {
    acaoEmMassa('cancelar');
  });
  document.getElementById('btnRestaurarSelecao')?.addEventListener('click', () => {
    acaoEmMassa('restaurar');
  });
  document.getElementById('btnJaEmitiSelecao')?.addEventListener('click', () => {
    acaoEmMassa('emitida_manual');
  });
  document.getElementById('btnDesmarcarSelecao')?.addEventListener('click', () => {
    acaoEmMassa('desmarcar_emitida');
  });

  document.getElementById('corpoNotas')?.addEventListener('click', (ev) => {
    const alvo = ev.target.closest('button');
    if (!alvo) return;
    if (alvo.dataset.editar) { editando.add(Number(alvo.dataset.editar)); renderizar(); }
    else if (alvo.dataset.cancelar) { editando.delete(Number(alvo.dataset.cancelar)); renderizar(); }
    else if (alvo.dataset.resolver) resolverEmpresa(Number(alvo.dataset.resolver));
    else if (alvo.dataset.liberar) liberarDuplicata(Number(alvo.dataset.liberar));
    else if (alvo.dataset.jaemitida) marcarEmitidaManual(Number(alvo.dataset.jaemitida), true);
    else if (alvo.dataset.desmarcar) marcarEmitidaManual(Number(alvo.dataset.desmarcar), false);
    else if (alvo.dataset.preencher) iniciarEmissao({ notaId: Number(alvo.dataset.preencher) });
    else if (alvo.dataset.editarDescricao) {
      editandoDescricao.add(Number(alvo.dataset.editarDescricao));
      renderizar();
    } else if (alvo.dataset.cancelarDescricao) {
      editandoDescricao.delete(Number(alvo.dataset.cancelarDescricao));
      renderizar();
    } else if (alvo.dataset.salvarDescricao) {
      resolverDescricao(Number(alvo.dataset.salvarDescricao));
    } else if (alvo.dataset.cancelarNota) {
      cancelarNota(Number(alvo.dataset.cancelarNota), true);
    } else if (alvo.dataset.restaurar) {
      cancelarNota(Number(alvo.dataset.restaurar), false);
    } else if (alvo.dataset.confirmarGrupo) {
      confirmarGrupo(alvo.dataset.confirmarGrupo);
    } else if (alvo.dataset.descartarGrupo) {
      descartarGrupo(alvo.dataset.descartarGrupo);
    } else if (alvo.dataset.desfazerGrupo) {
      desfazerGrupo(alvo.dataset.desfazerGrupo);
    }
  });

  // --- modo de emissao ---
  document.querySelectorAll('input[name="nfseModo"]').forEach((radio) => {
    radio.addEventListener('change', pintarModo);
  });
  pintarModo();

  document.getElementById('filtroCompetencia')?.addEventListener('change', (ev) => {
    // recarrega pelo servidor: o escopo decide contadores, lista e o proprio
    // rotulo da ultima importacao
    window.location.href = `/nfse?competencia=${encodeURIComponent(ev.target.value)}`;
  });

  document.getElementById('btnIniciarLote')?.addEventListener('click', () => {
    // o modo automatico emite documento fiscal sem olho humano em cada nota:
    // pede confirmacao explicita, uma vez, antes de comecar
    if (modoAtual() === 'automatico') {
      const el = document.getElementById('modalAutomatico');
      if (el) { bootstrap.Modal.getOrCreateInstance(el).show(); return; }
    }
    iniciarEmissao();
  });

  document.getElementById('btnConfirmarAutomatico')?.addEventListener('click', () => {
    const el = document.getElementById('modalAutomatico');
    if (el) bootstrap.Modal.getOrCreateInstance(el).hide();
    iniciarEmissao();
  });
  document.getElementById('btnPularNota')?.addEventListener('click', () => {
    comandoLote('/nfse/lote/pular', 'Pulando esta nota.');
  });
  document.getElementById('btnPausarLote')?.addEventListener('click', () => {
    comandoLote('/nfse/lote/pausar', 'Pausa pedida; termina a nota atual.');
  });
  document.getElementById('btnRetomarLote')?.addEventListener('click', () => {
    comandoLote('/nfse/lote/retomar', 'Retomando de onde parou.');
  });
  document.getElementById('btnPararLote')?.addEventListener('click', () => {
    comandoLote('/nfse/lote/parar', 'Interrompendo a fila.');
  });

  // A central consulta somente o estado persistido. Nenhuma recon ou sessão
  // fiscal é aberta pela inicialização da página.
  void inicializarContratoNfse({
    onEstado: aplicarGateContrato,
  }).then((central) => {
    atualizarContrato = central.atualizar;
  });

  fetch('/nfse/sessao/status')
    .then((r) => r.json())
    .then((d) => pintarSessao({ ...d, ativa: d.ativa }))
    .catch(() => {});

  // Reabrir a pagina no meio de uma fila precisa reencontrar o progresso: o
  // lote vive no servidor, nao nesta aba.
  consultarLote().then(() => {
    if (document.getElementById('nfseProgresso')?.classList.contains('d-none') === false) {
      acompanharLote();
    }
  });
});
