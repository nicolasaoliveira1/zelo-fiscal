import test from 'node:test';
import assert from 'node:assert/strict';

import {
  ORDENACOES_NFSE,
  SITUACOES_NFSE,
  filtrarOrdenarNotas,
  idsSelecionadosVisiveis,
  interpretarValorBrasileiro,
  prioridadeNota,
  situacaoOperacional,
} from '../app/static/js/nfse_filtros.js';

function nota(id, overrides = {}) {
  return {
    id,
    nome_csv: `Nome ${id}`,
    empresa: null,
    documento: null,
    valor: '10,00',
    status: 'pronta',
    emitivel: true,
    grupo: null,
    ...overrides,
  };
}

function ids(resultado) {
  return resultado.notas.map((item) => item.id);
}

test('busca nome no banco e empresa ignorando caixa e acentuação', () => {
  const notas = [
    nota(1, { nome_csv: 'Tomador São Bento' }),
    nota(2, { empresa: 'Empresa Arco-Íris', nome_csv: 'apelido diferente' }),
  ];

  assert.deepEqual(ids(filtrarOrdenarNotas(notas, { busca: 'sao bento' })), [1]);
  assert.deepEqual(ids(filtrarOrdenarNotas(notas, { busca: 'ARCO-IRIS' })), [2]);
  assert.deepEqual(ids(filtrarOrdenarNotas(notas, { busca: '   ' })), [1, 2]);
});

test('busca documento com ou sem pontuação e tolera campos ausentes', () => {
  const notas = [
    nota(1, { nome_csv: null, empresa: null, documento: '123.456.789-00' }),
    nota(2, { nome_csv: null, empresa: null, documento: null }),
  ];

  assert.deepEqual(ids(filtrarOrdenarNotas(notas, { busca: '12345678900' })), [1]);
  assert.deepEqual(ids(filtrarOrdenarNotas(notas, { busca: '123.456.789-00' })), [1]);
  assert.deepEqual(ids(filtrarOrdenarNotas(notas, { busca: 'ausente' })), []);
});

test('interpreta 1500, 1500,00 e 1.500,00 no mesmo centavo', () => {
  assert.equal(interpretarValorBrasileiro('1500'), 150000n);
  assert.equal(interpretarValorBrasileiro('1500,00'), 150000n);
  assert.equal(interpretarValorBrasileiro('1.500,00'), 150000n);
  assert.equal(interpretarValorBrasileiro('0,00'), 0n);
});

test('valor exato compara em centavos e rejeita moeda inválida sem recorte silencioso', () => {
  const notas = [nota(1, { valor: '1.500,00' }), nota(2, { valor: '15,00' })];

  assert.deepEqual(ids(filtrarOrdenarNotas(notas, { valor: '1500' })), [1]);
  assert.deepEqual(ids(filtrarOrdenarNotas(notas, { valor: '1.500,00' })), [1]);
  assert.deepEqual(ids(filtrarOrdenarNotas(notas, { valor: '-1' })), [1, 2]);
  const invalido = filtrarOrdenarNotas(notas, { busca: 'Nome 2', valor: '1.500.00' });
  assert.equal(invalido.valor_invalido, true);
  assert.deepEqual(ids(invalido), [2]);
});

test('classifica situações pela emitibilidade e pelos estados reais', () => {
  assert.equal(situacaoOperacional(nota(1, { status: 'pessoa_fisica', emitivel: true })), SITUACOES_NFSE.PRONTAS);
  assert.equal(situacaoOperacional(nota(2, { status: 'cadastro_pendente', emitivel: true })), SITUACOES_NFSE.PRONTAS);
  assert.equal(situacaoOperacional(nota(3, { status: 'preenchendo', emitivel: false })), SITUACOES_NFSE.ANDAMENTO);
  assert.equal(situacaoOperacional(nota(4, { status: 'emitida', emitivel: false })), SITUACOES_NFSE.RESOLVIDAS);
  assert.equal(situacaoOperacional(nota(5, { status: 'pronta', emitivel: true, divergencia_valor: true })), SITUACOES_NFSE.ATENCAO);
  assert.deepEqual(ids(filtrarOrdenarNotas([
    nota(1, { status: 'falha', emitivel: true }),
    nota(2, { status: 'pessoa_fisica', emitivel: true }),
    nota(3, { status: 'emitida', emitivel: false }),
  ], { situacao: SITUACOES_NFSE.ATENCAO })), [1]);
});

