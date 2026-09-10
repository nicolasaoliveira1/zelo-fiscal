// Central de contratos dos portais de certidões, no Diagnóstico.
//
// Só lê o estado persistido e reage a ação explícita do admin: nada aqui
// dispara recon, cria baseline ou promove versão durante a inicialização.
//
// Duas regras que a tela precisa manter, e que os testes cobrem:
// - id interno NUNCA aparece na tela (vai em data-attribute, para a ação);
// - erro fica NA LINHA do incidente, não numa faixa global.

import { showToast } from './toasts.js';

const ROTULO_ESTADO = {
  compativel: 'Estrutura compatível',
  autoajustado: 'Seletor autoajustado',
  bloqueado: 'Mudança aguardando revisão',
  desconhecido: 'Sem contrato ativo',
  sem_contrato: 'Sem contrato ativo',
};

const ROTULO_RESULTADO_RECON = {
  compativel: 'Nada mudou no portal.',
  autoajustado: 'Um seletor foi autoajustado.',
  bloqueado: 'O portal mudou: revise o incidente aberto.',
  desconhecido: 'Não foi possível observar o portal agora.',
  adiado: 'Há uma emissão em curso; o recon foi adiado.',
  sem_contrato: 'Este portal ainda não tem contrato ativo.',
};

export function rotuloEstado(estado) {
  return ROTULO_ESTADO[estado] || ROTULO_ESTADO.desconhecido;
}

export function rotuloResultadoRecon(resultado) {
  return ROTULO_RESULTADO_RECON[resultado] || ROTULO_RESULTADO_RECON.desconhecido;
}

function esc(valor) {
  const div = document.createElement('div');
  div.textContent = valor === null || valor === undefined ? '' : String(valor);
  // textContent -> innerHTML escapa &, < e >, mas NÃO as aspas. Metade destes
  // valores (fluxo, alvo, estado, severidade, id) entra dentro de atributos com
  // aspas duplas, onde uma aspa solta fecha o atributo e injeta markup.
  return div.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function dataHora(iso) {
  if (!iso) return '—';
  const data = new Date(iso);
  return Number.isNaN(data.getTime()) ? esc(iso) : data.toLocaleString('pt-BR');
}

function versaoLegivel(alvo) {
  return alvo.versao === null || alvo.versao === undefined ? '—' : `v${alvo.versao}`;
}

function incidenteHtml(incidente) {
  return `
    <div class="zl-contrato-incidente" data-incidente="${esc(incidente.id)}">
      <div class="zl-contrato-linha">
        <div class="zl-contrato-campo">
          <strong>${esc(incidente.mensagem)}</strong>
          <span class="zl-contrato-meta">
            ${esc(incidente.elemento || 'estrutura da página')}
            · visto pela primeira vez em ${dataHora(incidente.primeira_observacao_em)}
          </span>
        </div>
        <span class="badge rounded-pill border" data-severidade="${esc(incidente.severidade)}">
          ${esc(incidente.classificacao)}
        </span>
        <div class="d-flex gap-2">
          <button type="button" class="btn btn-sm btn-soft-primary" data-acao="aceitar">
            Revisar e ativar
          </button>
          <button type="button" class="btn btn-sm btn-ghost" data-acao="rejeitar">
            Manter a versão atual
          </button>
        </div>
      </div>
      <div class="zl-contrato-erro" role="alert"></div>
    </div>`;
}

function historicoHtml(alvo) {
  if (!alvo.historico || !alvo.historico.length) return '';
  const linhas = alvo.historico.map((versao) => {
    const origem = versao.origem === 'sistema' ? 'autoajuste' : 'revisão humana';
    const restaurar = versao.ativa
      ? '<span class="zl-contrato-meta">versão ativa</span>'
      : `<button type="button" class="btn btn-sm btn-ghost"
             data-acao="restaurar" data-contrato="${esc(versao.id)}">Restaurar</button>`;
    return `
      <li class="zl-contrato-linha py-1">
        <span class="zl-contrato-campo">
          <strong>v${esc(versao.versao)} · ${esc(origem)}</strong>
          <span class="zl-contrato-meta">${dataHora(versao.ativado_em || versao.criado_em)}</span>
        </span>
        ${restaurar}
      </li>`;
  }).join('');
  return `<ul class="list-unstyled mb-0 mt-2">${linhas}</ul>`;
}

export function alvoHtml(alvo) {
  const acoes = alvo.pode_criar_baseline
    ? `<button type="button" class="btn btn-sm btn-primary" data-acao="baseline">
         Observar o portal e ativar
       </button>`
    : `<button type="button" class="btn btn-sm btn-soft-primary" data-acao="recon">
         Verificar agora
       </button>
       <button type="button" class="btn btn-sm btn-ghost" data-acao="descartar">
         Descartar contrato
       </button>`;
  const incidentes = (alvo.incidentes || []).map(incidenteHtml).join('');
  return `
    <div class="mb-3" data-fluxo="${esc(alvo.fluxo)}" data-alvo="${esc(alvo.alvo)}">
      <div class="zl-contrato-faixa d-flex flex-wrap align-items-center gap-2"
           data-estado="${esc(alvo.estado)}">
        <span class="estado">${esc(alvo.nome)} — ${esc(rotuloEstado(alvo.estado))}</span>
        <span class="zl-contrato-meta">
          ${esc(versaoLegivel(alvo))} · ativa desde ${dataHora(alvo.ativado_em)}
        </span>
        <span class="ms-auto d-flex gap-2">${acoes}</span>
      </div>
      <div class="zl-contrato-erro mt-1" role="alert" data-erro-alvo></div>
      ${incidentes}
      ${historicoHtml(alvo)}
    </div>`;
}

export function renderizarAlvos(payload, root = document) {
  const container = root.querySelector('#contratos-portais');
  if (!container) return;
  const alvos = (payload && payload.alvos) || [];
  container.innerHTML = alvos.length
    ? alvos.map(alvoHtml).join('')
    : '<p class="text-muted mb-0">Nenhum portal com contrato adaptativo.</p>';
}

function mostrarErro(elemento, mensagem) {
  const linha = elemento.closest('.zl-contrato-incidente')
    || elemento.closest('[data-fluxo]');
  const destino = linha
    && (linha.querySelector('.zl-contrato-erro') || linha);
  if (destino) destino.textContent = mensagem;
}

async function enviar(url, corpo) {
  const resposta = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(corpo || {}),
  });
  const dados = await resposta.json().catch(() => ({}));
  if (!resposta.ok) {
    const erro = new Error(dados.message || 'A ação não pôde ser concluída.');
    erro.status = resposta.status;
    throw erro;
  }
  return dados;
}

