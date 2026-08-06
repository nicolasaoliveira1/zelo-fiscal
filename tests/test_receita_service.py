"""Aplicacao dos dados da Receita sobre o cadastro (spec 08, DATA-01.8/01.9, DATA-02.1).

Camada de dominio: nenhum HTTP aqui — `receita_client.consultar` e mockado.
"""
from datetime import date, datetime, timedelta

import pytest

from app import db
from app.models import DadosReceita, Empresa
from app.services import receita_client, receita_service


def _dto(**over):
    base = dict(
        cnpj='33000167000101', situacao='ATIVA', situacao_data=date(2005, 11, 3),
        razao_social='PETROLEO BRASILEIRO S A', nome_fantasia='PETROBRAS',
        # baseline CONCORDA com _empresa() (Porto Alegre/RS): assim so diverge
        # o que cada teste pedir explicitamente.
        municipio='Porto Alegre', uf='RS', cep='20031170',
        logradouro='AVENIDA REPUBLICA DO CHILE', numero='65', bairro='CENTRO',
        cnae_fiscal='600001', cnae_descricao='Extração de petróleo',
        opcao_simples=None, fonte='brasilapi',
    )
    base.update(over)
    return receita_client.DadosReceitaDTO(**base)


def _empresa(nome='Empresa X', cnpj='33.000.167/0001-01', cidade='Porto Alegre',
             estado='RS'):
    emp = Empresa(nome=nome, cnpj=cnpj, estado=estado, cidade=cidade)
    db.session.add(emp)
    db.session.commit()
    return emp


# --- empresa_ativa ------------------------------------------------------------

def test_empresa_sem_dados_receita_conta_como_ativa(app, ids):
    """A regressao mais cara desta spec: se empresa nao classificada contasse
    como morta, ligar a feature tiraria a carteira INTEIRA do lote automatico
    antes de o recheck rodar."""
    with app.app_context():
        emp = _empresa()
        assert emp.dados_receita is None
        assert receita_service.empresa_ativa(emp) is True


def test_empresa_com_situacao_nula_conta_como_ativa(app, ids):
    """DadosReceita existe mas a consulta nao trouxe situacao: mesma logica."""
    with app.app_context():
        emp = _empresa()
        emp.dados_receita = DadosReceita(situacao=None)
        db.session.commit()
        assert receita_service.empresa_ativa(emp) is True


def test_empresa_ativa_e_ativa(app, ids):
    with app.app_context():
        emp = _empresa()
        emp.dados_receita = DadosReceita(situacao='ATIVA')
        db.session.commit()
        assert receita_service.empresa_ativa(emp) is True


@pytest.mark.parametrize('situacao', ['BAIXADA', 'SUSPENSA', 'INAPTA', 'NULA'])
def test_situacoes_mortas_nao_sao_ativas(situacao, app, ids):
    with app.app_context():
        emp = _empresa()
        emp.dados_receita = DadosReceita(situacao=situacao)
        db.session.commit()
        assert receita_service.empresa_ativa(emp) is False


def test_situacao_com_acento_e_caixa_normaliza(app, ids):
    with app.app_context():
        emp = _empresa()
        emp.dados_receita = DadosReceita(situacao='  ativa  ')
        db.session.commit()
        assert receita_service.empresa_ativa(emp) is True


# --- enriquecer: invariante do nome (DATA-01.8) ------------------------------

def test_enriquecer_nunca_escreve_o_nome_da_empresa(app, ids):
    """Empresa.nome e a chave da pasta no drive de rede."""
    with app.app_context():
        emp = _empresa(nome='NOME DA PASTA')
        receita_service.enriquecer(emp, _dto(razao_social='RAZAO TOTALMENTE OUTRA'))

        recarregada = db.session.get(Empresa, emp.id)
        assert recarregada.nome == 'NOME DA PASTA'
        assert recarregada.dados_receita.razao_social == 'RAZAO TOTALMENTE OUTRA'


def test_nome_nao_e_campo_atualizavel(app, ids):
    """A lista de campos conciliaveis nao pode conter 'nome' — e o que impede a
    rota de aceite de virar um "escreva qualquer coluna da Empresa"."""
    assert 'nome' not in receita_service._CAMPOS_CADASTRO


