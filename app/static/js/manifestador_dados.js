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

/**
 * Item de vencimento vindo de `/manifestador/cofre`.
 *
 * @typedef {Object} ItemVencimento
 * @property {number} empresa_id
 * @property {string} empresa_nome
 * @property {string} not_after
 * @property {number} dias_restantes
 * @property {'vencido'|'vencendo'} causa
 */

/**
 * A frase da régua do cofre, em HTML.
 *
 * A régua tem UMA linha e responde à pergunta do dia. Nos dias em que há
 * certificado vencido ou vencendo, a pergunta é essa — é o que o operador veio
 * ver quando chegou pelo cartão da Visão Geral, que fala de vencimento. Nos
 * dias calmos ela volta ao pré-voo ("N de M empresas prontas"), que é o que a
 * régua sempre disse e o que a manifestação de fato exige.
 *
 * Os dois números NUNCA saem da mesma população, e por isso nunca aparecem
 * juntos: `prontas/total` conta empresas com certificado cadastrado, e
 * `vencendo/com_vencimento` conta só aquelas cujo vencimento é CONHECIDO. Somar
 * as duas leituras numa frase só transformaria "não sei" em "está em dia" — o
 * erro que o cartão da Visão Geral evita de propósito.
 *
 * Devolve HTML porque os números vão em `.manif-num`; só inteiros entram nele,
 * nunca texto de origem externa.
 *
 * @param {Object} estado
 * @param {boolean} estado.inventariado
 * @param {number} estado.prontas
 * @param {number} estado.total  empresas com certificado cadastrado
 * @param {ItemVencimento[]} estado.itens
 * @param {number} estado.com_vencimento
 * @param {number} estado.janela_dias
 * @returns {string}
 */
export function linha_do_cofre({
  inventariado, prontas, total, itens, com_vencimento, janela_dias,
}) {
  const num = (/** @type {number} */ valor) => `<strong class="manif-num">${Number(valor) || 0}</strong>`;

  /* Cofre nunca inventariado não é "0 vencendo": é não saber. A régua diz isso
   * com todas as letras em vez de exibir um zero tranquilizador. */
  if (!inventariado) return 'Cofre nunca inventariado';

  const lista = Array.isArray(itens) ? itens : [];
  const vencidos = lista.filter((item) => item.causa === 'vencido').length;
  const vencendo = lista.length - vencidos;

  /* Vencido e vencendo são estados diferentes com ações diferentes — o primeiro
   * já parou a manifestação daquela empresa, o segundo ainda dá tempo. Quando
   * os dois existem, os dois são ditos; o denominador sai de cena porque a
   * frase já carrega dois números e um terceiro só a embaralha. */
  if (vencidos && vencendo) {
    return `${num(vencidos)} ${vencidos === 1 ? 'vencido' : 'vencidos'} e `
      + `${num(vencendo)} vencendo em ${janela_dias} dias`;
  }
  if (vencidos) {
    return `${num(vencidos)} de ${num(com_vencimento)} `
      + `${vencidos === 1 ? 'certificado vencido' : 'certificados vencidos'}`;
  }
  if (vencendo) {
    return `${num(vencendo)} de ${num(com_vencimento)} `
      + `${vencendo === 1 ? 'vence' : 'vencem'} em ${janela_dias} dias`;
  }

  return `${num(prontas)} de ${num(total)} empresas prontas`;
}

/**
 * O estado vazio da lista de vencimentos do cofre.
 *
 * Lista vazia tem TRÊS causas, e só uma delas é boa notícia. Sem inventário não
 * se sabe nada; com inventário mas sem nenhum `not_after` conhecido também não —
 * `sem_arquivo` e `sem_pasta` não estão "sem vencer". Dizer "nenhum vence nos
 * próximos N dias" nesses dois casos transforma desconhecido em alívio, que é
 * exatamente o que o cartão da Visão Geral evita de propósito (e com as mesmas
 * palavras: as duas telas falam do mesmo dado).
 *
 * @param {Object} estado
 * @param {boolean} estado.inventariado
 * @param {number} estado.com_vencimento  certificados com vencimento CONHECIDO
 * @param {number} estado.janela_dias
 * @returns {{ classe: string, icone: string, texto: string }}
 */
export function vazio_de_vencimentos({ inventariado, com_vencimento, janela_dias }) {
  const conhecidos = Number(com_vencimento) || 0;
  const dias = Number(janela_dias) || 0;

  if (!inventariado) {
    return {
      classe: 'vg-falha',
      icone: 'question-circle',
      texto: 'O cofre ainda não foi inventariado — não sei quais certificados vencem.',
    };
  }
  if (conhecidos === 0) {
    return {
      classe: 'vg-falha',
      icone: 'question-circle',
      texto: 'Nenhum certificado tem vencimento conhecido.',
    };
  }
  if (conhecidos === 1) {
    return {
      classe: 'vg-vazio mb-0',
      icone: 'check2',
      texto: `O único certificado com vencimento conhecido não vence nos próximos ${dias} dias.`,
    };
  }
  return {
    classe: 'vg-vazio mb-0',
    icone: 'check2',
    texto: `Nenhum dos ${conhecidos} certificados vence nos próximos ${dias} dias.`,
  };
}
