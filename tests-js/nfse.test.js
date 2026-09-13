import test, { after, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><body></body>', { pretendToBeVisual: true });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.Event = dom.window.Event;
globalThis.requestAnimationFrame = (callback) => setTimeout(callback, 0);
dom.window.HTMLElement.prototype.scrollIntoView = function scrollIntoView() {};
globalThis.fetch = async () => ({ ok: true, json: async () => ({}) });

const {
  celulaDescricao,
  enviarEnsaio,
  notasHistoricasAptas,
  pintarEnsaio,
  pintarEmitidas,
  pintarOpcoesEnsaio,
  pintarSincronizacaoAdn,
  pintarSombra,
  consultarEmitidas,
  executarSombra,
  iniciarEmissao,
  solicitarEnvioEnsaio,
} = await import('../app/static/js/nfse.js');

after(() => dom.window.close());

beforeEach(() => {
  document.body.innerHTML = `
    <div id="toastStack"></div>
    <div id="emitidasPainel"></div>
    <input id="emitidasInicio" value="2026-08-01">
    <input id="emitidasFim" value="2026-08-31">
    <span id="emitidasEstado"></span>
    <button id="btnConsultarEmitidas" type="button">Consultar o portal</button>
    <button id="btnExecutarSombra" type="button">Conferir portal + ADN</button>
    <span id="nfseSombraEstado"></span>
    <div id="nfseSombraResultado"></div>
    <div id="nfseAdnSincronizacaoResultado"></div>`;
  pintarEnsaio(null);
});

function markupEnsaio() {
  document.body.innerHTML += `
    <select id="nfseEnsaioNota"></select>
    <button id="btnPrepararEnsaio" type="button">Preparar ensaio</button>
    <button id="btnEnviarEnsaio" type="button">Enviar ao ambiente de testes</button>
    <button id="btnReconsultarEnsaio" type="button">Reconsultar resultado</button>
    <span id="nfseEnsaioEstado"></span>
    <div id="nfseEnsaioResultado"></div>`;
}

function ensaio(overrides = {}) {
  return {
    id: 23,
    nota_nfse_id: 2,
    ambiente: 'restrita',
    serie: '7',
    numero: 12,
    identificador_dps: 'DPS' + '1'.repeat(42),
    estado: 'preparado',
    sem_validade_juridica: true,
    comparacao: {
      esperadas: [{
        caminho: '/DPS/infDPS/tpAmb', valor_referencia: '1', valor_dps: '2',
        motivo: 'O ensaio usa o ambiente restrito.',
      }],
      bloqueadoras: [],
      metadados: { ambiente_dps: '2' },
      pode_enviar: true,
    },
    chave_nfse_teste: null,
    codigo_rejeicao: null,
    motivo_rejeicao: null,
    ultima_falha: null,
    xml: {
      referencia_disponivel: true,
      dps_assinada_disponivel: true,
      nfse_teste_disponivel: false,
    },
    ...overrides,
  };
}

async function assentar() {
  for (let i = 0; i < 8; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
}

test('seleciona somente nota emitida com espelho oficial único', () => {
  const candidatas = notasHistoricasAptas([
    { id: 1, status: 'pronta', ensaio_elegivel: true },
    { id: 2, status: 'emitida', ensaio_elegivel: true },
    { id: 3, status: 'emitida', ensaio_elegivel: false },
    { id: 4, status: 'emitida' },
  ]);

  assert.deepEqual(candidatas.map((nota) => nota.id), [2]);
});

test('a tela separa preparação e envio e destaca a falta de validade jurídica', () => {
  const template = readFileSync('app/templates/nfse.html', 'utf8');
  const inicio = template.indexOf('id="nfseEnsaio"');
  const fim = template.indexOf('id="nfseApiAcesso"', inicio);
  const secao = template.slice(inicio, fim);

  assert.match(secao, /id="btnPrepararEnsaio"/);
  assert.match(secao, />\s*Preparar ensaio\s*</);
  assert.match(secao, /id="btnEnviarEnsaio"/);
  assert.match(secao, />\s*Enviar ao ambiente de testes\s*</);
  assert.match(secao, /Sem validade jurídica/);
  assert.doesNotMatch(secao, /id="btnPrepararEnsaio"[^>]*>\s*Emitir\s*</);
});

test('pinta candidatas históricas sem colocar nota pendente na seleção', () => {
  markupEnsaio();
  pintarOpcoesEnsaio([
    { id: 1, status: 'pronta', ensaio_elegivel: true, nome_csv: 'Pendente' },
    { id: 2, status: 'emitida', ensaio_elegivel: true,
      nome_csv: 'Tomador Sintético', competencia: '08/2026', valor: '400,00' },
  ]);

  const opcoes = [...document.querySelectorAll('#nfseEnsaioNota option')];
  assert.equal(opcoes.length, 2);
  assert.match(opcoes[1].textContent, /Tomador Sintético/);
  assert.match(opcoes[1].textContent, /R\$ 400,00/);
  assert.equal(opcoes.some((opcao) => opcao.textContent.includes('Pendente')), false);
});

test('divergência fiscal aparece campo a campo e desabilita o envio', () => {
  markupEnsaio();
  pintarOpcoesEnsaio([{ id: 2, status: 'emitida', ensaio_elegivel: true,
    nome_csv: 'Tomador Sintético' }]);
  document.getElementById('nfseEnsaioNota').value = '2';
  pintarEnsaio(ensaio({
    comparacao: {
      esperadas: [],
      bloqueadoras: [{
        caminho: '/DPS/infDPS/valores/vServPrest/vServ',
        valor_referencia: '400.00', valor_dps: '401.00',
        motivo: 'O valor fiscal diverge da referência.',
      }],
      metadados: {}, pode_enviar: false,
    },
  }));

  const resultado = document.getElementById('nfseEnsaioResultado');
  const enviar = document.getElementById('btnEnviarEnsaio');
  assert.equal(enviar.classList.contains('d-none'), false);
  assert.equal(enviar.disabled, true);
  assert.match(resultado.textContent, /vServ/);
  assert.match(resultado.textContent, /400\.00/);
  assert.match(resultado.textContent, /401\.00/);
  assert.doesNotMatch(resultado.innerHTML, /<NFSe/);
});

test('resultado indefinido oferece somente reconsulta', () => {
  markupEnsaio();
  pintarOpcoesEnsaio([{ id: 2, status: 'emitida', ensaio_elegivel: true }]);
  document.getElementById('nfseEnsaioNota').value = '2';
  pintarEnsaio(ensaio({
    estado: 'indefinida',
    comparacao: { esperadas: [], bloqueadoras: [], metadados: {}, pode_enviar: true },
    ultima_falha: 'Desfecho ainda não confirmado.',
  }));

  assert.equal(document.getElementById('btnEnviarEnsaio').classList.contains('d-none'), true);
  assert.equal(document.getElementById('btnReconsultarEnsaio').classList.contains('d-none'), false);
  assert.match(document.getElementById('nfseEnsaioEstado').textContent, /somente uma reconsulta/);
});

test('envio só acontece após confirmação explícita e leva o campo correto', async () => {
  markupEnsaio();
  pintarOpcoesEnsaio([{ id: 2, status: 'emitida', ensaio_elegivel: true }]);
  document.getElementById('nfseEnsaioNota').value = '2';
  pintarEnsaio(ensaio());
  const chamadas = [];
  globalThis.fetch = async (url, opcoes) => {
    chamadas.push({ url, opcoes });
    return { ok: true, json: async () => ({ ensaio: ensaio({ estado: 'indefinida' }) }) };
  };
  const confirmarAnterior = globalThis.confirm;
  try {
    globalThis.confirm = () => false;
    assert.equal(solicitarEnvioEnsaio(), false);
    assert.equal(chamadas.length, 0);

    globalThis.confirm = () => true;
    assert.equal(solicitarEnvioEnsaio(), true);
    await assentar();
  } finally {
    globalThis.confirm = confirmarAnterior;
  }

  assert.equal(chamadas.length, 1);
  assert.equal(chamadas[0].url, '/nfse/api/ensaios/23/enviar');
  assert.deepEqual(JSON.parse(chamadas[0].opcoes.body), { confirmar_envio: true });
});

function painel(overrides = {}) {
  return {
    consulta_id: 7,
    inicio: '2026-08-01',
    fim: '2026-08-17',
    mes_geracao: '08/2026',
    nunca_consultado: false,
    quantidade: 1,
    total: '400,00',
    outras_situacoes: {},
    consultado_em: '31/08/2026 15:10',
    sem_nota: [],
    sem_extrato: [],
    nao_conferiveis: 0,
    valor_diferente: [],
    ambigua: [],
    ...overrides,
  };
}

test('permite editar a descrição quando a categoria está a definir', () => {
  const html = celulaDescricao({
    id: 17,
    categoria: 'indefinida',
    status: 'descricao_pendente',
    descricao_extrato: 'DESCRIÇÃO SINTÉTICA DO PIX',
  });

  assert.match(html, /A definir/);
  assert.match(html, /data-editar-descricao="17"/);
  assert.match(html, /DESCRIÇÃO SINTÉTICA DO PIX/);
});

test('mantém a descrição bloqueada depois do preenchimento', () => {
  ['emitida', 'aguardando_confirmacao'].forEach((status) => {
    const html = celulaDescricao({
      id: 18,
      categoria: 'indefinida',
      status,
      descricao_extrato: 'DESCRIÇÃO SINTÉTICA DO PIX',
    });

    assert.doesNotMatch(html, /data-editar-descricao/);
  });
});

test('oferece o resumo imprimível somente depois de consultar o mês', () => {
  const painelAtual = document.getElementById('emitidasPainel');

  pintarEmitidas({
    mes_geracao: '08/2026',
    competencia: '07/2026',
    nunca_consultado: false,
    quantidade: 3,
    total: '1.234,56',
    outras_situacoes: {},
    consultado_em: '31/08/2026 10:30',
    sem_nota: [],
    sem_extrato: [],
    nao_conferiveis: 0,
    valor_diferente: [],
  });

  assert.match(painelAtual.innerHTML, /Imprimir \/ salvar PDF/);
  assert.match(painelAtual.innerHTML, /\/nfse\/emitidas\/resumo\?mes=08%2F2026/);

  pintarEmitidas({
    mes_geracao: '09/2026',
    competencia: '08/2026',
    nunca_consultado: true,
  });
  assert.doesNotMatch(painelAtual.innerHTML, /Imprimir \/ salvar PDF/);
});

test('mostra o intervalo e a seção de correspondência ambígua', () => {
  pintarEmitidas(painel({
    valor_diferente: [{
      nota: { nome_csv: 'CLIENTE TESTE', valor: '400,00' },
      emitida: { nome_tomador: 'CLIENTE TESTE', valor: '450,00' },
    }],
    ambigua: [{
      emitida: { nome_tomador: 'TOMADOR AMBÍGUO', valor: '300,00' },
      candidatas: [
        { nome_csv: 'CANDIDATA UM', valor: '300,00' },
        { nome_csv: 'CANDIDATA DOIS', valor: '300,00' },
      ],
    }],
  }));

  const texto = document.getElementById('emitidasPainel').textContent;
  assert.match(texto, /01\/08\/2026 a 17\/08\/2026/);
  assert.match(texto, /Correspondência ambígua \(1\)/);
  assert.match(texto, /valor final comparado/);
  assert.match(texto, /CANDIDATA UM/);
});

test('mostra o desfecho teto junto da faixa e das contagens do ADN', () => {
  pintarSincronizacaoAdn({
    faixa_nsu: { inicio: 4, fim: 12 },
    lidos: 8,
    gravados: 6,
    ignorados: 2,
    desfecho: 'teto',
  });

  const texto = document.getElementById('nfseAdnSincronizacaoResultado').textContent;
  assert.match(texto, /NSU 4 a 12/);
  assert.match(texto, /Limite de chamadas atingido/);
  assert.match(texto, /8 lido\(s\)/);
  assert.match(texto, /6 gravado\(s\)/);
  assert.match(texto, /2 ignorado\(s\)/);
});

test('pinta as três listas da conferência sombra e explica a situação normalizada', () => {
  pintarSombra({
    status: 'concluida',
    periodo: { inicio: '2026-08-01', fim: '2026-08-31' },
    fontes: { portal: { lidas: 2 }, adn: { lidos: 2 } },
    comparacao: {
      so_no_portal: [{ chave: 'CHAVE-SINTETICA-002', data_geracao: '2026-08-12', valor: '10,00' }],
      so_no_adn: [{ chave: 'CHAVE-SINTETICA-003', data_geracao: '2026-08-13', valor: '20,00' }],
      divergentes: [{
        chave: 'CHAVE-SINTETICA-001',
        portal: { chave: 'CHAVE-SINTETICA-001', data_geracao: '2026-08-12', valor: '30,00' },
        adn: { chave: 'CHAVE-SINTETICA-001', data_geracao: '2026-08-12', valor: '30,00' },
        campos: [],
        classificacao: 'esperada',
        situacao: {
          portal: 'gerada',
          adn: 'cancelada',
          explicacao: 'O ADN acrescentou um cancelamento.',
        },
      }],
      esperadas: [{
        chave: 'CHAVE-SINTETICA-001',
        portal: { chave: 'CHAVE-SINTETICA-001', valor: '30,00' },
        adn: { chave: 'CHAVE-SINTETICA-001', valor: '30,00' },
        campos: [],
        classificacao: 'esperada',
        situacao: { portal: 'gerada', adn: 'cancelada', explicacao: 'Cancelamento conhecido.' },
      }],
      inesperadas: [],
      total_diferencas: 3,
    },
  });

  const texto = document.getElementById('nfseSombraResultado').textContent;
  assert.match(texto, /Somente no portal \(1\)/);
  assert.match(texto, /Somente no ADN \(1\)/);
  assert.match(texto, /Divergências entre as fontes \(1\)/);
  assert.match(texto, /Diferenças esperadas \(1\)/);
  assert.match(texto, /Diferenças inesperadas \(0\)/);
  assert.match(texto, /gerada/);
  assert.match(texto, /cancelada/);
  assert.match(texto, /[Cc]ancelamento/);
});

test('não pinta igualdade quando a conferência sombra é inconclusiva', () => {
  pintarSombra({
    status: 'inconclusiva',
    fonte_falha: 'adn',
    mensagem: 'O serviço do ADN está indisponível.',
  });

  const texto = document.getElementById('nfseSombraResultado').textContent;
  assert.match(texto, /Resultado inconclusivo/);
  assert.match(texto, /ADN/);
  assert.match(texto, /Não é possível afirmar igualdade/);
  assert.doesNotMatch(texto, /As observações das duas fontes coincidem/);
});

test('execução sombra envia somente o intervalo e pinta o resultado', async () => {
  const chamadas = [];
  globalThis.fetch = async (url, opcoes) => {
    chamadas.push({ url, opcoes });
    return {
      ok: true,
      json: async () => ({
        status: 'ok',
        sombra: {
          status: 'concluida',
          periodo: { inicio: '2026-08-01', fim: '2026-08-31' },
          fontes: { portal: { lidas: 0 }, adn: { lidos: 0 } },
          comparacao: {
            so_no_portal: [], so_no_adn: [], divergentes: [],
            esperadas: [], inesperadas: [], total_diferencas: 0,
          },
        },
      }),
    };
  };

  await executarSombra(document.getElementById('btnExecutarSombra'));

  assert.equal(chamadas.length, 1);
  assert.equal(chamadas[0].url, '/nfse/emitidas/sombra');
  assert.deepEqual(JSON.parse(chamadas[0].opcoes.body), {
    inicio: '2026-08-01', fim: '2026-08-31',
  });
  assert.match(
    document.getElementById('nfseSombraResultado').textContent,
    /Nenhuma observação foi encontrada/);
});

test('consulta envia somente o intervalo, sem competência', async () => {
  const chamadas = [];
  globalThis.fetch = async (url, opcoes) => {
    chamadas.push({ url, opcoes });
    return {
      ok: true,
      json: async () => ({ status: 'ok', lidas: 0, blocos: 1, novas: 0,
        painel: painel() }),
    };
  };

  await consultarEmitidas(document.getElementById('btnConsultarEmitidas'));

  assert.equal(chamadas.length, 1);
  assert.deepEqual(JSON.parse(chamadas[0].opcoes.body), {
    inicio: '2026-08-01',
    fim: '2026-08-31',
  });
});

test('o campo e os recálculos manuais de competência não existem mais', () => {
  const template = readFileSync('app/templates/nfse.html', 'utf8');
  const script = readFileSync('app/static/js/nfse.js', 'utf8');

  assert.equal(template.includes('emitidasCompetencia'), false);
  assert.equal(script.includes('emitidasCompetencia'), false);
  assert.equal(script.includes('competenciaConferida'), false);
  assert.equal(script.includes('recarregarPainelEmitidas'), false);
});

test('a conferência oferece os filtros locais e todas as ordenações da spec', () => {
  const template = readFileSync('app/templates/nfse.html', 'utf8');
  const script = readFileSync('app/static/js/nfse.js', 'utf8');

  assert.match(template, /id="filtroTexto"[^>]*placeholder="Nome, empresa ou CPF\/CNPJ"/);
  assert.match(template, /id="filtroValor"[^>]*inputmode="decimal"/);
  assert.match(template, /id="filtroSituacao"/);
  assert.match(template, /Precisam de atenção/);
  assert.match(template, /Prontas para preencher/);
  assert.match(template, /Em andamento/);
  assert.match(template, /Resolvidas/);
  assert.match(template, /id="filtroOrdenacao"/);
  assert.match(template, /Nome — A a Z/);
  assert.match(template, /Nome — Z a A/);
  assert.match(template, /Valor — menor primeiro/);
  assert.match(template, /Valor — maior primeiro/);
  assert.match(template, /Emissão — mais recente/);
  assert.match(template, /Emissão — mais antiga/);
  assert.match(template, /Ordem de importação/);
  assert.match(template, /id="btnLimparFiltros"/);
  assert.match(template, /id="nfseContagemVisivel"/);
  assert.match(script, /filtrarOrdenarNotas\(notas, filtros\)/);
  assert.match(script, /Nenhum resultado corresponde aos filtros/);
  assert.match(script, /idsSelecionadosVisiveis\(selecionadas, notasVisiveis\)/);
  // A regra de "linha visível que aceita ação em massa" tem UM lugar: o núcleo
  // puro. A tela lê de lá em vez de repetir o predicado.
  const nucleo = readFileSync('app/static/js/nfse_filtros.js', 'utf8');
  assert.match(nucleo, /selecionavel !== false/);
  assert.match(script, /idsVisiveis\(notasVisiveis\)/);
  assert.equal(script.includes('selecionavel'), false);
  assert.match(script, /encodeURIComponent\(escopoAtual\(\)\)/);
});

test('o escopo "todas as competências" procura, mas não emite a lista inteira', async () => {
  // A fila do lote é exatamente o que a página mostra, e aqui ela mostra meses
  // já fechados. Documento fiscal não tem rollback: a lista inteira pede escopo
  // fechado, a nota a nota continua valendo.
  const template = readFileSync('app/templates/nfse.html', 'utf8');
  assert.match(template, /<option value="todas"/);
  assert.match(template, /Todas as competências/);

  document.body.innerHTML += `
    <select id="filtroCompetencia"><option value="todas" selected>Todas</option></select>
    <input type="radio" name="nfseModo" value="lote" checked>`;

  const chamadas = [];
  globalThis.fetch = async (url) => {
    chamadas.push(url);
    return { ok: true, json: async () => ({ status: 'ok' }) };
  };

  await iniciarEmissao();

  // nao chegou a pedir emissao nenhuma: o guarda barrou antes do fetch
  assert.deepEqual(chamadas, []);
  const script = readFileSync('app/static/js/nfse.js', 'utf8');
  assert.match(script, /!notaId && escopoAmplo\(\)/);
  assert.match(script, /serve para procurar/);
});
