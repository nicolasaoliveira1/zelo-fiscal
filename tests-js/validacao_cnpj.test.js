import test from 'node:test';
import assert from 'node:assert/strict';

import { digito_verificador_ok } from '../app/static/js/validacao_cnpj.js';

test('aceita CNPJ sintético com dígitos verificadores válidos', () => {
  assert.equal(digito_verificador_ok('98765432000198'), true);
});

test('recusa CNPJ com dígito verificador incorreto', () => {
  assert.equal(digito_verificador_ok('98765432000199'), false);
});

test('recusa sequência repetida e tamanho incorreto', () => {
  assert.equal(digito_verificador_ok('11111111111111'), false);
  assert.equal(digito_verificador_ok('9876543200019'), false);
});