export async function inicializarContratosPortais(opcoes = {}) {
  const root = opcoes.root || document;
  const base = opcoes.base || '/diagnostico/contratos-portais';
  const confirmar = opcoes.confirmar || ((mensagem) => window.confirm(mensagem));
  const container = root.querySelector('#contratos-portais');
  if (!container) return;

  async function recarregar() {
    const resposta = await fetch(base);
    renderizarAlvos(await resposta.json(), root);
  }

  container.addEventListener('click', async (evento) => {
    const botao = evento.target.closest('[data-acao]');
    if (!botao) return;
    const bloco = botao.closest('[data-fluxo]');
    const fluxo = bloco && bloco.dataset.fluxo;
    const alvo = bloco && bloco.dataset.alvo;
    const acao = botao.dataset.acao;
    const incidente = botao.closest('[data-incidente]');

    // Promover contrato muda o que a automação vai obedecer: confirmação
    // explícita, nunca um clique só.
    if (acao === 'baseline' && !confirmar(
      'O portal será aberto e observado agora. Se a tela tiver os controles '
      + 'declarados, ela vira a versão ativa e a automação passa a obedecê-la.')) return;
    if (acao === 'aceitar' && !confirmar(
      'Ativar a estrutura observada agora? O portal será observado de novo antes.')) return;
    if (acao === 'restaurar' && !confirmar(
      'Restaurar esta versão como ativa?')) return;
    if (acao === 'descartar' && !confirmar(
      'Descartar o contrato deste portal? A automação volta a usar os seletores '
      + 'fixos do código até você ativar uma versão nova.')) return;

    botao.disabled = true;
    mostrarErro(botao, '');
    try {
      let aviso = null;
      if (acao === 'baseline') {
        await enviar(`${base}/${fluxo}/${alvo}/baseline`, {});
        aviso = ['Baseline ativada.', 'success'];
      } else if (acao === 'recon') {
        const dados = await enviar(`${base}/${fluxo}/${alvo}/recon`, {});
        aviso = [rotuloResultadoRecon(dados.resultado), 'info'];
      } else if (acao === 'aceitar') {
        await enviar(
          `${base}/incidentes/${incidente.dataset.incidente}/aceitar`,
          { confirmado: true });
        aviso = ['Contrato atualizado.', 'success'];
      } else if (acao === 'rejeitar') {
        await enviar(
          `${base}/incidentes/${incidente.dataset.incidente}/rejeitar`, {});
        aviso = ['Mudança recusada; a versão atual continua.', 'info'];
      } else if (acao === 'restaurar') {
        await enviar(
          `${base}/${botao.dataset.contrato}/restaurar`, { confirmado: true });
        aviso = ['Versão restaurada.', 'success'];
      } else if (acao === 'descartar') {
        await enviar(`${base}/${fluxo}/${alvo}/descartar`, { confirmado: true });
        aviso = ['Contrato descartado; a automação voltou ao mapa fixo.', 'info'];
      }
      // Estado primeiro, aviso depois: a lista não pode deixar de refletir o
      // que já foi gravado porque a notificação falhou.
      await recarregar();
      if (aviso) showToast(aviso[0], aviso[1]);
    } catch (erro) {
      // Erro não expira sozinho: fica na linha até a próxima ação.
      mostrarErro(botao, erro.message);
      botao.disabled = false;
    }
  });

  await recarregar();
}
