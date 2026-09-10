import test, { after, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><body><div id="toastStack"></div></body>');
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.Event = dom.window.Event;
globalThis.requestAnimationFrame = (callback) => setTimeout(callback, 0);
after(() => dom.window.close());

const {
  alvoHtml,
  renderizarAlvos,
  rotuloEstado,
  rotuloResultadoRecon,
  inicializarContratosPortais,
} = await import('../app/static/js/contratos_portais.js');

const ALVO_BLOQUEADO = {
  nome: 'Trabalhista (CNDT/TST)',
  fluxo: 'trabalhista',
  alvo: 'cndt',
  estado: 'bloqueado',
  mensagem: 'mudança estrutural aguardando revisão',
  versao: 3,
  contrato_id: 41,
  ativado_em: '2026-09-01T10:00:00',
  pode_criar_baseline: false,
  incidentes: [{
    id: 77,
    classificacao: 'revisao',
    severidade: 'bloqueante',
    etapa: 'formulario',
    elemento: 'submeter',
    mensagem: 'A estrutura observada diverge do contrato ativo.',
    primeira_observacao_em: '2026-09-08T02:20:00',
    ultima_observacao_em: '2026-09-09T02:20:00',
  }],
  historico: [
    { id: 41, versao: 3, estado: 'ativa', origem: 'sistema', ativa: true,
      criado_em: '2026-09-01T10:00:00', ativado_em: '2026-09-01T10:00:00' },
    { id: 40, versao: 2, estado: 'historica', origem: 'usuario', ativa: false,
      criado_em: '2026-08-01T10:00:00', ativado_em: '2026-08-01T10:00:00' },
  ],
};

beforeEach(() => {
  document.body.innerHTML = '<div id="toastStack"></div><div id="contratos-portais"></div>';
});

test('o estado do alvo fica sempre visível, em texto, não só em cor', () => {
  const container = document.getElementById('contratos-portais');
  container.innerHTML = alvoHtml(ALVO_BLOQUEADO);

  const faixa = container.querySelector('.zl-contrato-faixa');
  assert.equal(faixa.dataset.estado, 'bloqueado');
  assert.match(faixa.textContent, /Mudança aguardando revisão/);
  assert.match(faixa.textContent, /v3/);
});

test('id interno não aparece na tela, só em data-attribute', () => {
  const container = document.getElementById('contratos-portais');
  container.innerHTML = alvoHtml(ALVO_BLOQUEADO);

  assert.doesNotMatch(container.textContent, /\b77\b/);
  assert.doesNotMatch(container.textContent, /\b41\b/);
  assert.equal(
    container.querySelector('.zl-contrato-incidente').dataset.incidente, '77');
  assert.equal(
    container.querySelector('[data-acao="restaurar"]').dataset.contrato, '40');
});

test('a versão ativa não oferece restaurar; as demais oferecem', () => {
  const container = document.getElementById('contratos-portais');
  container.innerHTML = alvoHtml(ALVO_BLOQUEADO);

  const restaurar = container.querySelectorAll('[data-acao="restaurar"]');
  assert.equal(restaurar.length, 1);
  assert.equal(restaurar[0].dataset.contrato, '40');
});

test('alvo sem contrato mostra a ação de baseline, não a de verificar', () => {
  const container = document.getElementById('contratos-portais');
  container.innerHTML = alvoHtml({
    ...ALVO_BLOQUEADO,
    estado: 'desconhecido', versao: null, ativado_em: null,
    pode_criar_baseline: true, incidentes: [], historico: [],
  });

  assert.ok(container.querySelector('[data-acao="baseline"]'));
  assert.equal(container.querySelector('[data-acao="recon"]'), null);
  assert.match(container.textContent, /Sem contrato ativo/);
  // O rótulo precisa dizer que o portal será observado: ativar não é só gravar.
  assert.match(
    container.querySelector('[data-acao="baseline"]').textContent, /Observar o portal/);
});

test('lista vazia diz que não há portal, sem quebrar', () => {
  renderizarAlvos({ alvos: [] });

  assert.match(
    document.getElementById('contratos-portais').textContent,
    /Nenhum portal com contrato adaptativo/);
});

test('rótulos cobrem os estados e caem no desconhecido sem quebrar', () => {
  assert.equal(rotuloEstado('autoajustado'), 'Seletor autoajustado');
  assert.equal(rotuloEstado('coisa-nova'), 'Sem contrato ativo');
  assert.match(rotuloResultadoRecon('adiado'), /emissão em curso/);
  assert.match(rotuloResultadoRecon('coisa-nova'), /Não foi possível/);
});

// --- ações ------------------------------------------------------------------

// O clique dispara uma cadeia de await (enviar -> recarregar -> render); um
// tick só não a esgota.
async function assentar() {
  for (let i = 0; i < 10; i += 1) await new Promise((r) => setTimeout(r, 0));
}

function comFetch(respostas) {
  const chamadas = [];
  globalThis.fetch = async (url, opcoes) => {
    chamadas.push({ url, opcoes });
    const resposta = respostas.shift();
    return {
      ok: resposta.ok !== false,
      status: resposta.status || 200,
      json: async () => resposta.corpo || {},
    };
  };
  return chamadas;
}

test('inicializar carrega o estado e não dispara nenhuma ação sozinho', async () => {
  const chamadas = comFetch([{ corpo: { alvos: [ALVO_BLOQUEADO] } }]);

  await inicializarContratosPortais({ confirmar: () => true });

  assert.equal(chamadas.length, 1);
  assert.equal(chamadas[0].opcoes, undefined);
  assert.ok(document.querySelector('.zl-contrato-incidente'));
});

test('ativar exige confirmação: recusada, nada é enviado', async () => {
  const chamadas = comFetch([{ corpo: { alvos: [ALVO_BLOQUEADO] } }]);
  await inicializarContratosPortais({ confirmar: () => false });
  chamadas.length = 0;

  document.querySelector('[data-acao="aceitar"]').click();
  await assentar();

  assert.equal(chamadas.length, 0);
});

test('ativar confirmado envia confirmado:true e recarrega', async () => {
  const chamadas = comFetch([
    { corpo: { alvos: [ALVO_BLOQUEADO] } },
    { corpo: { status: 'ok', versao: 4 } },
    { corpo: { alvos: [ALVO_BLOQUEADO] } },
  ]);
  await inicializarContratosPortais({ confirmar: () => true });

  document.querySelector('[data-acao="aceitar"]').click();
  await assentar();

  const envio = chamadas[1];
  assert.match(envio.url, /\/incidentes\/77\/aceitar$/);
  assert.equal(JSON.parse(envio.opcoes.body).confirmado, true);
  assert.equal(chamadas.length, 3);
});

test('erro fica na linha do incidente, não numa faixa global', async () => {
  comFetch([
    { corpo: { alvos: [ALVO_BLOQUEADO] } },
    { ok: false, status: 423, corpo: { message: 'Há uma emissão em curso neste portal.' } },
  ]);
  await inicializarContratosPortais({ confirmar: () => true });

  document.querySelector('[data-acao="aceitar"]').click();
  await assentar();

  const linha = document.querySelector('.zl-contrato-incidente');
  assert.match(linha.querySelector('.zl-contrato-erro').textContent, /emissão em curso/);
  assert.equal(
    document.querySelector('[data-erro-alvo]').textContent, '');
  // O botão volta a ficar clicável: erro não é beco sem saída.
  assert.equal(document.querySelector('[data-acao="aceitar"]').disabled, false);
});

test('verificar agora não pede confirmação e não promove nada', async () => {
  const semIncidente = { ...ALVO_BLOQUEADO, estado: 'compativel', incidentes: [] };
  const chamadas = comFetch([
    { corpo: { alvos: [semIncidente] } },
    { corpo: { status: 'ok', resultado: 'compativel' } },
    { corpo: { alvos: [semIncidente] } },
  ]);
  let confirmou = false;
  await inicializarContratosPortais({ confirmar: () => { confirmou = true; return true; } });

  document.querySelector('[data-acao="recon"]').click();
  await assentar();

  assert.equal(confirmou, false);
  assert.match(chamadas[1].url, /\/trabalhista\/cndt\/recon$/);
});

test('descartar pede confirmação e envia confirmado:true', async () => {
  const semIncidente = { ...ALVO_BLOQUEADO, estado: 'compativel', incidentes: [] };
  const chamadas = comFetch([
    { corpo: { alvos: [semIncidente] } },
    { corpo: { status: 'ok', versao: 3 } },
    { corpo: { alvos: [semIncidente] } },
  ]);
  let perguntou = null;
  await inicializarContratosPortais({
    confirmar: (texto) => { perguntou = texto; return true; },
  });

  document.querySelector('[data-acao="descartar"]').click();
  await assentar();

  assert.match(perguntou, /seletores fixos do código/);
  assert.match(chamadas[1].url, /\/trabalhista\/cndt\/descartar$/);
  assert.equal(JSON.parse(chamadas[1].opcoes.body).confirmado, true);
});

test('descartar recusado no diálogo não envia nada', async () => {
  const semIncidente = { ...ALVO_BLOQUEADO, estado: 'compativel', incidentes: [] };
  const chamadas = comFetch([{ corpo: { alvos: [semIncidente] } }]);
  await inicializarContratosPortais({ confirmar: () => false });
  chamadas.length = 0;

  document.querySelector('[data-acao="descartar"]').click();
  await assentar();

  assert.equal(chamadas.length, 0);
});

test('alvo sem contrato não oferece descartar', () => {
  const container = document.getElementById('contratos-portais');
  container.innerHTML = alvoHtml({
    ...ALVO_BLOQUEADO, estado: 'desconhecido', versao: null,
    pode_criar_baseline: true, incidentes: [], historico: [],
  });

  assert.equal(container.querySelector('[data-acao="descartar"]'), null);
});
