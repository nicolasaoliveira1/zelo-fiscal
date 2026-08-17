"""Importacao de chaves por colagem (MANIF-09, MANIF-11).

O balanco nomeia as chaves de cada grupo em vez de so conta-las: "3 recusadas"
manda o operador procurar quais; "estas 3" ele resolve na hora.
"""
from app import db
from app.models import ChaveManifestacao, Empresa, StatusManifestacao
from app.services import manifestador_import as imp

CHAVE_A = '43170107461248000107650010000045391000045390'
CHAVE_B = '43170107461248000107650010000045401000045404'
CHAVE_C = '43170107461248000107650010000045751000045752'
DV_ERRADO = CHAVE_A[:43] + str((int(CHAVE_A[43]) + 1) % 10)


def _empresa(nome='EMPRESA A', cnpj='11.222.333/0001-81'):
    emp = Empresa(nome=nome, cnpj=cnpj, estado='RS', cidade='Imbé')
    db.session.add(emp)
    db.session.commit()
    return emp


# --- caminho feliz ----------------------------------------------------------

def test_importa_chaves_coladas(app, ids):
    with app.app_context():
        emp = _empresa()
        balanco = imp.importar_colagem(emp, f'{CHAVE_A}\n{CHAVE_B}')

        assert balanco.aceitas == [CHAVE_A, CHAVE_B]
        assert ChaveManifestacao.query.count() == 2


def test_chave_importada_nasce_pendente_com_competencia_e_emitente(app, ids):
    with app.app_context():
        emp = _empresa()
        imp.importar_colagem(emp, CHAVE_A)

        linha = ChaveManifestacao.query.filter_by(chave=CHAVE_A).first()
        assert linha.status == StatusManifestacao.PENDENTE
        assert linha.competencia == '2017-01'
        assert linha.cnpj_emitente == '07461248000107'
        assert linha.empresa_id == emp.id
        assert linha.origem == 'colagem'
        assert linha.competencia_ajustada is False


# --- recusas nomeadas -------------------------------------------------------

def test_dv_invalido_e_recusado_nomeando_a_chave(app, ids):
    with app.app_context():
        emp = _empresa()
        balanco = imp.importar_colagem(emp, DV_ERRADO)

        assert balanco.dv_invalido == [DV_ERRADO]
        assert balanco.aceitas == []
        assert ChaveManifestacao.query.count() == 0


def test_chave_ruim_no_meio_nao_impede_as_demais(app, ids):
    """O bloco colado tem dezenas de linhas; abortar por uma obrigaria o
    operador a caçar e recolar tudo."""
    with app.app_context():
        emp = _empresa()
        balanco = imp.importar_colagem(
            emp, f'{CHAVE_A}\n{DV_ERRADO}\n{CHAVE_B}')

        assert balanco.aceitas == [CHAVE_A, CHAVE_B]
        assert balanco.dv_invalido == [DV_ERRADO]


def test_competencia_invalida_e_recusada_nomeando_a_chave(app, ids):
    """AAMM com mes 13 nao existe — e a chave nao pode entrar sem competencia,
    porque e por ela que o operador filtra a fila."""
    with app.app_context():
        emp = _empresa()
        # mes 13 quebra o DV, entao recalcula-se para isolar o motivo
        base = '43' + '1713' + CHAVE_A[6:43]
        chave = base + str(_dv(base))
        balanco = imp.importar_colagem(emp, chave)

        assert balanco.competencia_invalida == [chave]
        assert balanco.aceitas == []


def _dv(chave43):
    peso, soma = 2, 0
    for digito in reversed(chave43):
        soma += int(digito) * peso
        peso = 2 if peso == 9 else peso + 1
    resto = soma % 11
    return 0 if resto in (0, 1) else 11 - resto