# --- enriquecer: preencher x sinalizar (DATA-01.9) ---------------------------

def test_enriquecer_grava_o_espelho_da_receita(app, ids):
    with app.app_context():
        emp = _empresa()
        res = receita_service.enriquecer(emp, _dto())

        assert res['ok'] is True
        dados = db.session.get(Empresa, emp.id).dados_receita
        assert dados.situacao == 'ATIVA'
        assert dados.razao_social == 'PETROLEO BRASILEIRO S A'
        assert dados.cep == '20031170'
        assert dados.fonte == 'brasilapi'
        assert dados.verificado_em is not None


def test_enriquecer_preenche_campo_vazio(app, ids):
    with app.app_context():
        emp = _empresa(cidade='Porto Alegre')
        emp.cidade = ''
        db.session.commit()

        res = receita_service.enriquecer(emp, _dto(municipio='Tramandai'))

        assert 'cidade' in res['preenchidos']
        assert res['divergencias'] == []
        # entra ja canonicalizada
        assert db.session.get(Empresa, emp.id).cidade == 'Tramandaí'


def test_enriquecer_nao_sobrescreve_campo_divergente(app, ids):
    """Trocar a cidade em silencio quebraria o "Abrir Site" municipal, que casa
    por string."""
    with app.app_context():
        emp = _empresa(cidade='Porto Alegre')
        res = receita_service.enriquecer(emp, _dto(municipio='RIO DE JANEIRO'))

        assert db.session.get(Empresa, emp.id).cidade == 'Porto Alegre'
        assert res['preenchidos'] == []
        assert len(res['divergencias']) == 1
        div = res['divergencias'][0]
        assert div['campo'] == 'cidade'
        assert div['atual'] == 'Porto Alegre'
        assert div['receita'] == 'RIO DE JANEIRO'   # fora do mapa canonico: inalterada


def test_mesma_cidade_em_caixa_diferente_nao_e_divergencia(app, ids):
    """Sem comparacao normalizada, 'Porto Alegre' x 'PORTO ALEGRE' viraria
    divergencia falsa em toda empresa da carteira."""
    with app.app_context():
        emp = _empresa(cidade='Porto Alegre')
        res = receita_service.enriquecer(emp, _dto(municipio='PORTO ALEGRE'))
        assert res['divergencias'] == []


def test_estado_divergente_e_sinalizado(app, ids):
    with app.app_context():
        emp = _empresa(estado='RS', cidade='Porto Alegre')
        res = receita_service.enriquecer(emp, _dto(uf='RJ', municipio='Porto Alegre'))

        campos = {d['campo'] for d in res['divergencias']}
        assert 'estado' in campos
        assert db.session.get(Empresa, emp.id).estado == 'RS'


def test_enriquecer_e_idempotente(app, ids):
    """Rechecar duas vezes nao pode duplicar DadosReceita nem criar divergencia
    que nao existe."""
    with app.app_context():
        emp = _empresa(cidade='Porto Alegre')
        receita_service.enriquecer(emp, _dto(municipio='Porto Alegre'))
        res = receita_service.enriquecer(emp, _dto(municipio='Porto Alegre'))

        assert res['ok'] is True
        assert res['divergencias'] == []
        assert DadosReceita.query.filter_by(empresa_id=emp.id).count() == 1


def test_enriquecer_sem_dto_devolve_erro(app, ids):
    with app.app_context():
        emp = _empresa()
        res = receita_service.enriquecer(emp, None)
        assert res['ok'] is False
        assert res['erro']


def test_enriquecer_falha_de_commit_faz_rollback(app, ids, monkeypatch):
    with app.app_context():
        emp = _empresa()

        def _boom():
            raise RuntimeError('banco fora')
        monkeypatch.setattr(db.session, 'commit', _boom)

        res = receita_service.enriquecer(emp, _dto())
        assert res['ok'] is False
        assert 'banco fora' in res['erro']


# --- divergencias_atuais: leitura pura para a tela ---------------------------

def test_divergencias_atuais_sem_dados_receita_e_vazio(app, ids):
    with app.app_context():
        assert receita_service.divergencias_atuais(_empresa()) == []


