"""Nucleo de contagem de falhas SEGUIDAS por chave (spec 09, RESOP-02.1/02.2).

Extraido de `diagnostics`, onde a regra vivia embutida, para ser consumido
tambem pelo `circuit_breaker` — uma so implementacao de "N seguidas + reset no
sucesso" (principio de reuso do ROADMAP; mesmo movimento do `captcha_img`).

Sao DOIS conceitos de proposito:
- **chave**: o que se conta (o diagnostics conta por `(error_type, alvo)`);
- **grupo**: o que se zera junto (o diagnostics zera o alvo inteiro quando
  qualquer coisa da certo nele — e essa a histerese que impede o portal
  oscilando de abrir e fechar alerta a cada item).

Sem grupo explicito, o grupo e a propria chave (uso do breaker: chave = portal).
"""
import threading


class ContadorRecorrencia:
    def __init__(self, limiar=3):
        self.limiar = limiar
        self._lock = threading.Lock()
        self._contagem = {}       # chave -> ocorrencias seguidas
        self._grupo_chaves = {}   # grupo -> {chaves}

    def falha(self, chave, grupo=None):
        """Registra uma falha da chave e devolve a contagem acumulada."""
        grupo = chave if grupo is None else grupo
        with self._lock:
            n = self._contagem.get(chave, 0) + 1
            self._contagem[chave] = n
            self._grupo_chaves.setdefault(grupo, set()).add(chave)
            return n

    def sucesso(self, grupo):
        """Zera todas as chaves do grupo (qualquer desfecho nao-erro)."""
        with self._lock:
            for chave in self._grupo_chaves.pop(grupo, set()):
                self._contagem.pop(chave, None)

    def estourou(self, chave):
        with self._lock:
            return self._contagem.get(chave, 0) >= self.limiar

    def contagem(self, chave):
        with self._lock:
            return self._contagem.get(chave, 0)

    def limpar(self):
        with self._lock:
            self._contagem.clear()
            self._grupo_chaves.clear()
