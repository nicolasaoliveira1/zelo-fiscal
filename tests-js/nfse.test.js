import test from 'node:test';
import assert from 'node:assert/strict';

const elementos = new Map();

globalThis.document = {
  addEventListener() {},
  getElementById(id) { return elementos.get(id) || null; },
  createElement() {
    let texto = '';
    return {
      set textContent(valor) { texto = String(valor ?? ''); },
      get innerHTML() {
        return texto
          .replace(/&/g, '&amp;')
          .replace(/</g, '&lt;')
          .replace(/>/g, '&gt;');
      },
    };
  },
};

const { celulaDescricao, pintarEmitidas } = await import('../app/static/js/nfse.js');

test('permite editar a descrição quando a categoria está a definir', () => {
  const html = celulaDescricao({
    id: 17,
    categoria: 'indefinida',
    status: 'descricao_pendente',
    descricao_extrato: 'DESCRIÇÃO SINTÉTICA DO PIX',
  });

  assert.match(html, /A definir/);
  assert.match(html, /data-editar-descricao="17"/);
  assert.match(html, /DESCRIÇÃO SINTÉTICA DO PIX/);
});

test('mantém a descrição bloqueada depois do preenchimento', () => {
  ['emitida', 'aguardando_confirmacao'].forEach((status) => {
    const html = celulaDescricao({
      id: 18,
      categoria: 'indefinida',
      status,
      descricao_extrato: 'DESCRIÇÃO SINTÉTICA DO PIX',
    });

    assert.doesNotMatch(html, /data-editar-descricao/);
  });
});

test('oferece o resumo imprimível somente depois de consultar o mês', () => {
  const painel = { dataset: {}, innerHTML: '' };
  elementos.set('emitidasPainel', painel);

  pintarEmitidas({
    mes_geracao: '08/2026',
    competencia: '07/2026',
    nunca_consultado: false,
    quantidade: 3,
    total: '1.234,56',
    outras_situacoes: {},
    consultado_em: '31/08/2026 10:30',
    sem_nota: [],
    sem_extrato: [],
    nao_conferiveis: 0,
    valor_diferente: [],
  });

  assert.match(painel.innerHTML, /Imprimir \/ salvar PDF/);
  assert.match(painel.innerHTML, /\/nfse\/emitidas\/resumo\?mes=08%2F2026/);

  pintarEmitidas({
    mes_geracao: '09/2026',
    competencia: '08/2026',
    nunca_consultado: true,
  });
  assert.doesNotMatch(painel.innerHTML, /Imprimir \/ salvar PDF/);
  elementos.delete('emitidasPainel');
});
