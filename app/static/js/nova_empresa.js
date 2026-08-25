// @ts-check

// Buscar dados da Receita na tela de nova empresa (spec 08, DATA-01.3).
//
// A consulta acontece ANTES de salvar, de proposito: o operador ve o que veio e
// corrige se precisar. O POST de cadastro nunca toca a rede (DATA-01.4).
import { showToast } from './toasts.js';
import { marcarInvalido, validar } from './campos.js';
import { digito_verificador_ok } from './validacao_cnpj.js';

/**
 * Campos relevantes devolvidos pela consulta da Receita.
 *
 * @typedef {Object} DadosReceita
 * @property {string=} nome
 * @property {string=} estado
 * @property {string=} cidade
 * @property {string=} razao_social
 * @property {string=} nome_fantasia
 * @property {string=} situacao
 * @property {string=} logradouro
 * @property {string=} bairro
 * @property {string=} cep
 * @property {string=} cnae_descricao
 * @property {boolean=} ativa
 */

/** @type {Array<[string, keyof DadosReceita]>} */
const CAMPOS_PREVIA = [
    ['Razão social', 'razao_social'],
    ['Nome fantasia', 'nome_fantasia'],
    ['Situação', 'situacao'],
    ['Endereço', 'logradouro'],
    ['Bairro', 'bairro'],
    ['CEP', 'cep'],
    ['Atividade', 'cnae_descricao'],
];

/**
 * @param {HTMLInputElement | null} elemento
 * @param {string | undefined} valor
 * @returns {boolean}
 */
function preencherSeVazio(elemento, valor) {
    // Nunca sobrescreve o que o operador ja digitou — mesma regra do backend
    // (DATA-01.9): campo preenchido e decisao dele, nao da API.
    if (!elemento || !valor) return false;
    if ((elemento.value || '').trim()) return false;
    elemento.value = valor;
    return true;
}

/**
 * @param {HTMLSelectElement | null} select
 * @param {string | undefined} valor
 * @returns {boolean}
 */
function selecionarOpcao(select, valor) {
    if (!select || !valor) return false;
    const alvo = String(valor).trim().toUpperCase();
    for (const opcao of Array.from(select.options)) {
        const texto = (opcao.textContent || '').trim().toUpperCase();
        if (opcao.value.trim().toUpperCase() === alvo || texto === alvo) {
            select.value = opcao.value;
            select.dispatchEvent(new Event('change'));
            return true;
        }
    }
    return false;
}

/**
 * @param {HTMLElement} caixa
 * @param {DadosReceita} dados
 * @returns {void}
 */
function renderizarPrevia(caixa, dados) {
    const linhas = CAMPOS_PREVIA
        .filter(([, chave]) => dados[chave])
        .map(([rotulo, chave]) => {
            const dt = document.createElement('dt');
            dt.className = 'col-5 col-sm-4 text-body-secondary fw-normal';
            dt.textContent = rotulo;
            const dd = document.createElement('dd');
            dd.className = 'col-7 col-sm-8 mb-1';
            dd.textContent = String(dados[chave]);
            return [dt, dd];
        });

    caixa.replaceChildren();
    if (!linhas.length) return;

    const dl = document.createElement('dl');
    dl.className = 'row mb-0 small';
    linhas.flat().forEach((no) => dl.appendChild(no));

    if (dados.ativa === false) {
        // Unico destaque colorido do bloco: a situacao. Usa o `alert` que esta
        // mesma tela ja usa nas flash messages — sem classe nova.
        const aviso = document.createElement('div');
        aviso.className = 'alert alert-warning py-2 px-3 small mb-2';
        aviso.textContent = `Situação na Receita: ${dados.situacao || 'não ativa'}. `
            + 'O cadastro é permitido, mas ela ficará fora do lote automático.';
        caixa.appendChild(aviso);
    }
    caixa.appendChild(dl);
    caixa.classList.remove('d-none');
}

/**
 * Consulta a Receita e preenche apenas campos vazios da tela.
 *
 * @param {HTMLButtonElement} botao
 * @param {HTMLElement} previa
 * @returns {Promise<void>}
 */
