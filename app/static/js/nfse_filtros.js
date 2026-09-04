// Núcleo puro da conferência de NFS-e. Não acessa DOM, rede ou estado global:
// isso mantém as regras de recorte, classificação e ordenação testáveis sem
// abrir uma sessão do portal.

/** @typedef {Object} NotaConferencia */

const STATUS_ATENCAO = new Set([
  'empresa_pendente', 'cadastro_pendente', 'descricao_pendente',
  'duplicata', 'invalida', 'falha', 'pulada',
]);
const STATUS_ANDAMENTO = new Set(['preenchendo', 'aguardando_confirmacao']);
const STATUS_RESOLVIDO = new Set(['emitida', 'cancelada', 'agrupada']);

export const SITUACOES_NFSE = Object.freeze({
  TODAS: 'todas',
  ATENCAO: 'atencao',
  PRONTAS: 'prontas',
  ANDAMENTO: 'andamento',
  RESOLVIDAS: 'resolvidas',
});

export const ORDENACOES_NFSE = Object.freeze({
  PRIORIDADE: 'prioridade',
  NOME_ASC: 'nome_asc',
  NOME_DESC: 'nome_desc',
  VALOR_ASC: 'valor_asc',
  VALOR_DESC: 'valor_desc',
  EMISSAO_DESC: 'emissao_desc',
  EMISSAO_ASC: 'emissao_asc',
  IMPORTACAO: 'importacao',
});

/**
 * Normaliza texto humano para busca e ordenação.
 *
 * @param {unknown} valor
 * @returns {string}
 */
