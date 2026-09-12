from datetime import datetime, timedelta

import pytest

from app import db
from app.models import (
    CertificadoEmpresa,
    ConfiguracaoNfse,
    Empresa,
    EstadoCertificado,
)
from app.services import manifestador_cofre
from app.services import nfse_api_credencial as credencial
from app.services.pem_temporario import Credencial


def _configurar_empresa(app, ids, estado=None, not_after=None):
    with app.app_context():
        db.session.add(ConfiguracaoNfse(
            id=1, empresa_escritorio_id=ids['empresa']))
        if estado is not None:
            certificado = CertificadoEmpresa(
                empresa_id=ids['empresa'], caminho='certificado-sintetico.pfx',
                estado=estado, not_after=not_after)
            db.session.add(certificado)
        db.session.commit()


def test_diagnostico_sem_empresa_e_sem_excecao(app, ids):
    with app.app_context():
        diagnostico = credencial.diagnostico()

    assert diagnostico.disponivel is False
    assert diagnostico.causa == 'sem_empresa'
    assert diagnostico.cnpj is None
    assert diagnostico.not_after is None


def test_diagnostico_sem_certificado(app, ids):
    _configurar_empresa(app, ids)

    with app.app_context():
        diagnostico = credencial.diagnostico()
        cnpj = db.session.get(Empresa, ids['empresa']).cnpj

    assert diagnostico.disponivel is False
    assert diagnostico.causa == 'sem_certificado'
    assert diagnostico.cnpj == cnpj


def test_diagnostico_certificado_nao_pronto(app, ids):
    _configurar_empresa(app, ids, EstadoCertificado.SENHA_PENDENTE)

    with app.app_context():
        diagnostico = credencial.diagnostico()

    assert diagnostico.disponivel is False
    assert diagnostico.causa == 'nao_pronto'


def test_diagnostico_certificado_vencido_preserva_a_data(app, ids):
    vencimento = datetime.now().replace(microsecond=0) - timedelta(days=1)
    _configurar_empresa(app, ids, EstadoCertificado.VENCIDO, vencimento)

    with app.app_context():
        diagnostico = credencial.diagnostico()

    assert diagnostico.disponivel is False
    assert diagnostico.causa == 'vencido'
    assert diagnostico.causa != 'indisponivel'
    assert diagnostico.not_after == vencimento


def test_diagnostico_certificado_pronto(app, ids):
    vencimento = datetime.now().replace(microsecond=0) + timedelta(days=30)
    _configurar_empresa(app, ids, EstadoCertificado.PRONTO, vencimento)

    with app.app_context():
        diagnostico = credencial.diagnostico()

    assert diagnostico.disponivel is True
    assert diagnostico.causa == 'ok'
    assert diagnostico.not_after == vencimento


def test_credencial_do_escritorio_adapta_o_retorno_do_cofre(monkeypatch,
                                                            app, ids):
    _configurar_empresa(app, ids, EstadoCertificado.PRONTO)
    recebido = []

    def cofre_falso(empresa):
        recebido.append(empresa.id)
        return ('certificado-sintetico.pfx', 'senha-sintetica')

    monkeypatch.setattr(manifestador_cofre, 'credencial', cofre_falso)

    with app.app_context():
        resultado = credencial.credencial_do_escritorio()

    assert isinstance(resultado, Credencial)
    assert resultado.caminho == 'certificado-sintetico.pfx'
    assert resultado.senha == 'senha-sintetica'
    assert recebido == [ids['empresa']]
    assert 'senha-sintetica' not in repr(resultado)


def test_credencial_do_escritorio_nao_chama_cofre_sem_empresa(monkeypatch,
                                                               app, ids):
    monkeypatch.setattr(
        manifestador_cofre, 'credencial',
        lambda _empresa: pytest.fail('cofre não deve ser consultado'))

    with app.app_context():
        resultado = credencial.credencial_do_escritorio()

    assert resultado is None