async function buscarDados(botao, previa) {
    const campoCnpj = /** @type {HTMLInputElement | null} */ (document.getElementById('cnpj'));
    const cnpj = (campoCnpj?.value || '').trim();
    if (!cnpj) {
        // A-20: o erro pertence AO CAMPO. Toast some em 6s e nao diz qual campo.
        marcarInvalido(campoCnpj, 'Informe o CNPJ para consultar a Receita.');
        campoCnpj?.focus();
        return;
    }

    const textoOriginal = botao.textContent;
    botao.disabled = true;
    botao.textContent = 'Buscando...';

    try {
        const resp = await fetch('/empresa/receita/consultar', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ cnpj }),
        });
        const corpo = await resp.json().catch(() => ({}));

        if (!resp.ok) {
            // Erro da API nao pode quebrar a tela: o cadastro manual segue valendo.
            showToast(corpo.message || 'Não foi possível consultar a Receita.', 'warning');
            return;
        }

        const dados = corpo.dados || {};
        const preenchidos = [];
        const campoNome = /** @type {HTMLInputElement | null} */ (document.getElementById('nome'));
        const campoEstado = /** @type {HTMLSelectElement | null} */ (document.getElementById('estado'));
        const campoCidade = /** @type {HTMLSelectElement | null} */ (document.getElementById('cidade'));
        if (preencherSeVazio(campoNome, dados.nome)) {
            preenchidos.push('nome');
        }
        if (selecionarOpcao(campoEstado, dados.estado)) {
            preenchidos.push('estado');
        }
        if (selecionarOpcao(campoCidade, dados.cidade)) {
            preenchidos.push('cidade');
        } else if (dados.cidade) {
            showToast(`A Receita informa a cidade "${dados.cidade}", que não está `
                + 'cadastrada nos municípios. Selecione manualmente.', 'warning');
        }

        renderizarPrevia(previa, dados);
        showToast(preenchidos.length
            ? 'Dados da Receita preenchidos. Confira antes de cadastrar.'
            : 'Dados da Receita carregados — os campos já preenchidos foram mantidos.',
        'success');
    } catch (erro) {
        showToast('Falha ao consultar a Receita. Cadastre normalmente.', 'warning');
    } finally {
        botao.disabled = false;
        botao.textContent = textoOriginal;
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const botao = /** @type {HTMLButtonElement | null} */ (
        document.getElementById('btn-buscar-receita'));
    const previa = document.getElementById('previa-receita');
    if (!botao || !previa) return;
    botao.addEventListener('click', () => buscarDados(botao, previa));
});

/* ── Validação antes de enviar (A-20) ────────────────────────────────────
   Sem isto o caminho do erro era: POST, o servidor dá flash e REDIRECIONA,
   e o redirect descarta tudo o que foi digitado — o operador reescreve o
   formulário inteiro para trocar um dígito. Validar aqui faz os quatro erros
   comuns nunca chegarem lá.

   A regra do CNPJ é a MESMA do servidor (dígito verificador, não contagem de
   14 dígitos): contar dígitos deixava passar erro de digitação, e é por isso
   que o backend passou a conferir o DV. Cliente e servidor divergirem seria
   pior que não validar — o operador veria "ok" aqui e "inválido" lá. */
document.addEventListener('DOMContentLoaded', () => {
    const form = /** @type {HTMLFormElement | null} */ (
        document.querySelector('form[action*="adicionar"]'));
    if (!form) return;

    form.addEventListener('submit', (evento) => {
        const nome = /** @type {HTMLInputElement | null} */ (form.querySelector('#nome'));
        const cnpj = /** @type {HTMLInputElement | null} */ (form.querySelector('#cnpj'));
        const estado = /** @type {HTMLSelectElement | null} */ (form.querySelector('#estado'));
        const cidade = /** @type {HTMLSelectElement | null} */ (form.querySelector('#cidade'));
        const digitos = (cnpj?.value || '').replace(/\D/g, '');

        const tudoCerto = validar([
            [nome, nome && !nome.value.trim() ? 'Informe o nome da empresa.' : ''],
            [cnpj, !digitos
                ? 'Informe o CNPJ.'
                : (digitos.length !== 14
                    ? 'O CNPJ precisa ter 14 dígitos.'
                    : (!digito_verificador_ok(digitos)
                        ? 'O dígito verificador não confere. Confira a digitação.'
                        : ''))],
            [estado, estado && !estado.value ? 'Escolha o estado.' : ''],
            [cidade, cidade && !cidade.value ? 'Escolha a cidade.' : ''],
        ]);

        if (!tudoCerto) evento.preventDefault();
    });
});
