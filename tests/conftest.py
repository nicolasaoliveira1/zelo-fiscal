"""Fixtures compartilhadas dos testes (pytest).

Configura o ambiente (SECRET_KEY de teste + SQLite temporario) antes de
importar o app e fornece app/client/dados semeados a cada teste, com banco
compartilhado por worker e dados limpos por teste.
"""
import os
import tempfile

from sqlalchemy import text

# Ambiente de teste deve estar definido ANTES de importar o app (config.py le
# SECRET_KEY/DATABASE_URL no momento do import).
os.environ.setdefault('SECRET_KEY', 'test')
os.environ.setdefault('QUIET_WERKZEUG_LOGS', 'true')
# Nao escreve arquivo de log em disco durante os testes.
os.environ.setdefault('LOG_JSON_FILE', 'false')
# Nao sobe a thread escritora de diagnostico nos testes (sem efeitos colaterais).
os.environ.setdefault('DIAGNOSTICO_PERSISTIR', 'false')
# Mantem a precondicao do lote RS deterministica (flag desligada) nos testes.
os.environ.setdefault('RS_ALTCHA_AUTOSOLVE_ENABLED', 'false')
# Nao sobe o agendador (BackgroundScheduler) nos testes; os testes do agendador
# ligam explicitamente via app.config quando precisam.
os.environ.setdefault('AGENDADOR_ENABLED', 'false')
# Sem chave 2captcha por padrao nos testes: consultar_saldo vira no-op (None) e
# nenhum teste bate na API real. Os testes que precisam mockam/injetam a chave.
os.environ.setdefault('CAPTCHA_2_API_KEY', '')
# CSRF desligado no ambiente de teste (o client nao envia token); a imposicao de
# CSRF e provada num teste dedicado que religa a flag (tests/test_csrf.py).
os.environ.setdefault('WTF_CSRF_ENABLED', 'false')

# Opt-in de banco de teste: se TEST_DATABASE_URL estiver setado (ex.: o job de CI
# aponta para um MySQL de serviço), a suíte roda contra ele. O schema é criado uma
# vez por worker e os fixtures limpam apenas os dados entre testes; isso preserva
# a paridade de enum nativo/colação/DateTime sem pagar DDL a cada teste. Sem a
# variável, mantém o SQLite temporário de sempre (rápido, gate local).
_TEST_DB_URL = os.environ.get('TEST_DATABASE_URL')

# Sob pytest-xdist cada worker é um PROCESSO separado que importa este conftest
# do zero. O schema é criado uma vez por worker, e cada worker precisa do seu
# próprio banco para que a limpeza de dados de um processo não afete o vizinho.
# No SQLite isso sai de graça (o mkstemp abaixo roda por processo e já dá um
# arquivo único); no MySQL o nome vem fixo na URL e tem que ser sufixado aqui.
_XDIST_WORKER = os.environ.get('PYTEST_XDIST_WORKER')  # 'gw0', 'gw1', ... ou None


def _banco_por_worker(url, worker):
    """Deriva (e cria) um banco MySQL dedicado ao worker xdist.

    Sem worker (execucao serial) devolve a URL intacta — o comportamento
    historico do CI, byte a byte.
    """
    if not worker:
        return url
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    alvo = make_url(url)
    nome = f'{alvo.database}_{worker}'
    # Conecta ao servidor SEM database para poder emitir o CREATE. Mesma colacao
    # do banco base (utf8mb4_0900_ai_ci) para nao perder a paridade com producao,
    # que e a unica razao deste job existir.
    servidor = create_engine(alvo.set(database=None), isolation_level='AUTOCOMMIT')
    with servidor.connect() as conn:
        conn.execute(text(
            f'CREATE DATABASE IF NOT EXISTS `{nome}` '
            'CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci'
        ))
    servidor.dispose()
    # render_as_string(hide_password=False), nunca str(): str() de uma URL do
    # SQLAlchemy substitui a senha por "***" e o worker tentaria conectar com ela.
    return alvo.set(database=nome).render_as_string(hide_password=False)


