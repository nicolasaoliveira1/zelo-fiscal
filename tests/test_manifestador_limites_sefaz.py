"""Limites de uso do webservice da SEFAZ (NT 2018.002 e Ajuste SINIEF 14/2026).

Tres regras externas que o codigo tem de respeitar, e que nao sao opcionais:

1. **Consumo indevido (cStat 656)** — o mesmo evento com a mesma rejeicao mais
   de 20 vezes bloqueia o CNPJ por 1 hora. Continuar enviando durante o bloqueio
   REINICIA o cronometro, e 50 bloqueios consecutivos viram bloqueio PERMANENTE,
   que so a SEFAZ destrava. Por isso 656 PARA o lote em vez de virar mais uma
   linha vermelha.
2. **Teto de reenvios** — paramos em 3, muito antes dos 20: se a rejeicao nao
   mudou, o proximo envio tambem nao muda.
3. **Prazo de 90 dias** (era 180 ate 01/06/2026) — passado o prazo a SEFAZ
   registra Confirmacao automatica, e manifestar vira rejeicao certa.
"""
from datetime import date

from app import db
from app.automation.batch_state import MANIF_BATCH_LOCK, MANIF_BATCH_STATE
from app.models import (
    CertificadoEmpresa,
    ChaveManifestacao,
    Empresa,
    EstadoCertificado,
    StatusManifestacao,
)
from app.services import batch_engine, circuit_breaker
from app.services import manifestador_import as imp
from app.services import manifestador_lote as lote
from app.services import manifestador_service as svc
from app.services.nfe_sefaz import RespostaSefaz
from tests.test_manifestador_cofre import _fazer_pfx

CHAVE = '43170122333444000181650010000045391000045393'


def _empresa_pronta(tmp_path):
    emp = Empresa(nome='EMPRESA A', cnpj='11.222.333/0001-81', estado='RS',
                  cidade='Imbé')
    db.session.add(emp)
    db.session.commit()
    caminho = tmp_path / 'a.pfx'
    caminho.write_bytes(_fazer_pfx(cn='EMPRESA A LTDA:11222333000181'))
    emp.certificado = CertificadoEmpresa(caminho=str(caminho),
                                         estado=EstadoCertificado.PRONTO)
    db.session.commit()
    return emp


def _chave(empresa, status=StatusManifestacao.PENDENTE, tentativas=0, cstat=None):
    linha = ChaveManifestacao(chave=CHAVE, empresa_id=empresa.id,
                              competencia='2017-01', status=status,
                              tentativas=tentativas, cstat=cstat)
    db.session.add(linha)
    db.session.commit()
    return linha


class _EnvioFalso:
    def __init__(self, resposta):
        self.resposta = resposta
        self.chamadas = 0

    def __call__(self, evento, credencial=None, ambiente=None, **kwargs):
        self.chamadas += 1
        return self.resposta


def _resposta_656():
    return RespostaSefaz(cstat='656', xmotivo='Rejeicao: Consumo Indevido',
                         bruto='<x/>')


# --- 1. consumo indevido (656) ----------------------------------------------

def test_656_nao_marca_a_chave_como_rejeitada(app, ids, tmp_path, monkeypatch):
    """O evento nem foi avaliado: a SEFAZ recusou a REQUISICAO. Marcar a nota
    como rejeitada culparia a nota por um bloqueio que e nosso."""
    with app.app_context():
        emp = _empresa_pronta(tmp_path)
        linha = _chave(emp)
        monkeypatch.setattr(svc, 'enviar_evento', _EnvioFalso(_resposta_656()))

        resultado = svc.manifestar(linha.id)

        recarregada = db.session.get(ChaveManifestacao, linha.id)
        assert resultado.sucesso is False
        assert resultado.consumo_indevido is True
        assert recarregada.status == StatusManifestacao.PENDENTE
        assert recarregada.status != StatusManifestacao.REJEITADA


def test_656_avisa_que_insistir_piora(app, ids, tmp_path, monkeypatch):
    with app.app_context():
        emp = _empresa_pronta(tmp_path)
        linha = _chave(emp)
        monkeypatch.setattr(svc, 'enviar_evento', _EnvioFalso(_resposta_656()))

        resultado = svc.manifestar(linha.id)

        assert '656' in resultado.mensagem
        assert '1 hora' in resultado.mensagem


