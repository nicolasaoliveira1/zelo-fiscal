"""Selecao dos certificados que precisam de alerta (MANIF-26, T1)."""
from datetime import datetime, timedelta

from app import db
from app.models import CertificadoEmpresa, DadosReceita, Empresa, EstadoCertificado
from app.services import manifestador_cofre


def _empresa(nome, cnpj, situacao=None):
    empresa = Empresa(nome=nome, cnpj=cnpj, estado='RS', cidade='Imbe')
    if situacao:
        empresa.dados_receita = DadosReceita(situacao=situacao)
    db.session.add(empresa)
    db.session.flush()
    return empresa


def _certificado(empresa, not_after, estado=EstadoCertificado.PRONTO):
    db.session.add(CertificadoEmpresa(
        empresa_id=empresa.id, not_after=not_after, estado=estado))


def test_inclui_certificado_vencido_com_dias_negativos(app, ids):
    with app.app_context():
        empresa = _empresa('EMPRESA VENCIDA', '22.222.222/2222-22')
        _certificado(empresa, datetime.now() - timedelta(days=3))
        db.session.commit()

        itens = manifestador_cofre.certificados_a_vencer()

        item = next(item for item in itens if item['empresa_id'] == empresa.id)
        assert item['causa'] == 'vencido'
        assert item['dias_restantes'] < 0


def test_vencido_entra_independente_do_estado_gravado(app, ids):
    with app.app_context():
        empresa = _empresa('EMPRESA ESTADO ANTIGO', '22.222.222/2222-23')
        _certificado(empresa, datetime.now() - timedelta(days=1),
                     EstadoCertificado.PRONTO)
        db.session.commit()

        itens = manifestador_cofre.certificados_a_vencer()

        item = next(item for item in itens if item['empresa_id'] == empresa.id)
        assert item['estado'] == EstadoCertificado.PRONTO
        assert item['causa'] == 'vencido'


def test_inclui_certificado_dentro_da_janela(app, ids):
    with app.app_context():
        empresa = _empresa('EMPRESA PROXIMA', '22.222.222/2222-24')
        _certificado(empresa, datetime.now() + timedelta(days=10))
        db.session.commit()

        itens = manifestador_cofre.certificados_a_vencer(dias=10)

        item = next(item for item in itens if item['empresa_id'] == empresa.id)
        assert item['causa'] == 'vencendo'
        assert 0 <= item['dias_restantes'] <= 10


def test_exclui_certificado_fora_da_janela(app, ids):
    with app.app_context():
        empresa = _empresa('EMPRESA DISTANTE', '22.222.222/2222-25')
        _certificado(empresa, datetime.now() + timedelta(days=31))
        db.session.commit()

        itens = manifestador_cofre.certificados_a_vencer(dias=30)

        assert empresa.id not in {item['empresa_id'] for item in itens}


def test_exclui_certificado_sem_data_de_vencimento(app, ids):
    with app.app_context():
        empresa = _empresa('EMPRESA SEM DATA', '22.222.222/2222-26')
        _certificado(empresa, None)
        db.session.commit()

        itens = manifestador_cofre.certificados_a_vencer()

        assert empresa.id not in {item['empresa_id'] for item in itens}


def test_exclui_empresa_nao_ativa_na_receita(app, ids):
    with app.app_context():
        empresa = _empresa('EMPRESA BAIXADA', '22.222.222/2222-27', 'BAIXADA')
        _certificado(empresa, datetime.now() + timedelta(days=5))
        db.session.commit()

        itens = manifestador_cofre.certificados_a_vencer()

        assert empresa.id not in {item['empresa_id'] for item in itens}


def test_ordena_do_mais_critico_para_o_menos(app, ids):
    with app.app_context():
        mais_critica = _empresa('EMPRESA MAIS CRITICA', '22.222.222/2222-28')
        proxima = _empresa('EMPRESA PROXIMA', '22.222.222/2222-29')
        _certificado(mais_critica, datetime.now() - timedelta(days=4))
        _certificado(proxima, datetime.now() + timedelta(days=2))
        db.session.commit()

        itens = manifestador_cofre.certificados_a_vencer()
        selecionados = [item for item in itens if item['empresa_id'] in {
            mais_critica.id, proxima.id}]

        assert [item['empresa_id'] for item in selecionados] == [
            mais_critica.id, proxima.id]
