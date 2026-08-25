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
      <div id="nfseContratoIncidentes"></div>
      <div id="nfseContratoHistorico"></div>
      <div id="nfseReconEstado" role="status" aria-live="polite"></div>
    </section>
    <button id="btnReconContrato" type="button">Recon</button>
    <div id="modalConfigContrato"></div>
    <form id="formConfigContrato">
      <input id="nfseContratoIncidenteId">
      <p id="nfseContratoCampoSelecionado"></p>
      <select id="nfseContratoOrigem">
        <option value="" selected>Escolha uma origem…</option>
        <option value="fixo">Valor fixo</option>
        <option value="nota">Fonte da nota</option>
      </select>
      <div id="nfseContratoFonteGrupo"><select id="nfseContratoFonte"></select></div>
      <div id="nfseContratoValorGrupo"><input id="nfseContratoValorFixo"></div>
      <div id="nfseContratoRecomendacaoGrupo"><input id="nfseContratoConfirmarRecomendacao" type="checkbox"></div>
      <div id="nfseContratoErro" role="alert"></div>
      <button id="btnSalvarConfigContrato" type="submit">Salvar configuração</button>
    </form>
    <div id="modalValidarContrato"></div>
    <form id="formValidarContrato">
      <select id="nfseNotaValidacao"><option value="" selected disabled>Escolha uma nota…</option></select>
      <div id="nfseValidacaoErro" role="alert"></div>
      <button id="btnValidarContrato" type="submit">Iniciar validação</button>
    </form>
    <script id="dadosNotas" type="application/json">[
      {"id": 10, "status": "pronta", "nome_csv": "NOTA SINTÉTICA", "competencia": "08/2026"},
      {"id": 11, "status": "emitida", "nome_csv": "FORA DA FILA", "competencia": "08/2026"}
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
    [estadoBase(), 'compativel'],
    [estadoBase({ incidentes: [{ ...incidente, severidade: 'informativa' }] }), 'aviso'],
    [estadoBase({ ativo: { id: 1, versao: 1, elegivel_automatico: false } }), 'bloqueado'],
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

test('renderiza opções do portal com rótulo e código sem selecionar a primeira', async () => {
  const chamadas = [];
  await inicializarContratoNfse({
    root: document,
    fetchImpl: async (url) => {
      chamadas.push(url);
      return resposta(estadoBase());
    },
  });

  const origem = document.getElementById('nfseContratoOrigem');
  origem.value = 'nota';
  origem.dispatchEvent(new Event('change'));
  const select = document.getElementById('nfseContratoFonte');
  assert.equal(select.options[1].textContent, 'Documento');
  assert.equal(select.options[1].value, 'documento');
  assert.equal(select.options[1].selected, false);
  assert.equal(document.getElementById('nfseContratoFonteGrupo').hidden, false);
  assert.equal(document.getElementById('nfseContratoValorGrupo').hidden, true);
  assert.deepEqual(opcoesDaOrigem(fontes, 'nota'), [fontes[1]]);
  assert.equal(chamadas.length, 1);
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

test('inicialização consulta somente o estado e ações mostram loading sem duplicar envio', async () => {
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

  document.querySelector('[data-configurar-incidente]').click();
  const origem = document.getElementById('nfseContratoOrigem');
  origem.value = 'fixo';
  origem.dispatchEvent(new Event('change'));
  document.getElementById('nfseContratoValorFixo').value = 'OPCAO-SINTETICA';
  const formulario = document.getElementById('formConfigContrato');
  formulario.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  await proximoTurno();

  const botao = document.getElementById('btnSalvarConfigContrato');
  assert.equal(chamadas.filter((item) => item.opcoes).length, 1);
  assert.equal(botao.disabled, true);
  formulario.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  assert.equal(chamadas.filter((item) => item.opcoes).length, 1);

  liberarPost();
  await proximoTurno();
  await proximoTurno();
  assert.equal(botao.disabled, false);
  assert.equal(botao.dataset.carregando, undefined);
  assert.match(document.getElementById('nfseContratoErro').textContent, /ação/);
});

test('recomendação inequívoca exige confirmação explícita no payload', () => {
  assert.throws(
    () => montarDadosCandidato({
      origem: 'fixo', valorFixo: 'OPCAO-SINTETICA', fontes,
      recomendacao: { inequivoca: true },
    }),
    /Confirme explicitamente/,
  );
  assert.deepEqual(montarDadosCandidato({
    origem: 'fixo', valorFixo: 'OPCAO-SINTETICA', fontes,
    recomendacao: { inequivoca: true }, confirmarRecomendacao: true,
  }), { origem: 'fixo', valor_fixo: 'OPCAO-SINTETICA', confirmar_recomendacao: true });
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
  } finally {
    globalThis.fetch = fetchOriginal;
  }
});
