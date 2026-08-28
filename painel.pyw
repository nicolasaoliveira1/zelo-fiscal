"""Painel do Zelo: sobe o app, mostra o log e expoe as utilidades do dia a dia.

Tkinter puro (stdlib) para nao adicionar dependencia. Substitui o iniciar.bat:
o .bat agora so garante o venv e chama este painel.
"""

import json
import os
import queue
import subprocess
import threading
import tkinter as tk
import webbrowser
from tkinter import font as tkfont
from tkinter import messagebox

RAIZ = os.path.dirname(os.path.abspath(__file__))
PYTHON = os.path.join(RAIZ, 'venv', 'Scripts', 'python.exe')
GATILHO = os.path.join(RAIZ, 'reload.trigger')
URL = 'http://127.0.0.1:5000'
PORTA = 5000
ESTADO = os.path.join(RAIZ, 'tools', 'estado_migrations.py')

FUNDO = '#14161a'
FUNDO_LATERAL = '#1c1f26'
TEXTO = '#e6e8ec'
SUAVE = '#8b93a1'
OK = '#4ade80'
ERRO = '#f87171'
ATENCAO = '#fbbf24'


class Painel:
    def __init__(self, root):
        self.root = root
        self.proc = None
        self.fila = queue.Queue()

        root.title('Zelo — painel de desenvolvimento')
        root.geometry('1100x620')
        root.configure(bg=FUNDO)

        mono = tkfont.Font(family='Consolas', size=10)

        root.minsize(820, 380)

        # A lateral rola: numa janela baixa os ultimos botoes ficavam fora da
        # tela, e so dava para alcanca-los esticando a janela.
        moldura = tk.Frame(root, bg=FUNDO_LATERAL, width=210)
        moldura.pack(side='left', fill='y')
        moldura.pack_propagate(False)

        self.canvas_lateral = tk.Canvas(moldura, bg=FUNDO_LATERAL,
                                        highlightthickness=0, borderwidth=0)
        self.barra_lateral = tk.Scrollbar(moldura, orient='vertical',
                                          command=self.canvas_lateral.yview,
                                          bg=FUNDO_LATERAL, troughcolor=FUNDO,
                                          borderwidth=0)
        self.canvas_lateral.configure(yscrollcommand=self.barra_lateral.set)
        self.canvas_lateral.pack(side='left', fill='both', expand=True)

        lateral = tk.Frame(self.canvas_lateral, bg=FUNDO_LATERAL)
        self.janela_lateral = self.canvas_lateral.create_window(
            (0, 0), window=lateral, anchor='nw')
        lateral.bind('<Configure>', self._ajustar_lateral)
        self.canvas_lateral.bind('<Configure>', self._ajustar_lateral)
        # roda so com o ponteiro sobre a lateral: o log tem a rolagem dele.
        moldura.bind('<Enter>', lambda _: self.canvas_lateral.bind_all(
            '<MouseWheel>', self._rolar_lateral))
        moldura.bind('<Leave>', lambda _: self.canvas_lateral.unbind_all(
            '<MouseWheel>'))

        self.status = tk.Label(lateral, text='parado', bg=FUNDO_LATERAL, fg=SUAVE,
                               font=('Segoe UI', 10, 'bold'), anchor='w', padx=14)
        self.status.pack(fill='x', pady=(14, 2))

        self.lbl_branch = tk.Label(lateral, text='branch ...', bg=FUNDO_LATERAL,
                                   fg=TEXTO, font=('Segoe UI', 9), anchor='w',
                                   padx=14, wraplength=185, justify='left')
        self.lbl_branch.pack(fill='x')

        self.lbl_banco = tk.Label(lateral, text='banco ...', bg=FUNDO_LATERAL,
                                  fg=SUAVE, font=('Segoe UI', 8), anchor='w',
                                  padx=14, wraplength=185, justify='left')
        self.lbl_banco.pack(fill='x', pady=(0, 8))

        self._secao(lateral, 'servidor')
        self.btn_ligar = self._botao(lateral, 'Iniciar', self.iniciar)
        self.btn_parar = self._botao(lateral, 'Parar', self.parar)
        self._botao(lateral, 'Reload (gatilho)', self.reload_gatilho)
        self._botao(lateral, 'Reload total', self.reload_total)
        self._botao(lateral, f'Derrubar porta {PORTA}', self.derrubar_porta)

        self.auto_reload = tk.BooleanVar(value=True)
        tk.Checkbutton(
            lateral, text='reload automatico', variable=self.auto_reload,
            command=self.trocar_modo, bg=FUNDO_LATERAL, fg=TEXTO,
            activebackground=FUNDO_LATERAL, activeforeground=TEXTO,
            selectcolor=FUNDO, font=('Segoe UI', 9), anchor='w', padx=10,
            highlightthickness=0, borderwidth=0, cursor='hand2',
        ).pack(fill='x', padx=10, pady=(4, 0))

        self._secao(lateral, 'banco')
        self._botao(lateral, 'flask db upgrade', lambda: self.tarefa(
            [PYTHON, '-m', 'flask', 'db', 'upgrade'], 'db upgrade',
            {'FLASK_APP': 'run.py', 'AUTO_DB_UPGRADE': '0'}))
        self._botao(lateral, 'flask db current', lambda: self.tarefa(
            [PYTHON, '-m', 'flask', 'db', 'current'], 'db current',
            {'FLASK_APP': 'run.py', 'AUTO_DB_UPGRADE': '0'}))
        self._botao(lateral, 'Sincronizar com a branch', self.sincronizar)

        self._secao(lateral, 'utilidades')
        self._botao(lateral, 'Abrir no navegador', lambda: webbrowser.open(URL))
        self._botao(lateral, 'Instalar dependencias', lambda: self.tarefa(
            [PYTHON, '-m', 'pip', 'install', '-r', 'requirements.txt'], 'pip install'))
        self._botao(lateral, 'Rodar testes (-q)', lambda: self.tarefa(
            [PYTHON, '-m', 'pytest', '-q'], 'pytest'))
        self._botao(lateral, 'Abrir pasta de logs',
                    lambda: os.startfile(os.path.join(RAIZ, 'logs')))
        self._botao(lateral, 'Rechecar estado', self.checar_estado)
        self._botao(lateral, 'Limpar painel', self.limpar)

        self.log = tk.Text(root, bg=FUNDO, fg=TEXTO, font=mono, wrap='none',
                           insertbackground=TEXTO, borderwidth=0, padx=12, pady=8)
        barra = tk.Scrollbar(root, command=self.log.yview, bg=FUNDO_LATERAL,
                             troughcolor=FUNDO, borderwidth=0)
        self.log.configure(yscrollcommand=barra.set, state='disabled')
        barra.pack(side='right', fill='y')
        self.log.pack(side='left', fill='both', expand=True)

        self.log.tag_configure('painel', foreground=SUAVE)
        self.log.tag_configure('ok', foreground=OK)
        self.log.tag_configure('erro', foreground=ERRO)
        self.log.tag_configure('atencao', foreground=ATENCAO)

        root.protocol('WM_DELETE_WINDOW', self.fechar)
        self.root.after(80, self.drenar)
        self.escrever(f'painel pronto — raiz {RAIZ}', 'painel')
        self.checar_estado()
        self.ref_vista = self._ref_atual()
        self.vigiar_branch()
        # app de um cmd antigo continua de pe e o painel nao o conhece: avisa,
        # senao o Iniciar falha com "porta em uso" sem dizer por que.
        externos = self.pids_na_porta()
        if externos:
            self.escrever(
                f'atencao: ja tem algo escutando na porta {PORTA} (pid '
                + ', '.join(externos) + ') — use "Derrubar porta"', 'atencao')
        if not os.path.exists(PYTHON):
            self.escrever('venv nao encontrado. Crie com: python -m venv venv', 'erro')

    # --- widgets -----------------------------------------------------------
    def _ajustar_lateral(self, _=None):
        canvas = self.canvas_lateral
        canvas.configure(scrollregion=canvas.bbox('all'))
        canvas.itemconfigure(self.janela_lateral, width=canvas.winfo_width())
        # a barra so aparece quando ha o que rolar.
        conteudo = canvas.bbox('all')
        precisa = bool(conteudo) and conteudo[3] > canvas.winfo_height()
        if precisa and not self.barra_lateral.winfo_ismapped():
            # antes do canvas: o canvas ocupa a largura toda e, empacotada
            # depois, a barra ficaria com zero de largura (sem aparecer).
            self.barra_lateral.pack(side='right', fill='y',
                                    before=self.canvas_lateral)
        elif not precisa and self.barra_lateral.winfo_ismapped():
            self.barra_lateral.pack_forget()

    def _rolar_lateral(self, evento):
        self.canvas_lateral.yview_scroll(-1 * (evento.delta // 120), 'units')

    def _secao(self, pai, titulo):
        tk.Label(pai, text=titulo.upper(), bg=FUNDO_LATERAL, fg=SUAVE,
                 font=('Segoe UI', 8, 'bold'), anchor='w',
                 padx=14).pack(fill='x', pady=(12, 2))

    def _botao(self, pai, rotulo, comando):
        b = tk.Button(pai, text=rotulo, command=comando, bg='#262a33', fg=TEXTO,
                      activebackground='#323845', activeforeground=TEXTO,
                      relief='flat', anchor='w', padx=12, pady=6,
                      font=('Segoe UI', 9), cursor='hand2')
        b.pack(fill='x', padx=10, pady=2)
        return b

    # --- log ---------------------------------------------------------------
    def escrever(self, linha, tag=None):
        self.log.configure(state='normal')
        self.log.insert('end', linha.rstrip() + '\n', tag)
        self.log.see('end')
        self.log.configure(state='disabled')

    def limpar(self):
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')

    def drenar(self):
        try:
            while True:
                linha, tag = self.fila.get_nowait()
                self.escrever(linha, tag)
        except queue.Empty:
            pass
        self.atualizar_status()
        self.root.after(80, self.drenar)

    def _bombear(self, proc, rotulo):
        for linha in proc.stdout:
            baixa = linha.lower()
            tag = None
            if 'error' in baixa or 'traceback' in baixa or 'exception' in baixa:
                tag = 'erro'
            elif 'warning' in baixa or 'warn' in baixa:
                tag = 'atencao'
            self.fila.put((linha, tag))
        code = proc.wait()
        tag = 'ok' if code == 0 else 'erro'
        self.fila.put((f'[{rotulo}] terminou com codigo {code}', tag))

    def _popen(self, cmd, extra_env=None):
        env = os.environ.copy()
        env['PYTHONUNBUFFERED'] = '1'
        env['PYTHONIOENCODING'] = 'utf-8'
        if extra_env:
            env.update(extra_env)
        return subprocess.Popen(
            cmd, cwd=RAIZ, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1,
            encoding='utf-8', errors='replace',
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
        )

    # --- acoes -------------------------------------------------------------
    def tarefa(self, cmd, rotulo, extra_env=None):
        self.escrever(f'[{rotulo}] {" ".join(cmd[1:])}', 'painel')
        proc = self._popen(cmd, extra_env)
        threading.Thread(target=self._bombear, args=(proc, rotulo), daemon=True).start()

    def iniciar(self):
        if self.vivo():
            self.escrever('ja esta rodando', 'atencao')
            return
        modo = 'automatico' if self.auto_reload.get() else 'so pelo gatilho'
        self.escrever(f'[servidor] subindo run.py — reload {modo}', 'painel')
        self.proc = self._popen([PYTHON, 'run.py'], {
            'FLASK_DEBUG': '1',
            'FLASK_RELOAD_AUTO': '1' if self.auto_reload.get() else '0',
        })
        threading.Thread(target=self._bombear, args=(self.proc, 'servidor'),
                         daemon=True).start()

    def parar(self):
        if not self.vivo():
            self.escrever('nao esta rodando', 'atencao')
            return
        # o reloader do werkzeug roda num processo filho: mata a arvore inteira.
        subprocess.run(['taskkill', '/PID', str(self.proc.pid), '/T', '/F'],
                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        self.escrever('[servidor] parado', 'painel')

    def reload_gatilho(self):
        if not self.vivo():
            self.escrever('nao esta rodando — use Iniciar', 'atencao')
            return
        with open(GATILHO, 'a'):
            os.utime(GATILHO, None)
        self.escrever('[servidor] gatilho tocado, recarregando o codigo', 'painel')

    def reload_total(self):
        if self.vivo():
            self.parar()
            self.root.after(700, self.iniciar)
        else:
            self.iniciar()

    def trocar_modo(self):
        modo = 'automatico' if self.auto_reload.get() else 'so pelo gatilho'
        self.escrever(f'[servidor] reload {modo}', 'painel')
        if self.vivo():
            # o modo e decidido no app.run: so vale no proximo processo.
            self.escrever('[servidor] reiniciando para aplicar o modo', 'painel')
            self.reload_total()

    # --- vigia da branch ---------------------------------------------------
    def _ref_atual(self):
        """Le .git/HEAD sem chamar git: e um arquivo, e a troca reescreve ele."""
        try:
            caminho = os.path.join(RAIZ, '.git')
            if os.path.isfile(caminho):  # worktree: .git aponta para o real
                caminho = open(caminho, encoding='utf-8').read().split(':', 1)[1].strip()
            return open(os.path.join(caminho, 'HEAD'), encoding='utf-8').read().strip()
        except OSError:
            return None

    def vigiar_branch(self):
        ref = self._ref_atual()
        if ref and self.ref_vista and ref != self.ref_vista:
            self.escrever('[git] a branch mudou embaixo do app', 'atencao')
            if self.vivo() and self.auto_reload.get():
                # o checkout reescreve muitos .py de uma vez: o reloader
                # reinicia no meio disso, e o boot migra o banco da branch nova.
                self.escrever(
                    '[git] o servidor esta no modo automatico e vai reiniciar '
                    'sozinho com o codigo da branch nova — confira o banco',
                    'erro')
            self.checar_estado()
        self.ref_vista = ref
        self.root.after(2000, self.vigiar_branch)

    def vivo(self):
        return self.proc is not None and self.proc.poll() is None

    def pids_na_porta(self):
        """PIDs escutando na porta do app — inclusive de um cmd antigo."""
        r = subprocess.run(['netstat', '-ano', '-p', 'TCP'], capture_output=True,
                           text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        pids = set()
        for linha in r.stdout.splitlines():
            partes = linha.split()
            if len(partes) >= 5 and partes[3] == 'LISTENING' \
                    and partes[1].endswith(f':{PORTA}'):
                pids.add(partes[4])
        return sorted(pids)

    def derrubar_porta(self):
        pids = self.pids_na_porta()
        if not pids:
            self.escrever(f'nada escutando na porta {PORTA}', 'painel')
            return
        for pid in pids:
            subprocess.run(['taskkill', '/PID', pid, '/T', '/F'],
                           capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
            self.escrever(f'[servidor] derrubado pid {pid} (porta {PORTA})', 'painel')
        self.proc = None

    # --- estado do repo/banco ---------------------------------------------
    def checar_estado(self):
        self.lbl_branch.configure(text='branch ...', fg=SUAVE)
        threading.Thread(target=self._checar_estado, daemon=True).start()

    def _checar_estado(self):
        try:
            r = subprocess.run([PYTHON, ESTADO], cwd=RAIZ, capture_output=True,
                               text=True, encoding='utf-8', errors='replace',
                               creationflags=subprocess.CREATE_NO_WINDOW)
        except OSError as e:  # venv ausente, por exemplo
            self.estado = {'branch': '?', 'estado': 'erro', 'mensagem': str(e)}
            self.root.after(0, self._pintar_estado)
            return
        try:
            # o boot do app escreve no stdout antes do JSON: pega do 1o '{'.
            self.estado = json.loads(r.stdout[r.stdout.index('{'):])
        except (ValueError, json.JSONDecodeError):
            self.estado = {'branch': '?', 'estado': 'erro',
                           'mensagem': 'nao consegui ler o estado do banco'}
            self.fila.put((r.stdout.strip() or r.stderr.strip(), 'erro'))
        self.root.after(0, self._pintar_estado)

    def _pintar_estado(self):
        d = self.estado
        self.lbl_branch.configure(text='branch ' + d.get('branch', '?'), fg=TEXTO)
        cor = {'em_dia': OK, 'vazio': ATENCAO, 'atrasado': ATENCAO}.get(
            d.get('estado'), ERRO)
        alvo = d.get('banco', '?')
        if d.get('local') is False:
            alvo = 'PRODUCAO · ' + alvo
            cor = ERRO if d.get('estado') != 'em_dia' else ATENCAO
        self.lbl_banco.configure(text=f"{alvo}\n{d.get('mensagem', '')}", fg=cor)
        if d.get('estado') not in ('em_dia', None):
            self.escrever('[banco] ' + d.get('mensagem', ''),
                          'ok' if cor is OK else 'atencao')

    def sincronizar(self):
        d = getattr(self, 'estado', None)
        if not d:
            self.escrever('checando o estado primeiro...', 'painel')
            self.checar_estado()
            return
        if d.get('estado') == 'em_dia':
            self.escrever('[banco] ja esta em dia com a branch', 'ok')
            return
        if d.get('estado') == 'a_frente' and d.get('local') is False:
            messagebox.showerror(
                'Banco de producao',
                'O banco apontado pelo .env e remoto (%s).\n\n'
                'Reverter migration ali derruba tabela e coluna com o conteudo, '
                'e nao tem volta. O painel nao faz isso em producao.\n\n'
                'Para sair do desencontro, volte para a branch que tem a revisao '
                '%s.' % (d.get('banco'), d.get('revisao_banco')))
            self.escrever('[banco] sincronizacao recusada: banco de producao', 'erro')
            return
        if d.get('estado') == 'a_frente':
            reverter = d.get('reverter') or []
            if not reverter:
                self.escrever('[banco] ' + d.get('mensagem', ''), 'erro')
                return
            lista = '\n'.join(f"  · {c['revision']} — {c['titulo']}" for c in reverter)
            ok = messagebox.askyesno(
                'Reverter migrations de outra branch?',
                'O banco esta numa revisao que nao existe nesta branch.\n\n'
                'Para sincronizar, estas migrations serao REVERTIDAS:\n\n'
                f'{lista}\n\n'
                'Reverter derruba as colunas e tabelas que elas criaram, com o '
                'conteudo delas. Isso nao tem volta.\n\n'
                'Se ha dado que so existe nessas tabelas, cancele e volte para a '
                'branch ' + (reverter[0].get('ref', '').split('/')[-1] or '?') + '.',
                icon='warning', default='no')
            if not ok:
                self.escrever('[banco] sincronizacao cancelada', 'painel')
                return
        self.tarefa([PYTHON, ESTADO, '--sincronizar'], 'sincronizar')
        self.root.after(4000, self.checar_estado)

    def atualizar_status(self):
        if self.vivo():
            self.status.configure(text=f'rodando · pid {self.proc.pid}', fg=OK)
            self.btn_ligar.configure(state='disabled')
            self.btn_parar.configure(state='normal')
        else:
            self.status.configure(text='parado', fg=SUAVE)
            self.btn_ligar.configure(state='normal')
            self.btn_parar.configure(state='disabled')

    def fechar(self):
        if self.vivo():
            self.parar()
        self.root.destroy()


if __name__ == '__main__':
    raiz = tk.Tk()
    Painel(raiz)
    raiz.mainloop()