def test_texto_sem_chave_nenhuma_devolve_balanco_vazio(app, ids):
    with app.app_context():
        emp = _empresa()
        balanco = imp.importar_colagem(emp, 'nada aqui')

        assert balanco.total_lidas == 0
        assert balanco.aceitas == []


# --- duplicatas (MANIF-11) --------------------------------------------------

def test_mesma_chave_duas_vezes_no_bloco_e_duplicata_nao_erro(app, ids):
    with app.app_context():
        emp = _empresa()
        balanco = imp.importar_colagem(emp, f'{CHAVE_A}\n{CHAVE_A}')

        assert balanco.aceitas == [CHAVE_A]
        assert [d['chave'] for d in balanco.duplicatas] == [CHAVE_A]
        assert ChaveManifestacao.query.count() == 1


def test_reimportar_chave_existente_da_mesma_empresa_e_duplicata(app, ids):
    with app.app_context():
        emp = _empresa()
        imp.importar_colagem(emp, CHAVE_A)

        balanco = imp.importar_colagem(emp, CHAVE_A)

        assert balanco.aceitas == []
        assert balanco.duplicatas[0]['chave'] == CHAVE_A
        assert balanco.duplicatas[0]['status'] == StatusManifestacao.PENDENTE
        assert ChaveManifestacao.query.count() == 1


def test_duplicata_informa_o_desfecho_anterior(app, ids):
    """Saber que a chave ja foi MANIFESTADA muda o que o operador faz com ela."""
    with app.app_context():
        emp = _empresa()
        imp.importar_colagem(emp, CHAVE_A)
        linha = ChaveManifestacao.query.filter_by(chave=CHAVE_A).first()
        linha.status = StatusManifestacao.MANIFESTADA
        linha.protocolo = '143210000123456'
        db.session.commit()

        balanco = imp.importar_colagem(emp, CHAVE_A)

        assert balanco.duplicatas[0]['status'] == StatusManifestacao.MANIFESTADA
        assert balanco.duplicatas[0]['empresa'] == emp.nome


def test_reimportar_chave_existente_de_OUTRA_empresa_tira_da_fila(app, ids):
    """O caso perigoso: a mesma NF-e apontada para duas empresas. Manifestar sob
    a empresa errada e o erro mais caro do fluxo, entao a chave sai da fila e
    espera o operador dizer de quem ela e."""
    with app.app_context():
        emp_a = _empresa('EMPRESA A', '11.222.333/0001-81')
        emp_b = _empresa('EMPRESA B', '22.333.444/0001-92')
        imp.importar_colagem(emp_a, CHAVE_A)

        balanco = imp.importar_colagem(emp_b, CHAVE_A)

        linha = ChaveManifestacao.query.filter_by(chave=CHAVE_A).first()
        assert linha.status == StatusManifestacao.DUPLICATA
        assert balanco.duplicatas[0]['conflito'] is True
        assert linha.empresa_id == emp_a.id


def test_conflito_de_empresa_nao_derruba_chave_ja_manifestada(app, ids):
    """Fato fiscal consumado nao vira pendencia: a manifestacao ja aconteceu."""
    with app.app_context():
        emp_a = _empresa('EMPRESA A', '11.222.333/0001-81')
        emp_b = _empresa('EMPRESA B', '22.333.444/0001-92')
        imp.importar_colagem(emp_a, CHAVE_A)
        linha = ChaveManifestacao.query.filter_by(chave=CHAVE_A).first()
        linha.status = StatusManifestacao.MANIFESTADA
        db.session.commit()

        imp.importar_colagem(emp_b, CHAVE_A)

        assert ChaveManifestacao.query.filter_by(chave=CHAVE_A).first().status \
            == StatusManifestacao.MANIFESTADA


# --- liberacao (MANIF-11) ---------------------------------------------------