def test_656_PARA_o_lote_em_vez_de_seguir_para_a_proxima(app, ids, tmp_path,
                                                         monkeypatch):
    """O teste que protege o CNPJ do cliente: com 200 chaves na fila, seguir em
    frente seriam 200 requisicoes durante um bloqueio ativo, reiniciando o
    cronometro a cada uma — o caminho para o bloqueio permanente."""
    with app.app_context():
        emp = _empresa_pronta(tmp_path)
        linha = _chave(emp)
        monkeypatch.setattr(lote, 'manifestar',
                            lambda *a, **k: svc.Resultado(
                                False, 'bloqueado', _resposta_656()))
        batch_engine.reset_batch_state(MANIF_BATCH_STATE)
        with MANIF_BATCH_LOCK:
            MANIF_BATCH_STATE['status'] = 'running'
        try:
            lote._manifestar_item(linha.id, None, 'exec-1')

            # `request_pause` marca a parada e ja vira o status para
            # `paused` quando o lote estava rodando — retomavel, com a fila
            # intacta para depois do bloqueio de 1 hora.
            assert MANIF_BATCH_STATE['stop_requested'] is True
            assert MANIF_BATCH_STATE['stop_action'] == 'pause'
            assert MANIF_BATCH_STATE['status'] == 'paused'
        finally:
            batch_engine.reset_batch_state(MANIF_BATCH_STATE)
            circuit_breaker.limpar()


def test_656_alimenta_o_breaker_apesar_de_ter_cstat(app, ids):
    """A regra geral e "cStat presente = SEFAZ no ar, nao alimenta o breaker".
    656 e a excecao: ela responde justamente para dizer que o nosso acesso esta
    bloqueado, que e o que o breaker representa."""
    circuit_breaker.limpar()
    try:
        for _ in range(circuit_breaker.LIMIAR_PADRAO):
            lote._alimentar_breaker(
                svc.Resultado(False, 'bloqueado', _resposta_656()))

        assert circuit_breaker.aberto(lote.ALVO_BREAKER) is True
    finally:
        circuit_breaker.limpar()


def test_rejeicao_comum_continua_sem_alimentar_o_breaker(app, ids):
    """Contraprova: 596 nao pode parar o lote — a SEFAZ esta no ar e o problema
    e daquela nota."""
    circuit_breaker.limpar()
    try:
        for _ in range(circuit_breaker.LIMIAR_PADRAO * 2):
            lote._alimentar_breaker(svc.Resultado(
                False, 'recusada',
                RespostaSefaz(cstat='596', xmotivo='NF-e nao consta')))

        assert circuit_breaker.aberto(lote.ALVO_BREAKER) is False
    finally:
        circuit_breaker.limpar()


# --- 2. teto de reenvios ----------------------------------------------------

def test_mesma_rejeicao_incrementa_o_contador(app, ids, tmp_path, monkeypatch):
    with app.app_context():
        emp = _empresa_pronta(tmp_path)
        linha = _chave(emp)
        recusa = RespostaSefaz(cstat='596', xmotivo='NF-e nao consta',
                               bruto='<x/>')
        monkeypatch.setattr(svc, 'enviar_evento', _EnvioFalso(recusa))

        svc.manifestar(linha.id)
        assert db.session.get(ChaveManifestacao, linha.id).tentativas == 1

        svc.manifestar(linha.id)
        assert db.session.get(ChaveManifestacao, linha.id).tentativas == 2


def test_rejeicao_DIFERENTE_zera_o_contador(app, ids, tmp_path, monkeypatch):
    """A regra da SEFAZ e sobre a MESMA rejeicao repetida. Problema diferente e
    contagem nova — senao uma nota que falhou por dois motivos distintos seria
    punida como se estivesse martelando o mesmo erro."""
    with app.app_context():
        emp = _empresa_pronta(tmp_path)
        linha = _chave(emp, status=StatusManifestacao.REJEITADA,
                       tentativas=2, cstat='596')
        outra = RespostaSefaz(cstat='597', xmotivo='Outro motivo', bruto='<x/>')
        monkeypatch.setattr(svc, 'enviar_evento', _EnvioFalso(outra))

        svc.manifestar(linha.id)

        assert db.session.get(ChaveManifestacao, linha.id).tentativas == 1