def test_divergencias_atuais_lista_o_que_difere(app, ids):
    with app.app_context():
        emp = _empresa(cidade='Porto Alegre')
        receita_service.enriquecer(emp, _dto(municipio='RIO DE JANEIRO'))

        divergencias = receita_service.divergencias_atuais(emp)

        assert [d['campo'] for d in divergencias] == ['cidade']
        assert divergencias[0]['atual'] == 'Porto Alegre'
        assert divergencias[0]['receita'] == 'RIO DE JANEIRO'


def test_divergencias_atuais_nao_escreve_nada(app, ids):
    """A tela de detalhe chama isto a cada render: nao pode ter efeito colateral."""
    with app.app_context():
        emp = _empresa(cidade='Porto Alegre')
        receita_service.enriquecer(emp, _dto(municipio='RIO DE JANEIRO'))

        receita_service.divergencias_atuais(emp)
        receita_service.divergencias_atuais(emp)
        db.session.rollback()   # descarta qualquer escrita pendente

        assert db.session.get(Empresa, emp.id).cidade == 'Porto Alegre'


def test_divergencias_atuais_vazio_quando_tudo_concorda(app, ids):
    with app.app_context():
        emp = _empresa(cidade='Porto Alegre', estado='RS')
        receita_service.enriquecer(emp, _dto(municipio='PORTO ALEGRE', uf='RS'))
        assert receita_service.divergencias_atuais(emp) == []


def test_divergencias_atuais_ignora_campo_vazio_no_cadastro(app, ids):
    """Campo vazio nao e divergencia — `enriquecer` ja teria preenchido."""
    with app.app_context():
        emp = _empresa(cidade='Porto Alegre')
        receita_service.enriquecer(emp, _dto(municipio='Tramandai'))
        emp.cidade = ''
        db.session.commit()

        assert receita_service.divergencias_atuais(emp) == []


# --- aceitar_divergencia ------------------------------------------------------

def test_aceitar_divergencia_aplica_valor_da_receita(app, ids):
    with app.app_context():
        emp = _empresa(cidade='Porto Alegre')
        receita_service.enriquecer(emp, _dto(municipio='Tramandai'))

        ok, erro = receita_service.aceitar_divergencia(emp, 'cidade')

        assert (ok, erro) == (True, None)
        assert db.session.get(Empresa, emp.id).cidade == 'Tramandaí'


def test_aceitar_divergencia_registra_auditoria(app, ids, monkeypatch):
    registros = []
    monkeypatch.setattr(receita_service.auditoria, 'registrar',
                        lambda acao, **kw: registros.append((acao, kw)))
    with app.app_context():
        emp = _empresa(cidade='Porto Alegre')
        receita_service.enriquecer(emp, _dto(municipio='Tramandai'))
        receita_service.aceitar_divergencia(emp, 'cidade')

    assert registros
    acao, kw = registros[-1]
    assert acao == 'empresa.receita_aceitar'
    assert kw['alvo_tipo'] == 'empresa'
    assert 'cidade' in kw['detalhe']


def test_aceitar_divergencia_recusa_o_campo_nome(app, ids):
    """DATA-01.8: nem por esta porta o nome pode ser trocado."""
    with app.app_context():
        emp = _empresa(nome='NOME DA PASTA')
        receita_service.enriquecer(emp, _dto(razao_social='OUTRA RAZAO'))

        ok, erro = receita_service.aceitar_divergencia(emp, 'nome')

        assert ok is False
        assert erro
        assert db.session.get(Empresa, emp.id).nome == 'NOME DA PASTA'


def test_aceitar_divergencia_recusa_campo_desconhecido(app, ids):
    with app.app_context():
        emp = _empresa()
        receita_service.enriquecer(emp, _dto())
        ok, erro = receita_service.aceitar_divergencia(emp, 'inscricao_mobiliaria')
        assert ok is False and erro


def test_aceitar_divergencia_sem_dados_receita(app, ids):
    with app.app_context():
        emp = _empresa()
        ok, erro = receita_service.aceitar_divergencia(emp, 'cidade')
        assert ok is False and erro


# --- selecao do recheck (DATA-02.6) ------------------------------------------