if _TEST_DB_URL:
    os.environ['DATABASE_URL'] = _banco_por_worker(_TEST_DB_URL, _XDIST_WORKER)
else:
    _fd, _DBPATH = tempfile.mkstemp(suffix='.db')
    os.close(_fd)
    os.environ['DATABASE_URL'] = 'sqlite:///' + _DBPATH.replace(os.sep, '/')

# Diretorio existente para que o preflight (rede/Chrome) passe de forma
# deterministica nos testes, independente da maquina/CI.
_TMPDIR = tempfile.mkdtemp()
os.environ.setdefault('CAMINHO_REDE', _TMPDIR)
os.environ.setdefault('CHROME_PROFILE_DIR', _TMPDIR)

import pytest  # noqa: E402

from werkzeug.security import generate_password_hash  # noqa: E402

from app import create_app, db  # noqa: E402
from app.models import Certidao, Empresa, TipoCertidao, Usuario  # noqa: E402


# Os testes historicamente chamam create_all/drop_all dentro de fixtures locais.
# Guardamos os métodos originais para criar e destruir o schema uma única vez por
# worker e fazemos os chamados de drop_all virarem limpeza de dados. Assim, a
# superfície dos testes continua igual, mas o MySQL não precisa executar DDL em
# todo teste.
_CRIAR_SCHEMA = db.create_all
_DESTRUIR_SCHEMA = db.drop_all


def _limpar_dados():
    """Remove os dados sem destruir o schema compartilhado do worker."""
    db.session.remove()
    tabelas = reversed(db.metadata.sorted_tables)

    if db.engine.dialect.name == 'mysql':
        with db.engine.begin() as conexao:
            conexao.execute(text('SET FOREIGN_KEY_CHECKS=0'))
            try:
                for tabela in tabelas:
                    conexao.execute(text(f'TRUNCATE TABLE `{tabela.name}`'))
            finally:
                conexao.execute(text('SET FOREIGN_KEY_CHECKS=1'))
    else:
        with db.engine.begin() as conexao:
            for tabela in tabelas:
                conexao.execute(tabela.delete())

        # O SQLite só cria sqlite_sequence quando há uma tabela AUTOINCREMENT.
        # Resetar a sequência conserva o comportamento anterior de drop/create
        # para testes que verificam ids fixos.
        with db.engine.begin() as conexao:
            existe_sequence = conexao.scalar(text(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='sqlite_sequence'"
            ))
            if existe_sequence:
                conexao.execute(text('DELETE FROM sqlite_sequence'))

    db.session.remove()


@pytest.fixture(scope='session', autouse=True)
def _schema_do_worker(app):
    """Cria o schema uma vez e o remove somente ao terminar o worker."""
    with app.app_context():
        _CRIAR_SCHEMA()
    yield
    with app.app_context():
        db.session.remove()
        _DESTRUIR_SCHEMA()


# Mantém as chamadas existentes nos fixtures sem reconstruir tabelas. O
# monkeypatch é restrito ao processo de pytest; o app nunca usa esses métodos em
# runtime.
db.create_all = lambda *args, **kwargs: None
db.drop_all = lambda *args, **kwargs: _limpar_dados()

# Credenciais por papel usadas pelos fixtures de client autenticado.
USUARIOS_TESTE = {
    'admin': ('admin_test', 'senha-admin-1'),
    'operador': ('op_test', 'senha-op-1'),
    'leitura': ('leitura_test', 'senha-leitura-1'),
}

