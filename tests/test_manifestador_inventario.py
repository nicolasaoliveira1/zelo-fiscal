"""Inventario do cofre: varredura do drive e os 6 estados (MANIF-01/03/04/06).

Os estados nao foram imaginados — reproduzem o que a varredura real das 93
empresas ativas produziu (`.specs/features/manifestador-nfe/recon.md`):
69 prontas, 10 vencidas, 9 sem arquivo, 4 com senha diferente e 1 so com e-CPF
de socios (BOLL REPRESENTACOES).
"""
from datetime import datetime

from app import db
from app.models import CertificadoEmpresa, Empresa, EstadoCertificado
from app.services import manifestador_cofre as cofre
from tests.test_manifestador_cofre import _fazer_pfx


def _empresa(nome, cnpj):
    emp = Empresa(nome=nome, cnpj=cnpj, estado='RS', cidade='Imbé')
    db.session.add(emp)
    db.session.commit()
    return emp


def _montar_drive(tmp_path, monkeypatch, pastas_por_empresa):
    """Substitui a busca da pasta no `Z:` por um mapa nome->pasta local."""
    monkeypatch.setattr(cofre, 'encontrar_pasta_empresa',
                        lambda nome: pastas_por_empresa.get(nome))
    monkeypatch.setattr(cofre, 'rede_disponivel', lambda: True)
    return tmp_path


def _estado(empresa_id):
    return db.session.get(CertificadoEmpresa,
                          CertificadoEmpresa.query.filter_by(
                              empresa_id=empresa_id).first().id).estado


# --- os seis estados --------------------------------------------------------

def test_pronto_quando_abre_casa_e_esta_valido(app, ids, tmp_path, monkeypatch):
    with app.app_context():
        emp = _empresa('EMPRESA PRONTA', '11.222.333/0001-81')
        pasta = tmp_path / 'PRONTA' / 'DOCUMENTOS' / 'CERTIFIC'
        pasta.mkdir(parents=True)
        (pasta / 'qualquer-nome.pfx').write_bytes(
            _fazer_pfx(cn='EMPRESA PRONTA LTDA:11222333000181'))
        _montar_drive(tmp_path, monkeypatch, {'EMPRESA PRONTA': tmp_path / 'PRONTA'})

        cofre.inventariar()

        cert = db.session.get(Empresa, emp.id).certificado
        assert cert.estado == EstadoCertificado.PRONTO
        assert cert.cnpj_certificado == '11222333000181'
        assert cert.subject_cn == 'EMPRESA PRONTA LTDA:11222333000181'


def test_vencido_quando_o_cnpj_bate_mas_a_validade_passou(app, ids, tmp_path,
                                                          monkeypatch):
    with app.app_context():
        emp = _empresa('EMPRESA VENCIDA', '11.222.333/0001-81')
        pasta = tmp_path / 'VENC'
        pasta.mkdir()
        (pasta / 'c.pfx').write_bytes(
            _fazer_pfx(cn='EMPRESA VENCIDA:11222333000181', dias_validade=-5))
        _montar_drive(tmp_path, monkeypatch, {'EMPRESA VENCIDA': pasta})

        cofre.inventariar()

        assert db.session.get(Empresa, emp.id).certificado.estado == \
            EstadoCertificado.VENCIDO


def test_senha_pendente_quando_nenhuma_senha_conhecida_abre(app, ids, tmp_path,
                                                            monkeypatch):
    with app.app_context():
        emp = _empresa('EMPRESA SENHA', '11.222.333/0001-81')
        pasta = tmp_path / 'SENHA'
        pasta.mkdir()
        (pasta / 'c.pfx').write_bytes(
            _fazer_pfx(cn='X:11222333000181', senha=b'Isa@2110'))
        _montar_drive(tmp_path, monkeypatch, {'EMPRESA SENHA': pasta})

        cofre.inventariar()

        cert = db.session.get(Empresa, emp.id).certificado
        assert cert.estado == EstadoCertificado.SENHA_PENDENTE
        assert cert.senha_cifrada is None
        assert 'c.pfx' in cert.caminho


