import test from 'node:test';
import assert from 'node:assert/strict';

import {
  agrupar_arquivos_xml,
  balanco_vazio,
  chave_segmentada,
  escapar_html,
  somar_balanco,
} from '../app/static/js/manifestador_dados.js';

test('escapa texto para conteúdo e atributos HTML', () => {
  assert.equal(escapar_html('<teste> " & \'x\''), '&lt;teste&gt; &quot; &amp; &#39;x&#39;');
});

test('segmenta uma chave de acesso em nove campos', () => {
  const chave = Array.from({ length: 44 }, (_, indice) => String(indice % 10)).join('');
  const html = chave_segmentada(chave);

  assert.equal((html.match(/<span class="/g) || []).length, 9);
  assert.match(html, /class="f-cuf">01<\/span>/);
  assert.match(html, /class="f-dv">3<\/span>/);
});

test('chave inválida é devolvida como texto escapado', () => {
  assert.equal(chave_segmentada('<chave>'), '&lt;chave&gt;');
});

test('soma blocos de importação e preserva categorias', () => {
  const acumulado = balanco_vazio();
  somar_balanco(acumulado, {
    total_lidas: 2,
    aceitas: [{ ordem: 1 }],
    duplicatas: [{ ordem: 2 }],
  });
  somar_balanco(acumulado, {
    total_lidas: 1,
    aceitas: [{ ordem: 3 }],
    fora_do_prazo: [{ ordem: 4 }],
  });

  assert.equal(acumulado.total_lidas, 3);
  assert.equal(acumulado.aceitas.length, 2);
  assert.equal(acumulado.duplicatas.length, 1);
  assert.equal(acumulado.fora_do_prazo.length, 1);
});

test('agrupa somente XMLs pela origem da entrada', () => {
  const grupos = agrupar_arquivos_xml([
    { name: 'registro-01.xml', webkitRelativePath: 'pasta-teste/sub/registro-01.xml' },
    { name: 'registro-02.XML', webkitRelativePath: 'pasta-teste/registro-02.XML' },
    { name: 'imagem.png', webkitRelativePath: 'pasta-teste/imagem.png' },
    { name: 'avulso.xml' },
  ]);

  assert.deepEqual([...grupos.keys()], ['pasta-teste', 'Arquivos avulsos']);
  assert.equal(grupos.get('pasta-teste').length, 2);
  assert.equal(grupos.get('Arquivos avulsos').length, 1);
});
