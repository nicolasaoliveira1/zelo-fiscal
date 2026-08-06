"""Testes do cálculo de alvos de lote (batch_engine.calc_targets) com banco."""
import pytest
from datetime import date, timedelta

from app import db
from app.models import (
    Certidao,
    ConfiguracaoSistema,
    Empresa,
    StatusEspecial,
    TipoCertidao,
)
from app.services import batch_engine


def test_calc_targets_default(app, ids):
    with app.app_context():
        fgts = Certidao.query.filter_by(tipo=TipoCertidao.FGTS).first()
        est = Certidao.query.filter_by(tipo=TipoCertidao.ESTADUAL).first()
        muni = Certidao.query.filter_by(tipo=TipoCertidao.MUNICIPAL).first()
        fgts.data_validade = date.today() - timedelta(days=5)    # vencida
        est.data_validade = date.today() + timedelta(days=3)     # a vencer (<= 7)
        muni.data_validade = date.today() + timedelta(days=100)  # fora do limite
        db.session.commit()

        dados = batch_engine.calc_targets(fgts.id, scope='default')
        assert fgts.id in dados['ids']
        assert est.id in dados['ids']
        assert muni.id not in dados['ids']      # alem do limite 'a vencer'
        assert dados['ids'][0] == fgts.id        # certidao de inicio vem primeiro
        assert dados['vencidas'] >= 1
        assert dados['a_vencer'] >= 1
        assert dados['scope'] == 'default'


def test_calc_targets_respeita_prazo_do_tipo(app, ids):
    # FGTS com prazo menor que o global nao deve contar certidoes alem da
    # propria janela so porque outro tipo tem prazo maior.
    with app.app_context():
        config = ConfiguracaoSistema.query.get(1)
        if config is None:
            config = ConfiguracaoSistema(id=1, a_vencer_dias=7)
            db.session.add(config)
        config.a_vencer_dias = 7
        config.a_vencer_dias_fgts = 3

        fgts = Certidao.query.filter_by(tipo=TipoCertidao.FGTS).first()
        fgts.data_validade = date.today() + timedelta(days=5)  # fora da janela FGTS (3)
        db.session.commit()

        dados = batch_engine.calc_targets(
            fgts.id, scope='default', tipo=TipoCertidao.FGTS
        )
        assert fgts.id not in dados['ids']
        assert dados['a_vencer'] == 0


def test_calc_targets_pendentes(app, ids):
    with app.app_context():
        fgts = Certidao.query.filter_by(tipo=TipoCertidao.FGTS).first()
        fgts.status_especial = StatusEspecial.PENDENTE
        fgts.data_validade = None
        db.session.commit()

        dados = batch_engine.calc_targets(fgts.id, scope='pendentes')
        assert dados['ids'] == [fgts.id]
        assert dados['scope'] == 'pendentes'
        assert dados['pendentes'] >= 1


def test_pendente_com_data_vencida_excluido_do_escopo_default(app, ids):
    # Certidao PENDENTE com data_validade no passado deve ser EXCLUIDA do escopo
    # 'default' (assim como o dashboard a classifica como 'pendentes', nao 'vencidas').
    with app.app_context():
        fgts = Certidao.query.filter_by(tipo=TipoCertidao.FGTS).first()
        fgts.status_especial = StatusEspecial.PENDENTE
        fgts.data_validade = date.today() - timedelta(days=10)  # vencida, mas PENDENTE
        db.session.commit()

        dados = batch_engine.calc_targets(fgts.id, scope='default')
        assert fgts.id not in dados['ids']
        assert dados['vencidas'] == 0

        # no escopo 'pendentes' ela deve aparecer normalmente
        dados_p = batch_engine.calc_targets(fgts.id, scope='pendentes')
        assert fgts.id in dados_p['ids']


def test_calc_targets_vazio_quando_sem_data(app, ids):
    # certidoes semeadas sem data nao entram no escopo default
    with app.app_context():
        fgts = Certidao.query.filter_by(tipo=TipoCertidao.FGTS).first()
        dados = batch_engine.calc_targets(fgts.id, scope='default')
        assert dados['ids'] == []
        assert dados['total'] == 0