def test_liberar_conflito_define_a_empresa_e_devolve_a_fila(app, ids):
    with app.app_context():
        emp_a = _empresa('EMPRESA A', '11.222.333/0001-81')
        emp_b = _empresa('EMPRESA B', '22.333.444/0001-92')
        imp.importar_colagem(emp_a, CHAVE_A)
        imp.importar_colagem(emp_b, CHAVE_A)
        linha = ChaveManifestacao.query.filter_by(chave=CHAVE_A).first()

        assert imp.liberar_duplicata(linha, empresa=emp_b, ator_id=7) is True

        recarregada = db.session.get(ChaveManifestacao, linha.id)
        assert recarregada.status == StatusManifestacao.PENDENTE
        assert recarregada.empresa_id == emp_b.id
        assert recarregada.liberado_por_id == 7


def test_liberar_chave_ja_manifestada_exige_confirmacao(app, ids):
    """Reenviar e inofensivo (a SEFAZ responde duplicidade), mas devolver uma
    nota fechada a fila sem confirmacao esconderia que ela ja saiu."""
    with app.app_context():
        emp = _empresa()
        imp.importar_colagem(emp, CHAVE_A)
        linha = ChaveManifestacao.query.filter_by(chave=CHAVE_A).first()
        linha.status = StatusManifestacao.MANIFESTADA
        db.session.commit()

        assert imp.liberar_duplicata(linha, ator_id=7) is False
        assert db.session.get(ChaveManifestacao, linha.id).status == \
            StatusManifestacao.MANIFESTADA

        assert imp.liberar_duplicata(linha, ator_id=7, confirmar=True) is True
        assert db.session.get(ChaveManifestacao, linha.id).status == \
            StatusManifestacao.PENDENTE


def test_liberar_registra_quem_liberou(app, ids):
    with app.app_context():
        emp = _empresa()
        imp.importar_colagem(emp, CHAVE_A)
        linha = ChaveManifestacao.query.filter_by(chave=CHAVE_A).first()
        linha.status = StatusManifestacao.MANIFESTADA
        db.session.commit()

        imp.liberar_duplicata(linha, ator_id=42, confirmar=True)

        assert db.session.get(ChaveManifestacao, linha.id).liberado_por_id == 42


# --- competencia editavel (MANIF-09) ----------------------------------------

def test_ajustar_competencia_grava_e_marca_como_ajustada(app, ids):
    with app.app_context():
        emp = _empresa()
        imp.importar_colagem(emp, CHAVE_A)
        linha = ChaveManifestacao.query.filter_by(chave=CHAVE_A).first()

        assert imp.ajustar_competencia(linha, '2017-02') is True

        recarregada = db.session.get(ChaveManifestacao, linha.id)
        assert recarregada.competencia == '2017-02'
        assert recarregada.competencia_ajustada is True


def test_ajustar_competencia_recusa_formato_invalido(app, ids):
    with app.app_context():
        emp = _empresa()
        imp.importar_colagem(emp, CHAVE_A)
        linha = ChaveManifestacao.query.filter_by(chave=CHAVE_A).first()

        for ruim in ('2017-13', '17-01', 'janeiro', '', None):
            assert imp.ajustar_competencia(linha, ruim) is False

        assert db.session.get(ChaveManifestacao, linha.id).competencia == '2017-01'


# --- balanco ----------------------------------------------------------------

def test_balanco_soma_todos_os_grupos(app, ids):
    with app.app_context():
        emp = _empresa()
        texto = f'{CHAVE_A}\n{DV_ERRADO}\n{CHAVE_B}\n{CHAVE_B}\n{CHAVE_C}'
        balanco = imp.importar_colagem(emp, texto)

        assert balanco.total_lidas == 5
        assert len(balanco.aceitas) == 3
        assert len(balanco.dv_invalido) == 1
        assert len(balanco.duplicatas) == 1
        assert (len(balanco.aceitas) + len(balanco.dv_invalido)
                + len(balanco.duplicatas) + len(balanco.competencia_invalida)
                + len(balanco.sem_empresa)) == balanco.total_lidas
