"""A costura: manifestar UMA chave, do banco ao desfecho gravado
(MANIF-16, MANIF-17, MANIF-19).

Todo o resto do sistema fala com a SEFAZ por aqui. E por isso que trocar o
webservice pelo portal (a contingencia da spec) custaria so a implementacao de
`manifestar` — modelo, import, cofre, UI e lote nao mudam.

Nenhum teste toca a rede: o envio e injetado.
"""
from app import db
from app.models import (
    CertificadoEmpresa,
    ChaveManifestacao,
    Empresa,
    EstadoCertificado,
    EventoAuditoria,
    StatusManifestacao,
)
from app.services import manifestador_cofre as cofre
from app.services import manifestador_service as svc
from app.services.nfe_sefaz import RespostaSefaz
from tests.test_manifestador_cofre import _fazer_pfx

CHAVE = '43170107461248000107650010000045391000045390'
TAG_CNPJ = '{http://www.portalfiscal.inf.br/nfe}CNPJ'
TAG_TPEVENTO = '{http://www.portalfiscal.inf.br/nfe}tpEvento'


def _empresa_pronta(tmp_path, nome='EMPRESA A', cnpj='11.222.333/0001-81',
                    cn='EMPRESA A LTDA:11222333000181'):
    emp = Empresa(nome=nome, cnpj=cnpj, estado='RS', cidade='Imbé')
    db.session.add(emp)
    db.session.commit()
    caminho = tmp_path / f'{nome}.pfx'
    caminho.write_bytes(_fazer_pfx(cn=cn))
    emp.certificado = CertificadoEmpresa(
        caminho=str(caminho), estado=EstadoCertificado.PRONTO,
        cnpj_certificado=''.join(c for c in cnpj if c.isdigit()))
    db.session.commit()
    return emp


def _chave(empresa, chave=CHAVE, status=StatusManifestacao.PENDENTE):
    linha = ChaveManifestacao(chave=chave, empresa_id=empresa.id,
                              competencia='2017-01', status=status)
    db.session.add(linha)
    db.session.commit()
    return linha


class _EnvioFalso:
    """Substitui `nfe_sefaz.enviar_evento`, guardando o que recebeu."""

    def __init__(self, resposta=None, excecao=None):
        self.resposta = resposta or RespostaSefaz(
            cstat='135', xmotivo='Evento registrado e vinculado a NF-e',
            protocolo='143210000123456', bruto='<ok/>')
        self.excecao = excecao
        self.chamadas = []

    def __call__(self, evento, credencial=None, ambiente=None, **kwargs):
        self.chamadas.append({'evento': evento, 'credencial': credencial})
        if self.excecao:
            raise self.excecao
        return self.resposta


def _com_envio(monkeypatch, envio):
    monkeypatch.setattr(svc, 'enviar_evento', envio)
    return envio


# --- desfechos (MANIF-16) ---------------------------------------------------

def test_evento_registrado_fecha_a_chave_como_manifestada(app, ids, tmp_path,
                                                          monkeypatch):
    with app.app_context():
        emp = _empresa_pronta(tmp_path)
        linha = _chave(emp)
        _com_envio(monkeypatch, _EnvioFalso())

        resultado = svc.manifestar(linha.id)

        recarregada = db.session.get(ChaveManifestacao, linha.id)
        assert resultado.sucesso is True
        assert recarregada.status == StatusManifestacao.MANIFESTADA
        assert recarregada.cstat == '135'
        assert recarregada.protocolo == '143210000123456'
        assert recarregada.manifestado_em is not None
        assert recarregada.ja_existia is False
        assert recarregada.tipo_evento == svc.CONFIRMACAO


def test_duplicidade_fecha_como_manifestada_com_marca(app, ids, tmp_path,
                                                      monkeypatch):
    """573 e o desfecho DESEJADO: a nota ja estava manifestada."""
    with app.app_context():
        emp = _empresa_pronta(tmp_path)
        linha = _chave(emp)
        _com_envio(monkeypatch, _EnvioFalso(RespostaSefaz(
            cstat='573', xmotivo='Rejeicao: Duplicidade de evento',
            bruto='<x/>')))

        resultado = svc.manifestar(linha.id)

        recarregada = db.session.get(ChaveManifestacao, linha.id)
        assert resultado.sucesso is True
        assert recarregada.status == StatusManifestacao.MANIFESTADA
        assert recarregada.ja_existia is True
        assert recarregada.cstat == '573'


def test_rejeicao_guarda_o_texto_oficial_e_fica_reprocessavel(app, ids,
                                                              tmp_path,
                                                              monkeypatch):
    with app.app_context():
        emp = _empresa_pronta(tmp_path)
        linha = _chave(emp)
        _com_envio(monkeypatch, _EnvioFalso(RespostaSefaz(
            cstat='596', xmotivo='Rejeicao: NF-e nao consta na base de dados',
            bruto='<x/>')))

        resultado = svc.manifestar(linha.id)

        recarregada = db.session.get(ChaveManifestacao, linha.id)
        assert resultado.sucesso is False
        assert recarregada.status == StatusManifestacao.REJEITADA
        assert recarregada.cstat == '596'
        assert recarregada.xmotivo == \
            'Rejeicao: NF-e nao consta na base de dados'
        assert recarregada.manifestado_em is None