def test_start_incluida_false_para_nao_definida_com_outras_vencidas(app, ids):
    # Bug: clicar em emitir numa certidao "Nao definida" (sem validade) abria o
    # modal de lote so porque OUTRAS do mesmo tipo estavam vencidas. A clicada
    # nao pertence ao lote -> start_incluida deve ser False (front emite so ela).
    with app.app_context():
        clicada = Certidao.query.filter_by(tipo=TipoCertidao.FGTS).first()
        clicada.data_validade = None                              # "Nao definida"
        # segunda empresa com FGTS vencida (compoe o "lote" de outras)
        outra_empresa = Empresa(nome='Outra', cnpj='22.222.222/2222-22',
                                estado='RS', cidade='Tramandai')
        db.session.add(outra_empresa)
        db.session.commit()
        outra = Certidao(tipo=TipoCertidao.FGTS, empresa=outra_empresa,
                         data_validade=date.today() - timedelta(days=5))
        db.session.add(outra)
        db.session.commit()

        dados = batch_engine.calc_targets(clicada.id, scope='default')
        assert dados['start_incluida'] is False
        assert clicada.id not in dados['ids']
        assert outra.id in dados['ids']


def test_start_incluida_true_quando_clicada_no_lote(app, ids):
    with app.app_context():
        fgts = Certidao.query.filter_by(tipo=TipoCertidao.FGTS).first()
        fgts.data_validade = date.today() - timedelta(days=5)     # vencida
        db.session.commit()

        dados = batch_engine.calc_targets(fgts.id, scope='default')
        assert dados['start_incluida'] is True
        assert dados['ids'][0] == fgts.id


# --- filtro por situacao cadastral (spec 08, DATA-02.2/02.3) -----------------

def _empresa_com_situacao(nome, cnpj, situacao, cidade='Porto Alegre'):
    from app.models import DadosReceita
    emp = Empresa(nome=nome, cnpj=cnpj, estado='RS', cidade=cidade)
    db.session.add(emp)
    db.session.commit()
    if situacao is not None:
        emp.dados_receita = DadosReceita(situacao=situacao)
        db.session.commit()
    return emp


def _certidao_a_vencer(empresa, tipo=TipoCertidao.FGTS):
    cert = Certidao(tipo=tipo, empresa=empresa,
                    data_validade=date.today() + timedelta(days=3))
    db.session.add(cert)
    db.session.commit()
    return cert


def test_empresa_baixada_sai_dos_alvos(app, ids):
    with app.app_context():
        viva = _empresa_com_situacao('Viva', '33.000.167/0001-01', 'ATIVA')
        morta = _empresa_com_situacao('Morta', '11.222.333/0001-81', 'BAIXADA')
        cert_viva = _certidao_a_vencer(viva)
        cert_morta = _certidao_a_vencer(morta)

        dados = batch_engine.calc_targets(None, scope='default')

        assert cert_viva.id in dados['ids']
        assert cert_morta.id not in dados['ids']


def test_empresa_sem_dados_receita_continua_no_lote(app, ids):
    """A regressao mais cara da spec: no primeiro deploy a tabela esta VAZIA.
    Se empresa nao classificada saisse do lote, a carteira inteira sairia junto."""
    with app.app_context():
        sem_espelho = _empresa_com_situacao('Sem Espelho', '33.000.167/0001-01', None)
        cert = _certidao_a_vencer(sem_espelho)

        dados = batch_engine.calc_targets(None, scope='default')

        assert sem_espelho.dados_receita is None
        assert cert.id in dados['ids']


def test_situacao_nula_no_espelho_continua_no_lote(app, ids):
    with app.app_context():
        emp = _empresa_com_situacao('Espelho Vazio', '33.000.167/0001-01', '')
        cert = _certidao_a_vencer(emp)
        assert cert.id in batch_engine.calc_targets(None, scope='default')['ids']


@pytest.mark.parametrize('situacao', ['BAIXADA', 'SUSPENSA', 'INAPTA', 'NULA'])
def test_todas_as_situacoes_mortas_saem(situacao, app, ids):
    with app.app_context():
        emp = _empresa_com_situacao('Morta', '33.000.167/0001-01', situacao)
        cert = _certidao_a_vencer(emp)
        assert cert.id not in batch_engine.calc_targets(None, scope='default')['ids']


