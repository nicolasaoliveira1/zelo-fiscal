import test from 'node:test';
import assert from 'node:assert/strict';

import {
  agrupar_arquivos_xml,
  balanco_vazio,
  chave_segmentada,
  escapar_html,
  linha_do_cofre,
  somar_balanco,
  vazio_de_vencimentos,
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

const cofreBase = {
  inventariado: true,
  prontas: 45,
  total: 47,
  itens: [],
  com_vencimento: 40,
  janela_dias: 30,
};

const vencido = (nome) => ({
  empresa_id: 1, empresa_nome: nome, not_after: '2026-01-01T00:00:00',
  dias_restantes: -5, causa: 'vencido',
});
const vencendo = (nome, dias) => ({
  empresa_id: 2, empresa_nome: nome, not_after: '2026-09-10T00:00:00',
  dias_restantes: dias, causa: 'vencendo',
});

test('em dia, a régua volta ao pré-voo do manifestador', () => {
  assert.match(linha_do_cofre(cofreBase), /45<\/strong> de <strong[^>]*>47<\/strong> empresas prontas/);
});

test('havendo vencimento, a régua muda de assunto e usa a população certa', () => {
  const linha = linha_do_cofre({ ...cofreBase, itens: [vencendo('A', 10), vencendo('B', 12)] });

  assert.match(linha, /2<\/strong> de <strong[^>]*>40<\/strong> vencem em 30 dias/);
  // o denominador é `com_vencimento`, nunca o total do cofre: contar quem não
  // tem data conhecida transformaria "não sei" em "está em dia"
  assert.doesNotMatch(linha, /47/);
  assert.doesNotMatch(linha, /prontas/);
});

test('vencido e vencendo são ditos separados — as ações são diferentes', () => {
  const linha = linha_do_cofre({ ...cofreBase, itens: [vencido('A'), vencendo('B', 9)] });

  assert.match(linha, /1<\/strong> vencido e <strong[^>]*>1<\/strong> vencendo em 30 dias/);
});

test('só vencidos: plural correto e denominador de volta', () => {
  const linha = linha_do_cofre({ ...cofreBase, itens: [vencido('A'), vencido('B')] });

  assert.match(linha, /2<\/strong> de <strong[^>]*>40<\/strong> certificados vencidos/);
});

test('um só vencendo fala no singular', () => {
  assert.match(linha_do_cofre({ ...cofreBase, itens: [vencendo('A', 3)] }), /1<\/strong> de <strong[^>]*>40<\/strong> vence em 30 dias/);
});

test('cofre nunca inventariado não vira zero tranquilizador', () => {
  const linha = linha_do_cofre({ ...cofreBase, inventariado: false });

  assert.equal(linha, 'Cofre nunca inventariado');
  assert.doesNotMatch(linha, /\d/);
});

test('lista vazia sem inventário diz que não sabe, não que está em dia', () => {
  const vazio = vazio_de_vencimentos({ inventariado: false, com_vencimento: 0, janela_dias: 30 });

  assert.equal(vazio.icone, 'question-circle');
  assert.match(vazio.texto, /não foi inventariado/);
  assert.doesNotMatch(vazio.texto, /30/);
});

test('inventariado mas sem nenhum vencimento conhecido também é "não sei"', () => {
  // todos os certificados em `sem_arquivo`/`sem_pasta`: a janela não se aplica
  const vazio = vazio_de_vencimentos({ inventariado: true, com_vencimento: 0, janela_dias: 30 });

  assert.equal(vazio.icone, 'question-circle');
  assert.equal(vazio.texto, 'Nenhum certificado tem vencimento conhecido.');
  assert.doesNotMatch(vazio.texto, /\d/);
});

test('com população conhecida, a ausência vira afirmação com denominador', () => {
  const varios = vazio_de_vencimentos({ inventariado: true, com_vencimento: 40, janela_dias: 30 });

  assert.equal(varios.icone, 'check2');
  assert.equal(varios.texto, 'Nenhum dos 40 certificados vence nos próximos 30 dias.');
});

test('população de um fala no singular', () => {
  const unico = vazio_de_vencimentos({ inventariado: true, com_vencimento: 1, janela_dias: 30 });

  assert.equal(unico.icone, 'check2');
  assert.match(unico.texto, /^O único certificado com vencimento conhecido não vence/);
});
