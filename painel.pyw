"""Painel do Zelo: sobe o app, mostra o log e expoe as utilidades do dia a dia.

Tkinter puro (stdlib) para nao adicionar dependencia. Substitui o iniciar.bat:
o .bat agora so garante o venv e chama este painel.
"""

import os
import queue
import subprocess
import threading
import tkinter as tk
import webbrowser
from tkinter import font as tkfont

RAIZ = os.path.dirname(os.path.abspath(__file__))
PYTHON = os.path.join(RAIZ, 'venv', 'Scripts', 'python.exe')
GATILHO = os.path.join(RAIZ, 'reload.trigger')
URL = 'http://127.0.0.1:5000'

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

        lateral = tk.Frame(root, bg=FUNDO_LATERAL, width=210)
        lateral.pack(side='left', fill='y')
        lateral.pack_propagate(False)

        self.status = tk.Label(lateral, text='parado', bg=FUNDO_LATERAL, fg=SUAVE,
                               font=('Segoe UI', 10, 'bold'), anchor='w', padx=14, pady=14)
        self.status.pack(fill='x')

        self._secao(lateral, 'servidor')
        self.btn_ligar = self._botao(lateral, 'Iniciar', self.iniciar)
        self.btn_parar = self._botao(lateral, 'Parar', self.parar)
        self._botao(lateral, 'Reload (gatilho)', self.reload_gatilho)
        self._botao(lateral, 'Reload total', self.reload_total)

        self._secao(lateral, 'banco')
        self._botao(lateral, 'flask db upgrade', lambda: self.tarefa(
            [PYTHON, '-m', 'flask', 'db', 'upgrade'], 'db upgrade',
            {'FLASK_APP': 'run.py', 'AUTO_DB_UPGRADE': '0'}))
        self._botao(lateral, 'flask db current', lambda: self.tarefa(
            [PYTHON, '-m', 'flask', 'db', 'current'], 'db current',
            {'FLASK_APP': 'run.py', 'AUTO_DB_UPGRADE': '0'}))

        self._secao(lateral, 'utilidades')
        self._botao(lateral, 'Abrir no navegador', lambda: webbrowser.open(URL))
        self._botao(lateral, 'Instalar dependencias', lambda: self.tarefa(
            [PYTHON, '-m', 'pip', 'install', '-r', 'requirements.txt'], 'pip install'))
        self._botao(lateral, 'Rodar testes (-q)', lambda: self.tarefa(
            [PYTHON, '-m', 'pytest', '-q'], 'pytest'))
        self._botao(lateral, 'Abrir pasta de logs',
                    lambda: os.startfile(os.path.join(RAIZ, 'logs')))
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
        if not os.path.exists(PYTHON):
            self.escrever('venv nao encontrado. Crie com: python -m venv venv', 'erro')

    # --- widgets -----------------------------------------------------------
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
        self.escrever('[servidor] subindo run.py (FLASK_DEBUG=1)', 'painel')
        self.proc = self._popen([PYTHON, 'run.py'], {'FLASK_DEBUG': '1'})
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

    def vivo(self):
        return self.proc is not None and self.proc.poll() is None

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