@pytest.mark.parametrize('tipo', [TipoCertidao.FGTS, TipoCertidao.ESTADUAL,
                                  TipoCertidao.MUNICIPAL, TipoCertidao.TRABALHISTA])
def test_filtro_vale_para_os_quatro_fluxos_de_lote(tipo, app, ids):
    """DATA-02.2: o filtro esta no ponto unico, entao pega os 4 de uma vez."""
    with app.app_context():
        morta = _empresa_com_situacao('Morta', '33.000.167/0001-01', 'BAIXADA')
        cert = _certidao_a_vencer(morta, tipo=tipo)

        dados = batch_engine.calc_targets(
            None, extra_filter=lambda q: q.filter(Certidao.tipo == tipo),
            scope='default', tipo=tipo)

        assert cert.id not in dados['ids']


def test_contagens_refletem_o_que_vai_rodar(app, ids):
    """Se o total exclui a baixada, vencidas/a_vencer tambem — senao o modal
    mostraria um numero que nao bate com o que o lote processa."""
    with app.app_context():
        morta = _empresa_com_situacao('Morta', '33.000.167/0001-01', 'BAIXADA')
        vencida = Certidao(tipo=TipoCertidao.FGTS, empresa=morta,
                           data_validade=date.today() - timedelta(days=5))
        db.session.add(vencida)
        db.session.commit()

        dados = batch_engine.calc_targets(None, scope='default')

        assert vencida.id not in dados['ids']
        assert dados['vencidas'] == 0
        assert dados['excluidas_situacao'] >= 1


def test_excluidas_situacao_conta_zero_sem_baixada(app, ids):
    with app.app_context():
        viva = _empresa_com_situacao('Viva', '33.000.167/0001-01', 'ATIVA')
        _certidao_a_vencer(viva)
        assert batch_engine.calc_targets(None, scope='default')['excluidas_situacao'] == 0


def test_filtro_vale_no_escopo_pendentes(app, ids):
    with app.app_context():
        morta = _empresa_com_situacao('Morta', '33.000.167/0001-01', 'BAIXADA')
        pendente = Certidao(tipo=TipoCertidao.FGTS, empresa=morta,
                            status_especial=StatusEspecial.PENDENTE)
        db.session.add(pendente)
        db.session.commit()

        dados = batch_engine.calc_targets(None, scope='pendentes')
        assert pendente.id not in dados['ids']


def test_filtro_convive_com_extra_filter_que_junta_empresa(app, ids):
    """Dois extra_filter ja fazem join com Empresa; o join da situacao e por
    empresa_id justamente para nao colidir com eles."""
    with app.app_context():
        morta = _empresa_com_situacao('Morta RS', '33.000.167/0001-01', 'BAIXADA')
        cert = _certidao_a_vencer(morta, tipo=TipoCertidao.ESTADUAL)

        dados = batch_engine.calc_targets(
            None,
            extra_filter=lambda q: (q.join(Empresa, Empresa.id == Certidao.empresa_id)
                                     .filter(Certidao.tipo == TipoCertidao.ESTADUAL)
                                     .filter(Empresa.estado == 'RS')),
            scope='default', tipo=TipoCertidao.ESTADUAL)

        assert cert.id not in dados['ids']


def test_uma_query_so_sem_n_mais_1(app, ids):
    """A situacao vem como coluna do join. Com lazy load por linha, uma carteira
    de centenas de empresas viraria centenas de SELECTs a cada abertura de modal."""
    from sqlalchemy import event

    with app.app_context():
        for indice, cnpj in enumerate(['33.000.167/0001-01', '11.222.333/0001-81',
                                       '11.444.777/0001-61']):
            emp = _empresa_com_situacao(f'Empresa {indice}', cnpj, 'ATIVA')
            _certidao_a_vencer(emp)

        selects = []
        motor = db.engine

        def _contar(conn, cursor, statement, *a):
            if statement.strip().lower().startswith('select'):
                selects.append(statement)

        event.listen(motor, 'before_cursor_execute', _contar)
        try:
            batch_engine.calc_targets(None, scope='default')
        finally:
            event.remove(motor, 'before_cursor_execute', _contar)

        # 1 query de alvos (+ eventual leitura de ConfiguracaoSistema pelo
        # get_a_vencer_dias). Nunca uma por certidao.
        assert len(selects) <= 2, selects