def test_sucesso_zera_o_contador(app, ids, tmp_path, monkeypatch):
    with app.app_context():
        emp = _empresa_pronta(tmp_path)
        linha = _chave(emp, status=StatusManifestacao.REJEITADA,
                       tentativas=2, cstat='596')
        ok = RespostaSefaz(cstat='135', xmotivo='Evento registrado',
                           protocolo='1', bruto='<x/>')
        monkeypatch.setattr(svc, 'enviar_evento', _EnvioFalso(ok))

        svc.manifestar(linha.id)

        assert db.session.get(ChaveManifestacao, linha.id).tentativas == 0


def test_chave_no_teto_sai_da_fila(app, ids, tmp_path):
    """Muito antes dos 20 da SEFAZ: se a rejeicao nao mudou em 3 tentativas, o
    proximo envio tambem nao vai mudar."""
    with app.app_context():
        emp = _empresa_pronta(tmp_path)
        linha = _chave(emp, status=StatusManifestacao.REJEITADA,
                       tentativas=svc.TETO_REENVIOS, cstat='596')

        assert svc.manifestavel(linha) is False
        assert lote.calcular_alvos(modo='carteira')['ids'] == []


def test_abaixo_do_teto_continua_na_fila(app, ids, tmp_path):
    with app.app_context():
        emp = _empresa_pronta(tmp_path)
        linha = _chave(emp, status=StatusManifestacao.REJEITADA,
                       tentativas=svc.TETO_REENVIOS - 1, cstat='596')

        assert svc.manifestavel(linha) is True
        assert lote.calcular_alvos(modo='carteira')['ids'] == [linha.id]


def test_teto_fica_bem_abaixo_do_limite_da_sefaz():
    """20 e onde a SEFAZ bloqueia; parar perto disso ja teria custado o CNPJ."""
    assert svc.TETO_REENVIOS <= 5


def test_chave_no_teto_nao_e_reenviada_pelo_servico(app, ids, tmp_path,
                                                    monkeypatch):
    with app.app_context():
        emp = _empresa_pronta(tmp_path)
        linha = _chave(emp, status=StatusManifestacao.REJEITADA,
                       tentativas=svc.TETO_REENVIOS, cstat='596')
        envio = _EnvioFalso(RespostaSefaz(cstat='596', bruto='<x/>'))
        monkeypatch.setattr(svc, 'enviar_evento', envio)

        resultado = svc.manifestar(linha.id)

        assert resultado.sucesso is False
        assert envio.chamadas == 0


# --- 3. prazo de 90 dias ----------------------------------------------------

def test_nota_recente_esta_no_prazo():
    # chave com AAMM do mes corrente
    hoje = date(2026, 8, 18)
    chave = '43' + '2608' + CHAVE[6:]
    assert imp.fora_do_prazo(chave, hoje=hoje) is False


def test_nota_de_mais_de_90_dias_e_sinalizada():
    """Ajuste SINIEF 14/2026: 90 dias da autorizacao, nao mais 180."""
    hoje = date(2026, 8, 18)
    chave = '43' + '2601' + CHAVE[6:]   # janeiro/2026
    assert imp.fora_do_prazo(chave, hoje=hoje) is True


def test_a_medida_parte_do_FIM_do_mes_da_chave():
    """A chave so tem o mes de emissao; medir do fim do mes e o ponto mais
    tardio possivel da autorizacao, entao o aviso nunca acusa nota que ainda
    esta no prazo."""
    # abril/2026 termina em 30/04; 90 dias depois e 29/07
    chave = '43' + '2604' + CHAVE[6:]
    assert imp.fora_do_prazo(chave, hoje=date(2026, 7, 28)) is False
    assert imp.fora_do_prazo(chave, hoje=date(2026, 8, 1)) is True


def test_prazo_e_de_90_dias_e_nao_de_180():
    assert imp.DIAS_PRAZO_MANIFESTACAO == 90


def test_import_avisa_sem_recusar(app, ids):
    """AVISO, nao bloqueio: `AAMM` e aproximacao, e recusar com base nela
    impediria manifestacao legitima."""
    with app.app_context():
        emp = Empresa(nome='E', cnpj='11.222.333/0001-81', estado='RS',
                      cidade='Imbé')
        db.session.add(emp)
        db.session.commit()

        balanco = imp.importar_colagem(emp, CHAVE)   # AAMM 1701, bem antigo

        assert balanco.aceitas == [CHAVE]
        assert balanco.fora_do_prazo == [CHAVE]
        assert ChaveManifestacao.query.count() == 1