def test_cnpj_divergente_quando_so_ha_e_cpf_de_socios(app, ids, tmp_path,
                                                      monkeypatch):
    """O caso BOLL REPRESENTACOES: a pasta tem 4 e-CPF de socios e nenhum
    e-CNPJ. Manifestar por e-CPF exigiria procuracao eletronica."""
    with app.app_context():
        emp = _empresa('BOLL REPRESENTACOES', '02.668.535/0001-60')
        pasta = tmp_path / 'BOLL'
        pasta.mkdir()
        (pasta / 'JOSE FELIPE BOLL.p12').write_bytes(
            _fazer_pfx(cn='JOSE FELIPE BOLL:92278361015'))
        (pasta / 'MAICO FRAGA ABEL.p12').write_bytes(
            _fazer_pfx(cn='MAICO FRAGA ABEL:83567984004'))
        _montar_drive(tmp_path, monkeypatch, {'BOLL REPRESENTACOES': pasta})

        cofre.inventariar()

        cert = db.session.get(Empresa, emp.id).certificado
        assert cert.estado == EstadoCertificado.CNPJ_DIVERGENTE
        assert 'JOSE FELIPE BOLL' in cert.detalhe


def test_sem_arquivo_quando_a_pasta_existe_e_esta_sem_pfx(app, ids, tmp_path,
                                                          monkeypatch):
    with app.app_context():
        emp = _empresa('EMPRESA VAZIA', '11.222.333/0001-81')
        pasta = tmp_path / 'VAZIA'
        (pasta / 'DOCUMENTOS').mkdir(parents=True)
        (pasta / 'DOCUMENTOS' / 'contrato.pdf').write_bytes(b'%PDF')
        _montar_drive(tmp_path, monkeypatch, {'EMPRESA VAZIA': pasta})

        cofre.inventariar()

        assert db.session.get(Empresa, emp.id).certificado.estado == \
            EstadoCertificado.SEM_ARQUIVO


def test_sem_pasta_quando_a_empresa_nao_tem_pasta_no_drive(app, ids, tmp_path,
                                                           monkeypatch):
    with app.app_context():
        emp = _empresa('EMPRESA SEM PASTA', '11.222.333/0001-81')
        _montar_drive(tmp_path, monkeypatch, {})

        cofre.inventariar()

        assert db.session.get(Empresa, emp.id).certificado.estado == \
            EstadoCertificado.SEM_PASTA


# --- varredura: nome nao importa, profundidade importa ----------------------

def test_acha_pfx_qualquer_que_seja_o_nome_da_pasta_e_do_arquivo(app, ids,
                                                                 tmp_path,
                                                                 monkeypatch):
    """Na carteira real as pastas variam (CERTIFICADO, CERTIFIC, DOCUMENTOS) e o
    arquivo costuma ter o nome do DONO, nao da empresa."""
    with app.app_context():
        emp = _empresa('EMPRESA X', '11.222.333/0001-81')
        pasta = tmp_path / 'X' / 'DOC. EMPRESA' / 'CERTIFICADO A-1 SENHA 17022013'
        pasta.mkdir(parents=True)
        (pasta / 'EVERTON_GREGORIO_DE_FREITAS.pfx').write_bytes(
            _fazer_pfx(cn='EMPRESA X LTDA:11222333000181'))
        _montar_drive(tmp_path, monkeypatch, {'EMPRESA X': tmp_path / 'X'})

        cofre.inventariar()

        assert db.session.get(Empresa, emp.id).certificado.estado == \
            EstadoCertificado.PRONTO


