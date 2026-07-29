// Painel de emissao de NFSe (NFSE-17).
//
// Sem framework, no padrao do dashboard.js (AD-015): modulo ES nativo,
// versionado por static_versionado. O CSRF vai no wrapper global de fetch
// definido no base.html.

import { showToast } from './toasts.js';

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
};

const lerJson = (id) => {
  const el = document.getElementById(id);
  try { return JSON.parse(el?.textContent || '[]'); } catch { return []; }
};

let notas = lerJson('dadosNotas');
const empresas = lerJson('dadosEmpresas');
let aliquotaConfirmada = false;
// linhas cujo vinculo o operador reabriu para corrigir
const editando = new Set();

const esc = (texto) => {
  const div = document.createElement('div');
  div.textContent = texto == null ? '' : String(texto);
  return div.innerHTML;
};

async function chamar(url, opcoes = {}) {
  const resposta = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    ...opcoes,
  });
  const dados = await resposta.json().catch(() => ({}));
  if (!resposta.ok) {
    throw new Error(dados.message || `Falha na requisição (${resposta.status}).`);
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
               title="Trocar a empresa ou o documento desta linha">Editar</button>` : '';
  return `${rotulo}${editar}`;
}

function acoesDaLinha(nota) {
  const partes = [];

  if (nota.status === 'emitida') {
    // so o que o operador marcou na mao pode ser desmarcado; o que a automacao
    // emitiu de fato nao volta atras por um clique
    if (nota.origem_emissao === 'manual') {
      partes.push(`<button class="btn btn-ghost btn-sm" data-desmarcar="${nota.id}">Desmarcar</button>`);
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
    partes.push(`<button class="btn btn-ghost btn-sm" data-jaemitida="${nota.id}" title="Marcar como já emitida por fora">Já emiti</button>`);
    partes.push(`<button class="btn btn-primary btn-sm" data-preencher="${nota.id}">Preencher</button>`);
  }
  if (nota.status === 'aguardando_confirmacao') {
    partes.push(`<button class="btn btn-primary btn-sm" data-jaemitida="${nota.id}">Emiti no portal</button>`);
  }
  return partes.join('');
}

function linha(nota) {
  const alerta = (nota.divergencia_valor ? ' nfse-linha-alerta' : '')
    + (nota.origem_emissao === 'manual' ? ' nfse-emitida-manual' : '');
  const aviso = nota.divergencia_valor
    ? ' <i class="bi bi-exclamation-triangle" title="Soma das parcelas não bate com o valor final"></i>' : '';
  return `
    <tr class="${alerta}" data-linha="${nota.id}">
      <td>${esc(nota.nome_csv)}</td>
      <td>${celulaEmpresa(nota)}</td>
      <td class="nfse-mono">${esc(nota.documento || '—')}</td>
      <td class="nfse-mono">${esc(nota.competencia || '—')}</td>
      <td class="nfse-mono text-end">${esc(nota.valor || '—')}${aviso}</td>
      <td><span class="nfse-status st-${nota.status}">${esc(ROTULO_STATUS[nota.status] || nota.status)}</span>
          ${nota.erro ? `<div class="nfse-hint">${esc(nota.erro)}</div>` : ''}</td>
      <td><div class="nfse-acoes-linha">${acoesDaLinha(nota)}</div></td>
    </tr>`;
}

function renderizar() {
  const corpo = document.getElementById('corpoNotas');
  const vazio = document.getElementById('nfseVazio');
  if (!corpo) return;
  corpo.innerHTML = notas.map(linha).join('');
  if (vazio) vazio.classList.toggle('d-none', notas.length > 0);
  atualizarContadores();
}

function atualizarContadores() {
  const conta = {};
  notas.forEach((n) => { conta[n.status] = (conta[n.status] || 0) + 1; });
  const valores = {
    total: notas.length,
    divergencias: notas.filter((n) => n.divergencia_valor).length,
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
      estado.textContent = 'Alíquota confirmada. Já dá para preencher as notas.';
    } else if (aliquota) {
      estado.textContent = 'Confira se a alíquota bate com a do mês antes de preencher.';
    } else if (ativa) {
      estado.textContent = 'Não consegui ler a alíquota do portal. Confira no navegador e confirme.';
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
    showToast('Escolha uma empresa OU informe um CPF/CNPJ. Limpe o que não quer usar.', 'error');
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

async function preencherNota(id, botao) {
  if (!aliquotaConfirmada) {
    showToast('Confira a alíquota do Simples antes de preencher.', 'error');
    return;
  }
  botao.disabled = true;
  botao.textContent = 'Preenchendo…';
  try {
    const dados = await chamar(`/nfse/nota/${id}/preencher`);
    if (dados.nota) substituir(dados.nota);
    showToast('Nota preenchida. Confira no navegador e emita.', 'success');
  } catch (erro) {
    showToast(erro.message, 'error');
    const linhaAtual = notas.find((n) => n.id === id);
    if (linhaAtual) { linhaAtual.status = 'falha'; linhaAtual.erro = erro.message; renderizar(); }
  } finally {
    botao.disabled = false;
    botao.textContent = 'Preencher';
  }
}

// --- ligacao --------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
  renderizar();

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

  document.getElementById('corpoNotas')?.addEventListener('click', (ev) => {
    const alvo = ev.target.closest('button');
    if (!alvo) return;
    if (alvo.dataset.editar) { editando.add(Number(alvo.dataset.editar)); renderizar(); }
    else if (alvo.dataset.cancelar) { editando.delete(Number(alvo.dataset.cancelar)); renderizar(); }
    else if (alvo.dataset.resolver) resolverEmpresa(Number(alvo.dataset.resolver));
    else if (alvo.dataset.liberar) liberarDuplicata(Number(alvo.dataset.liberar));
    else if (alvo.dataset.jaemitida) marcarEmitidaManual(Number(alvo.dataset.jaemitida), true);
    else if (alvo.dataset.desmarcar) marcarEmitidaManual(Number(alvo.dataset.desmarcar), false);
    else if (alvo.dataset.preencher) preencherNota(Number(alvo.dataset.preencher), alvo);
  });

  fetch('/nfse/sessao/status')
    .then((r) => r.json())
    .then((d) => pintarSessao({ ...d, ativa: d.ativa }))
    .catch(() => {});
});
