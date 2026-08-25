// @ts-check

// Bloco "Dados da Receita" no detalhe da empresa (spec 08, DATA-01.9/DATA-02.9).
//
// Duas acoes, ambas do operador: rechecar a empresa agora e aceitar um valor
// divergente. Aceitar NUNCA acontece sozinho — o backend so sinaliza a
// divergencia, porque trocar a cidade em silencio quebraria o "Abrir Site"
// municipal, que casa por string.
import { showToast } from './toasts.js';

/**
 * Envelope comum das ações de espelho da Receita.
 *
 * @typedef {Object} RespostaReceita
 * @property {string=} message
 * @property {string=} request_id
 * @property {string=} valor
 * @property {string[]=} preenchidos
 * @property {unknown[]=} divergencias
 */

/**
 * @param {HTMLButtonElement} botao
 * @param {string} rotulo
 * @param {() => Promise<unknown>} acao
 * @returns {Promise<unknown>}
 */
function comCarregando(botao, rotulo, acao) {
    const original = botao.textContent;
    botao.disabled = true;
    botao.textContent = rotulo;
    return acao().finally(() => {
        botao.disabled = false;
        botao.textContent = original;
    });
}

/**
 * Envia uma ação para o espelho da Receita e preserva o status HTTP.
 *
 * @param {string} url
 * @param {Record<string, unknown>=} corpo
 * @returns {Promise<{ok: boolean, dados: RespostaReceita}>}
 */
async function chamar(url, corpo) {
    const resp = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(corpo || {}),
    });
    const dados = await resp.json().catch(() => ({}));
    return { ok: resp.ok, dados };
}

/**
 * @param {HTMLButtonElement} botao
 * @param {string} empresaId
 * @returns {Promise<void>}
 */
async function atualizar(botao, empresaId) {
    const { ok, dados } = await chamar(`/empresa/${empresaId}/receita/atualizar`);
    if (!ok) {
        showToast(dados.message || 'Nao foi possivel consultar a Receita.', 'warning');
        return;
    }

    const partes = [];
    if (dados.preenchidos?.length) {
        partes.push(`preenchido: ${dados.preenchidos.join(', ')}`);
    }
    if (dados.divergencias?.length) {
        partes.push(`${dados.divergencias.length} divergencia(s) para conferir`);
    }
    showToast(partes.length
        ? `Dados atualizados (${partes.join('; ')}).`
        : 'Dados atualizados — nada mudou no cadastro.', 'success');

    // Recarrega para o bloco refletir o novo espelho e as divergencias. A tela
    // e server-rendered; remontar no cliente duplicaria o template.
    setTimeout(() => window.location.reload(), 900);
}

/**
 * @param {HTMLButtonElement} botao
 * @param {string} empresaId
 * @returns {Promise<void>}
 */
async function aceitar(botao, empresaId) {
    const campo = botao.dataset.campo;
    const { ok, dados } = await chamar(`/empresa/${empresaId}/receita/aceitar`, { campo });
    if (!ok) {
        showToast(dados.message || `Nao foi possivel atualizar ${campo}.`, 'warning');
        return;
    }
    showToast(`${campo} atualizado para "${dados.valor}".`, 'success');
    setTimeout(() => window.location.reload(), 900);
}

document.addEventListener('DOMContentLoaded', () => {
    const card = document.getElementById('card-receita');
    if (!card) return;
    const empresaId = card.dataset.empresaId;
    if (!empresaId) return;

    const btnAtualizar = /** @type {HTMLButtonElement | null} */ (
        document.getElementById('btn-atualizar-receita'));
    if (btnAtualizar) {
        btnAtualizar.addEventListener('click', () => comCarregando(
            btnAtualizar, 'Consultando...', () => atualizar(btnAtualizar, empresaId)));
    }

    card.querySelectorAll('.btn-aceitar-receita').forEach((elemento) => {
        const botao = /** @type {HTMLButtonElement} */ (elemento);
        botao.addEventListener('click', () => comCarregando(
            botao, 'Aplicando...', () => aceitar(botao, empresaId)));
    });
});
