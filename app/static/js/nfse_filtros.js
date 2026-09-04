// Núcleo puro da conferência de NFS-e. Não acessa DOM, rede ou estado global:
// isso mantém as regras de recorte, classificação e ordenação testáveis sem
// abrir uma sessão do portal.

/** @typedef {Object} NotaConferencia */

const STATUS_ANDAMENTO = new Set(['preenchendo', 'aguardando_confirmacao']);
const STATUS_RESOLVIDO = new Set(['emitida', 'cancelada', 'agrupada']);
// Trabalho interrompido: o servidor volta a considerar estas linhas emitíveis
// (nova tentativa), mas elas continuam pedindo olho antes das que nunca falharam.
const STATUS_INTERROMPIDO = new Set(['falha', 'pulada']);

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

// A fila que a spec descreve (NFSE-FILTRO-08), de cima para baixo.
const PRIORIDADE_POR_SITUACAO = Object.freeze({
  [SITUACOES_NFSE.ANDAMENTO]: 0,
  [SITUACOES_NFSE.ATENCAO]: 1,
  [SITUACOES_NFSE.PRONTAS]: 3,
  [SITUACOES_NFSE.RESOLVIDAS]: 4,
});

// Termo sem nenhuma letra: é consulta de CPF/CNPJ. Com letra é busca de nome —
// e aí o dígito solto NÃO pode virar consulta de documento, senão "Loja 3" casa
// com todo tomador cujo CNPJ tenha um 3 em qualquer posição.
const SO_DOCUMENTO = /^[\d.\-/\s]+$/;

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
  return BigInt(inteiro) * 100n + BigInt(decimalCru.padEnd(2, '0'));
}

/**
 * Classifica uma linha no controle operacional da tela.
 *
 * A emitibilidade vem do servidor (`nfse_service.emitivel`) e vale mais que o
 * rótulo: `cadastro_pendente` com CNPJ digitado, `pessoa_fisica` e a duplicata
 * já liberada pelo operador são emitíveis apesar do status de pendência —
 * listar aqui quais status "são atenção" já tinha deixado a duplicata liberada
 * fora de "Prontas". Bloqueio real (divergência, proposta de agrupamento em
 * aberto) tem precedência, e trabalho interrompido continua em atenção.
 *
 * @param {NotaConferencia} nota
 * @returns {'atencao'|'prontas'|'andamento'|'resolvidas'}
 */
export function situacaoOperacional(nota) {
  const status = nota?.status;
  if (STATUS_RESOLVIDO.has(status)) return SITUACOES_NFSE.RESOLVIDAS;
  if (STATUS_ANDAMENTO.has(status)) return SITUACOES_NFSE.ANDAMENTO;
  if (nota?.divergencia_valor || nota?.grupo?.pendente) return SITUACOES_NFSE.ATENCAO;
  if (nota?.emitivel === true && !STATUS_INTERROMPIDO.has(status)) {
    return SITUACOES_NFSE.PRONTAS;
  }
  return SITUACOES_NFSE.ATENCAO;
}

/**
 * Produz a prioridade definida pela fila de conferência (NFSE-FILTRO-08).
 *
 * Deriva da mesma classificação da tela: duas cascatas paralelas já tinham
 * divergido — a nota emitida com `divergencia_valor` (marca da importação, que
 * a emissão não apaga) subia ao topo dos bloqueios sendo trabalho concluído.
 *
 * @param {NotaConferencia} nota
 * @returns {number}
 */