export function normalizarTexto(valor) {
  return String(valor ?? '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLocaleLowerCase('pt-BR')
    .trim()
    .replace(/\s+/g, ' ');
}

/**
 * Converte moeda brasileira em centavos exatos.
 *
 * `undefined` significa entrada não vazia e inválida; `null` significa campo
 * vazio. BigInt evita que a comparação de um valor fiscal dependa de float.
 *
 * @param {unknown} valor
 * @returns {bigint|null|undefined}
 */
export function interpretarValorBrasileiro(valor) {
  const texto = String(valor ?? '').trim().replace(/^R\$\s*/i, '');
  if (!texto) return null;

  // O ponto é separador de milhar e a vírgula é separador decimal. A forma sem
  // separador de milhar também é aceita para que "1500" seja inequívoco.
  if (!/^(?:\d+|\d{1,3}(?:\.\d{3})+)(?:,\d{1,2})?$/.test(texto)) {
    return undefined;
  }

  const [inteiroCru, decimalCru = ''] = texto.split(',');
  const inteiro = inteiroCru.replaceAll('.', '');
  const decimal = decimalCru.padEnd(2, '0');
  return BigInt(inteiro) * 100n + BigInt(decimal || '0');
}

/**
 * Classifica uma linha no controle operacional da tela.
 *
 * A emitibilidade vem do servidor. Status pendente, divergência e proposta de
 * agrupamento têm precedência porque bloqueiam o fluxo mesmo que a linha ainda
 * pareça pronta. Falha e pulada continuam emitíveis para nova tentativa, mas
 * ficam em atenção por serem trabalho interrompido.
 *
 * @param {NotaConferencia} nota
 * @returns {'atencao'|'prontas'|'andamento'|'resolvidas'}
 */
export function situacaoOperacional(nota) {
  const status = nota?.status;
  if (STATUS_RESOLVIDO.has(status)) return SITUACOES_NFSE.RESOLVIDAS;
  if (STATUS_ANDAMENTO.has(status)) return SITUACOES_NFSE.ANDAMENTO;

  const bloqueia = Boolean(nota?.divergencia_valor || nota?.grupo?.pendente);
  if (bloqueia) return SITUACOES_NFSE.ATENCAO;
  // Ainda não cadastrada é uma pendência de cadastro, mas o domínio permite
  // preencher a nota quando há CNPJ: o rótulo não pode invalidar a decisão de
  // emitibilidade.
  if (status === 'cadastro_pendente' && nota?.emitivel === true) {
    return SITUACOES_NFSE.PRONTAS;
  }
  if (STATUS_ATENCAO.has(status)) return SITUACOES_NFSE.ATENCAO;
  if (nota?.emitivel === true) return SITUACOES_NFSE.PRONTAS;
  return SITUACOES_NFSE.ATENCAO;
}

/**
 * Produz a prioridade definida pela fila de conferência.
 *
 * @param {NotaConferencia} nota
 * @returns {number}
 */
export function prioridadeNota(nota) {
  if (STATUS_ANDAMENTO.has(nota?.status)) return 0;
  if (Boolean(nota?.divergencia_valor || nota?.grupo?.pendente)) return 1;
  if (nota?.status === 'cadastro_pendente' && nota?.emitivel === true) return 3;
  if (STATUS_ATENCAO.has(nota?.status)) {
    if (nota?.status === 'falha' || nota?.status === 'pulada') return 2;
    return 1;
  }
  if (nota?.emitivel === true) return 3;
  return 4;
}

function valorDaNota(nota) {
  const valor = interpretarValorBrasileiro(nota?.valor);
  return valor === undefined ? null : valor;
}

function nomeDaNota(nota) {
  return normalizarTexto(nota?.empresa || nota?.nome_csv);
}

function documentoDaNota(nota) {
  return String(nota?.documento ?? '').replace(/\D/g, '');
}

function combinaTexto(nota, termo) {
  if (!termo) return true;
  const texto = normalizarTexto(termo);
  const documento = texto.replace(/\D/g, '');
  const nomeCasa = [nota?.nome_csv, nota?.empresa]
    .map(normalizarTexto)
    .some((campo) => campo.includes(texto));
  const documentoCasa = documento.length > 0
    && documentoDaNota(nota).includes(documento);
  return nomeCasa || documentoCasa;
}

function combinaValor(nota, valorCentavos) {
  if (valorCentavos === null) return true;
  return valorDaNota(nota) === valorCentavos;
}

function blocosDaFila(notas) {
  const blocos = [];
  const porToken = new Map();

  notas.forEach((nota, indice) => {
    const token = nota?.grupo?.token;
    if (!token) {
      blocos.push({ linhas: [nota], indice, indice_lider: indice });
      return;
    }

    let bloco = porToken.get(token);
    if (!bloco) {
      bloco = { linhas: [], indice, indice_lider: null };
      porToken.set(token, bloco);
      blocos.push(bloco);
    }
    bloco.linhas.push(nota);
    if (nota.grupo?.lider) bloco.indice_lider = indice;
  });

  blocos.forEach((bloco) => {
    if (bloco.indice_lider === null) bloco.indice_lider = bloco.indice;
  });
  return blocos;
}

function liderDoBloco(bloco) {
  return bloco.linhas.find((nota) => nota?.grupo?.lider) || bloco.linhas[0];
}

function combinaBloco(bloco, filtros, valorCentavos) {
  return bloco.linhas.some((nota) => (
    combinaTexto(nota, filtros.busca)
    && combinaValor(nota, valorCentavos)
    && (filtros.situacao === SITUACOES_NFSE.TODAS
      || situacaoOperacional(nota) === filtros.situacao)
  ));
}

function compararTexto(a, b) {
  if (a === b) return 0;
  return a < b ? -1 : 1;
}

function compararValor(a, b, descendente = false) {
  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  if (a === b) return 0;
  const resultado = a < b ? -1 : 1;
  return descendente ? -resultado : resultado;
}

function compararData(a, b, descendente = false) {
  const dataA = a?.emitida_em ? Date.parse(a.emitida_em) : NaN;
  const dataB = b?.emitida_em ? Date.parse(b.emitida_em) : NaN;
  const conhecidaA = Number.isFinite(dataA);
  const conhecidaB = Number.isFinite(dataB);
  if (!conhecidaA && !conhecidaB) return 0;
  if (!conhecidaA) return 1;
  if (!conhecidaB) return -1;
  if (dataA === dataB) return 0;
  const resultado = dataA < dataB ? -1 : 1;
  return descendente ? -resultado : resultado;
}

function compararBlocos(a, b, ordem) {
  const notaA = liderDoBloco(a);
  const notaB = liderDoBloco(b);
  let resultado = 0;

  switch (ordem) {
    case ORDENACOES_NFSE.NOME_ASC:
      resultado = compararTexto(nomeDaNota(notaA), nomeDaNota(notaB));
      break;
    case ORDENACOES_NFSE.NOME_DESC:
      resultado = compararTexto(nomeDaNota(notaB), nomeDaNota(notaA));
      break;
    case ORDENACOES_NFSE.VALOR_ASC:
      resultado = compararValor(valorDaNota(notaA), valorDaNota(notaB));
      break;
    case ORDENACOES_NFSE.VALOR_DESC:
      resultado = compararValor(valorDaNota(notaA), valorDaNota(notaB), true);
      break;
    case ORDENACOES_NFSE.EMISSAO_ASC:
      resultado = compararData(notaA, notaB);
      break;
    case ORDENACOES_NFSE.EMISSAO_DESC:
      resultado = compararData(notaA, notaB, true);
      break;
    case ORDENACOES_NFSE.IMPORTACAO:
      resultado = a.indice_lider - b.indice_lider;
      break;
    case ORDENACOES_NFSE.PRIORIDADE:
    default:
      resultado = prioridadeNota(notaA) - prioridadeNota(notaB);
      break;
  }

  return resultado || (a.indice_lider - b.indice_lider);
}

/**
 * Aplica os refinamentos e devolve as linhas já prontas para renderização.
 * Um agrupamento entra inteiro quando qualquer uma de suas linhas corresponde.
 *
 * @param {NotaConferencia[]} notas
 * @param {{busca?: string, valor?: string, situacao?: string, ordem?: string}} filtros
 * @returns {{notas: NotaConferencia[], valor_invalido: boolean}}
 */
export function filtrarOrdenarNotas(notas, filtros = {}) {
  const opcoes = {
    busca: String(filtros.busca ?? ''),
    valor: String(filtros.valor ?? ''),
    situacao: filtros.situacao || SITUACOES_NFSE.TODAS,
    ordem: filtros.ordem || ORDENACOES_NFSE.PRIORIDADE,
  };
  const interpretado = interpretarValorBrasileiro(opcoes.valor);
  const valorInvalido = interpretado === undefined;
  const valorCentavos = valorInvalido ? null : interpretado;

  const blocos = blocosDaFila(Array.isArray(notas) ? notas : [])
    .filter((bloco) => combinaBloco(bloco, opcoes, valorCentavos))
    .sort((a, b) => compararBlocos(a, b, opcoes.ordem));

  return {
    notas: blocos.flatMap((bloco) => bloco.linhas),
    valor_invalido: valorInvalido,
  };
}

/**
 * Retorna somente ids que continuam visíveis no retrato filtrado.
 *
 * @param {NotaConferencia[]} notas
 * @returns {Set<number>}
 */
export function idsVisiveis(notas) {
  return new Set((Array.isArray(notas) ? notas : []).map((nota) => nota.id));
}

/**
 * Retorna somente ids que podem receber a ação em massa no retrato visível.
 *
 * @param {Iterable<number>} selecionadas
 * @param {NotaConferencia[]} notas
 * @returns {Set<number>}
 */
export function idsSelecionadosVisiveis(selecionadas, notas) {
  const visiveis = new Set((Array.isArray(notas) ? notas : [])
    .filter((nota) => nota?.selecionavel !== false)
    .map((nota) => nota.id));
  return new Set([...selecionadas].filter((id) => visiveis.has(id)));
}
