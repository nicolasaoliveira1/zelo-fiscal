import os
import sys

from app import create_app, db
from app.models import Empresa, Certidao
from app.services.deps_check import dependencias_faltantes

app = create_app()

@app.shell_context_processor
def make_shell_context():
    return {'db': db, 'Empresa': Empresa, 'Certidao': Certidao}


PORTA = int(os.environ.get('PORT') or os.environ.get('FLASK_RUN_PORT') or 5000)


def _porta_ocupada(porta, host='127.0.0.1', timeout=2.0):
    """Alguem ja atende nessa porta?

    Checa por CONEXAO, nao por bind: no Windows o socket do servidor sobe com
    `SO_REUSEADDR` e um segundo bind na mesma porta **da certo**, ao contrario
    do Linux. O resultado e dois servidores escutando e o Windows escolhendo
    quem atende cada conexao — foi assim que uma sessao inteira foi gasta
    investigando "o app nao responde" enquanto as requisicoes caiam num
    processo morto (2026-09-09).

    Pior: o socket sobrevive ao dono. Ja apareceu `LISTENING` com um PID que
    `taskkill` nao encontra e sem nenhum python vivo na maquina; ai o connect
    completa e a requisicao morre em silencio. Por isso a checagem e connect.
    """
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as teste:
        teste.settimeout(timeout)
        try:
            return teste.connect_ex((host, porta)) == 0
        except OSError:
            return False


def _recusar_porta_ocupada(porta):
    print(f'ERRO: ja ha algo escutando em 127.0.0.1:{porta}.')
    print('Subir por cima cria um segundo servidor na mesma porta e as')
    print('requisicoes passam a cair em qualquer um dos dois.')
    print('')
    print(f'  netstat -ano | findstr :{porta}      (ve quem esta la)')
    print('  taskkill /PID <pid> /T /F            (derruba)')
    print('')
    print('Se o PID nao existir mais, o socket ficou orfao no Windows:')
    print('  net stop winnat && net start winnat  (como administrador)')
    print('Ou use outra porta:  set PORT=5001')
    sys.exit(1)


def _processo_que_serve(debug):
    """Distingue o servidor real do processo pai do reloader do Werkzeug."""
    return not debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true'


def _garantir_servicos_recorrentes(debug):
    """Recusa subir o servidor sem os jobs recorrentes configurados."""
    if not _processo_que_serve(debug):
        return None

    from app.services import agendador
    return agendador.garantir_iniciado_no_processo_servidor(app)

if __name__ == '__main__':
    # Fail-fast acionavel: nao sobe meio quebrado se faltar dependencia critica.
    faltando = dependencias_faltantes()
    if faltando:
        print('ERRO: dependencias ausentes: ' + ', '.join(faltando))
        print('Rode "iniciar.bat" (ou "pip install -r requirements.txt" no venv ativo) e tente de novo.')
        sys.exit(1)

    # debug desligado por padrao (esta ferramenta escreve em disco de rede e no
    # registro do Windows); habilite localmente com FLASK_DEBUG=1 quando precisar.
    debug = os.environ.get('FLASK_DEBUG', '').strip().lower() in {'1', 'true', 'yes', 'on'}

    # O create_app faz o primeiro init, mas o reloader possui um processo pai e
    # outro que serve. Confirma no segundo, antes de abrir a porta, que todos os
    # jobs recorrentes realmente estão registrados e com o scheduler rodando.
    _garantir_servicos_recorrentes(debug)

    # Quem checa e quem ABRE a porta: o processo unico, ou o pai do reloader.
    # O filho reusa o socket que o pai ja abriu, entao checar nele faria o
    # filho recusar a porta do proprio pai a cada reload.
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true' and _porta_ocupada(PORTA):
        _recusar_porta_ocupada(PORTA)

    if debug:
        # Dois modos de reload, escolhidos por FLASK_RELOAD_AUTO (o painel seta;
        # na mao o padrao e o de sempre, vigiando o codigo):
        #   1 -> reloader normal, reinicia a cada .py salvo;
        #   0 -> vigia so o arquivo-gatilho, para editar codigo sem derrubar uma
        #        sessao de teste em andamento.
        # O gatilho vale nos dois: o botao "Reload" do painel funciona igual.
        automatico = os.environ.get('FLASK_RELOAD_AUTO', '1').strip().lower() \
            not in {'0', 'false', 'no', 'off'}
        gatilho = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reload.trigger')
        open(gatilho, 'a').close()
        if automatico:
            app.run(debug=True, port=PORTA, use_reloader=True,
                    extra_files=[gatilho])
        else:
            app.run(debug=True, port=PORTA, use_reloader=True,
                    extra_files=[gatilho], exclude_patterns=['*.py'])
    else:
        app.run(debug=False, port=PORTA)
