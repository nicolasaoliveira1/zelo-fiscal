import test, { after, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><body></body>', { pretendToBeVisual: true });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.Event = dom.window.Event;
globalThis.requestAnimationFrame = (callback) => setTimeout(callback, 0);
dom.window.HTMLElement.prototype.scrollIntoView = function scrollIntoView() {};
globalThis.fetch = async () => ({ ok: true, json: async () => ({}) });

const { pintarEmitidas, consultarEmitidas } = await import('../app/static/js/nfse.js');

after(() => dom.window.close());

beforeEach(() => {
  document.body.innerHTML = `
    <div id="toastStack"></div>
    <div id="emitidasPainel"></div>
    <input id="emitidasInicio" value="2026-08-01">
    <input id="emitidasFim" value="2026-08-31">
    <span id="emitidasEstado"></span>
    <button id="btnConsultarEmitidas" type="button">Consultar o portal</button>`;
});

function painel(overrides = {}) {
  return {
    consulta_id: 7,
    inicio: '2026-08-01',
    fim: '2026-08-17',
    mes_geracao: '08/2026',
    nunca_consultado: false,
    quantidade: 1,
    total: '400,00',
    outras_situacoes: {},
    consultado_em: '31/08/2026 15:10',
    sem_nota: [],
    sem_extrato: [],
    nao_conferiveis: 0,
    valor_diferente: [],
    ambigua: [],
    ...overrides,
  };
}

test('mostra o intervalo e a seção de correspondência ambígua', () => {
  pintarEmitidas(painel({
    valor_diferente: [{
      nota: { nome_csv: 'CLIENTE TESTE', valor: '400,00' },
      emitida: { nome_tomador: 'CLIENTE TESTE', valor: '450,00' },
    }],
    ambigua: [{
      emitida: { nome_tomador: 'TOMADOR AMBÍGUO', valor: '300,00' },
      candidatas: [
        { nome_csv: 'CANDIDATA UM', valor: '300,00' },
        { nome_csv: 'CANDIDATA DOIS', valor: '300,00' },
      ],
    }],
  }));

  const texto = document.getElementById('emitidasPainel').textContent;
  assert.match(texto, /01\/08\/2026 a 17\/08\/2026/);
  assert.match(texto, /Correspondência ambígua \(1\)/);
  assert.match(texto, /valor final comparado/);
  assert.match(texto, /CANDIDATA UM/);
});

test('consulta envia somente o intervalo, sem competência', async () => {
  const chamadas = [];
  globalThis.fetch = async (url, opcoes) => {
    chamadas.push({ url, opcoes });
    return {
      ok: true,
      json: async () => ({ status: 'ok', lidas: 0, blocos: 1, novas: 0,
        painel: painel() }),
    };
  };

  await consultarEmitidas(document.getElementById('btnConsultarEmitidas'));

  assert.equal(chamadas.length, 1);
  assert.deepEqual(JSON.parse(chamadas[0].opcoes.body), {
    inicio: '2026-08-01',
    fim: '2026-08-31',
  });
});

test('o campo e os recálculos manuais de competência não existem mais', () => {
  const template = readFileSync('app/templates/nfse.html', 'utf8');
  const script = readFileSync('app/static/js/nfse.js', 'utf8');

  assert.equal(template.includes('emitidasCompetencia'), false);
  assert.equal(script.includes('emitidasCompetencia'), false);
  assert.equal(script.includes('competenciaConferida'), false);
  assert.equal(script.includes('recarregarPainelEmitidas'), false);
});