def test_recheck_pega_nunca_verificada_primeiro(app, ids):
    with app.app_context():
        recente = _empresa('Recente', '33.000.167/0001-01')
        recente.dados_receita = DadosReceita(verificado_em=datetime.now())
        _empresa('Nunca', '11.222.333/0001-81')   # sem DadosReceita
        antiga = _empresa('Antiga', '11.444.777/0001-61')
        antiga.dados_receita = DadosReceita(
            verificado_em=datetime.now() - timedelta(days=90))
        db.session.commit()

        alvos = receita_service.empresas_para_recheck(idade_dias=30, limite=10)
        nomes = [e.nome for e in alvos]

        # nunca verificada vem antes da que tem carimbo antigo
        assert nomes.index('Nunca') < nomes.index('Antiga')
        assert 'Recente' not in nomes       # dentro da janela: nao recheca


def test_recheck_respeita_o_limite(app, ids):
    with app.app_context():
        _empresa('A', '33.000.167/0001-01')
        _empresa('B', '11.222.333/0001-81')
        _empresa('C', '11.444.777/0001-61')

        alvos = receita_service.empresas_para_recheck(idade_dias=30, limite=2)
        assert len(alvos) == 2


def test_recheck_mais_velhas_primeiro(app, ids):
    with app.app_context():
        media = _empresa('Media', '33.000.167/0001-01')
        media.dados_receita = DadosReceita(
            verificado_em=datetime.now() - timedelta(days=40))
        velha = _empresa('Velha', '11.222.333/0001-81')
        velha.dados_receita = DadosReceita(
            verificado_em=datetime.now() - timedelta(days=200))
        db.session.commit()

        alvos = receita_service.empresas_para_recheck(idade_dias=30, limite=10)
        nomes = [e.nome for e in alvos if e.nome in {'Media', 'Velha'}]
        assert nomes == ['Velha', 'Media']


# --- rechecar_lote ------------------------------------------------------------

def _sem_pausa(monkeypatch):
    """A suite nao dorme de verdade."""
    monkeypatch.setattr(receita_service.time, 'sleep', lambda _s: None)


def test_rechecar_lote_atualiza_situacao(app, ids, monkeypatch):
    _sem_pausa(monkeypatch)
    with app.app_context():
        emp = _empresa('Alvo', '33.000.167/0001-01')
        monkeypatch.setattr(receita_service.receita_client, 'consultar',
                            lambda _cnpj: (_dto(situacao='BAIXADA'), None))

        resumo = receita_service.rechecar_lote(idade_dias=30, limite=10)

        assert resumo['verificadas'] >= 1
        assert db.session.get(Empresa, emp.id).dados_receita.situacao == 'BAIXADA'


def test_rechecar_lote_reporta_transicao_para_baixada(app, ids, monkeypatch):
    """So a TRANSICAO alerta — e o que o alerta anti-spam consome."""
    _sem_pausa(monkeypatch)
    with app.app_context():
        emp = _empresa('Vai Baixar', '33.000.167/0001-01')
        emp.dados_receita = DadosReceita(situacao='ATIVA')
        db.session.commit()

        monkeypatch.setattr(receita_service.receita_client, 'consultar',
                            lambda _cnpj: (_dto(situacao='BAIXADA'), None))
        resumo = receita_service.rechecar_lote(idade_dias=0, limite=10)

        ids_baixadas = [linha[0] for linha in resumo['baixadas']]
        assert emp.id in ids_baixadas


def test_rechecar_lote_nao_realerta_quem_ja_estava_baixada(app, ids, monkeypatch):
    """Ligar a feature nao pode disparar alerta retroativo para a carteira."""
    _sem_pausa(monkeypatch)
    with app.app_context():
        emp = _empresa('Ja Baixada', '33.000.167/0001-01')
        emp.dados_receita = DadosReceita(situacao='BAIXADA')
        db.session.commit()

        monkeypatch.setattr(receita_service.receita_client, 'consultar',
                            lambda _cnpj: (_dto(situacao='BAIXADA'), None))
        resumo = receita_service.rechecar_lote(idade_dias=0, limite=10)

        assert resumo['baixadas'] == []


