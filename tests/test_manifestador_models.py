"""Modelos do manifestador de NF-e (MANIF-03, MANIF-16 — esquema).

Duas tabelas com papeis distintos:

- `CertificadoEmpresa` e 1:1 com `Empresa` e guarda o que o DRIVE informa sobre
  o certificado da empresa. Mesmo principio da `DadosReceita` (AD-024): nao
  escreve em `Empresa`, porque juntar as duas perderia a divergencia.
- `ChaveManifestacao` e a fila duravel por item (AD-010) — nao ha
  `TarefaEmissao` aqui, que exige `certidao_id`.
"""
from datetime import datetime, timedelta

import sqlalchemy as sa

from app import db
from app.models import (
    ChaveManifestacao,
    CertificadoEmpresa,
    Empresa,
    EstadoCertificado,
    StatusManifestacao,
)

CHAVE_A = '43170122333444000181650010000045391000045393'
CHAVE_B = '43170122333444000181650010000045401000045408'


def _empresa(nome='Empresa Manifesto', cnpj='33.000.167/0001-01'):
    emp = Empresa(nome=nome, cnpj=cnpj, estado='RS', cidade='Imbé')
    db.session.add(emp)
    db.session.commit()
    return emp


# --- CertificadoEmpresa -----------------------------------------------------

def test_certificado_e_um_por_empresa(app, ids):
    with app.app_context():
        emp = _empresa()
        emp.certificado = CertificadoEmpresa(
            caminho=r'Z:\PASTAS EMPRESAS\X\DOCUMENTOS\cert.pfx',
            estado=EstadoCertificado.PRONTO)
        db.session.commit()

        recarregada = db.session.get(Empresa, emp.id)
        assert recarregada.certificado.estado == EstadoCertificado.PRONTO
        assert recarregada.certificado.empresa.id == emp.id


def test_remover_empresa_remove_certificado(app, ids):
    """cascade delete-orphan: senao sobra linha orfa com FK pendurada."""
    with app.app_context():
        emp = _empresa()
        emp.certificado = CertificadoEmpresa(caminho='c.pfx',
                                             estado=EstadoCertificado.PRONTO)
        db.session.commit()
        empresa_id = emp.id

        db.session.delete(emp)
        db.session.commit()

        assert CertificadoEmpresa.query.filter_by(empresa_id=empresa_id).count() == 0


def test_uma_empresa_nao_aceita_dois_certificados(app, ids):
    """empresa_id e UNIQUE: o cofre guarda um certificado por empresa."""
    with app.app_context():
        emp = _empresa()
        db.session.add(CertificadoEmpresa(empresa_id=emp.id, caminho='a.pfx',
                                          estado=EstadoCertificado.PRONTO))
        db.session.commit()

        db.session.add(CertificadoEmpresa(empresa_id=emp.id, caminho='b.pfx',
                                          estado=EstadoCertificado.VENCIDO))
        try:
            db.session.commit()
            duplicou = True
        except Exception:
            db.session.rollback()
            duplicou = False
        assert duplicou is False


def test_campos_do_certificado_persistem(app, ids):
    """Round-trip dos campos que o inventario grava (MANIF-03)."""
    with app.app_context():
        emp = _empresa()
        vence = datetime(2027, 7, 20, 12, 0, 0)
        emp.certificado = CertificadoEmpresa(
            caminho=r'Z:\PASTAS EMPRESAS\MARTINS\DOCUMENTOS\CERTIFIC\a.pfx',
            senha_cifrada='gAAAAAB-fake-token',
            subject_cn='MARTINS & FILHOS LTDA:11222333000181',
            issuer_cn='AC SyngularID Multipla',
            cnpj_certificado='11222333000181',
            not_after=vence,
            estado=EstadoCertificado.PRONTO,
            detalhe='',
            verificado_em=datetime.now())
        db.session.commit()

        cert = db.session.get(Empresa, emp.id).certificado
        assert cert.caminho.endswith('a.pfx')
        assert cert.senha_cifrada == 'gAAAAAB-fake-token'
        assert cert.subject_cn == 'MARTINS & FILHOS LTDA:11222333000181'
        assert cert.issuer_cn == 'AC SyngularID Multipla'
        assert cert.cnpj_certificado == '11222333000181'
        assert cert.not_after == vence
        assert cert.verificado_em is not None


