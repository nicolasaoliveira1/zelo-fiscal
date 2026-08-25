import test, { after, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><body></body>');
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.Event = dom.window.Event;
dom.window.HTMLElement.prototype.scrollIntoView = function scrollIntoView() {};
after(() => dom.window.close());

const { limparInvalido, marcarInvalido, validar } = await import('../app/static/js/campos.js');

beforeEach(() => {
  document.body.innerHTML = `
    <form>
      <input id="primeiro" aria-describedby="ajuda">
      <small id="ajuda">Ajuda</small>
      <input id="segundo">
    </form>`;
});

test('marca campo, preserva ajuda existente e cria mensagem acessível', () => {
  const campo = document.getElementById('primeiro');

  marcarInvalido(campo, 'Informe um valor válido.');

  assert.equal(campo.classList.contains('is-invalid'), true);
  assert.equal(campo.getAttribute('aria-invalid'), 'true');
  assert.equal(campo.getAttribute('aria-describedby'), 'ajuda primeiro-erro');
  assert.equal(document.getElementById('primeiro-erro').textContent, 'Informe um valor válido.');
});

test('limpa campo inválido sem remover ajuda existente', () => {
  const campo = document.getElementById('primeiro');
  marcarInvalido(campo, 'Corrija o campo.');
  limparInvalido(campo);

  assert.equal(campo.classList.contains('is-invalid'), false);
  assert.equal(campo.hasAttribute('aria-invalid'), false);
  assert.equal(campo.getAttribute('aria-describedby'), 'ajuda');
  assert.equal(document.getElementById('primeiro-erro').textContent, '');
});

test('valida todos os campos inválidos e devolve o resultado', () => {
  const primeiro = document.getElementById('primeiro');
  const segundo = document.getElementById('segundo');

  assert.equal(validar([
    [primeiro, 'Primeiro campo obrigatório.'],
    [segundo, 'Segundo campo obrigatório.'],
  ]), false);
  assert.equal(primeiro.classList.contains('is-invalid'), true);
  assert.equal(segundo.classList.contains('is-invalid'), true);

  assert.equal(validar([
    [primeiro, ''],
    [segundo, ''],
  ]), true);
  assert.equal(primeiro.classList.contains('is-invalid'), false);
  assert.equal(segundo.classList.contains('is-invalid'), false);
});