def test_nao_desce_alem_da_profundidade_maxima(app, ids, tmp_path, monkeypatch):
    """Limite existe para a varredura nao custar minutos numa pasta funda."""
    with app.app_context():
        emp = _empresa('EMPRESA FUNDA', '11.222.333/0001-81')
        fundo = tmp_path / 'F' / 'a' / 'b' / 'c' / 'd' / 'e'
        fundo.mkdir(parents=True)
        (fundo / 'c.pfx').write_bytes(_fazer_pfx(cn='X:11222333000181'))
        _montar_drive(tmp_path, monkeypatch, {'EMPRESA FUNDA': tmp_path / 'F'})

        cofre.inventariar()

        assert db.session.get(Empresa, emp.id).certificado.estado == \
            EstadoCertificado.SEM_ARQUIVO


def test_entre_dois_validos_vence_o_de_vencimento_mais_distante(app, ids,
                                                                tmp_path,
                                                                monkeypatch):
    with app.app_context():
        emp = _empresa('EMPRESA DUPLA', '11.222.333/0001-81')
        pasta = tmp_path / 'D'
        pasta.mkdir()
        (pasta / 'antigo.pfx').write_bytes(
            _fazer_pfx(cn='X:11222333000181', dias_validade=20))
        (pasta / 'novo.pfx').write_bytes(
            _fazer_pfx(cn='X:11222333000181', dias_validade=400))
        _montar_drive(tmp_path, monkeypatch, {'EMPRESA DUPLA': pasta})

        cofre.inventariar()

        cert = db.session.get(Empresa, emp.id).certificado
        assert cert.estado == EstadoCertificado.PRONTO
        assert cert.caminho.endswith('novo.pfx')


# --- senha guardada e sugestao (MANIF-04) -----------------------------------

def test_senha_guardada_e_tentada_antes_das_padrao(app, ids, tmp_path,
                                                   monkeypatch):
    """Sem isso, reinventariar jogaria de volta para SENHA_PENDENTE as 4
    empresas cuja senha o operador ja informou."""
    with app.app_context():
        app.config['MANIF_VAULT_KEY'] = cofre.gerar_chave_cofre()
        emp = _empresa('EMPRESA CUSTOM', '11.222.333/0001-81')
        pasta = tmp_path / 'C'
        pasta.mkdir()
        (pasta / 'c.pfx').write_bytes(
            _fazer_pfx(cn='X:11222333000181', senha=b'Isa@2110'))
        _montar_drive(tmp_path, monkeypatch, {'EMPRESA CUSTOM': pasta})

        db.session.add(CertificadoEmpresa(
            empresa_id=emp.id, caminho=str(pasta / 'c.pfx'),
            senha_cifrada=cofre.cifrar_senha('Isa@2110'),
            estado=EstadoCertificado.PRONTO))
        db.session.commit()

        cofre.inventariar()

        assert db.session.get(Empresa, emp.id).certificado.estado == \
            EstadoCertificado.PRONTO


def test_sugerir_senha_le_o_trecho_depois_da_palavra_senha():
    """Os 3 casos reais em que a senha esta escrita no caminho."""
    assert cofre.sugerir_senha(
        r'Z:\X\DOC. EMPRESA\CERTIFICADO A-1 SENHA 17022013\certificado a-1.pfx'
    ) == '17022013'
    assert cofre.sugerir_senha(
        r'Z:\EDOO\CERTIFICADO SENHA 042026\EDOO_33132899000155.pfx') == '042026'
    assert cofre.sugerir_senha(
        r'Z:\MISTER\CERTIFICADO A-1 SENHA Isa@2110\1010068261.pfx') == 'Isa@2110'


def test_sugerir_senha_nao_oferece_lixo_longo():
    """O 4o caso real tem o token ANTES da palavra SENHA, e o que vem depois e o
    nome do arquivo. Melhor nao sugerir nada que sugerir 70 caracteres."""
    caminho = (r'Z:\IMOBISIS\DOCUMENTOS\1234 SENHA '
               r'EVERTON_GREGORIO_DE_FREITAS_01840573023_17770259000114_'
               r'1707527913345690800.pfx')
    assert cofre.sugerir_senha(caminho) is None


def test_sugerir_senha_devolve_none_sem_a_palavra():
    assert cofre.sugerir_senha(r'Z:\X\DOCUMENTOS\cert.pfx') is None