def test_estado_do_certificado_e_string_nao_enum_nativo(app, ids):
    """AD-016/AD-020: enum nativo diverge entre SQLite e MySQL."""
    with app.app_context():
        emp = _empresa()
        emp.certificado = CertificadoEmpresa(caminho='c.pfx',
                                             estado='estado_futuro')
        db.session.commit()
        assert db.session.get(Empresa, emp.id).certificado.estado == \
            'estado_futuro'

    assert isinstance(CertificadoEmpresa.__table__.c.estado.type, sa.String)


def test_os_seis_estados_do_cofre_existem():
    """Os estados sairam do inventario real (recon.md), nao de imaginacao."""
    assert EstadoCertificado.PRONTO == 'pronto'
    assert EstadoCertificado.SENHA_PENDENTE == 'senha_pendente'
    assert EstadoCertificado.VENCIDO == 'vencido'
    assert EstadoCertificado.CNPJ_DIVERGENTE == 'cnpj_divergente'
    assert EstadoCertificado.SEM_ARQUIVO == 'sem_arquivo'
    assert EstadoCertificado.SEM_PASTA == 'sem_pasta'


# --- ChaveManifestacao ------------------------------------------------------

def test_chave_pertence_a_empresa(app, ids):
    with app.app_context():
        emp = _empresa()
        db.session.add(ChaveManifestacao(
            chave=CHAVE_A, empresa_id=emp.id, competencia='2017-01',
            status=StatusManifestacao.PENDENTE))
        db.session.commit()

        recarregada = db.session.get(Empresa, emp.id)
        assert [c.chave for c in recarregada.chaves_manifestacao] == [CHAVE_A]


def test_chave_e_unica_no_sistema_inteiro(app, ids):
    """A chave identifica a NF-e globalmente: nao ha duas linhas para a mesma."""
    with app.app_context():
        emp_a = _empresa('A', '33.000.167/0001-01')
        emp_b = _empresa('B', '11.222.333/0001-81')
        db.session.add(ChaveManifestacao(chave=CHAVE_A, empresa_id=emp_a.id,
                                         status=StatusManifestacao.PENDENTE))
        db.session.commit()

        db.session.add(ChaveManifestacao(chave=CHAVE_A, empresa_id=emp_b.id,
                                         status=StatusManifestacao.PENDENTE))
        try:
            db.session.commit()
            duplicou = True
        except Exception:
            db.session.rollback()
            duplicou = False
        assert duplicou is False


def test_remover_empresa_remove_chaves(app, ids):
    with app.app_context():
        emp = _empresa()
        db.session.add(ChaveManifestacao(chave=CHAVE_A, empresa_id=emp.id,
                                         status=StatusManifestacao.PENDENTE))
        db.session.commit()
        empresa_id = emp.id

        db.session.delete(emp)
        db.session.commit()

        assert ChaveManifestacao.query.filter_by(empresa_id=empresa_id).count() == 0