# Hash barato, calculado UMA vez por sessao, para semear os usuarios do fixture.
#
# `set_senha` usa o default do werkzeug (scrypt:32768:8:1), que custa ~133ms por
# chamada de proposito — e o que protege a senha em producao. Nos fixtures isso
# era pago 3x por teste (~346ms) em ~500 testes, mais um check_password_hash
# (~116ms) a cada login do client: mais da metade do tempo total da suite gasto
# provando o custo do scrypt, que nenhum desses testes esta verificando.
#
# `check_password_hash` le o metodo do PROPRIO hash armazenado, entao semear com
# pbkdf2:sha256:1 barateia os dois lados (verify cai para ~0.02ms) sem tocar em
# nada do app. A forca real do hash de producao continua provada onde importa:
# tests/test_auth_models.py chama `set_senha` direto e nao passa por aqui.
_HASHES_TESTE = {
    papel: generate_password_hash(senha, method='pbkdf2:sha256:1')
    for papel, (_, senha) in USUARIOS_TESTE.items()
}


@pytest.fixture(autouse=True)
def _sem_cert_store_real(monkeypatch):
    """Nenhum teste le o repositorio de certificados da maquina.

    `PoliticaCertificado.montar` resolve o issuer no store do Windows, entao sem
    este duble o resultado dependeria de quais certificados estao instalados em
    quem roda a suite. None = "nao sei", que e o caminho de fallback para o
    issuer configurado; os testes da resolucao sobrescrevem este duble.
    """
    from app.automation import cert_store

    monkeypatch.setattr(cert_store, 'encontrar_issuer', lambda subject_cn: None)


@pytest.fixture(scope='session')
def app():
    return create_app()


@pytest.fixture()
def ids(app):
    """Semeia uma empresa RS/Tramandaí com as 5 certidões (sem data) e devolve
    os ids por tipo. Os dados são limpos ao final do teste."""
    with app.app_context():
        db.create_all()
        empresa = Empresa(nome='Empresa Teste', cnpj='11.111.111/1111-11',
                          estado='RS', cidade='Tramandai')
        db.session.add(empresa)
        db.session.commit()
        for tipo in TipoCertidao:
            db.session.add(Certidao(tipo=tipo, empresa=empresa))
        db.session.commit()
        # usuarios por papel (papel_key coincide com o valor de PapelUsuario).
        # senha_hash atribuido direto do hash barato pre-calculado (ver
        # _HASHES_TESTE) em vez de set_senha, que rodaria scrypt a cada teste.
        for papel_key, (uname, _senha) in USUARIOS_TESTE.items():
            u = Usuario(username=uname, papel=papel_key)
            u.senha_hash = _HASHES_TESTE[papel_key]
            db.session.add(u)
        db.session.commit()
        mapa = {
            'fgts': Certidao.query.filter_by(tipo=TipoCertidao.FGTS).first().id,
            'rs': Certidao.query.filter_by(tipo=TipoCertidao.ESTADUAL).first().id,
            'municipal': Certidao.query.filter_by(tipo=TipoCertidao.MUNICIPAL).first().id,
            'trabalhista': Certidao.query.filter_by(tipo=TipoCertidao.TRABALHISTA).first().id,
            'empresa': empresa.id,
        }
    yield mapa
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def cid(ids):
    """Id de uma certidao (trabalhista) para os endpoints de validade/pendencia."""
    return ids['trabalhista']


@pytest.fixture()
def client(app, ids):
    """Client autenticado como admin (a maioria dos testes de rota opera assim)."""
    c = app.test_client()
    uname, senha = USUARIOS_TESTE['admin']
    c.post('/login', data={'username': uname, 'senha': senha})
    return c


@pytest.fixture()
def client_anon(app, ids):
    """Client sem login, para testar enforcement de autenticacao."""
    return app.test_client()


@pytest.fixture()
def login_as(app, ids):
    """Fabrica um client autenticado com o papel pedido: login_as('operador')."""
    def _login(papel):
        c = app.test_client()
        uname, senha = USUARIOS_TESTE[papel]
        c.post('/login', data={'username': uname, 'senha': senha})
        return c
    return _login