# --- indefinida (MANIF-17) --------------------------------------------------

def test_resposta_que_nao_chega_vira_indefinida(app, ids, tmp_path, monkeypatch):
    """Nunca `manifestada` (perderia um evento que talvez nao exista) e nunca
    de volta a `pendente` (reenviaria um ja protocolado)."""
    with app.app_context():
        emp = _empresa_pronta(tmp_path)
        linha = _chave(emp)
        _com_envio(monkeypatch, _EnvioFalso(RespostaSefaz(
            bruto='', indefinido=True, erro='tempo esgotado lendo a resposta')))

        resultado = svc.manifestar(linha.id)

        recarregada = db.session.get(ChaveManifestacao, linha.id)
        assert resultado.sucesso is False
        assert recarregada.status == StatusManifestacao.INDEFINIDA


def test_falha_de_rede_antes_do_envio_volta_para_pendente(app, ids, tmp_path,
                                                          monkeypatch):
    """O pedido nao saiu: repetir e seguro, entao a chave continua na fila."""
    with app.app_context():
        emp = _empresa_pronta(tmp_path)
        linha = _chave(emp)
        _com_envio(monkeypatch, _EnvioFalso(RespostaSefaz(
            bruto='', erro='sem rota para o host')))

        resultado = svc.manifestar(linha.id)

        recarregada = db.session.get(ChaveManifestacao, linha.id)
        assert resultado.sucesso is False
        assert recarregada.status == StatusManifestacao.PENDENTE
        assert 'rota' in (recarregada.xmotivo or '')


def test_excecao_inesperada_nao_deixa_a_chave_travada_em_enviando(app, ids,
                                                                  tmp_path,
                                                                  monkeypatch):
    """`enviando` nao tem acao nenhuma na interface: uma chave presa ali some do
    fluxo sem ninguem perceber."""
    with app.app_context():
        emp = _empresa_pronta(tmp_path)
        linha = _chave(emp)
        _com_envio(monkeypatch, _EnvioFalso(excecao=RuntimeError('estourou')))

        resultado = svc.manifestar(linha.id)

        recarregada = db.session.get(ChaveManifestacao, linha.id)
        assert resultado.sucesso is False
        assert recarregada.status != StatusManifestacao.ENVIANDO


# --- pre-condicoes: nada de rede sem certificado ----------------------------

def test_empresa_sem_certificado_pronto_e_recusada_sem_tocar_a_rede(app, ids,
                                                                    monkeypatch):
    with app.app_context():
        emp = Empresa(nome='SEM CERT', cnpj='11.222.333/0001-81', estado='RS',
                      cidade='Imbé')
        db.session.add(emp)
        db.session.commit()
        linha = _chave(emp)
        envio = _com_envio(monkeypatch, _EnvioFalso())

        resultado = svc.manifestar(linha.id)

        assert resultado.sucesso is False
        assert envio.chamadas == []
        assert 'SEM CERT' in resultado.mensagem
        assert db.session.get(ChaveManifestacao, linha.id).status == \
            StatusManifestacao.PENDENTE


def test_certificado_vencido_e_recusado_nomeando_a_empresa(app, ids, tmp_path,
                                                           monkeypatch):
    with app.app_context():
        emp = _empresa_pronta(tmp_path)
        emp.certificado.estado = EstadoCertificado.VENCIDO
        db.session.commit()
        linha = _chave(emp)
        envio = _com_envio(monkeypatch, _EnvioFalso())

        resultado = svc.manifestar(linha.id)

        assert resultado.sucesso is False
        assert envio.chamadas == []
        assert 'EMPRESA A' in resultado.mensagem


def test_usa_o_certificado_da_empresa_da_chave(app, ids, tmp_path, monkeypatch):
    """Manifestar com o certificado errado cria evento sob outro CNPJ."""
    with app.app_context():
        outra = _empresa_pronta(tmp_path, 'OUTRA', '99.888.777/0001-66',
                                'OUTRA LTDA:99888777000166')
        alvo = _empresa_pronta(tmp_path, 'ALVO', '11.222.333/0001-81',
                               'ALVO LTDA:11222333000181')
        linha = _chave(alvo)
        envio = _com_envio(monkeypatch, _EnvioFalso())

        svc.manifestar(linha.id)

        usado = envio.chamadas[0]['credencial'].caminho
        assert usado == cofre.credencial(alvo)[0]
        assert usado != cofre.credencial(outra)[0]


def test_cnpj_do_evento_e_o_da_empresa_destinataria(app, ids, tmp_path,
                                                    monkeypatch):
    with app.app_context():
        emp = _empresa_pronta(tmp_path)
        linha = _chave(emp)
        envio = _com_envio(monkeypatch, _EnvioFalso())

        svc.manifestar(linha.id)

        evento = envio.chamadas[0]['evento']
        assert evento.find(f'.//{TAG_CNPJ}').text == '11222333000181'


