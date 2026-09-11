import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const fonte = readFileSync('app/static/js/certidoes.js', 'utf8');

test('emissão individual dentro do lote usa POST', () => {
  const inicio = fonte.indexOf('if (config.singleBtn)');
  const fim = fonte.indexOf('if (config.pauseBtn)', inicio);
  const handler = fonte.slice(inicio, fim);

  assert.match(handler, /fetch\(singleUrl,\s*\{\s*method:\s*['"]POST['"]\s*\}\)/);
});