def test_sugestao_nao_grava_nada_sozinha(app, ids, tmp_path, monkeypatch):
    """A sugestao e proposta; so `gravar_senha` persiste (MANIF-04)."""
    with app.app_context():
        app.config['MANIF_VAULT_KEY'] = cofre.gerar_chave_cofre()
        emp = _empresa('EMPRESA SUG', '11.222.333/0001-81')
        pasta = tmp_path / 'CERTIFICADO A-1 SENHA Isa@2110'
        pasta.mkdir()
        (pasta / 'c.pfx').write_bytes(
            _fazer_pfx(cn='X:11222333000181', senha=b'Isa@2110'))
        _montar_drive(tmp_path, monkeypatch, {'EMPRESA SUG': pasta})

        cofre.inventariar()

        cert = db.session.get(Empresa, emp.id).certificado
        assert cert.estado == EstadoCertificado.SENHA_PENDENTE
        assert cert.senha_cifrada is None


def test_gravar_senha_destrava_a_empresa(app, ids, tmp_path, monkeypatch):
    with app.app_context():
        app.config['MANIF_VAULT_KEY'] = cofre.gerar_chave_cofre()
        emp = _empresa('EMPRESA SUG', '11.222.333/0001-81')
        pasta = tmp_path / 'S'
        pasta.mkdir()
        (pasta / 'c.pfx').write_bytes(
            _fazer_pfx(cn='X:11222333000181', senha=b'Isa@2110'))
        _montar_drive(tmp_path, monkeypatch, {'EMPRESA SUG': pasta})
        cofre.inventariar()

        assert cofre.gravar_senha(emp, 'Isa@2110') is True

        cert = db.session.get(Empresa, emp.id).certificado
        assert cert.estado == EstadoCertificado.PRONTO
        assert cofre.decifrar_senha(cert.senha_cifrada) == 'Isa@2110'


def test_gravar_senha_errada_e_recusada(app, ids, tmp_path, monkeypatch):
    """Gravar sem conferir deixaria o pre-voo dizendo PRONTO para uma empresa
    que falharia no meio do lote."""
    with app.app_context():
        app.config['MANIF_VAULT_KEY'] = cofre.gerar_chave_cofre()
        emp = _empresa('EMPRESA SUG', '11.222.333/0001-81')
        pasta = tmp_path / 'S'
        pasta.mkdir()
        (pasta / 'c.pfx').write_bytes(
            _fazer_pfx(cn='X:11222333000181', senha=b'Isa@2110'))
        _montar_drive(tmp_path, monkeypatch, {'EMPRESA SUG': pasta})
        cofre.inventariar()

        assert cofre.gravar_senha(emp, 'senha-errada') is False
        assert db.session.get(Empresa, emp.id).certificado.senha_cifrada is None


# --- pre-voo e indisponibilidade da rede ------------------------------------

def test_estado_da_carteira_conta_por_estado_sem_tocar_a_rede(app, ids,
                                                              monkeypatch):
    """A tela le banco. Se tocasse a rede, abrir a pagina custaria os ~135s da
    varredura medida."""
    with app.app_context():
        for i, estado in enumerate((EstadoCertificado.PRONTO,
                                    EstadoCertificado.PRONTO,
                                    EstadoCertificado.VENCIDO,
                                    EstadoCertificado.SENHA_PENDENTE)):
            emp = _empresa(f'EMP {i}', f'11.222.333/000{i}-81')
            db.session.add(CertificadoEmpresa(empresa_id=emp.id, estado=estado))
        db.session.commit()

        def _explode(_nome):
            raise AssertionError('estado_da_carteira nao pode tocar a rede')

        monkeypatch.setattr(cofre, 'encontrar_pasta_empresa', _explode)

        resumo = cofre.estado_da_carteira()

        assert resumo[EstadoCertificado.PRONTO] == 2
        assert resumo[EstadoCertificado.VENCIDO] == 1
        assert resumo[EstadoCertificado.SENHA_PENDENTE] == 1