def test_evento_enviado_esta_assinado(app, ids, tmp_path, monkeypatch):
    from app.services import nfe_assinatura as assin

    with app.app_context():
        emp = _empresa_pronta(tmp_path)
        linha = _chave(emp)
        envio = _com_envio(monkeypatch, _EnvioFalso())

        svc.manifestar(linha.id)

        assert assin.verificar(envio.chamadas[0]['evento']) is True


# --- manifestavel: a regra unica --------------------------------------------

def test_manifestavel_aceita_pendente_rejeitada_e_indefinida(app, ids):
    with app.app_context():
        emp = Empresa(nome='E', cnpj='11.222.333/0001-81', estado='RS',
                      cidade='Imbé')
        db.session.add(emp)
        db.session.commit()

        for status in (StatusManifestacao.PENDENTE,
                       StatusManifestacao.REJEITADA,
                       StatusManifestacao.INDEFINIDA):
            linha = ChaveManifestacao(chave=CHAVE, empresa_id=emp.id,
                                      status=status)
            assert svc.manifestavel(linha) is True


def test_manifestavel_recusa_manifestada_duplicata_e_enviando(app, ids):
    with app.app_context():
        emp = Empresa(nome='E', cnpj='11.222.333/0001-81', estado='RS',
                      cidade='Imbé')
        db.session.add(emp)
        db.session.commit()

        for status in (StatusManifestacao.MANIFESTADA,
                       StatusManifestacao.DUPLICATA,
                       StatusManifestacao.ENVIANDO):
            linha = ChaveManifestacao(chave=CHAVE, empresa_id=emp.id,
                                      status=status)
            assert svc.manifestavel(linha) is False


def test_chave_ja_manifestada_e_recusada_sem_tocar_a_rede(app, ids, tmp_path,
                                                          monkeypatch):
    with app.app_context():
        emp = _empresa_pronta(tmp_path)
        linha = _chave(emp, status=StatusManifestacao.MANIFESTADA)
        envio = _com_envio(monkeypatch, _EnvioFalso())

        resultado = svc.manifestar(linha.id)

        assert resultado.sucesso is False
        assert envio.chamadas == []


def test_chave_inexistente_e_recusada(app, ids, monkeypatch):
    with app.app_context():
        envio = _com_envio(monkeypatch, _EnvioFalso())
        resultado = svc.manifestar(999999)

        assert resultado.sucesso is False
        assert envio.chamadas == []


# --- auditoria (MANIF-19) ---------------------------------------------------

def test_manifestacao_gera_evento_de_auditoria(app, ids, tmp_path, monkeypatch):
    """Ato fiscal irreversivel: quem, quando, qual chave, qual desfecho."""
    with app.app_context():
        emp = _empresa_pronta(tmp_path)
        linha = _chave(emp)
        _com_envio(monkeypatch, _EnvioFalso())

        svc.manifestar(linha.id)

        evento = EventoAuditoria.query.filter_by(acao='manifestacao').first()
        assert evento is not None
        assert evento.alvo_tipo == 'chave_manifestacao'
        assert evento.alvo_id == linha.id
        assert evento.resultado == 'ok'
        assert CHAVE in evento.detalhe
        assert '135' in evento.detalhe
        assert '210200' in evento.detalhe


def test_rejeicao_tambem_e_auditada_como_erro(app, ids, tmp_path, monkeypatch):
    with app.app_context():
        emp = _empresa_pronta(tmp_path)
        linha = _chave(emp)
        _com_envio(monkeypatch, _EnvioFalso(RespostaSefaz(
            cstat='596', xmotivo='Rejeicao', bruto='<x/>')))

        svc.manifestar(linha.id)

        evento = EventoAuditoria.query.filter_by(acao='manifestacao').first()
        assert evento.resultado == 'erro'
        assert '596' in evento.detalhe


def test_recusa_por_certificado_nao_audita_manifestacao(app, ids, monkeypatch):
    """Nada foi manifestado, entao nao ha ato fiscal a registrar."""
    with app.app_context():
        emp = Empresa(nome='SEM CERT', cnpj='11.222.333/0001-81', estado='RS',
                      cidade='Imbé')
        db.session.add(emp)
        db.session.commit()
        linha = _chave(emp)
        _com_envio(monkeypatch, _EnvioFalso())

        svc.manifestar(linha.id)

        assert EventoAuditoria.query.filter_by(acao='manifestacao').count() == 0


# --- tipo de evento ---------------------------------------------------------

def test_tipo_de_evento_escolhido_chega_no_xml_e_no_banco(app, ids, tmp_path,
                                                          monkeypatch):
    with app.app_context():
        emp = _empresa_pronta(tmp_path)
        linha = _chave(emp)
        envio = _com_envio(monkeypatch, _EnvioFalso())

        svc.manifestar(linha.id, tipo_evento=svc.DESCONHECIMENTO)

        evento = envio.chamadas[0]['evento']
        assert evento.find(f'.//{TAG_TPEVENTO}').text == '210220'
        assert db.session.get(ChaveManifestacao, linha.id).tipo_evento == '210220'
