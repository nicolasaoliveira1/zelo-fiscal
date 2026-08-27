"""Compara o banco com as migrations da branch atual — e conserta se pedir.

Existe por um erro que aparece toda troca de branch: o banco fica gravado numa
revisao que so existe na outra branch, e o boot morre com "Can't locate
revision". Aqui isso vira diagnostico ("banco a frente") e, com --sincronizar,
conserta buscando as migrations que faltam nos outros refs do git.

Uso:
    python tools/estado_migrations.py            # JSON com o diagnostico
    python tools/estado_migrations.py --sincronizar
"""

import json
import os
import re
import subprocess
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSOES = os.path.join(RAIZ, 'migrations', 'versions')
sys.path.insert(0, RAIZ)


LOCAIS = ('localhost', '127.0.0.1', '::1', '')


def _e_local(url):
    """Banco na propria maquina? Host remoto aqui e producao do escritorio."""
    from sqlalchemy.engine import make_url
    try:
        return (make_url(url).host or '') in LOCAIS
    except Exception:
        return False


def _git(*args):
    r = subprocess.run(['git', *args], cwd=RAIZ, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ''


def branch_atual():
    return _git('rev-parse', '--abbrev-ref', 'HEAD') or '(desconhecida)'


def _revisao_do_banco():
    """Le alembic_version direto, sem passar pelo alembic (que erraria)."""
    os.environ['AUTO_DB_UPGRADE'] = '0'
    from sqlalchemy import inspect, text

    from app import create_app, db

    app = create_app()
    with app.app_context():
        if 'alembic_version' not in inspect(db.engine).get_table_names():
            return None, str(db.engine.url)
        linha = db.session.execute(text('SELECT version_num FROM alembic_version')).first()
        return (linha[0] if linha else None), str(db.engine.url)


def _revisoes_locais():
    """revision -> down_revision, lendo os arquivos desta branch."""
    mapa = {}
    for nome in os.listdir(VERSOES):
        if not nome.endswith('.py'):
            continue
        texto = open(os.path.join(VERSOES, nome), encoding='utf-8').read()
        rev = re.search(r"^revision = ['\"]([^'\"]+)", texto, re.M)
        if rev:
            desce = re.search(r"^down_revision = ['\"]([^'\"]+)", texto, re.M)
            mapa[rev.group(1)] = desce.group(1) if desce else None
    return mapa


def _cabeca_local(mapa):
    filhos = {d for d in mapa.values() if d}
    cabecas = [r for r in mapa if r not in filhos]
    return cabecas[0] if len(cabecas) == 1 else (cabecas[0] if cabecas else None)


def _catalogo_de_outros_refs():
    """revision -> (ref, caminho, conteudo) para migrations ausentes aqui."""
    catalogo = {}
    locais = set(os.listdir(VERSOES))
    refs = _git('for-each-ref', '--format=%(refname)', 'refs/heads', 'refs/remotes').splitlines()
    for ref in refs:
        for caminho in _git('ls-tree', '-r', '--name-only', ref, '--',
                            'migrations/versions').splitlines():
            if not caminho.endswith('.py') or os.path.basename(caminho) in locais:
                continue
            conteudo = _git('show', f'{ref}:{caminho}')
            rev = re.search(r"^revision = ['\"]([^'\"]+)", conteudo, re.M)
            if rev and rev.group(1) not in catalogo:
                catalogo[rev.group(1)] = (ref, caminho, conteudo)
    return catalogo


def _cadeia_ate_conhecida(rev_banco, locais, catalogo):
    """Migrations de fora, do banco para baixo, ate cair numa que existe aqui."""
    cadeia, atual = [], rev_banco
    while atual and atual not in locais:
        if atual not in catalogo:
            return cadeia, None
        ref, caminho, conteudo = catalogo[atual]
        cadeia.append({'revision': atual, 'ref': ref, 'caminho': caminho,
                       'conteudo': conteudo,
                       'titulo': (conteudo.lstrip().split('\n', 1)[0]
                                  .lstrip('"\'').strip() or os.path.basename(caminho))})
        desce = re.search(r"^down_revision = ['\"]([^'\"]+)", conteudo, re.M)
        atual = desce.group(1) if desce else None
    return cadeia, atual


def diagnosticar():
    locais = _revisoes_locais()
    cabeca = _cabeca_local(locais)
    try:
        rev_banco, url = _revisao_do_banco()
    except Exception as e:
        return {'estado': 'sem_banco', 'branch': branch_atual(), 'erro': str(e),
                'mensagem': 'banco inacessivel'}

    base = {'branch': branch_atual(), 'banco': url.split('@')[-1],
            'revisao_banco': rev_banco, 'cabeca_codigo': cabeca,
            'local': _e_local(url)}

    if rev_banco is None:
        return {**base, 'estado': 'vazio', 'mensagem': 'banco novo — falta upgrade'}
    if rev_banco == cabeca:
        return {**base, 'estado': 'em_dia', 'mensagem': 'banco em dia com a branch'}
    if rev_banco in locais:
        return {**base, 'estado': 'atrasado', 'mensagem': 'migrations pendentes — upgrade'}

    cadeia, destino = _cadeia_ate_conhecida(rev_banco, locais, _catalogo_de_outros_refs())
    return {**base, 'estado': 'a_frente', 'destino': destino,
            'reverter': [{'revision': c['revision'], 'titulo': c['titulo'],
                          'ref': c['ref']} for c in cadeia],
            'mensagem': ('banco a frente da branch — %d migration(s) de outra branch'
                         % len(cadeia)) if cadeia else
                        'banco numa revisao que nao existe em nenhuma branch'}


def _flask(*args):
    env = {**os.environ, 'FLASK_APP': 'run.py', 'AUTO_DB_UPGRADE': '0',
           'PYTHONUNBUFFERED': '1'}
    return subprocess.run([sys.executable, '-m', 'flask', 'db', *args],
                          cwd=RAIZ, env=env).returncode


def sincronizar():
    d = diagnosticar()
    estado = d['estado']
    print(f"branch {d.get('branch')} · {d.get('mensagem')}")

    if estado == 'em_dia':
        return 0
    if estado in ('vazio', 'atrasado'):
        if not d.get('local'):
            print('ATENCAO: upgrade em banco remoto (%s).' % d.get('banco'))
        return _flask('upgrade')
    if estado == 'sem_banco':
        print('banco inacessivel:', d.get('erro'))
        return 1

    if not d.get('local'):
        print('RECUSADO: descer o schema so e permitido em banco local.')
        print(f"  banco: {d.get('banco')}")
        print('  Este e o banco de producao do escritorio. Reverter migration')
        print('  aqui derruba tabela e coluna com o conteudo — nao tem volta.')
        print('  Para sair do desencontro, volte para a branch que tem a')
        print('  revisao %s.' % d.get('revisao_banco'))
        return 1

    locais = _revisoes_locais()
    cadeia, destino = _cadeia_ate_conhecida(d['revisao_banco'], locais,
                                            _catalogo_de_outros_refs())
    if not destino:
        print('nao achei em nenhum ref do git a migration', d['revisao_banco'])
        return 1

    # As migrations de fora entram so para o alembic conseguir descer o caminho,
    # e saem em seguida: nao viram arquivo desta branch.
    emprestados = []
    try:
        for c in cadeia:
            alvo = os.path.join(VERSOES, os.path.basename(c['caminho']))
            with open(alvo, 'w', encoding='utf-8') as fh:
                fh.write(c['conteudo'])
            emprestados.append(alvo)
            print(f"  emprestado de {c['ref']}: {os.path.basename(alvo)}")
        codigo = _flask('downgrade', destino)
    finally:
        for alvo in emprestados:
            os.path.exists(alvo) and os.remove(alvo)
    if codigo:
        return codigo
    return _flask('upgrade')


if __name__ == '__main__':
    if '--sincronizar' in sys.argv:
        sys.exit(sincronizar())
    print(json.dumps(diagnosticar(), ensure_ascii=False, indent=2))
