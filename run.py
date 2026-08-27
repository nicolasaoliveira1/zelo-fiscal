import os
import sys

from app import create_app, db
from app.models import Empresa, Certidao
from app.services.deps_check import dependencias_faltantes

app = create_app()

@app.shell_context_processor
def make_shell_context():
    return {'db': db, 'Empresa': Empresa, 'Certidao': Certidao}

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
            app.run(debug=True, use_reloader=True, extra_files=[gatilho])
        else:
            app.run(debug=True, use_reloader=True,
                    extra_files=[gatilho], exclude_patterns=['*.py'])
    else:
        app.run(debug=False)