test('prioridade coloca andamento, bloqueios, falhas, emitíveis e resolvidas', () => {
  const notas = [
    nota(1, { status: 'emitida', emitivel: false }),
    nota(2, { status: 'pronta', emitivel: true, grupo: { token: 'g', pendente: true, lider: true } }),
    nota(3, { status: 'falha', emitivel: true }),
    nota(4, { status: 'pronta', emitivel: true }),
    nota(5, { status: 'aguardando_confirmacao', emitivel: false }),
  ];

  assert.deepEqual(ids(filtrarOrdenarNotas(notas)), [5, 2, 3, 4, 1]);
  assert.equal(prioridadeNota(notas[4]), 0);
  assert.equal(prioridadeNota(notas[1]), 1);
  assert.equal(prioridadeNota(notas[2]), 2);
});

test('ordena nome com fallback, direções e desempate pela importação', () => {
  const notas = [
    nota(1, { empresa: null, nome_csv: 'Zeta' }),
    nota(2, { empresa: 'Álfa', nome_csv: 'Zeta' }),
    nota(3, { empresa: 'Alfa', nome_csv: 'Outra' }),
  ];

  assert.deepEqual(ids(filtrarOrdenarNotas(notas, { ordem: ORDENACOES_NFSE.NOME_ASC })), [2, 3, 1]);
  assert.deepEqual(ids(filtrarOrdenarNotas(notas, { ordem: ORDENACOES_NFSE.NOME_DESC })), [1, 2, 3]);
});

test('ordena valor e mantém a importação no empate', () => {
  const notas = [
    nota(1, { valor: '20,00' }),
    nota(2, { valor: '10,00' }),
    nota(3, { valor: '10,00' }),
    nota(4, { valor: null }),
  ];

  assert.deepEqual(ids(filtrarOrdenarNotas(notas, { ordem: ORDENACOES_NFSE.VALOR_ASC })), [2, 3, 1, 4]);
  assert.deepEqual(ids(filtrarOrdenarNotas(notas, { ordem: ORDENACOES_NFSE.VALOR_DESC })), [1, 2, 3, 4]);
});

test('ordena emissão em ambas as direções, com datas ausentes depois', () => {
  const notas = [
    nota(1, { emitida_em: null }),
    nota(2, { emitida_em: '2026-09-03T10:00:00' }),
    nota(3, { emitida_em: '2026-09-03T10:00:00' }),
    nota(4, { emitida_em: '2026-09-04T10:00:00' }),
  ];

  assert.deepEqual(ids(filtrarOrdenarNotas(notas, { ordem: ORDENACOES_NFSE.EMISSAO_DESC })), [4, 2, 3, 1]);
  assert.deepEqual(ids(filtrarOrdenarNotas(notas, { ordem: ORDENACOES_NFSE.EMISSAO_ASC })), [2, 3, 4, 1]);
});

test('mantém o agrupamento completo, a ordem interna e posiciona pela liderança', () => {
  const grupo = (lider) => ({ token: 'grupo-sintetico', pendente: true, lider });
  const notas = [
    nota(1, { nome_csv: 'Membro encontrado', grupo: grupo(false) }),
    nota(2, { nome_csv: 'Fora do grupo' }),
    nota(3, { nome_csv: 'Líder do grupo', grupo: grupo(true) }),
  ];

  const resultado = filtrarOrdenarNotas(notas, { busca: 'membro encontrado', ordem: ORDENACOES_NFSE.NOME_ASC });
  assert.deepEqual(ids(resultado), [1, 3]);

  const porImportacao = filtrarOrdenarNotas(notas, { ordem: ORDENACOES_NFSE.IMPORTACAO });
  assert.deepEqual(ids(porImportacao), [2, 1, 3]);
});

