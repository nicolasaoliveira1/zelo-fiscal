// Erro de campo, inline (achado A-20 da auditoria de UI/UX).
//
// O sistema tinha 100 campos de entrada e ZERO ocorrencias de is-invalid,
// invalid-feedback ou was-validated: nao existia validacao inline em lugar
// nenhum. O erro de preenchimento chegava por balao nativo do navegador (so
// para `required`), por flash no topo (que perde o vinculo com o campo) ou por
// toast — que ate ha pouco nem aparecia fora da tela de Certidoes.
//
// Onde isso custa mais caro: no cadastro de empresa, porque CNPJ e cidade
// errados desviam a automacao municipal inteira (a cidade e casada por string);
// e nos vinculos da NFS-e, porque documento errado do tomador vira nota fiscal
// para outro cliente, que so se desfaz com cancelamento junto a prefeitura.
//
// Reusa o estado que o Bootstrap ja tem (is-invalid + invalid-feedback) em vez
// de inventar um proprio. O modulo cuida do que o Bootstrap NAO faz: ligar
// campo e mensagem por aria-describedby, marcar aria-invalid, limpar sozinho
// quando o operador corrige, e levar o foco ao primeiro campo com problema.

const SUFIXO = '-erro';

function idDoErro(campo) {
    return (campo.id || campo.name || 'campo') + SUFIXO;
}

/** Devolve (criando se preciso) o elemento de mensagem logo abaixo do campo. */
function caixaDeErro(campo) {
    const id = idDoErro(campo);
    let caixa = document.getElementById(id);
    if (!caixa) {
        caixa = document.createElement('div');
        caixa.id = id;
        caixa.className = 'invalid-feedback zl-erro-campo';
        // depois do campo, ou depois do grupo quando ha addon (input-group)
        const grupo = campo.closest('.input-group');
        const ancora = grupo || campo;
        ancora.parentNode.insertBefore(caixa, ancora.nextSibling);
    }
    return caixa;
}

/**
 * Marca um campo como invalido e escreve a mensagem abaixo dele.
 * A mensagem diz O QUE fazer, nao so o que esta errado.
 */
export function marcarInvalido(campo, mensagem) {
    if (!campo) return;
    const caixa = caixaDeErro(campo);
    caixa.textContent = mensagem;
    campo.classList.add('is-invalid');
    campo.setAttribute('aria-invalid', 'true');

    // preserva um aria-describedby que ja exista (ex.: texto de ajuda)
    const atual = (campo.getAttribute('aria-describedby') || '')
        .split(/\s+/).filter(Boolean).filter((x) => x !== caixa.id);
    campo.setAttribute('aria-describedby', [...atual, caixa.id].join(' '));

    // limpa sozinho assim que o operador mexe: manter o erro na tela depois de
    // corrigido faz o proximo erro de verdade ser ignorado.
    if (!campo.dataset.zlLimpaErro) {
        campo.dataset.zlLimpaErro = '1';
        ['input', 'change'].forEach((evt) =>
            campo.addEventListener(evt, () => limparInvalido(campo)));
    }
}

/** Tira a marca de invalido e some com a mensagem. */
export function limparInvalido(campo) {
    if (!campo || !campo.classList.contains('is-invalid')) return;
    campo.classList.remove('is-invalid');
    campo.removeAttribute('aria-invalid');
    const caixa = document.getElementById(idDoErro(campo));
    if (caixa) {
        caixa.textContent = '';
        const resto = (campo.getAttribute('aria-describedby') || '')
            .split(/\s+/).filter(Boolean).filter((x) => x !== caixa.id);
        if (resto.length) campo.setAttribute('aria-describedby', resto.join(' '));
        else campo.removeAttribute('aria-describedby');
    }
}

/** Limpa todos os campos invalidos de um formulario (ou da pagina). */
export function limparTodos(escopo) {
    (escopo || document).querySelectorAll('.is-invalid').forEach(limparInvalido);
}

/**
 * Valida uma lista de [campo, mensagem] onde a mensagem so vem quando ha erro.
 * Marca todos, leva o foco ao PRIMEIRO e devolve true se esta tudo certo —
 * assim quem chama escreve `if (!validar([...])) return;`.
 *
 * Marca todos de uma vez em vez de parar no primeiro: corrigir um erro,
 * reenviar e descobrir o proximo e o padrao que faz o operador desistir.
 */
export function validar(pares) {
    const invalidos = [];
    pares.forEach(([campo, mensagem]) => {
        if (!campo) return;
        if (mensagem) {
            marcarInvalido(campo, mensagem);
            invalidos.push(campo);
        } else {
            limparInvalido(campo);
        }
    });
    if (invalidos.length) {
        invalidos[0].focus();
        invalidos[0].scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
    return invalidos.length === 0;
}
