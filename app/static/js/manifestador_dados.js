// @ts-check

/** @typedef {{ name: string, webkitRelativePath?: string }} ArquivoEntrada */
/** @typedef {{ caminho: string, arquivo: { size: number } }} ItemArquivo */

/**
 * Estrutura acumulada da importação de XMLs. Os itens das listas variam por
 * categoria e são mantidos opacos neste módulo de apresentação.
 *
 * @typedef {Object} BalancoImportacao
 * @property {number} total_lidas
 * @property {unknown[]} aceitas
 * @property {unknown[]} dv_invalido
 * @property {unknown[]} competencia_invalida
 * @property {unknown[]} duplicatas
 * @property {unknown[]} sem_empresa
 * @property {unknown[]} nao_e_nfe
 * @property {unknown[]} fora_do_prazo
 */

/** @type {Array<[number, number, string]>} */
const CAMPOS_CHAVE = [
  [0, 2, 'f-cuf'], [2, 6, 'f-aamm'], [6, 20, 'f-cnpj'], [20, 22, 'f-mod'],
  [22, 25, 'f-serie'], [25, 34, 'f-nnf'], [34, 35, 'f-tp'], [35, 43, 'f-cnf'],
  [43, 44, 'f-dv'],
];

/** @typedef {'aceitas'|'dv_invalido'|'competencia_invalida'|'duplicatas'|'sem_empresa'|'nao_e_nfe'|'fora_do_prazo'} CampoListaBalanco */

/** @type {CampoListaBalanco[]} */
const CAMPOS_BALANCO = [
  'aceitas', 'dv_invalido', 'competencia_invalida', 'duplicatas',
  'sem_empresa', 'nao_e_nfe', 'fora_do_prazo',
];

/**
 * Escapa texto usado em conteúdo HTML e em atributos delimitados por aspas.
 *
 * @param {unknown} texto
 * @returns {string}
 */
export function escapar_html(texto) {
  return String(texto ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/**
 * Segmenta a chave de acesso nos campos que têm significado fiscal.
 *
 * @param {string | null | undefined} chave
 * @returns {string}
 */
export function chave_segmentada(chave) {
  if (!chave || chave.length !== 44) return escapar_html(chave || '');
  return CAMPOS_CHAVE
    .map(([inicio, fim, classe]) =>
      `<span class="${classe}">${escapar_html(chave.slice(inicio, fim))}</span>`)
    .join('');
}

/**
 * Cria o acumulador vazio para uma importação de XMLs.
 *
 * @returns {BalancoImportacao}
 */
export function balanco_vazio() {
  return {
    total_lidas: 0,
    aceitas: [],
    dv_invalido: [],
    competencia_invalida: [],
    duplicatas: [],
    sem_empresa: [],
    nao_e_nfe: [],
    fora_do_prazo: [],
  };
}

/**
 * Soma um bloco de importação sem perder as categorias já acumuladas.
 *
 * @param {BalancoImportacao} acumulado
 * @param {Partial<BalancoImportacao> | null | undefined} parcial
 * @returns {void}
 */
export function somar_balanco(acumulado, parcial) {
  acumulado.total_lidas += Number(parcial?.total_lidas || 0);
  for (const campo of CAMPOS_BALANCO) {
    const itens = parcial?.[campo];
    if (Array.isArray(itens)) acumulado[campo].push(...itens);
  }
}

/**
 * Agrupa apenas XMLs pela pasta de origem escolhida pelo operador.
 *
 * @param {ArrayLike<ArquivoEntrada> | Iterable<ArquivoEntrada>} lista
 * @returns {Map<string, ItemArquivo[]>}
 */
export function agrupar_arquivos_xml(lista) {
  const grupos = new Map();
  Array.from(lista).forEach((arquivo) => {
    if (!/\.xml$/i.test(arquivo.name)) return;
    const caminho = arquivo.webkitRelativePath || arquivo.name;
    const raiz = caminho.includes('/') ? caminho.split('/')[0] : 'Arquivos avulsos';
    if (!grupos.has(raiz)) grupos.set(raiz, []);
    grupos.get(raiz).push({ caminho, arquivo });
  });
  return grupos;
}
