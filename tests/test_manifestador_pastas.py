"""Importacao de XML por PASTA: o que o servidor precisa aguentar (MANIF-10).

O percurso das pastas e das subpastas acontece no navegador — e o unico lugar
que enxerga o disco do operador. O que sobra para o servidor sao duas
obrigacoes, e as duas quebram em silencio se ninguem as fixar:

1. **o caminho tem de sobreviver ao envio.** Vindo de uma pasta, um arquivo
   recusado so e encontravel se a recusa disser "Julho 2026/sub/nota.xml"; um
   "nota.xml" solto manda o operador procurar em 40 subpastas;
2. **o bloco tem de caber numa requisicao.** O Werkzeug recusa mais de 1.000
   partes, e a pasta de um mes passa disso — por isso o JS envia em blocos de
   200. O teto e o motivo do bloco existir, e mudar um sem o outro devolve um
   413 no meio da importacao.
"""
import io

from app import db
from app.models import ChaveManifestacao, Empresa

# cUF(2) AAMM(4) CNPJ(14) mod(2) serie(3) nNF(9) tpEmis(1) cNF(8) DV(1)
CHAVE_BASE = '43250722333444000181550010000012341000012344'
CNPJ_DEST = '11222333000181'

# O que o JS manda por requisicao (`POR_ENVIO` em manifestador.js) e o teto do
# Werkzeug que obriga a fatiar. Deixar os dois lado a lado e o que faz o teste
# falhar quando alguem mexer em um so.
POR_ENVIO = 200
TETO_DE_PARTES = 1000


def _dv(base43):
    """Modulo 11, pesos 2..9 ciclando da direita — mesma regra do `dv_valido`."""
    peso, soma = 2, 0
    for digito in reversed(base43):
        soma += int(digito) * peso
        peso = 2 if peso == 9 else peso + 1
    resto = soma % 11
    return str(0 if resto in (0, 1) else 11 - resto)


def _chave(numero):
    """Chave valida e distinta, variando so o nNF."""
    base = CHAVE_BASE[:25] + str(numero).zfill(9) + CHAVE_BASE[34:43]
    return base + _dv(base)


def _xml(chave, cnpj_dest=CNPJ_DEST):
    ns = 'http://www.portalfiscal.inf.br/nfe'
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<nfeProc xmlns="{ns}" versao="4.00"><NFe xmlns="{ns}">'
        f'<infNFe versao="4.00" Id="NFe{chave}">'
        f'<ide><dhEmi>2025-07-15T10:30:00-03:00</dhEmi></ide>'
        f'<emit><CNPJ>22333444000181</CNPJ></emit>'
        f'<dest><CNPJ>{cnpj_dest}</CNPJ></dest>'
        f'</infNFe></NFe></nfeProc>'
    ).encode('utf-8')


def _empresa():
    emp = Empresa(nome='EMPRESA DESTINATARIA', cnpj='11.222.333/0001-81',
                  estado='RS', cidade='Imbé')
    db.session.add(emp)
    db.session.commit()
    return emp


def _enviar(client, arquivos):
    return client.post(
        '/manifestador/importar/xml',
        data={'arquivo': [(io.BytesIO(corpo), nome) for nome, corpo in arquivos]},
        content_type='multipart/form-data')


# --- o caminho sobrevive ----------------------------------------------------

def test_recusa_nomeia_o_arquivo_com_a_pasta_de_onde_veio(app, ids, client):
    """Sem o caminho, "sem empresa da carteira" nao diz ONDE esta o arquivo."""
    caminho = 'NFe Julho 2026/Entradas/nota-4540.xml'
    resposta = _enviar(client, [(caminho, _xml(_chave(1), cnpj_dest='99888777000166'))])

    balanco = resposta.get_json()['balanco']
    assert balanco['sem_empresa'] == [caminho]


def test_arquivo_avulso_continua_nomeado_so_pelo_nome(app, ids, client):
    """Quem escolhe arquivo solto nao tem pasta para mostrar — e nao inventamos."""
    resposta = _enviar(client, [('nota.xml', _xml(_chave(2), cnpj_dest='99888777000166'))])

    assert resposta.get_json()['balanco']['sem_empresa'] == ['nota.xml']


def test_mesma_nota_em_subpastas_diferentes_e_uma_chave_so(app, ids, client):
    """A subpasta muda o nome do arquivo, nao a nota: a segunda vira duplicata."""
    with app.app_context():
        _empresa()

    chave = _chave(3)
    resposta = _enviar(client, [
        ('Julho/A/nota.xml', _xml(chave)),
        ('Julho/B/nota.xml', _xml(chave)),
    ])

    balanco = resposta.get_json()['balanco']
    assert balanco['aceitas'] == [chave]
    assert [d['chave'] for d in balanco['duplicatas']] == [chave]


# --- o bloco cabe -----------------------------------------------------------

def test_um_bloco_inteiro_entra_numa_requisicao_so(app, ids, client):
    """`POR_ENVIO` arquivos de uma vez — o tamanho que o JS usa para fatiar."""
    with app.app_context():
        _empresa()

    arquivos = [(f'Julho/nota{i}.xml', _xml(_chave(i))) for i in range(POR_ENVIO)]
    resposta = _enviar(client, arquivos)

    assert resposta.status_code == 200
    balanco = resposta.get_json()['balanco']
    assert balanco['total_lidas'] == POR_ENVIO
    assert len(balanco['aceitas']) == POR_ENVIO
    with app.app_context():
        assert ChaveManifestacao.query.count() == POR_ENVIO


def test_acima_do_teto_de_partes_a_requisicao_inteira_e_recusada(app, ids, client):
    """O motivo de existir o bloco.

    Nao e regra nossa: e o `max_form_parts` do Werkzeug. Fixar aqui e o que faz
    alguem descobrir que o teto mudou ANTES de subir o `POR_ENVIO` do JS e ver
    uma importacao de mil notas morrer com 413 no meio."""
    with app.app_context():
        _empresa()

    arquivos = [(f'nota{i}.xml', b'<a/>') for i in range(TETO_DE_PARTES + 1)]
    resposta = _enviar(client, arquivos)

    assert resposta.status_code == 413
    assert POR_ENVIO <= TETO_DE_PARTES
