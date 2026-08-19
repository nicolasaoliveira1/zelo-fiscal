"""Chave de acesso da NF-e: extracao de texto sujo, DV e competencia
(MANIF-07, MANIF-08, MANIF-09).

Camada conferivel, zero rede — e onde o erro e barato. Chave errada descoberta
aqui nao custa nada; descoberta na SEFAZ custa uma rejeicao e a duvida sobre o
que de fato aconteceu.

As tres chaves usadas sao REAIS, lidas de NF-e assinadas no drive
(`.specs/features/manifestador-nfe/recon.md` §3).
"""
from app.services import manifestador_import as imp

CHAVE_A = '43170107461248000107650010000045391000045390'
CHAVE_B = '43170107461248000107650010000045401000045404'
CHAVE_C = '43170107461248000107650010000045751000045752'


# --- digito verificador (MANIF-08) ------------------------------------------

def test_dv_das_chaves_reais_confere():
    for chave in (CHAVE_A, CHAVE_B, CHAVE_C):
        assert imp.dv_valido(chave) is True


def test_dv_adulterado_e_recusado():
    """Troca so o ultimo digito: e o erro tipico de digitacao/leitura."""
    for chave in (CHAVE_A, CHAVE_B, CHAVE_C):
        errado = chave[:43] + str((int(chave[43]) + 1) % 10)
        assert imp.dv_valido(errado) is False


def test_digito_trocado_no_meio_e_recusado():
    """O modulo 11 pega troca de digito no corpo, nao so no verificador."""
    quebrada = CHAVE_A[:10] + str((int(CHAVE_A[10]) + 1) % 10) + CHAVE_A[11:]
    assert imp.dv_valido(quebrada) is False


def test_chave_com_tamanho_errado_e_recusada():
    assert imp.dv_valido(CHAVE_A[:43]) is False
    assert imp.dv_valido(CHAVE_A + '0') is False
    assert imp.dv_valido('') is False
    assert imp.dv_valido(None) is False


# --- decomposicao (MANIF-09) ------------------------------------------------

def test_decompor_separa_os_nove_campos():
    partes = imp.decompor(CHAVE_A)
    assert partes.cuf == '43'
    assert partes.aamm == '1701'
    assert partes.cnpj_emitente == '07461248000107'
    assert partes.modelo == '65'
    assert partes.serie == '001'
    assert partes.numero == '000004539'
    assert partes.dv == '0'


def test_o_cnpj_da_chave_e_do_emitente_nao_do_destinatario():
    """Por isso a chave sozinha NAO identifica a empresa da carteira: quem
    aparece nos digitos 7-20 e quem emitiu a nota."""
    assert imp.decompor(CHAVE_A).cnpj_emitente == '07461248000107'


def test_competencia_sai_dos_digitos_3_a_6():
    assert imp.competencia_da_chave(CHAVE_A) == '2017-01'


def test_competencia_vira_o_ano_corretamente():
    """AAMM=2512 e dezembro de 2025, nao dezembro de 2012."""
    chave = '43' + '2512' + CHAVE_A[6:]
    assert imp.competencia_da_chave(chave) == '2025-12'


def test_competencia_com_mes_invalido_e_recusada():
    for mes in ('00', '13', '99'):
        chave = '43' + '17' + mes + CHAVE_A[6:]
        assert imp.competencia_da_chave(chave) is None


# --- extracao de texto sujo (MANIF-07) --------------------------------------

def test_extrai_chave_de_linha_limpa():
    assert imp.extrair_chaves(CHAVE_A) == [CHAVE_A]


def test_extrai_com_espacos_a_cada_quatro_digitos():
    """Formato tipico do copiar-colar de DANFE em PDF."""
    espacada = ' '.join(CHAVE_A[i:i + 4] for i in range(0, 44, 4))
    assert imp.extrair_chaves(espacada) == [CHAVE_A]


def test_extrai_com_pontos_e_hifens():
    pontuada = CHAVE_A[:10] + '.' + CHAVE_A[10:20] + '-' + CHAVE_A[20:]
    assert imp.extrair_chaves(pontuada) == [CHAVE_A]


def test_extrai_varias_chaves_uma_por_linha():
    texto = f'{CHAVE_A}\n{CHAVE_B}\n{CHAVE_C}\n'
    assert imp.extrair_chaves(texto) == [CHAVE_A, CHAVE_B, CHAVE_C]


def test_ignora_rotulos_e_lixo_ao_redor():
    texto = (f'Chave de acesso: {CHAVE_A}\n'
             f'--- separador ---\n'
             f'NFe numero 4540  {CHAVE_B}\n')
    assert imp.extrair_chaves(texto) == [CHAVE_A, CHAVE_B]


def test_texto_sem_chave_devolve_lista_vazia():
    assert imp.extrair_chaves('nenhuma chave aqui, so 12345') == []
    assert imp.extrair_chaves('') == []
    assert imp.extrair_chaves(None) == []


def test_chaves_grudadas_sem_separador_sao_separadas():
    """Um bloco de 88 digitos sao duas chaves, nao uma chave e sobra."""
    assert imp.extrair_chaves(CHAVE_A + CHAVE_B) == [CHAVE_A, CHAVE_B]


def test_bloco_de_tamanho_impossivel_nao_vira_chave():
    """A regra e deliberada: um bloco de digitos ou tem 44, ou e multiplo de 44,
    ou nao e chave. Recortar uma janela de 44 de um bloco de 49 produziria uma
    chave DESALINHADA — e manifestar a NF-e de outra pessoa e o erro mais caro
    que esta camada existe para impedir."""
    assert imp.extrair_chaves(CHAVE_A + '12345') == []
    assert imp.extrair_chaves('9' * 43) == []
    assert imp.extrair_chaves('9' * 45) == []


def test_numero_da_nota_antes_da_chave_e_descartado():
    """MANIF-07 manda ignorar numero de nota ao redor. O descarte so acontece em
    fronteira de separador que ja existia no texto, e so quando o DV da fatia
    resultante confere — ou seja, nunca produz janela desalinhada."""
    assert imp.extrair_chaves(f'12345 {CHAVE_A}') == [CHAVE_A]
    assert imp.extrair_chaves(f'NFe numero 4540  {CHAVE_B}') == [CHAVE_B]


def test_digitos_grudados_na_chave_sem_separador_nao_viram_chave_torta():
    """Sem separador nao ha fronteira segura para recortar: um bloco unico de 49
    digitos e recusado inteiro. Recortar uma janela de 44 dali apontaria para a
    NF-e de outra pessoa, e manifestar a nota errada nao tem desfazer."""
    assert imp.extrair_chaves(CHAVE_A + '12345') == []
    assert imp.extrair_chaves('12345' + CHAVE_A) == []


def test_extracao_preserva_a_ordem_do_texto():
    texto = f'{CHAVE_C}\n{CHAVE_A}\n{CHAVE_B}'
    assert imp.extrair_chaves(texto) == [CHAVE_C, CHAVE_A, CHAVE_B]


def test_chave_repetida_no_texto_aparece_duas_vezes():
    """Deduplicar aqui esconderia do operador que ele colou repetido; quem
    decide o que fazer com duplicata e a importacao (MANIF-11)."""
    assert imp.extrair_chaves(f'{CHAVE_A}\n{CHAVE_A}') == [CHAVE_A, CHAVE_A]
