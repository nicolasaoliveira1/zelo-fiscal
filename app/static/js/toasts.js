// @ts-check

// Sistema de toasts empilhados de Certidões.
// Extraído de certidoes.js (spec 05, REFA-03) como módulo ES autocontido:
// mantem seu proprio estado/DOM e expoe apenas showToast. Carrega o elemento
// #toastStack no import (modulo deferido: DOM ja parseado).

/** @typedef {'success'|'error'|'warning'|'info'} TipoToast */
/** @typedef {{ el: HTMLElement, timer: number | undefined, leaving: boolean, persistente: boolean }} Toast */

const toastStack = document.getElementById('toastStack');

// ---- Pilha de toasts acumulativos -------------------------------
/** @type {Toast[]} */
const toasts = [];          // index 0 = mais novo (na frente)
let stackHovered = false;
/** @type {number | undefined} */
let leaveTimer;
const PEEK = 10;            // px que cada toast de tras "espia" (recolhido)
const GAP = 9;             // espaco entre toasts (expandido)
const MAX_PEEK = 3;        // quantos toasts de tras ficam visiveis recolhidos
const MAX_TOASTS = 6;      // limite na pilha
const TOAST_DELAY = 6000;

/**
 * @param {TipoToast} type
 * @returns {string}
 */
function toastClass(type) {
    const classes = {
        success: 'is-success',
        error: 'is-danger',
        warning: 'is-warning',
        info: 'is-info',
    };
    return classes[type] || classes.info;
}

function reflow() {
    let acumulado = 0;
    toasts.forEach((t, i) => {
        if (t.leaving) return;
        let y, escala, opacidade;
        if (stackHovered) {
            y = -acumulado;
            escala = 1;
            opacidade = 1;
            acumulado += t.el.offsetHeight + GAP;
        } else {
            const nivel = Math.min(i, MAX_PEEK);
            y = -(nivel * PEEK);
            escala = 1 - nivel * 0.05;
            opacidade = i > MAX_PEEK ? 0 : 1;
        }
        t.el.style.transform = `translateY(${y}px) scale(${escala})`;
        t.el.style.opacity = String(opacidade);
        t.el.style.zIndex = String(1000 - i);
        t.el.style.pointerEvents = opacidade === 0 ? 'none' : 'auto';
    });
}

/** @param {Toast} t */
function removeToast(t) {
    if (t.leaving) return;
    t.leaving = true;
    clearTimeout(t.timer);
    t.el.style.transform = 'translateX(120%)';
    t.el.style.opacity = '0';
    setTimeout(() => {
        const idx = toasts.indexOf(t);
        if (idx !== -1) toasts.splice(idx, 1);
        t.el.remove();
        reflow();
    }, 350);
}

/** @param {Toast} t */
function scheduleDismiss(t) {
    clearTimeout(t.timer);
    if (stackHovered) return;   // nao some enquanto o mouse esta na pilha
    // A-21: ERRO NAO EXPIRA. 66 das 98 chamadas do sistema sao de erro, e
    // ate aqui a principal via de erro era efemera e irrecuperavel: passou,
    // perdeu, sem historico em lugar nenhum da interface. Erro sai por
    // dispensa explicita — o operador decide quando ja leu.
    if (t.persistente) return;
    t.timer = setTimeout(() => removeToast(t), TOAST_DELAY);
}

/**
 * Exibe uma mensagem na pilha global de toasts.
 *
 * @param {string} message
 * @param {TipoToast} [type='success']
 * @returns {void}
 */
export function showToast(message, type = 'success') {
    if (!toastStack) return;

    const el = document.createElement('div');
    el.className = 'stk-toast ' + toastClass(type);
    // A-22: 'alert' e assertivo e INTERROMPE a leitura em curso. Certo para os
    // 66 toasts de erro do sistema; excessivo para os 16 de sucesso e os 8 de
    // info, ainda mais com a pilha aceitando 6 ao mesmo tempo — viraria uma
    // sequencia de interrupcoes. 'status' e polite: espera a pausa.
    el.setAttribute('role', type === 'error' ? 'alert' : 'status');

    const body = document.createElement('div');
    body.className = 'stk-body';
    body.textContent = message;

    const close = document.createElement('button');
    close.className = 'stk-close';
    close.type = 'button';
    close.setAttribute('aria-label', 'Fechar');
    close.innerHTML = '&times;';

    el.appendChild(body);
    el.appendChild(close);

    // estado inicial: entra de baixo
    el.style.transform = 'translateY(24px) scale(.92)';
    el.style.opacity = '0';
    toastStack.appendChild(el);

    const t = { el, timer: undefined, leaving: false, persistente: type === 'error' };
    toasts.unshift(t);
    close.addEventListener('click', () => removeToast(t));

    // remove os mais antigos se passar do limite
    while (toasts.filter((x) => !x.leaving).length > MAX_TOASTS) {
        const antigo = [...toasts].reverse().find((x) => !x.leaving);
        if (antigo) removeToast(antigo); else break;
    }

    requestAnimationFrame(reflow);
    scheduleDismiss(t);
}

if (toastStack) {
    // A pilha pausava so por mouseenter: quem opera por teclado nao tinha como
    // segurar a mensagem. focusin/focusout cobrem o mesmo pelo teclado.
    ['mouseenter', 'focusin'].forEach((evt) => toastStack.addEventListener(evt, () => {
        clearTimeout(leaveTimer);
        stackHovered = true;
        toasts.forEach((t) => clearTimeout(t.timer));
        reflow();
    }));
    ['mouseleave', 'focusout'].forEach((evt) => toastStack.addEventListener(evt, () => {
        clearTimeout(leaveTimer);
        leaveTimer = setTimeout(() => {
            stackHovered = false;
            reflow();
            toasts.forEach(scheduleDismiss);
        }, 150);
    }));
}