test('situação de grupo inclui o bloco quando só uma linha corresponde', () => {
  const notas = [
    nota(1, { nome_csv: 'Linha líder', status: 'emitida', emitivel: false,
      grupo: { token: 'g2', pendente: false, lider: true } }),
    nota(2, { nome_csv: 'Linha irmã', grupo: { token: 'g2', pendente: false, lider: false } }),
    nota(3, { nome_csv: 'Sem grupo', status: 'pronta', emitivel: true }),
  ];

  assert.deepEqual(ids(filtrarOrdenarNotas(notas, {
    situacao: SITUACOES_NFSE.RESOLVIDAS,
  })), [1, 2]);
});

test('ações em massa descartam ids ocultos ou não selecionáveis', () => {
  const resultado = idsSelecionadosVisiveis(new Set([1, 2, 4]), [
    nota(2),
    nota(4, { selecionavel: false }),
  ]);

  assert.deepEqual([...resultado], [2]);
});

test('polling reaplica o mesmo recorte ao novo retrato da fila', () => {
  const filtros = {
    busca: 'Tomador',
    valor: '1500,00',
    situacao: SITUACOES_NFSE.PRONTAS,
    ordem: ORDENACOES_NFSE.VALOR_DESC,
  };
  const inicial = [
    nota(1, { nome_csv: 'Tomador A', valor: '1.500,00' }),
    nota(2, { nome_csv: 'Outro', valor: '1.500,00', status: 'emitida', emitivel: false }),
  ];
  const atualizado = [
    nota(1, { nome_csv: 'Tomador A', valor: '1.500,00', status: 'emitida', emitivel: false }),
    nota(2, { nome_csv: 'Tomador B', valor: '1.500,00' }),
  ];

  assert.deepEqual(ids(filtrarOrdenarNotas(inicial, filtros)), [1]);
  assert.deepEqual(ids(filtrarOrdenarNotas(atualizado, filtros)), [2]);
});

test('duplicata liberada pelo operador conta como pronta, não como atenção', () => {
  // O servidor já disse emitivel: o rótulo "Possível duplicata" sobrevive à
  // liberação e não pode esconder a linha de "Prontas para preencher".
  const liberada = nota(1, { status: 'duplicata', emitivel: true, duplicata_liberada: true });

  assert.equal(situacaoOperacional(liberada), SITUACOES_NFSE.PRONTAS);
  assert.equal(prioridadeNota(liberada), 3);
  assert.deepEqual(ids(filtrarOrdenarNotas([liberada], {
    situacao: SITUACOES_NFSE.PRONTAS,
  })), [1]);
});

test('nota resolvida não sobe na fila por divergência antiga da importação', () => {
  // `divergencia_valor` é marca da importação e a emissão não a apaga.
  const emitida = nota(1, { status: 'emitida', emitivel: false, divergencia_valor: true });
  const pronta = nota(2, { status: 'pronta', emitivel: true });

  assert.equal(situacaoOperacional(emitida), SITUACOES_NFSE.RESOLVIDAS);
  assert.equal(prioridadeNota(emitida), 4);
  assert.deepEqual(ids(filtrarOrdenarNotas([emitida, pronta])), [2, 1]);
});

test('dígito dentro de um nome não vira consulta de documento', () => {
  const notas = [
    nota(1, { nome_csv: 'Loja 3 Comercio', documento: '11222333000181' }),
    nota(2, { nome_csv: 'Padaria Central', documento: '99333777000100' }),
  ];

  assert.deepEqual(ids(filtrarOrdenarNotas(notas, { busca: 'Loja 3' })), [1]);
  // termo sem letra nenhuma continua sendo busca por documento
  assert.deepEqual(ids(filtrarOrdenarNotas(notas, { busca: '112.223' })), [1]);
});