def test_rechecar_lote_falha_de_uma_nao_derruba_as_outras(app, ids, monkeypatch):
    """DATA-02.8."""
    _sem_pausa(monkeypatch)
    with app.app_context():
        _empresa('Falha', '33.000.167/0001-01')
        _empresa('Ok', '11.222.333/0001-81')

        def _consultar(cnpj):
            if '33000167' in (cnpj or '').replace('.', '').replace('/', '').replace('-', ''):
                raise RuntimeError('boom')
            return _dto(), None

        monkeypatch.setattr(receita_service.receita_client, 'consultar', _consultar)
        resumo = receita_service.rechecar_lote(idade_dias=0, limite=10)

        assert resumo['falhas'] >= 1
        assert resumo['verificadas'] >= 1


def test_rechecar_lote_conta_falha_quando_api_nao_responde(app, ids, monkeypatch):
    _sem_pausa(monkeypatch)
    with app.app_context():
        _empresa('Sem Resposta', '33.000.167/0001-01')
        monkeypatch.setattr(receita_service.receita_client, 'consultar',
                            lambda _cnpj: (None, receita_client.ERRO_INDISPONIVEL))

        resumo = receita_service.rechecar_lote(idade_dias=0, limite=10)
        assert resumo['falhas'] >= 1
        assert resumo['verificadas'] == 0


def test_rechecar_lote_nunca_escreve_o_nome(app, ids, monkeypatch):
    """DATA-01.8 tambem pelo caminho do job."""
    _sem_pausa(monkeypatch)
    with app.app_context():
        emp = _empresa('NOME DA PASTA', '33.000.167/0001-01')
        monkeypatch.setattr(receita_service.receita_client, 'consultar',
                            lambda _cnpj: (_dto(razao_social='OUTRA RAZAO'), None))

        receita_service.rechecar_lote(idade_dias=0, limite=10)
        assert db.session.get(Empresa, emp.id).nome == 'NOME DA PASTA'


def test_rechecar_lote_pausa_entre_chamadas(app, ids, monkeypatch):
    """Sem pausa o job estoura o rate limit da ReceitaWS (~3 req/min)."""
    pausas = []
    monkeypatch.setattr(receita_service.time, 'sleep', lambda s: pausas.append(s))
    with app.app_context():
        _empresa('A', '33.000.167/0001-01')
        _empresa('B', '11.222.333/0001-81')
        monkeypatch.setattr(receita_service.receita_client, 'consultar',
                            lambda _cnpj: (_dto(), None))

        receita_service.rechecar_lote(idade_dias=0, limite=10, pausa_s=2.0)

    # uma pausa a menos que o numero de empresas: nao dorme antes da primeira
    assert pausas and all(p == 2.0 for p in pausas)


# --- consultar_para_formulario (DATA-01.3) -----------------------------------

def test_consultar_para_formulario_nao_persiste(app, ids, monkeypatch):
    with app.app_context():
        antes = DadosReceita.query.count()
        monkeypatch.setattr(receita_service.receita_client, 'consultar',
                            lambda _cnpj: (_dto(), None))

        dados, erro = receita_service.consultar_para_formulario('33000167000101')

        assert erro is None
        assert dados['cidade'] == 'Porto Alegre'
        assert dados['estado'] == 'RS'
        assert dados['ativa'] is True
        assert DadosReceita.query.count() == antes


def test_consultar_para_formulario_marca_empresa_baixada(app, ids, monkeypatch):
    with app.app_context():
        monkeypatch.setattr(receita_service.receita_client, 'consultar',
                            lambda _cnpj: (_dto(situacao='BAIXADA'), None))
        dados, _erro = receita_service.consultar_para_formulario('33000167000101')
        assert dados['ativa'] is False
        assert dados['situacao'] == 'BAIXADA'


def test_consultar_para_formulario_propaga_erro(app, ids, monkeypatch):
    with app.app_context():
        monkeypatch.setattr(receita_service.receita_client, 'consultar',
                            lambda _cnpj: (None, receita_client.ERRO_NAO_ENCONTRADO))
        dados, erro = receita_service.consultar_para_formulario('33000167000101')
        assert dados is None
        assert erro == receita_client.ERRO_NAO_ENCONTRADO