def test_campos_do_desfecho_persistem(app, ids):
    """O que a SEFAZ respondeu e gravado cru — cStat, xMotivo e protocolo."""
    with app.app_context():
        emp = _empresa()
        chave = ChaveManifestacao(
            chave=CHAVE_A, empresa_id=emp.id, competencia='2017-01',
            competencia_ajustada=True, cnpj_emitente='22333444000181',
            origem='colagem', status=StatusManifestacao.MANIFESTADA,
            tipo_evento='210200', cstat='135',
            xmotivo='Evento registrado e vinculado a NF-e',
            protocolo='143210000123456', ja_existia=False,
            manifestado_em=datetime.now())
        db.session.add(chave)
        db.session.commit()

        recarregada = db.session.get(ChaveManifestacao, chave.id)
        assert recarregada.competencia == '2017-01'
        assert recarregada.competencia_ajustada is True
        assert recarregada.cnpj_emitente == '22333444000181'
        assert recarregada.origem == 'colagem'
        assert recarregada.tipo_evento == '210200'
        assert recarregada.cstat == '135'
        assert recarregada.xmotivo == 'Evento registrado e vinculado a NF-e'
        assert recarregada.protocolo == '143210000123456'
        assert recarregada.ja_existia is False
        assert recarregada.manifestado_em is not None


def test_status_da_chave_e_string_nao_enum_nativo(app, ids):
    with app.app_context():
        emp = _empresa()
        db.session.add(ChaveManifestacao(chave=CHAVE_B, empresa_id=emp.id,
                                         status='status_futuro'))
        db.session.commit()
        assert ChaveManifestacao.query.filter_by(chave=CHAVE_B).first().status == \
            'status_futuro'

    assert isinstance(ChaveManifestacao.__table__.c.status.type, sa.String)


def test_os_seis_status_da_chave_existem():
    assert StatusManifestacao.PENDENTE == 'pendente'
    assert StatusManifestacao.ENVIANDO == 'enviando'
    assert StatusManifestacao.MANIFESTADA == 'manifestada'
    assert StatusManifestacao.REJEITADA == 'rejeitada'
    assert StatusManifestacao.INDEFINIDA == 'indefinida'
    assert StatusManifestacao.DUPLICATA == 'duplicata'


def test_status_padrao_e_pendente(app, ids):
    with app.app_context():
        emp = _empresa()
        chave = ChaveManifestacao(chave=CHAVE_A, empresa_id=emp.id)
        db.session.add(chave)
        db.session.commit()
        assert chave.status == StatusManifestacao.PENDENTE
        assert chave.ja_existia is False
        assert chave.competencia_ajustada is False


def test_empresa_e_competencia_tem_indice():
    """A tela filtra por empresa e competencia; sem indice vira varredura."""
    indexados = {tuple(c.name for c in idx.columns)
                 for idx in ChaveManifestacao.__table__.indexes}
    assert ('empresa_id',) in indexados
    assert ('competencia',) in indexados


# --- invariantes de projeto -------------------------------------------------

def test_empresa_nao_ganhou_coluna_de_certificado():
    """AD-024: dado externo vive em tabela propria. Se um certificado (ou uma
    senha) aparecer como coluna da Empresa, a divergencia entre o cadastro e o
    drive deixa de ser observavel — e este teste e o tripwire disso."""
    colunas = {c.name for c in Empresa.__table__.columns}
    assert colunas == {'id', 'nome', 'cnpj', 'estado', 'cidade',
                       'inscricao_mobiliaria'}


def test_carimbos_em_hora_local_naive(app, ids):
    """AD-004: carimbo de dominio e hora LOCAL naive, nao UTC.

    A janela e larga o bastante para o CI lento e para o arredondamento do
    DATETIME do MySQL (AD-020), e estreita o bastante para reprovar UTC, que no
    Brasil fica ~3h distante."""
    with app.app_context():
        emp = _empresa()
        chave = ChaveManifestacao(chave=CHAVE_A, empresa_id=emp.id)
        emp.certificado = CertificadoEmpresa(caminho='c.pfx',
                                             estado=EstadoCertificado.PRONTO)
        db.session.add(chave)
        db.session.commit()

        agora = datetime.now()
        for carimbo in (chave.importado_em, chave.atualizado_em,
                        emp.certificado.verificado_em):
            assert carimbo.tzinfo is None
            assert abs(carimbo - agora) < timedelta(minutes=5)
