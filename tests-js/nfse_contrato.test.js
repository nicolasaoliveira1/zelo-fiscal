import test, { after, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><body></body>', { pretendToBeVisual: true });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.Event = dom.window.Event;
globalThis.requestAnimationFrame = (callback) => setTimeout(callback, 0);
dom.window.HTMLElement.prototype.scrollIntoView = function scrollIntoView() {};

const {
  inicializarContratoNfse,
  montarDadosCandidato,
  opcoesDaOrigem,
  renderizarEstadoContrato,
} = await import('../app/static/js/nfse_contrato.js');
const {
  aplicarGateContrato,
  contratoPermiteAutomatico,
  iniciarEmissao,
} = await import('../app/static/js/nfse.js');

after(() => dom.window.close());

const fontes = [
  { origem: 'fixo', fonte: null, rotulo: 'Valor fixo' },
  { origem: 'nota', fonte: 'documento', rotulo: 'Documento' },
  { origem: 'derivado', fonte: 'data_emissao', rotulo: 'Data de emissão' },
  { origem: 'configuracao', fonte: 'item_nbs', rotulo: 'Item NBS' },
  { origem: 'padrao_portal', fonte: null, rotulo: 'Padrão do portal' },
  { origem: 'intocavel', fonte: null, rotulo: 'Não tocar' },
];

const incidente = {
  id: 8,
  etapa: 'servico',
  tipo: 'controle_novo',
  severidade: 'critica',
  estado: 'aberto',
  campo: {
    rotulo: 'Campo sintético',
    obrigatorio: true,
    chave_esperada: null,
    chave_observada: 'campo.sintetico',
  },
  observacoes: 1,
  opcoes: [{ valor: 'OPCAO-SINTETICA', rotulo: 'Opção sintética' }],
};

function estadoBase(overrides = {}) {
  return {
    ativo: { id: 1, versao: 1, elegivel_automatico: true },
    candidatas: [],
    incidentes: [],
    fontes,
    ...overrides,
  };
}

function markup() {
  return `
    <div id="toastStack"></div>
    <section id="nfseContratoCentral">
      <div id="nfseContratoStatus" role="status" aria-live="polite">
        <div id="nfseContratoStatusTitulo"></div>
        <div id="nfseContratoStatusTexto"></div>
      </div>
      <div id="nfseReconSugestoes" class="d-none"></div>
      <div id="nfseContratoIncidentes"></div>
      <div id="nfseContratoHistorico"></div>
      <div id="nfseReconEstado" role="status" aria-live="polite"></div>
    </section>
    <button id="btnReconContrato" type="button">Recon</button>
    <span id="nfseReconPasses" class="d-none"></span>
    <button id="btnReconDescartar" class="d-none" type="button">Descartar</button>
    <button id="btnDescartarIncidentes" type="button">Descartar incidentes</button>
    <div id="modalValidarContrato"></div>
    <form id="formValidarContrato">
      <select id="nfseNotaValidacao"><option value="" selected disabled>Escolha uma nota…</option></select>
      <div id="nfseValidacaoErro" role="alert"></div>
      <button id="btnValidarContrato" type="submit">Iniciar validação</button>
    </form>
    <script id="dadosNotas" type="application/json">[
      {"id": 10, "status": "pronta", "emitivel": true, "nome_csv": "NOTA SINTÉTICA", "competencia": "08/2026"},
      {"id": 11, "status": "emitida", "emitivel": false, "nome_csv": "FORA DA FILA", "competencia": "08/2026"},
      {"id": 12, "status": "pronta", "emitivel": false, "nome_csv": "PROPOSTA PENDENTE", "competencia": "08/2026"}
    ]</script>`;
}

function resposta(dados, ok = true) {
  return { ok, json: async () => dados };
}

function proximoTurno() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

beforeEach(() => {
  document.body.innerHTML = markup();
});

test('renderiza os quatro estados e mantém texto recebido fora do HTML', () => {
  const estados = [
    // O servidor manda quando ele responde.
    [estadoBase({ estado_visual: 'compativel' }), 'compativel'],
    [estadoBase({ estado_visual: 'bloqueado' }), 'bloqueado'],
    // Fallback para payload antigo, seguindo a MESMA regra do servidor:
    // qualquer incidente pendente bloqueia, inclusive `informativa`. As cópias
    // antigas chamavam isso de "aviso" e mostravam faixa amarela ao lado do
    // rádio do automático desabilitado, sem explicar por quê.
    [estadoBase({ incidentes: [{ ...incidente, severidade: 'informativa' }] }), 'bloqueado'],
    [estadoBase({ ativo: { id: 1, versao: 1, elegivel_automatico: false } }), 'bloqueado'],
    [estadoBase(), 'compativel'],
    [{}, 'desconhecido'],
  ];

  estados.forEach(([estado, esperado]) => {
    renderizarEstadoContrato(estado, document);
    assert.equal(document.getElementById('nfseContratoStatus').dataset.estado, esperado);
  });

  renderizarEstadoContrato(estadoBase({
    incidentes: [{
      ...incidente,
      campo: { ...incidente.campo, rotulo: '<img src=x>sentinela' },
    }],
  }), document);
  assert.equal(document.querySelector('img'), null);
  assert.match(document.getElementById('nfseContratoIncidentes').textContent, /<img src=x>sentinela/);
});

test('a configuração acontece na própria linha, sem modal', async () => {
  const chamadas = [];
  await inicializarContratoNfse({
    root: document,
    fetchImpl: async (url, opcoes) => {
      chamadas.push({ url, opcoes });
      return resposta(estadoBase({ incidentes: [incidente] }));
    },
  });

  const linha = document.querySelector('.nfse-contrato-incidente .nfse-contrato-config');
  assert.ok(linha, 'a linha do incidente traz o formulário embutido');
  assert.equal(linha.dataset.configIncidente, String(incidente.id));

  const origem = linha.querySelector('[data-campo="origem"]');
  const fonte = linha.querySelector('[data-campo="fonte"]');
  const valor = linha.querySelector('[data-campo="valor_fixo"]');

  assert.equal(fonte.hidden, true);
  assert.equal(valor.hidden, true);

  origem.value = 'nota';
  origem.dispatchEvent(new Event('change', { bubbles: true }));
  assert.equal(fonte.hidden, false);
  assert.equal(valor.hidden, true);
  assert.equal(fonte.options[1].textContent, 'Documento');
  assert.equal(fonte.options[1].selected, false);

  origem.value = 'fixo';
  origem.dispatchEvent(new Event('change', { bubbles: true }));
  assert.equal(fonte.hidden, true);
  assert.equal(valor.hidden, false);

  assert.deepEqual(opcoesDaOrigem(fontes, 'nota'), [fontes[1]]);
  assert.equal(chamadas.length, 1);
});

test('valor fixo vira escolha entre as opções declaradas pelo controle', async () => {
  // O operador escolhe "Não", não decora que "Não" é 0.
  const comOpcoes = {
    ...incidente,
    opcoes: [{ valor: '1', rotulo: 'Sim' }, { valor: '0', rotulo: 'Não' }],
  };
  await inicializarContratoNfse({
    root: document,
    fetchImpl: async (url, opcoes) => (opcoes
      ? resposta({}) : resposta(estadoBase({ incidentes: [comOpcoes] }))),
  });

  const valor = document.querySelector('[data-campo="valor_fixo"]');
  assert.equal(valor.tagName, 'SELECT');
  assert.deepEqual(
    [...valor.options].map((o) => [o.value, o.textContent]),
    [['', 'Escolha a opção…'], ['1', 'Sim (1)'], ['0', 'Não (0)']],
  );
  assert.equal(valor.options[1].selected, false);
  // A lista expansível some: existia só para descobrir qual código era qual.
  assert.equal(document.querySelector('.nfse-contrato-opcoes'), null);
});

test('salvar na linha envia só o payload do catálogo', async () => {
  const posts = [];
  await inicializarContratoNfse({
    root: document,
    fetchImpl: async (url, opcoes) => {
      if (opcoes) {
        posts.push({ url, corpo: JSON.parse(opcoes.body) });
        return resposta({ status: 'ok' });
      }
      return resposta(estadoBase({ incidentes: [incidente] }));
    },
  });

  const forma = document.querySelector('.nfse-contrato-config');
  const origem = forma.querySelector('[data-campo="origem"]');
  origem.value = 'fixo';
  origem.dispatchEvent(new Event('change', { bubbles: true }));
  forma.querySelector('[data-campo="valor_fixo"]').value = 'OPCAO-SINTETICA';
  forma.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  await proximoTurno();
  await proximoTurno();
  await proximoTurno();

  assert.equal(posts.length, 1);
  assert.equal(posts[0].url, `/nfse/contrato/incidente/${incidente.id}/configurar`);
  assert.deepEqual(posts[0].corpo, { origem: 'fixo', valor_fixo: 'OPCAO-SINTETICA' });
});

test('origem inválida para na linha e não chega a chamar a rota', async () => {
  const posts = [];
  await inicializarContratoNfse({
    root: document,
    fetchImpl: async (url, opcoes) => {
      if (opcoes) { posts.push(url); return resposta({}); }
      return resposta(estadoBase({ incidentes: [incidente] }));
    },
  });

  document.querySelector('.nfse-contrato-config')
    .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  await proximoTurno();

  assert.equal(posts.length, 0);
  assert.match(document.querySelector('.nfse-contrato-erro').textContent, /origem/i);
});

test('salvar uma linha preserva as escolhas ainda não salvas das outras', async () => {
  // Marcar dez incidentes e ir salvando um a um só é possível se o redesenho
  // que vem depois do Salvar não apagar o que ainda não foi salvo.
  const outro = {
    ...incidente,
    id: 9,
    campo: { ...incidente.campo, chave_observada: 'campo.outro', rotulo: 'Campo outro' },
  };
  await inicializarContratoNfse({
    root: document,
    fetchImpl: async (url, opcoes) => (opcoes
      ? resposta({ status: 'ok' })
      : resposta(estadoBase({ incidentes: [incidente, outro] }))),
  });

  const linhaDe = (id) => document.querySelector(
    `.nfse-contrato-incidente[data-incidente-id="${id}"] .nfse-contrato-config`,
  );

  [incidente.id, outro.id].forEach((id) => {
    const forma = linhaDe(id);
    const origem = forma.querySelector('[data-campo="origem"]');
    origem.value = 'fixo';
    origem.dispatchEvent(new Event('change', { bubbles: true }));
    forma.querySelector('[data-campo="valor_fixo"]').value = 'OPCAO-SINTETICA';
  });

  linhaDe(incidente.id).dispatchEvent(
    new Event('submit', { bubbles: true, cancelable: true }),
  );
  await proximoTurno();
  await proximoTurno();
  await proximoTurno();

  const preservada = linhaDe(outro.id);
  assert.equal(preservada.querySelector('[data-campo="origem"]').value, 'fixo');
  assert.equal(preservada.querySelector('[data-campo="valor_fixo"]').value, 'OPCAO-SINTETICA');
  assert.equal(preservada.querySelector('[data-campo="valor_fixo"]').hidden, false);
  assert.equal(preservada.querySelector('[data-campo="fonte"]').hidden, true);
});

test('incidente configurado oferece desfazer, e a candidata é descartável', async () => {
  const posts = [];
  globalThis.confirm = () => true;
  await inicializarContratoNfse({
    root: document,
    fetchImpl: async (url, opcoes) => {
      if (opcoes) { posts.push(url); return resposta({ reabertos: 2 }); }
      return resposta(estadoBase({
        incidentes: [{ ...incidente, estado: 'configurado', contrato_candidato_id: 9 }],
        candidatas: [{ id: 9, versao: 2, estado: 'candidata' }],
      }));
    },
  });

  assert.equal(document.querySelector('.nfse-contrato-config'), null);
  // A linha oferece "Editar", que desfaz SÓ ela; o descarte total é o botão do
  // histórico, e é esse que este teste exercita.
  const editar = document.querySelector('[data-editar-incidente]');
  assert.equal(editar.textContent, 'Editar');
  const desfazer = document.querySelector('[data-desfazer-candidata]');
  assert.equal(desfazer.dataset.desfazerCandidata, '9');

  desfazer.click();
  await proximoTurno();
  await proximoTurno();
  await proximoTurno();

  assert.deepEqual(posts, ['/nfse/contrato/9/descartar']);
});

test('candidata reprovada mostra POR QUE reprovou no histórico', async () => {
  // Sem isto a linha fica idêntica à de uma candidata que nunca foi validada,
  // e quem acabou de emitir a nota de validação conclui que o fluxo não rodou
  // — quando ele rodou, conferiu e recusou.
  await inicializarContratoNfse({
    root: document,
    fetchImpl: async () => resposta(estadoBase({
      candidatas: [{
        id: 9, versao: 2, estado: 'candidata',
        erro_validacao: '3 divergência(s) na revisão: A descricao na tela nao e a esperada.',
      }],
    })),
  });

  const linha = document.querySelector('[data-contrato-candidato="9"]');
  assert.ok(linha.textContent.includes('3 divergência(s) na revisão'));
  assert.ok(linha.textContent.includes('A descricao na tela nao e a esperada'));
});

test('Editar na linha chama o reabrir daquele incidente, não o descarte total', async () => {
  const posts = [];
  await inicializarContratoNfse({
    root: document,
    fetchImpl: async (url, opcoes) => {
      if (opcoes) { posts.push(url); return resposta({ contrato: null }); }
      return resposta(estadoBase({
        incidentes: [{ ...incidente, id: 7, estado: 'configurado', contrato_candidato_id: 9 }],
        candidatas: [{ id: 9, versao: 2, estado: 'candidata' }],
      }));
    },
  });

  document.querySelector('[data-editar-incidente]').click();
  await proximoTurno();
  await proximoTurno();
  await proximoTurno();

  assert.deepEqual(posts, ['/nfse/contrato/incidente/7/reabrir']);
});

test('monta payloads somente com as origens e fontes do catálogo', () => {
  assert.deepEqual(montarDadosCandidato({
    origem: 'fixo', valorFixo: 'OPCAO-SINTETICA', fontes,
  }), { origem: 'fixo', valor_fixo: 'OPCAO-SINTETICA' });
  assert.deepEqual(montarDadosCandidato({ origem: 'nota', fonte: 'documento', fontes }), {
    origem: 'nota', fonte: 'documento',
  });
  assert.deepEqual(montarDadosCandidato({ origem: 'derivado', fonte: 'data_emissao', fontes }), {
    origem: 'derivado', fonte: 'data_emissao',
  });
  assert.deepEqual(montarDadosCandidato({ origem: 'configuracao', fonte: 'item_nbs', fontes }), {
    origem: 'configuracao', fonte: 'item_nbs',
  });
  assert.deepEqual(montarDadosCandidato({ origem: 'padrao_portal', fontes }), {
    origem: 'padrao_portal',
  });
  assert.deepEqual(montarDadosCandidato({ origem: 'intocavel', fontes }), {
    origem: 'intocavel',
  });
});

test('recusa opção fora do catálogo e recomendação ambígua', () => {
  assert.throws(
    () => montarDadosCandidato({ origem: 'nota', fonte: 'inexistente', fontes }),
    /fonte válida/,
  );
  assert.throws(
    () => montarDadosCandidato({
      origem: 'fixo',
      valorFixo: 'OPCAO-SINTETICA',
      fontes,
      recomendacao: { ambigua: true, candidatos: ['a', 'b'] },
    }),
    /ambígua/,
  );
});

test('inicialização consulta somente o estado e a linha não duplica envio', async () => {
  const chamadas = [];
  let liberarPost;
  const inicializacao = inicializarContratoNfse({
    root: document,
    fetchImpl: async (url, opcoes) => {
      chamadas.push({ url, opcoes });
      if (!opcoes) return resposta(estadoBase({ incidentes: [incidente] }));
      return new Promise((resolve) => { liberarPost = () => resolve(resposta({}, false)); });
    },
  });
  await inicializacao;
  assert.equal(chamadas.length, 1);
  assert.equal(chamadas[0].opcoes, undefined);

  const forma = document.querySelector('.nfse-contrato-config');
  const origem = forma.querySelector('[data-campo="origem"]');
  origem.value = 'fixo';
  origem.dispatchEvent(new Event('change', { bubbles: true }));
  forma.querySelector('[data-campo="valor_fixo"]').value = 'OPCAO-SINTETICA';
  forma.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  await proximoTurno();

  const botao = forma.querySelector('button[type="submit"]');
  assert.equal(chamadas.filter((item) => item.opcoes).length, 1);
  assert.equal(botao.disabled, true);
  forma.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  assert.equal(chamadas.filter((item) => item.opcoes).length, 1);

  liberarPost();
  await proximoTurno();
  await proximoTurno();
  assert.equal(botao.disabled, false);
  assert.equal(botao.dataset.carregando, undefined);
  // A falha fica na própria linha, não num modal que já foi fechado.
  assert.ok(document.querySelector('.nfse-contrato-erro').textContent);
});

test('recomendação inequívoca exige confirmação explícita no payload', () => {
  assert.throws(
    () => montarDadosCandidato({
      origem: 'fixo', valorFixo: 'OPCAO-SINTETICA', fontes,
      recomendacao: {
        inequivoca: true,
        chave_observada: 'campo.sintetico',
        candidatos: ['campo.sintetico'],
      },
    }),
    /Confirme explicitamente/,
  );
  assert.deepEqual(montarDadosCandidato({
    origem: 'fixo', valorFixo: 'OPCAO-SINTETICA', fontes,
    recomendacao: {
      inequivoca: true,
      chave_observada: 'campo.sintetico',
      candidatos: ['campo.sintetico'],
    },
    confirmarRecomendacao: true,
  }), {
    origem: 'fixo',
    valor_fixo: 'OPCAO-SINTETICA',
    confirmar_recomendacao: true,
    chave_observada: 'campo.sintetico',
  });
});

test('recomendação ambígua aceita somente escolha manual confirmada', () => {
  const recomendacao = {
    ambigua: true,
    candidatos: ['campo.a', 'campo.b'],
  };
  assert.deepEqual(montarDadosCandidato({
    origem: 'fixo',
    valorFixo: 'OPCAO-SINTETICA',
    fontes,
    recomendacao,
    chaveObservada: 'campo.b',
    confirmarRecomendacao: true,
  }), {
    origem: 'fixo',
    valor_fixo: 'OPCAO-SINTETICA',
    confirmar_recomendacao: true,
    chave_observada: 'campo.b',
  });
});

test('botão de recon chama somente a ação explícita e mostra estado desconhecido', async () => {
  const chamadas = [];
  await inicializarContratoNfse({
    root: document,
    fetchImpl: async (url, opcoes) => {
      chamadas.push({ url, opcoes });
      if (opcoes) {
        return resposta({
          observacao: { compatibilidade: 'desconhecida' },
        });
      }
      return resposta(estadoBase());
    },
  });

  document.getElementById('btnReconContrato').click();
  await proximoTurno();
  await proximoTurno();
  await proximoTurno();

  const posts = chamadas.filter((item) => item.opcoes);
  assert.equal(posts.length, 1);
  assert.equal(posts[0].url, '/nfse/contrato/recon');
  assert.equal(posts[0].opcoes.method, 'POST');
  assert.equal(document.getElementById('nfseContratoStatus').dataset.estado, 'desconhecido');
  assert.match(document.getElementById('nfseReconEstado').textContent, /desconhecida/);
});

test('cada recon é um passe acumulado, e o descarte zera a contagem', async () => {
  const posts = [];
  let passe = 0;
  await inicializarContratoNfse({
    root: document,
    fetchImpl: async (url, opcoes) => {
      if (!opcoes) return resposta(estadoBase());
      posts.push(url);
      if (url === '/nfse/contrato/recon/descartar') {
        return resposta({ passe: 0, controles_acumulados: 0 });
      }
      passe += 1;
      return resposta({
        passe,
        controles_acumulados: passe * 10,
        observacao: { compatibilidade: 'aviso', evidencias: [] },
      });
    },
  });

  const botao = document.getElementById('btnReconContrato');
  const rotulo = document.getElementById('nfseReconPasses');
  const descartar = document.getElementById('btnReconDescartar');
  assert.equal(rotulo.classList.contains('d-none'), true);

  botao.click();
  await proximoTurno(); await proximoTurno(); await proximoTurno();
  assert.match(rotulo.textContent, /passe 1 . 10 controles/);
  assert.equal(descartar.classList.contains('d-none'), false);

  botao.click();
  await proximoTurno(); await proximoTurno(); await proximoTurno();
  assert.match(rotulo.textContent, /passe 2 . 20 controles/);

  descartar.click();
  await proximoTurno(); await proximoTurno();
  assert.equal(rotulo.classList.contains('d-none'), true);
  assert.deepEqual(posts, [
    '/nfse/contrato/recon', '/nfse/contrato/recon', '/nfse/contrato/recon/descartar',
  ]);
});

test('a recon propõe intocável e pendente sem aplicar nada', async () => {
  // O bloco do tomador chega preenchido junto com o CNPJ: a recon reconhece,
  // mas quem decide é o operador.
  await inicializarContratoNfse({
    root: document,
    fetchImpl: async (url, opcoes) => {
      if (!opcoes) return resposta(estadoBase());
      return resposta({
        passe: 2,
        controles_acumulados: 40,
        sugestoes: [
          { chave: 'Tomador.Nome', rotulo: 'Nome/Razão Social', sugestao: 'intocavel',
            motivo: 'ficou preenchido entre os passes', obrigatorio: false },
          { chave: 'PreencherInfoIBSCBS', rotulo: 'Preencher as informações IBS/CBS?',
            sugestao: 'preencher', motivo: 'continua vazio e o portal exige',
            obrigatorio: true },
        ],
        observacao: { compatibilidade: 'aviso', evidencias: [] },
      });
    },
  });

  document.getElementById('btnReconContrato').click();
  await proximoTurno(); await proximoTurno(); await proximoTurno();

  const painel = document.getElementById('nfseReconSugestoes');
  assert.equal(painel.classList.contains('d-none'), false);
  assert.match(painel.textContent, /não tocar/i);
  assert.match(painel.textContent, /Nome\/Razão Social/);
  // Sem incidente aberto correspondente não há o que configurar, e diz isso.
  assert.match(painel.textContent, /sem incidente aberto/);
});

test('sugestão com incidente aberto leva ao campo e pré-seleciona a origem', async () => {
  await inicializarContratoNfse({
    root: document,
    fetchImpl: async (url, opcoes) => {
      if (!opcoes) return resposta(estadoBase({ incidentes: [incidente] }));
      return resposta({
        passe: 2,
        controles_acumulados: 40,
        sugestoes: [{
          chave: 'campo.sintetico', rotulo: 'Campo sintético', sugestao: 'intocavel',
          motivo: 'ficou preenchido entre os passes', obrigatorio: false,
        }],
        observacao: { compatibilidade: 'aviso', evidencias: [] },
      });
    },
  });

  document.getElementById('btnReconContrato').click();
  await proximoTurno(); await proximoTurno(); await proximoTurno();

  const acao = document.querySelector('[data-sugestao-incidente]');
  assert.equal(acao.dataset.sugestaoOrigem, 'intocavel');
  acao.click();

  // Pré-seleciona e leva até a linha; salvar continua sendo ato do operador.
  const origem = document.querySelector(
    `.nfse-contrato-incidente[data-incidente-id="${incidente.id}"] [data-campo="origem"]`,
  );
  assert.equal(origem.value, 'intocavel');
  assert.equal(origem.closest('form').querySelector('[data-campo="fonte"]').hidden, true);
});

test('gate fechado desabilita automático e continua recusando início se o DOM for alterado', async () => {
  document.body.insertAdjacentHTML('beforeend', `
    <input type="radio" name="nfseModo" id="modoAutomatico" value="automatico" checked>
    <button id="btnIniciarLote" type="button">Emitir</button>
    <div id="nfseModoDesc"></div>
    <div id="nfseContratoStatus" data-estado="bloqueado"></div>
    <div id="nfseContratoStatusTexto">Resolva o incidente sintético.</div>`);
  let chamadas = 0;
  const fetchOriginal = globalThis.fetch;
  globalThis.fetch = async () => {
    chamadas += 1;
    return resposta({});
  };
  try {
    const estado = estadoBase({
      ativo: { id: 1, versao: 2, elegivel_automatico: false },
    });
    assert.equal(contratoPermiteAutomatico(estado), false);
    assert.equal(aplicarGateContrato(estado), false);
    const automatico = document.getElementById('modoAutomatico');
    const iniciar = document.getElementById('btnIniciarLote');
    assert.equal(automatico.disabled, true);
    assert.equal(iniciar.disabled, true);
    assert.match(document.getElementById('nfseModoDesc').textContent, /Indisponível/);

    automatico.disabled = false;
    iniciar.disabled = false;
    await iniciarEmissao();
    assert.equal(chamadas, 0);
    assert.equal(contratoPermiteAutomatico(estadoBase({
      incidentes: [{ ...incidente, severidade: 'informativa' }],
    })), false);
    assert.equal(contratoPermiteAutomatico(estadoBase({
      incidentes: [{ ...incidente, estado: 'configurado' }],
    })), false);
  } finally {
    globalThis.fetch = fetchOriginal;
  }
});


test('a fila de validação segue o veredito do servidor, não o status', async () => {
  // `nfse_service.emitivel` barra proposta de agrupamento pendente e duplicata
  // não liberada — coisas que uma lista de status não vê. A nota 12 está
  // "pronta" e mesmo assim não é emitível.
  await inicializarContratoNfse({
    root: document,
    fetchImpl: async () => resposta(estadoBase()),
  });

  const select = document.getElementById('nfseNotaValidacao');
  const ids = [...select.options].map((o) => o.value).filter(Boolean);

  assert.deepEqual(ids, ['10']);
});