def test_rede_indisponivel_preserva_o_inventario_anterior(app, ids, monkeypatch):
    """Z: fora do ar e erro de AMBIENTE. Marcar todo mundo como sem_pasta
    apagaria um inventario bom e mandaria o operador procurar defeito onde nao
    ha."""
    with app.app_context():
        emp = _empresa('EMPRESA OK', '11.222.333/0001-81')
        db.session.add(CertificadoEmpresa(
            empresa_id=emp.id, caminho=r'Z:\X\c.pfx',
            estado=EstadoCertificado.PRONTO))
        db.session.commit()

        monkeypatch.setattr(cofre, 'rede_disponivel', lambda: False)

        try:
            cofre.inventariar()
            levantou = False
        except cofre.CofreError as exc:
            levantou = True
            assert 'rede' in str(exc).lower()

        assert levantou is True
        assert db.session.get(Empresa, emp.id).certificado.estado == \
            EstadoCertificado.PRONTO


def test_inventario_pula_empresa_nao_ativa_na_receita(app, ids, tmp_path,
                                                      monkeypatch):
    """Mesma regra unica de 'empresa viva' do lote (AD-024)."""
    from app.models import DadosReceita

    with app.app_context():
        viva = _empresa('EMPRESA VIVA', '11.222.333/0001-81')
        morta = _empresa('EMPRESA BAIXADA', '22.333.444/0001-92')
        morta.dados_receita = DadosReceita(situacao='BAIXADA')
        db.session.commit()
        _montar_drive(tmp_path, monkeypatch, {})

        cofre.inventariar()

        assert db.session.get(Empresa, viva.id).certificado is not None
        assert db.session.get(Empresa, morta.id).certificado is None


def test_senha_pendente_preserva_o_vencimento_ja_conhecido(app, ids, tmp_path,
                                                           monkeypatch):
    """O certificado trocado na pasta com senha nova nao pode APAGAR o alerta.

    Zerar `not_after` em `senha_pendente` fazia o aviso de vencimento sumir em
    silencio justo no caso em que o operador mais precisa dele. O arquivo
    continua la; o que se perdeu foi a capacidade de abri-lo."""
    with app.app_context():
        emp = _empresa('EMPRESA TROCOU SENHA', '11.222.333/0001-81')
        pasta = tmp_path / 'D'
        pasta.mkdir()
        (pasta / 'd.pfx').write_bytes(
            _fazer_pfx(cn='X:11222333000181', senha=b'senha-que-ninguem-sabe'))
        _montar_drive(tmp_path, monkeypatch, {'EMPRESA TROCOU SENHA': pasta})

        vencimento = datetime(2026, 9, 10, 8, 0)
        db.session.add(CertificadoEmpresa(
            empresa_id=emp.id, estado=EstadoCertificado.PRONTO,
            not_after=vencimento))
        db.session.commit()

        cofre.inventariar()

        cert = db.session.get(Empresa, emp.id).certificado
        assert cert.estado == EstadoCertificado.SENHA_PENDENTE
        assert cert.not_after == vencimento


def test_sem_arquivo_zera_o_vencimento(app, ids, tmp_path, monkeypatch):
    """O oposto do caso acima: sem arquivo nenhum, afirmar um vencimento seria
    inventar dado sobre um certificado que nao esta mais la."""
    with app.app_context():
        emp = _empresa('EMPRESA SEM PFX', '11.222.333/0001-81')
        pasta = tmp_path / 'E'
        pasta.mkdir()
        _montar_drive(tmp_path, monkeypatch, {'EMPRESA SEM PFX': pasta})

        db.session.add(CertificadoEmpresa(
            empresa_id=emp.id, estado=EstadoCertificado.PRONTO,
            not_after=datetime(2026, 9, 10, 8, 0)))
        db.session.commit()

        cofre.inventariar()

        cert = db.session.get(Empresa, emp.id).certificado
        assert cert.estado == EstadoCertificado.SEM_ARQUIVO
        assert cert.not_after is None