export function prioridadeNota(nota) {
  const situacao = situacaoOperacional(nota);
  // Falha e pulada são atenção, mas abaixo do bloqueio que ninguém respondeu.
  if (situacao === SITUACOES_NFSE.ATENCAO && STATUS_INTERROMPIDO.has(nota?.status)) {
    return 2;
  }
  return PRIORIDADE_POR_SITUACAO[situacao];
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

/**
 * Prepara o termo uma vez por recorte, não uma vez por linha.
 *
 * @param {string} termo
 * @returns {{texto: string, documento: string}|null}
 */
function prepararBusca(termo) {
  const texto = normalizarTexto(termo);
  if (!texto) return null;
  return {
    texto,
    documento: SO_DOCUMENTO.test(texto) ? texto.replace(/\D/g, '') : '',
  };
}

function combinaTexto(nota, busca) {
  if (!busca) return true;
  const nomeCasa = [nota?.nome_csv, nota?.empresa]
    .map(normalizarTexto)
    .some((campo) => campo.includes(busca.texto));
  const documentoCasa = busca.documento.length > 0
    && documentoDaNota(nota).includes(busca.documento);
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

/**
 * Calcula as chaves de ordenação uma vez por bloco.
 *
 * Dentro do comparador cada normalização de nome e cada leitura de valor
 * rodariam O(n log n) vezes — a cada tecla digitada na busca.
 */
function chavesDoBloco(bloco) {
  const lider = liderDoBloco(bloco);
  bloco.nome = nomeDaNota(lider);
  bloco.valor = valorDaNota(lider);
  bloco.data = lider?.emitida_em ? Date.parse(lider.emitida_em) : NaN;
  bloco.prioridade = prioridadeNota(lider);
  return bloco;
}

function combinaBloco(bloco, busca, valorCentavos, situacao) {
  return bloco.linhas.some((nota) => (
    combinaTexto(nota, busca)
    && combinaValor(nota, valorCentavos)
    && (situacao === SITUACOES_NFSE.TODAS || situacaoOperacional(nota) === situacao)
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
  const conhecidaA = Number.isFinite(a);
  const conhecidaB = Number.isFinite(b);
  // Sem data fica sempre depois, nas duas direções: a ausência não é "mais
  // antiga", é desconhecida.
  if (!conhecidaA && !conhecidaB) return 0;
  if (!conhecidaA) return 1;
  if (!conhecidaB) return -1;
  if (a === b) return 0;
  const resultado = a < b ? -1 : 1;
  return descendente ? -resultado : resultado;
}

function compararBlocos(a, b, ordem) {
  let resultado = 0;

  switch (ordem) {
    case ORDENACOES_NFSE.NOME_ASC:
      resultado = compararTexto(a.nome, b.nome);
      break;
    case ORDENACOES_NFSE.NOME_DESC:
      resultado = compararTexto(b.nome, a.nome);
      break;
    case ORDENACOES_NFSE.VALOR_ASC:
      resultado = compararValor(a.valor, b.valor);
      break;
    case ORDENACOES_NFSE.VALOR_DESC:
      resultado = compararValor(a.valor, b.valor, true);
      break;
    case ORDENACOES_NFSE.EMISSAO_ASC:
      resultado = compararData(a.data, b.data);
      break;
    case ORDENACOES_NFSE.EMISSAO_DESC:
      resultado = compararData(a.data, b.data, true);
      break;
    case ORDENACOES_NFSE.IMPORTACAO:
      // o desempate abaixo JA e a ordem da importacao
      break;
    case ORDENACOES_NFSE.PRIORIDADE:
    default:
      resultado = a.prioridade - b.prioridade;
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
  const situacao = filtros.situacao || SITUACOES_NFSE.TODAS;
  const ordem = filtros.ordem || ORDENACOES_NFSE.PRIORIDADE;
  const busca = prepararBusca(filtros.busca);
  const interpretado = interpretarValorBrasileiro(filtros.valor);
  const valorInvalido = interpretado === undefined;
  const valorCentavos = valorInvalido ? null : interpretado;

  const blocos = blocosDaFila(Array.isArray(notas) ? notas : [])
    .filter((bloco) => combinaBloco(bloco, busca, valorCentavos, situacao))
    .map(chavesDoBloco)
    .sort((a, b) => compararBlocos(a, b, ordem));

  return {
    notas: blocos.flatMap((bloco) => bloco.linhas),
    valor_invalido: valorInvalido,
  };
}

/**
 * Ids do recorte visível que aceitam ação em massa.
 *
 * Ponto único da regra: a seleção, o "marcar todas" e o disparo da ação em
 * massa leem daqui — três cópias do mesmo predicado divergiriam.
 *
 * @param {NotaConferencia[]} notas
 * @returns {Set<number>}
 */
export function idsVisiveis(notas) {
  return new Set((Array.isArray(notas) ? notas : [])
    .filter((nota) => nota?.selecionavel !== false)
    .map((nota) => nota.id));
}

/**
 * Retorna somente ids que podem receber a ação em massa no retrato visível.
 *
 * @param {Iterable<number>} selecionadas
 * @param {NotaConferencia[]} notas
 * @returns {Set<number>}
 */
export function idsSelecionadosVisiveis(selecionadas, notas) {
  const visiveis = idsVisiveis(notas);
  return new Set([...selecionadas].filter((id) => visiveis.has(id)));
}
