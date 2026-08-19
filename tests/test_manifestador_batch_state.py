"""Estado do lote de manifestacao e alvo do breaker (MANIF-18).

Duas coisas pequenas mas com consequencia grande se erradas: o lote precisa de
lock e estado PROPRIOS (para nao brigar com certidao nem NFSe) e o breaker
precisa do alvo declarado no proprio `circuit_breaker` — onde os dois lados o
enxergam.
"""
from app.automation import batch_state
from app.services import circuit_breaker, portal_health


# --- estado proprio do lote -------------------------------------------------

def test_manifestador_tem_lock_e_estado_proprios():
    assert batch_state.MANIF_BATCH_LOCK is not None
    assert batch_state.MANIF_BATCH_STATE is not None
    assert batch_state.MANIF_BATCH_LOCK is not batch_state.NFSE_BATCH_LOCK
    assert batch_state.MANIF_BATCH_STATE is not batch_state.NFSE_BATCH_STATE


def test_estado_nasce_com_as_chaves_do_motor():
    """Espelha `batch_state_defaults()`: o payload de status e compartilhado com
    os lotes de certidao, e uma chave faltando quebraria o polling da tela."""
    from app.services import batch_engine

    assert set(batch_state.MANIF_BATCH_STATE) >= set(
        batch_engine.batch_state_defaults())


def test_opcoes_ficam_fora_do_estado_do_lote():
    """Mesma razao do NFSe: `init_batch_run` chama `reset_batch_state` dentro do
    lock, entao opcao escrita no estado antes de iniciar seria apagada, e
    escrever depois correria com o worker ja lendo."""
    opcoes = batch_state.manif_batch_opcoes()

    assert 'modo' in opcoes
    assert 'tipo_evento' in opcoes
    assert 'modo' not in batch_state.MANIF_BATCH_STATE


def test_opcoes_devolvem_copia_e_nao_o_dicionario_vivo():
    opcoes = batch_state.manif_batch_opcoes()
    opcoes['modo'] = 'mexido'

    assert batch_state.manif_batch_opcoes()['modo'] != 'mexido'


def test_definir_opcoes_altera_o_que_o_worker_le():
    try:
        batch_state.definir_manif_opcoes(modo='carteira', tipo_evento='210220')
        opcoes = batch_state.manif_batch_opcoes()

        assert opcoes['modo'] == 'carteira'
        assert opcoes['tipo_evento'] == '210220'
    finally:
        batch_state.definir_manif_opcoes(modo='empresa', tipo_evento='210200')


def test_lote_do_manifestador_nao_entra_na_guarda_do_navegador():
    """A guarda global existe porque so ha UM Chrome. O manifestador nao abre
    navegador nenhum, entao inclui-lo faria um lote de manifestacao bloquear a
    emissao de certidao sem motivo."""
    rotulos = [rotulo for rotulo, _lock, _estado
               in batch_state._LOTES_REGISTRADOS]

    assert 'Manifestador' not in rotulos


# --- alvo do breaker --------------------------------------------------------

def test_alvo_da_sefaz_mora_no_circuit_breaker():
    """Os rotulos sao a chave dos DOIS lados (quem alimenta e o painel que le).
    Declarar em outro lugar faria a tela mostrar verde num portal que o lote ja
    parou de tentar (AD-026)."""
    assert circuit_breaker.ALVO_SEFAZ_AN
    assert isinstance(circuit_breaker.ALVO_SEFAZ_AN, str)


def test_alvo_da_sefaz_nao_colide_com_os_existentes():
    existentes = {circuit_breaker.ALVO_FGTS, circuit_breaker.ALVO_ESTADUAL_RS,
                  circuit_breaker.ALVO_TRABALHISTA, circuit_breaker.ALVO_FEDERAL,
                  circuit_breaker.ALVO_MUNICIPAL_GENERICO}

    assert circuit_breaker.ALVO_SEFAZ_AN not in existentes


def test_breaker_abre_e_fecha_para_o_alvo_da_sefaz():
    alvo = circuit_breaker.ALVO_SEFAZ_AN
    circuit_breaker.limpar()
    try:
        assert circuit_breaker.aberto(alvo) is False

        for _ in range(circuit_breaker.LIMIAR_PADRAO):
            circuit_breaker.registrar_falha(alvo, mensagem='SEFAZ fora')

        assert circuit_breaker.aberto(alvo) is True
    finally:
        circuit_breaker.limpar()


def test_sucesso_zera_a_contagem_do_alvo():
    alvo = circuit_breaker.ALVO_SEFAZ_AN
    circuit_breaker.limpar()
    try:
        circuit_breaker.registrar_falha(alvo, mensagem='oscilou')
        circuit_breaker.registrar_sucesso(alvo)

        for _ in range(circuit_breaker.LIMIAR_PADRAO - 1):
            circuit_breaker.registrar_falha(alvo, mensagem='oscilou')

        assert circuit_breaker.aberto(alvo) is False
    finally:
        circuit_breaker.limpar()


def test_painel_de_saude_enxerga_o_alvo_novo():
    """Sem lista paralela: se o painel nao conhecer o alvo, um lote pausado por
    breaker apareceria como portal saudavel."""
    chaves = {chave for chave, _nome, _url in portal_health._portais_fixos()}

    assert circuit_breaker.ALVO_SEFAZ_AN in chaves
