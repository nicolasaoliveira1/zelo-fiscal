import test from 'node:test';
import assert from 'node:assert/strict';

globalThis.document = {
  addEventListener() {},
  getElementById() { return null; },
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

const { celulaDescricao } = await import('../app/static/js/nfse.js');

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
