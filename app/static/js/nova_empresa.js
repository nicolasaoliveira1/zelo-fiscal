// Buscar dados da Receita na tela de nova empresa (spec 08, DATA-01.3).
//
// A consulta acontece ANTES de salvar, de proposito: o operador ve o que veio e
// corrige se precisar. O POST de cadastro nunca toca a rede (DATA-01.4).
import { showToast } from './toasts.js';

const CAMPOS_PREVIA = [
    ['Razão social', 'razao_social'],
    ['Nome fantasia', 'nome_fantasia'],
    ['Situação', 'situacao'],
    ['Endereço', 'logradouro'],
    ['Bairro', 'bairro'],
    ['CEP', 'cep'],
    ['Atividade', 'cnae_descricao'],
];

function preencherSeVazio(elemento, valor) {
    // Nunca sobrescreve o que o operador ja digitou — mesma regra do backend
    // (DATA-01.9): campo preenchido e decisao dele, nao da API.
    if (!elemento || !valor) return false;
    if ((elemento.value || '').trim()) return false;
    elemento.value = valor;
    return true;
}

function selecionarOpcao(select, valor) {
    if (!select || !valor) return false;
    const alvo = String(valor).trim().toUpperCase();
    for (const opcao of select.options) {
        const texto = (opcao.textContent || '').trim().toUpperCase();
        if (opcao.value.trim().toUpperCase() === alvo || texto === alvo) {
            select.value = opcao.value;
            select.dispatchEvent(new Event('change'));
            return true;
        }
    }
    return false;
}

function renderizarPrevia(caixa, dados) {
    const linhas = CAMPOS_PREVIA
        .filter(([, chave]) => dados[chave])
        .map(([rotulo, chave]) => {
            const dt = document.createElement('dt');
            dt.className = 'col-5 col-sm-4 text-body-secondary fw-normal';
            dt.textContent = rotulo;
            const dd = document.createElement('dd');
            dd.className = 'col-7 col-sm-8 mb-1';
            dd.textContent = dados[chave];
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

async function buscarDados(botao, previa) {
    const campoCnpj = document.getElementById('cnpj');
    const cnpj = (campoCnpj?.value || '').trim();
    if (!cnpj) {
        showToast('Informe o CNPJ antes de buscar.', 'warning');
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
        if (preencherSeVazio(document.getElementById('nome'), dados.nome)) {
            preenchidos.push('nome');
        }
        if (selecionarOpcao(document.getElementById('estado'), dados.estado)) {
            preenchidos.push('estado');
        }
        if (selecionarOpcao(document.getElementById('cidade'), dados.cidade)) {
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
    const botao = document.getElementById('btn-buscar-receita');
    const previa = document.getElementById('previa-receita');
    if (!botao || !previa) return;
    botao.addEventListener('click', () => buscarDados(botao, previa));
});
