import enum
from sqlalchemy import event
from app import db
from app.utils import utcnow_naive
from datetime import date, datetime, timezone
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash


class TipoCertidao(enum.Enum):
    FEDERAL = "Federal"
    FGTS = "FGTS"
    ESTADUAL = "Estadual"
    MUNICIPAL = "Municipal"
    TRABALHISTA = "Trabalhista"


class SubtipoCertidao(enum.Enum):
    GERAL = "Geral"
    MOBILIARIO = "Mobiliário"


class StatusEspecial(enum.Enum):
    PENDENTE = "Pendente"


class Empresa(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False)
    cnpj = db.Column(db.String(18), unique=True, nullable=False)
    estado = db.Column(db.String(2), nullable=False, default='RS')
    cidade = db.Column(db.String(50), nullable=False)
    inscricao_mobiliaria = db.Column(db.String(6), nullable=True)

    certidoes = db.relationship(
        'Certidao', backref='empresa', lazy='selectin', cascade="all, delete-orphan")
    # 1:1 com o espelho da Receita. delete-orphan porque remover a empresa tem
    # de levar o dado dela junto — senao fica linha orfa com FK pendurada.
    # selectin como `certidoes`: a listagem de empresas le a situacao de cada
    # linha, e com lazy='select' isso viraria uma query por empresa.
    dados_receita = db.relationship(
        'DadosReceita', backref='empresa', uselist=False, lazy='selectin',
        cascade='all, delete-orphan')
    # 1:1 com o certificado achado no drive (manifestador). selectin como
    # `dados_receita`: o pre-voo do cofre le o estado de todas as empresas de uma
    # vez, e lazy='select' viraria uma query por linha.
    certificado = db.relationship(
        'CertificadoEmpresa', backref='empresa', uselist=False, lazy='selectin',
        cascade='all, delete-orphan')
    # lazy PADRAO de proposito, ao contrario dos dois acima: a listagem de
    # empresas nao mostra chave de NF-e, e sao centenas por empresa — carregar
    # junto traria milhares de linhas para uma tela que nao as usa. Quem precisa
    # delas consulta `ChaveManifestacao` direto, com filtro.
    chaves_manifestacao = db.relationship(
        'ChaveManifestacao', backref='empresa', cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Empresa {self.nome}>'


class DadosReceita(db.Model):
    """O que a Receita diz sobre a empresa — a contraparte do cadastro.

    Tabela separada da `Empresa` de proposito, pela mesma razao que separou
    `NotaEmitidaNfse` de `NotaNfse`: `Empresa` guarda o que o ESCRITORIO
    decidiu, esta guarda o que a RECEITA informa. Guardar junto perderia
    justamente a diferenca entre as duas, que e o dado interessante — a
    divergencia que o operador precisa conferir.

    Invariante inegociavel (spec 08, DATA-01.8): NADA aqui sobrescreve
    `Empresa.nome`. Aquele campo e a chave de busca da pasta no drive de rede
    (`file_manager.encontrar_pasta_empresa`); trocar pelo `razao_social` da
    Receita quebraria o casamento com as pastas ja existentes. Por isso a razao
    social vive aqui, como coluna propria.
    """
    __tablename__ = 'dados_receita'

    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'),
                           unique=True, nullable=False)

    # --- situacao cadastral (DATA-02) ---
    # String, nunca db.Enum nativo: o enum nativo diverge entre SQLite e MySQL e
    # a suite roda nos dois (AD-016/AD-020).
    situacao = db.Column(db.String(40), nullable=True)
    situacao_data = db.Column(db.Date, nullable=True)
    situacao_motivo = db.Column(db.String(200), nullable=True)

    # --- identificacao ---
    razao_social = db.Column(db.String(200), nullable=True)
    nome_fantasia = db.Column(db.String(200), nullable=True)
    data_inicio_atividade = db.Column(db.Date, nullable=True)
    porte = db.Column(db.String(40), nullable=True)
    natureza_juridica = db.Column(db.String(120), nullable=True)
    matriz_filial = db.Column(db.String(20), nullable=True)

    # --- endereco ---
    logradouro = db.Column(db.String(200), nullable=True)
    numero = db.Column(db.String(20), nullable=True)
    complemento = db.Column(db.String(120), nullable=True)
    bairro = db.Column(db.String(100), nullable=True)
    cep = db.Column(db.String(9), nullable=True)
    # Casa com `Municipio` melhor que string de cidade; a coluna entra agora,
    # trocar o matching da automacao fica fora do escopo desta spec.
    municipio_ibge = db.Column(db.String(7), nullable=True)
    municipio = db.Column(db.String(100), nullable=True)
    uf = db.Column(db.String(2), nullable=True)

    # --- fiscal ---
    cnae_fiscal = db.Column(db.String(10), nullable=True)
    cnae_descricao = db.Column(db.String(200), nullable=True)
    # Tri-estado de proposito: None = "a fonte nao informou", que e diferente de
    # False = "nao optante". A BrasilAPI devolve null nos dois casos antigos.
    opcao_simples = db.Column(db.Boolean, nullable=True)
    opcao_simples_data = db.Column(db.Date, nullable=True)

    # --- proveniencia ---
    fonte = db.Column(db.String(20), nullable=True)
    # Hora local naive (AD-004): e carimbo de dominio, nao log tecnico. Indexado
    # porque o job de recheck ordena por ele a cada execucao.
    verificado_em = db.Column(db.DateTime, nullable=True, index=True)

    def __repr__(self):
        return f'<DadosReceita empresa={self.empresa_id} {self.situacao}>'


class Certidao(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.Enum(TipoCertidao), nullable=False)
    subtipo = db.Column(
        db.Enum(
            SubtipoCertidao,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
            name='subtipocertidao'
        ),
        nullable=True
    )

    data_validade = db.Column(db.Date, nullable=True)
    caminho_arquivo = db.Column(db.String(500), nullable=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey(
        'empresa.id'), nullable=False)
    status_especial = db.Column(db.Enum(StatusEspecial), nullable=True)
    # Ultima modificacao (hora local naive). default cobre a criacao/INSERT
    # (onde onupdate nao dispara); onupdate cobre qualquer UPDATE persistido.
    # Alimenta a ordenação "Última atualização" de Certidões.
    atualizado_em = db.Column(
        db.DateTime, nullable=True,
        default=datetime.now, onupdate=datetime.now)

    def __repr__(self):
        if self.subtipo:
            return f'<Certidao {self.tipo.value} - {self.subtipo.value} - {self.empresa.nome}>'
        return f'<Certidao {self.tipo.value} - {self.empresa.nome}>'

    @property
    def status(self):
        if self.status_especial == StatusEspecial.PENDENTE:
            return 'vermelho'

        if self.data_validade is None:
            return 'cinza'
        hoje = date.today()
        diferenca_dias = (self.data_validade - hoje).days
        limite_dias = get_a_vencer_dias(tipo=self.tipo)
        if diferenca_dias < 0:
            return 'vermelho'
        elif diferenca_dias <= limite_dias:
            return 'amarelo'
        else:
            return 'verde'

    @property
    def ordem_exibicao(self):
        ordem_tipo = {
            TipoCertidao.FEDERAL: 1,
            TipoCertidao.FGTS: 2,
            TipoCertidao.ESTADUAL: 3,
            TipoCertidao.MUNICIPAL: 4,
            TipoCertidao.TRABALHISTA: 5,
        }
        subtipo_ordem = 0
        if self.tipo == TipoCertidao.MUNICIPAL and self.subtipo:
            if self.subtipo == SubtipoCertidao.GERAL:
                subtipo_ordem = 1
            elif self.subtipo == SubtipoCertidao.MOBILIARIO:
                subtipo_ordem = 2
        return (ordem_tipo.get(self.tipo, 99), subtipo_ordem, self.id or 0)


class Municipio(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), unique=True, nullable=False)
    url_certidao = db.Column(db.String(300), nullable=False)

    automacao_ativa = db.Column(db.Boolean, nullable=False, default=True)
    validade_dias = db.Column(db.Integer, nullable=True)
    usar_slow_typing = db.Column(db.Boolean, nullable=False, default=False)
    config_automacao = db.Column(db.Text, nullable=True)

    cnpj_field_id = db.Column(db.String(100), nullable=True)
    by = db.Column(db.String(20), nullable=True)

    inscricao_field_id = db.Column(db.String(100), nullable=True)
    inscricao_field_by = db.Column(db.String(20), nullable=True)

    pre_fill_click_id = db.Column(db.String(100), nullable=True)
    pre_fill_click_by = db.Column(db.String(20), nullable=True)

    shadow_host_selector = db.Column(db.String(100), nullable=True)
    inner_input_selector = db.Column(db.String(100), nullable=True)

    def __repr__(self):
        return f'<Municipio {self.nome}>'


class EventoDiagnostico(db.Model):
    """Historico persistente de erros/avisos para o painel de diagnostico.
    Sobrevive a reinicios do sistema (o buffer em memoria nao)."""
    __tablename__ = 'evento_diagnostico'

    id = db.Column(db.Integer, primary_key=True)
    criado_em = db.Column(db.DateTime, nullable=False, default=utcnow_naive, index=True)
    evento = db.Column(db.String(80), nullable=False)
    nivel = db.Column(db.String(10), nullable=False, default='ERROR')
    error_type = db.Column(db.String(30), nullable=True)
    alvo = db.Column(db.String(80), nullable=True)
    mensagem = db.Column(db.String(500), nullable=True)
    request_id = db.Column(db.String(40), nullable=True)
    execution_id = db.Column(db.String(40), nullable=True)
    certidao_id = db.Column(db.Integer, nullable=True)
    empresa_id = db.Column(db.Integer, nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            # criado_em e gravado em UTC naive (default=utcnow_naive); marca o
            # fuso como UTC ao serializar para que o front (new Date) converta
            # corretamente para o horario local do PC (Brasilia)
            'criado_em': (
                self.criado_em.replace(tzinfo=timezone.utc).isoformat()
                if self.criado_em else None
            ),
            'evento': self.evento,
            'nivel': self.nivel,
            'error_type': self.error_type,
            'alvo': self.alvo,
            'mensagem': self.mensagem,
            'request_id': self.request_id,
            'execution_id': self.execution_id,
            'certidao_id': self.certidao_id,
            'empresa_id': self.empresa_id,
        }

    def __repr__(self):
        return f'<EventoDiagnostico {self.nivel} {self.evento}>'


class ExecucaoLote(db.Model):
    """Registro persistente de cada lote iniciado (FGTS, Estadual RS, Municipal).

    Sobrevive a reinicios do sistema (o batch_state em memoria nao). Alimenta o
    relatorio "quando foi o ultimo lote de X" — evita reprocessar pendentes cedo
    demais e gastar creditos de captcha à toa. Grava-se no INICIO do lote, pois é
    quando os creditos passam a ser consumidos."""
    __tablename__ = 'execucao_lote'

    id = db.Column(db.Integer, primary_key=True)
    # nome do lote conforme cfg['nome_lote']: 'FGTS' | 'Estadual RS' | 'Municipal'
    tipo = db.Column(db.String(30), nullable=False, index=True)
    # escopo do lote: 'pendentes' (reprocessa positivas) | 'default' (vencidas/a vencer)
    escopo = db.Column(db.String(20), nullable=False, default='default')
    # quem disparou o lote: 'manual' (rota HTTP / operador) | 'agendador' (emissao
    # proativa, spec 02). Permite medir quanto da operacao ja e automatica sem
    # esconder nenhum lote dos relatorios (spec 07, COV-04). Registros anteriores a
    # esta coluna ficam 'manual' (backfill do default na migration).
    origem = db.Column(db.String(12), nullable=False, default='manual', index=True)
    total = db.Column(db.Integer, nullable=False, default=0)
    iniciado_em = db.Column(
        db.DateTime, nullable=False, default=utcnow_naive, index=True)
    execution_id = db.Column(db.String(40), nullable=True)

    # desfecho do lote (gravado no fim de run_batch_loop via on_finish). Null
    # enquanto roda / se o lote começou antes deste recurso. status:
    # 'completed' | 'stopped' | 'error' | 'paused'. Para escopo 'pendentes',
    # `sucesso` = pendentes que emitiram (viraram negativa) e `pendentes_resultado`
    # = as que seguiram pendentes.
    finalizado_em = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), nullable=True)
    sucesso = db.Column(db.Integer, nullable=False, default=0)
    pendentes_resultado = db.Column(db.Integer, nullable=False, default=0)
    falhas = db.Column(db.Integer, nullable=False, default=0)

    def __repr__(self):
        return f'<ExecucaoLote {self.tipo}/{self.escopo} {self.iniciado_em}>'


class SnapshotCertidao(db.Model):
    """Foto diária das contagens de certidões por tipo × status, para o gráfico
    de evolução no tempo (ex.: pendentes descendo). Não há histórico reconstruível
    a partir da Certidao (o estado é sobrescrito), então acumulamos uma foto por
    dia. Gravada de forma lazy (sem scheduler) na 1ª visita do dia."""
    __tablename__ = 'snapshot_certidao'

    id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.Date, nullable=False, index=True)
    tipo = db.Column(db.String(20), nullable=False)  # TipoCertidao.value
    # validas | a_vencer | vencidas | pendentes | sem_data
    status = db.Column(db.String(12), nullable=False)
    quantidade = db.Column(db.Integer, nullable=False, default=0)

    __table_args__ = (
        db.UniqueConstraint('data', 'tipo', 'status',
                            name='uq_snapshot_dia_tipo_status'),
    )

    def __repr__(self):
        return f'<SnapshotCertidao {self.data} {self.tipo}/{self.status}={self.quantidade}>'


class TarefaEmissao(db.Model):
    """Fila durável de emissão proativa (spec 02, AD-010). Uma linha por certidão
    a emitir. É a camada de durabilidade: sobrevive a restart (reconciliação de
    órfãs no boot) e habilita retry por item; o batch_state em memória permanece
    para o progresso do lote em curso. Carimbos em hora local naive (AD-004),
    como Certidao.atualizado_em."""
    __tablename__ = 'tarefa_emissao'

    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(20), nullable=False, index=True)  # TipoCertidao.value
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)
    certidao_id = db.Column(
        db.Integer, db.ForeignKey('certidao.id'), nullable=False, index=True)
    # pendente | rodando | ok | falha | retry
    status = db.Column(db.String(12), nullable=False, default='pendente', index=True)
    tentativas = db.Column(db.Integer, nullable=False, default=0)
    agendada_em = db.Column(db.DateTime, nullable=False, default=datetime.now)
    iniciada_em = db.Column(db.DateTime, nullable=True)
    concluida_em = db.Column(db.DateTime, nullable=True)
    erro = db.Column(db.String(500), nullable=True)
    execution_id = db.Column(db.String(40), nullable=True, index=True)

    def __repr__(self):
        return f'<TarefaEmissao {self.tipo} cert={self.certidao_id} {self.status}>'


class NotificacaoLog(db.Model):
    """Trilha durável do que já foi empurrado por e-mail (spec 03, AD-011).

    Serve de anti-spam: antes de enviar uma notificação consulta-se o último
    envio da mesma `chave` e só se dispara fora da janela. Sobrevive a restart
    (não reenvia após reboot) e vira histórico do que foi notificado. Sem unique
    constraint — mantém um registro por envio (mesma chave em janelas diferentes).
    Carimbo em hora local naive (AD-004), como Certidao.atualizado_em."""
    __tablename__ = 'notificacao_log'

    id = db.Column(db.Integer, primary_key=True)
    # 'digest' | 'saldo_baixo' | 'falha:<error_type>:<alvo>'
    #   | 'empresa_baixada:<id>' | 'municipio_quebrado:<nome>'
    #   | 'portal_fora:<alvo>' | 'solver_captcha:<alvo>'
    chave = db.Column(db.String(120), nullable=False, index=True)
    # 'digest' | 'alerta_saldo' | 'alerta_falha' | 'alerta_municipio'
    #   | 'alerta_empresa_baixada' | 'alerta_portal' | 'alerta_solver'
    # Folga proposital no tamanho: no MySQL (strict mode) um tipo mais longo que a
    # coluna e ERRO, e como _registrar_envio e best-effort o registro simplesmente
    # nao entra — o anti-spam para de funcionar EM SILENCIO e o alerta vira um
    # e-mail por execucao do job. No SQLite passaria truncado sem reclamar.
    tipo = db.Column(db.String(40), nullable=False)
    detalhe = db.Column(db.String(500), nullable=True)
    enviada_em = db.Column(db.DateTime, nullable=False, default=datetime.now)

    def __repr__(self):
        return f'<NotificacaoLog {self.chave} {self.enviada_em}>'


class PautaNotificacao(db.Model):
    """Fila do que ainda NAO foi contado no resumo diario (AD-029).

    Os jobs nao mandam mais um e-mail por achado: eles *anotam* o achado aqui, e
    um unico job diario junta tudo num so e-mail. A tabela existe porque os
    produtores rodam em horarios diferentes (verificacao de municipios, recheck
    da Receita, inventario do cofre) e o breaker abre no meio de um lote — um
    buffer em memoria perderia o achado no primeiro restart, que e justamente
    quando ha mais o que contar.

    `enviada_em` NULL = pendente; preenchida = ja saiu num resumo (vira historico,
    nao e apagada). O anti-spam continua no `NotificacaoLog`, gravado no momento
    do envio: aqui a chave serve para nao anotar o MESMO achado duas vezes
    enquanto ele espera o proximo resumo.

    Carimbos em hora local naive (AD-004), como o NotificacaoLog."""
    __tablename__ = 'pauta_notificacao'

    id = db.Column(db.Integer, primary_key=True)
    # Mesmo vocabulario de chave do NotificacaoLog (é a mesma identidade de
    # achado): 'certificado_vencido:<empresa_id>' | 'municipio_quebrado:<nome>' ...
    chave = db.Column(db.String(120), nullable=False, index=True)
    # Mesmo vocabulario de tipo do NotificacaoLog, e pela mesma razao a mesma
    # folga de tamanho: gravacao best-effort que estoura a coluna some em
    # silencio no MySQL e o achado nunca chega ao resumo.
    tipo = db.Column(db.String(40), nullable=False)
    # Titulo curto (uma linha na secao do resumo) e corpo com o detalhe. Text no
    # corpo porque alerta de municipio ja carrega lista de seletores.
    titulo = db.Column(db.String(200), nullable=False)
    corpo = db.Column(db.Text, nullable=True)
    criada_em = db.Column(db.DateTime, nullable=False, default=datetime.now)
    enviada_em = db.Column(db.DateTime, nullable=True, index=True)

    def __repr__(self):
        return f'<PautaNotificacao {self.chave} {self.criada_em}>'


class PapelUsuario:
    """Papéis fixos (String, não db.Enum — portabilidade SQLite↔MySQL; ver AD-005).

    Rank: leitura < operador < admin (admin = superusuário)."""
    ADMIN = 'admin'
    OPERADOR = 'operador'
    LEITURA = 'leitura'
    TODOS = (ADMIN, OPERADOR, LEITURA)


class Usuario(db.Model, UserMixin):
    """Credenciais e papel do usuário; integra Flask-Login (AD-007)."""

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    senha_hash = db.Column(db.String(255), nullable=False)
    papel = db.Column(db.String(20), nullable=False, default=PapelUsuario.LEITURA)
    ativo = db.Column(db.Boolean, nullable=False, default=True)
    criado_em = db.Column(db.DateTime, nullable=False, default=utcnow_naive)

    def set_senha(self, senha):
        # werkzeug scrypt por padrão; persiste só o hash (AUTH-02)
        self.senha_hash = generate_password_hash(senha)

    def checar_senha(self, senha):
        return check_password_hash(self.senha_hash, senha)

    @property
    def is_active(self):
        # sobrescreve UserMixin: sessão de usuário desativado é barrada (edge case)
        return self.ativo

    def __repr__(self):
        return f'<Usuario {self.username} ({self.papel})>'


class EventoAuditoria(db.Model):
    """Trilha de ações sensíveis (quem/quando/ação/alvo/IP/resultado).

    Espelha EventoDiagnostico; criado_em em UTC naive, serializado como UTC no
    to_dict (AD-006 — exceção explícita a AD-004: auditoria é registro técnico)."""
    __tablename__ = 'evento_auditoria'

    id = db.Column(db.Integer, primary_key=True)
    criado_em = db.Column(db.DateTime, nullable=False, default=utcnow_naive, index=True)
    usuario_id = db.Column(db.Integer, nullable=True, index=True)
    usuario_nome = db.Column(db.String(80), nullable=True)  # snapshot: sobrevive à remoção
    papel = db.Column(db.String(20), nullable=True)
    acao = db.Column(db.String(80), nullable=False, index=True)
    alvo_tipo = db.Column(db.String(40), nullable=True)
    alvo_id = db.Column(db.Integer, nullable=True)
    ip = db.Column(db.String(45), nullable=True)  # cabe IPv6
    resultado = db.Column(db.String(10), nullable=False, default='ok')  # 'ok' | 'erro'
    detalhe = db.Column(db.String(500), nullable=True)
    request_id = db.Column(db.String(40), nullable=True)

    @property
    def criado_em_iso(self):
        # criado_em em UTC naive; marca tzinfo=UTC para o front (new Date)
        # converter para o horário local do PC (mesmo padrão do EventoDiagnostico)
        return (self.criado_em.replace(tzinfo=timezone.utc).isoformat()
                if self.criado_em else None)

    def to_dict(self):
        return {
            'id': self.id,
            'criado_em': self.criado_em_iso,
            'usuario_id': self.usuario_id,
            'usuario_nome': self.usuario_nome,
            'papel': self.papel,
            'acao': self.acao,
            'alvo_tipo': self.alvo_tipo,
            'alvo_id': self.alvo_id,
            'ip': self.ip,
            'resultado': self.resultado,
            'detalhe': self.detalhe,
            'request_id': self.request_id,
        }

    def __repr__(self):
        return f'<EventoAuditoria {self.acao} {self.resultado}>'


class ConfiguracaoSistema(db.Model):
    __tablename__ = 'configuracao_sistema'

    id = db.Column(db.Integer, primary_key=True)
    a_vencer_dias = db.Column(db.Integer, nullable=False, default=7)
    a_vencer_dias_federal = db.Column(db.Integer, nullable=True)
    a_vencer_dias_fgts = db.Column(db.Integer, nullable=True)
    a_vencer_dias_estadual = db.Column(db.Integer, nullable=True)
    a_vencer_dias_municipal = db.Column(db.Integer, nullable=True)
    a_vencer_dias_trabalhista = db.Column(db.Integer, nullable=True)
    # caminho base da rede onde os PDFs sao organizados; em branco usa env/default
    caminho_rede = db.Column(db.String(500), nullable=True)
    # Agendador de emissao proativa (spec 02). Ligado por padrao (decisao do
    # operador); horario em hora local naive (0-23, AD-004).
    agendador_ativo = db.Column(db.Boolean, nullable=False, default=True)
    agendador_hora = db.Column(db.Integer, nullable=False, default=3)
    # Notificacoes por e-mail (spec 03). Destinatarios separados por virgula/;/
    # linha; cadencia do digest 'semanal' (default) ou 'diaria'. Credenciais SMTP
    # ficam em env (config.py), nunca aqui.
    notif_destinatarios = db.Column(db.String(1000), nullable=True)
    notif_cadencia = db.Column(db.String(10), nullable=False, default='semanal')
    # Janela (dias) de antecedencia do aviso de vencimento de certificado digital
    # (AD-029). Estava so no env (MANIF_CERT_ALERTA_DIAS), fora do alcance de quem
    # opera; a coluna passa a mandar e o env vira apenas o default de instalacao.
    cert_alerta_dias = db.Column(db.Integer, nullable=False, default=10)
    # Recheck da situacao cadastral na Receita (spec 08, DATA-02.6/02.7). O job
    # e fatiado de proposito: a ReceitaWS aceita ~3 req/min, entao a carteira
    # gira em alguns dias em vez de estourar cota num dia so.
    receita_recheck_ativo = db.Column(db.Boolean, nullable=False, default=True)
    receita_recheck_idade_dias = db.Column(db.Integer, nullable=False, default=30)
    receita_recheck_limite = db.Column(db.Integer, nullable=False, default=50)

    def __repr__(self):
        return f'<ConfiguracaoSistema {self.id}>'


class StatusNotaNfse:
    """Status da NotaNfse em String, nao db.Enum nativo (AD-016/AD-020: o enum
    nativo diverge entre SQLite e MySQL e a suite roda nos dois).

    Fluxo feliz: EMPRESA_PENDENTE -> PRONTA -> PREENCHENDO ->
    AGUARDANDO_CONFIRMACAO -> EMITIDA. Ramos: DUPLICATA (exige liberacao),
    CADASTRO_PENDENTE (CNPJ digitado, empresa a cadastrar), INVALIDA (linha
    malformada), PULADA, FALHA, CANCELADA, DESCRICAO_PENDENTE, AGRUPADA."""
    EMPRESA_PENDENTE = 'empresa_pendente'
    # CNPJ informado a mao, empresa ainda nao cadastrada: emite e mantem o
    # convite para cadastrar nos meses seguintes
    CADASTRO_PENDENTE = 'cadastro_pendente'
    # tomador pessoa fisica: estado FINAL, nunca vira cadastro de Empresa
    PESSOA_FISICA = 'pessoa_fisica'
    # so no extrato do Inter: a descricao do Pix nao disse nem a competencia nem
    # o servico, entao nao ha texto para a nota. Fica FORA da fila ate o
    # operador decidir — chutar aqui escreve a coisa errada no documento fiscal
    DESCRICAO_PENDENTE = 'descricao_pendente'
    # linha absorvida por outra nota num agrupamento confirmado pelo operador
    # (entradas + estorno viram uma nota so). Estado FINAL, aponta a sobrevivente
    AGRUPADA = 'agrupada'
    PRONTA = 'pronta'
    PREENCHENDO = 'preenchendo'
    AGUARDANDO_CONFIRMACAO = 'aguardando_confirmacao'
    EMITIDA = 'emitida'
    DUPLICATA = 'duplicata'
    INVALIDA = 'invalida'
    PULADA = 'pulada'
    FALHA = 'falha'
    # o contador decidiu que esta linha nao vira nota. Reversivel pela mesma
    # rota (o operador desfaz e a linha volta ao estado que teria).
    #
    # Diferente de PULADA, que e "nao agora": PULADA continua em
    # STATUS_EMITIVEIS e volta na proxima rodada do lote. CANCELADA e uma
    # decisao, e por isso OCUPA a competencia — reimportar o mesmo extrato traz
    # a linha de volta como DUPLICATA (liberavel), nunca como PRONTA. Sem isso a
    # decisao do operador se perderia no proximo import e a nota que ele
    # dispensou seria emitida.
    CANCELADA = 'cancelada'


class OrigemVinculoNfse:
    """Como o CNPJ da nota foi resolvido — trilha de auditoria do match (NFSE-03)."""
    EXATO = 'exato'
    APELIDO = 'apelido'
    FUZZY = 'fuzzy'
    MANUAL = 'manual'


class ConfiguracaoNfse(db.Model):
    """Campos fixos da NFSe (registro unico, id=1) — NFSE-08/09.

    Todos os defaults vieram da recon do Emissor Nacional (T0), nao de suposicao.
    O template da descricao precisa conter o placeholder `{competencia}`; a
    validacao vive em `app/services/nfse_config.py`."""
    __tablename__ = 'configuracao_nfse'

    id = db.Column(db.Integer, primary_key=True)
    regime_apuracao_sn = db.Column(db.String(4), nullable=False, default='1')
    municipio_servico_codigo = db.Column(db.String(10), nullable=False, default='4310330')
    municipio_servico_nome = db.Column(db.String(60), nullable=False, default='Imbé/RS')
    codigo_tributacao = db.Column(db.String(20), nullable=False, default='17.19.01')
    item_nbs = db.Column(db.String(20), nullable=False, default='113022100')
    descricao_template = db.Column(
        db.String(300), nullable=False,
        default='HONORÁRIOS PROFISSIONAIS REFERENTES AO MÊS DE {competencia}')
    piscofins_situacao = db.Column(db.String(4), nullable=False, default='0')
    piscofins_tipo_retencao = db.Column(db.String(4), nullable=False, default='0')
    # Categoria do extrato do Inter que marca um recebimento de cliente. E o
    # UNICO filtro que separa honorarios de qualquer outro credito na conta, e o
    # nome e digitado pelo operador no app do banco — se ele renomear la, o
    # import para de achar as linhas. Por isso e campo editavel, nao constante.
    categoria_extrato = db.Column(
        db.String(60), nullable=False, default='HONORÁRIOS - CLIENTES')

    def __repr__(self):
        return f'<ConfiguracaoNfse {self.id}>'


class LoteNfse(db.Model):
    """Uma importacao do CSV de cobrancas do banco (NFSE-06).

    Carimbo em hora local naive (AD-004), como Certidao.atualizado_em."""
    __tablename__ = 'lote_nfse'

    id = db.Column(db.Integer, primary_key=True)
    nome_arquivo = db.Column(db.String(200), nullable=True)
    criado_em = db.Column(db.DateTime, nullable=False, default=datetime.now, index=True)
    total = db.Column(db.Integer, nullable=False, default=0)
    execution_id = db.Column(db.String(40), nullable=True)

    notas = db.relationship(
        'NotaNfse', backref='lote', lazy='selectin', cascade='all, delete-orphan')

    def __repr__(self):
        return f'<LoteNfse {self.id} {self.nome_arquivo} ({self.total})>'


class NotaNfse(db.Model):
    """Uma linha do CSV do banco = uma NFSe a emitir (NFSE-01..07).

    Valores monetarios em Numeric(12,2), nunca Float: o numero vai para um
    documento fiscal e Float acumula erro de arredondamento. `valor_final`
    (coluna I do CSV) e o valor a emitir; `divergencia_valor` sinaliza quando
    F+G-H nao bate com I (rede de seguranca contra CSV corrompido)."""
    __tablename__ = 'nota_nfse'

    id = db.Column(db.Integer, primary_key=True)
    lote_id = db.Column(
        db.Integer, db.ForeignKey('lote_nfse.id'), nullable=False, index=True)
    # nulo = pendente de resolucao (empresa nao vinculada)
    empresa_id = db.Column(
        db.Integer, db.ForeignKey('empresa.id'), nullable=True, index=True)

    nome_csv = db.Column(db.String(140), nullable=True)
    # normalizado (caixa alta, sem acento, espacos colapsados): chave do apelido
    nome_csv_norm = db.Column(db.String(140), nullable=True, index=True)
    # CPF ou CNPJ do tomador: nem todo cliente e pessoa juridica
    documento = db.Column(db.String(18), nullable=True, index=True)
    tipo_documento = db.Column(db.String(4), nullable=True)

    data_pagamento = db.Column(db.Date, nullable=True)
    vencimento = db.Column(db.Date, nullable=True)

    valor_titulo = db.Column(db.Numeric(12, 2), nullable=True)
    acrescimos = db.Column(db.Numeric(12, 2), nullable=True)
    deducoes = db.Column(db.Numeric(12, 2), nullable=True)
    valor_final = db.Column(db.Numeric(12, 2), nullable=True)

    # 'MM/AAAA'. Duas origens, deliberadamente diferentes: no CSV de cobrancas e
    # DERIVADA (mes anterior ao vencimento do titulo); no extrato do Inter e
    # LITERAL, lida da descricao do Pix. Nota de servico avulso nao tem
    # competencia escrita, entao grava-se o mes do pagamento — serve para
    # agrupar e filtrar, mas NAO entra no texto da nota (ver descricao_servico).
    competencia = db.Column(db.String(7), nullable=True, index=True)

    # Descricao pronta do servico, quando o Pix nao foi de honorarios
    # ('ALTERAÇÃO CONTRATUAL', 'BAIXA DE EMPRESA'). NULL = honorarios, e a
    # descricao sai do template da ConfiguracaoNfse com a competencia. Manter
    # NULL como "honorarios" e o que preserva o comportamento do CSV sem
    # backfill: toda nota que ja existe continua usando o template.
    descricao_servico = db.Column(db.String(300), nullable=True)
    # Flag PROPRIA, e nao derivada de `descricao_servico is None`: nota de
    # servico tambem grava competencia (mes do pagamento, para agrupar e
    # filtrar), entao nao ha combinacao de campos que distinga "e honorarios"
    # de "o sistema nao soube dizer o que e". Sem esta coluna, a pendencia se
    # perderia assim que o operador resolvesse a empresa.
    descricao_pendente = db.Column(db.Boolean, nullable=False, default=False)
    # 'csv' (cobrancas do Banrisul) | 'inter' (extrato PDF do Banco Inter)
    origem_extrato = db.Column(db.String(10), nullable=True)
    # descricao crua do lancamento, como veio do banco. Fica na tela ao lado da
    # descricao resolvida: quando o operador confere uma nota de servico, o que
    # ele precisa ver e o texto original do Pix, nao a interpretacao do sistema.
    descricao_extrato = db.Column(db.String(300), nullable=True)

    # Valor como veio do extrato, sempre. `valor_final` e o valor A EMITIR e
    # pode ser reescrito por um agrupamento; este nao muda nunca. E o que
    # permite desfazer o agrupamento e responder "de quanto era a linha no
    # banco?" sem reimportar o arquivo.
    valor_extrato = db.Column(db.Numeric(12, 2), nullable=True)

    # --- proposta de agrupamento (entradas + estorno viram uma nota so) ------
    # Token compartilhado pelas notas do mesmo grupo proposto. Enquanto existe e
    # nao foi confirmado nem descartado, as notas ficam FORA da fila: emitir uma
    # entrada bruta cujo estorno o operador ainda nao avaliou e emitir a maior.
    grupo_sugerido = db.Column(db.String(40), nullable=True, index=True)
    # valor liquido do grupo (entradas - saidas), so na nota lider da proposta
    grupo_valor_liquido = db.Column(db.Numeric(12, 2), nullable=True)
    # a conta por extenso, para o operador conferir antes de aceitar
    # ('684,00 + 2.000,00 - 1.784,00 (estorno 08/07)')
    grupo_detalhe = db.Column(db.String(300), nullable=True)
    grupo_descartado = db.Column(db.Boolean, nullable=False, default=False)
    # Agrupamento JA aplicado. O token NAO e apagado ao confirmar, e e isso que
    # torna o desfazer possivel: sem ele nao haveria como reencontrar as irmas.
    grupo_confirmado = db.Column(db.Boolean, nullable=False, default=False)
    # Descricao que a nota juntada vai levar. Editavel pelo operador na propria
    # faixa da proposta; o default vem do servico escrito em alguma das linhas
    # do grupo ("ALT. CONTRATO" veio na de 684,00, nao na de 2.000,00).
    grupo_descricao = db.Column(db.String(300), nullable=True)
    # Retrato da lider ANTES do agrupamento, para o desfazer devolver o que era.
    # O valor anterior nao entra aqui: ele vive em `valor_extrato`, que nunca
    # muda. Descricao e pendencia precisam de retrato porque o operador pode
    # te-las definido a mao antes de juntar, e re-deduzi-las do extrato
    # descartaria em silencio o que ele digitou.
    grupo_descricao_anterior = db.Column(db.String(300), nullable=True)
    grupo_pendente_anterior = db.Column(db.Boolean, nullable=True)
    # nota que absorveu esta linha depois do agrupamento confirmado
    agrupada_em_id = db.Column(
        db.Integer, db.ForeignKey('nota_nfse.id'), nullable=True)
    # valor ajustado a mao (so em nota agrupada): deixa rastro de que o numero
    # nao veio direto do extrato
    valor_ajustado = db.Column(db.Boolean, nullable=False, default=False)

    status = db.Column(
        db.String(24), nullable=False,
        default=StatusNotaNfse.EMPRESA_PENDENTE, index=True)
    origem_vinculo = db.Column(db.String(10), nullable=True)
    score_match = db.Column(db.Integer, nullable=True)
    divergencia_valor = db.Column(db.Boolean, nullable=False, default=False)

    duplicata_de_id = db.Column(
        db.Integer, db.ForeignKey('nota_nfse.id'), nullable=True)
    # Relacionamento (nao muda o schema) para o import poder apontar a duplicata
    # para a nota original ANTES de ela existir no banco: no laco do import
    # nenhuma das duas tem id ainda, e so o objeto esta disponivel.
    duplicata_de = db.relationship(
        'NotaNfse', remote_side=[id], foreign_keys=[duplicata_de_id])
    duplicata_liberada = db.Column(db.Boolean, nullable=False, default=False)

    emitida_em = db.Column(db.DateTime, nullable=True)
    # 'automacao' | 'manual': o operador pode marcar nota que ja emitiu fora do
    # sistema, e ela passa a contar na trava de duplicidade
    origem_emissao = db.Column(db.String(12), nullable=True)
    erro = db.Column(db.String(500), nullable=True)

    def __repr__(self):
        return f'<NotaNfse {self.nome_csv} {self.competencia} {self.status}>'


class ApelidoNfse(db.Model):
    """Memoria do que fazer com um nome vindo do banco (NFSE-03).

    Guarda DOIS tipos de vinculo, por isso `empresa_id` e opcional:

    - nome -> Empresa cadastrada (o caso comum);
    - nome -> documento avulso (CPF, ou CNPJ de empresa ainda nao cadastrada).

    O segundo existe porque parte dos tomadores e pessoa fisica e nunca vai
    virar cadastro: sem essa memoria o operador redigitaria o CPF todo mes.

    N:1 de proposito: o banco escreve o mesmo cliente de varias formas ao longo
    do tempo (truncamento em 35 chars, abreviacoes), e uma coluna unica em
    Empresa so guardaria uma. Carimbo em hora local naive (AD-004)."""
    __tablename__ = 'apelido_nfse'

    id = db.Column(db.Integer, primary_key=True)
    nome_norm = db.Column(db.String(140), unique=True, nullable=False)
    empresa_id = db.Column(
        db.Integer, db.ForeignKey('empresa.id'), nullable=True, index=True)
    documento = db.Column(db.String(18), nullable=True)
    tipo_documento = db.Column(db.String(4), nullable=True)
    criado_em = db.Column(db.DateTime, nullable=False, default=datetime.now)

    def __repr__(self):
        return f'<ApelidoNfse {self.nome_norm} -> {self.empresa_id or self.documento}>'


class SituacaoNotaEmitida:
    """Codigos de situacao do Emissor Nacional, como o portal os escreve.

    So `GERADA` foi observado na recon (185 linhas, todas iguais) — os codigos
    de cancelada e substituida sao DESCONHECIDOS. Por isso o total do mes soma
    apenas o que e comprovadamente `GERADA`, e qualquer outro codigo e contado
    a parte e mostrado; adivinhar aqui erraria o total de um documento fiscal
    nos dois sentidos possiveis."""
    GERADA = 'P100_GERADA'


class NotaEmitidaNfse(db.Model):
    """Uma NFS-e como o portal a registra — a contraparte da `NotaNfse`.

    As duas nao se confundem e por isso sao tabelas separadas: `NotaNfse` e a
    fila de trabalho montada a partir do extrato do banco ("o que eu preciso
    emitir"), esta e o espelho do que a Receita registra ("o que eu de fato
    emiti"). E o confronto entre elas que responde quem pagou e ficou sem nota,
    e que nota saiu sem pagamento correspondente.

    Chave natural: a chave de acesso de 50 digitos. Ela vem do href do
    "Visualizar" e NAO do `data-chave` da linha, que e um token opaco de uso
    interno do portal (ver recon)."""
    __tablename__ = 'nota_emitida_nfse'

    id = db.Column(db.Integer, primary_key=True)
    # 50 digitos; unica, e o que torna a consulta idempotente — reconsultar o
    # mesmo mes atualiza em vez de duplicar
    chave = db.Column(db.String(60), unique=True, nullable=False)

    # Data em que a nota foi GERADA no portal. E o fato que define "emitido no
    # mes X" — o total do mes sai daqui.
    data_geracao = db.Column(db.Date, nullable=True, index=True)

    # A "Competencia" que o portal mostra e a data de competencia do DPS, que a
    # nossa propria automacao preenche com HOJE (`preencher_etapa_pessoas`).
    # Ou seja: e o mes da EMISSAO, nao o mes de referencia do honorario.
    #
    # O nome carrega o `_dps` de proposito. Enquanto se chamava `competencia`
    # ela foi casada com `NotaNfse.competencia` — que e o mes de REFERENCIA — e
    # a conciliacao acusou como "sem nota" toda linha paga num mes e emitida no
    # seguinte, que e o caso normal (o cliente paga em julho o honorario de
    # junho). Ver ND-027.
    competencia_dps = db.Column(db.String(7), nullable=True, index=True)

    documento = db.Column(db.String(18), nullable=True, index=True)
    nome_tomador = db.Column(db.String(140), nullable=True)
    municipio = db.Column(db.String(60), nullable=True)
    valor = db.Column(db.Numeric(12, 2), nullable=True)

    # codigo cru do portal ('P100_GERADA'), nunca o rotulo traduzido: o rotulo e
    # o title de uma imagem e muda com tema/idioma, o codigo nao
    situacao = db.Column(db.String(30), nullable=True, index=True)

    # quando esta linha foi vista no portal pela ultima vez
    consultado_em = db.Column(db.DateTime, nullable=False, default=datetime.now)

    # conciliacao com a fila de trabalho, quando o par foi encontrado
    nota_id = db.Column(
        db.Integer, db.ForeignKey('nota_nfse.id'), nullable=True, index=True)

    def __repr__(self):
        return f'<NotaEmitidaNfse {self.chave} {self.competencia} {self.valor}>'


class ContratoNfse(db.Model):
    """Versão imutável da estrutura e das decisões do formulário da NFS-e."""
    __tablename__ = 'contrato_nfse'
    __table_args__ = (
        db.UniqueConstraint('versao', name='uq_contrato_nfse_versao'),
        db.UniqueConstraint('ativa_unica', name='uq_contrato_nfse_ativa'),
    )

    id = db.Column(db.Integer, primary_key=True)
    versao = db.Column(db.Integer, nullable=False)
    estado = db.Column(db.String(20), nullable=False, index=True)
    # Sentinela que faz "só uma versão ativa" ser regra DO BANCO, e não apenas
    # do serviço: vale 1 quando `estado == 'ativa'` e NULL nos demais casos.
    # NULL não colide em índice único nem no MySQL nem no SQLite, então uma
    # segunda ativa esbarra na constraint. O `with_for_update()` de `ativar()`
    # não cobria isto: o dialeto SQLite descarta `FOR UPDATE` em silêncio, e
    # SQLite é o banco padrão quando `DATABASE_URL` não está definido.
    # Quem mantém a coluna é o listener abaixo, nunca o chamador — todo write
    # de `estado` passa pelo ORM, e um write novo não pode depender de alguém
    # lembrar de atualizar duas colunas.
    ativa_unica = db.Column(db.Integer, nullable=True)
    fingerprint = db.Column(db.String(64), nullable=False, index=True)
    elegivel_automatico = db.Column(db.Boolean, nullable=False, default=False)
    criado_em = db.Column(db.DateTime, nullable=False, default=utcnow_naive)
    validado_em = db.Column(db.DateTime, nullable=True)
    ativado_em = db.Column(db.DateTime, nullable=True)
    criado_por_id = db.Column(
        db.Integer, db.ForeignKey('usuario.id', ondelete='SET NULL'),
        nullable=True, index=True)
    ativado_por_id = db.Column(
        db.Integer, db.ForeignKey('usuario.id', ondelete='SET NULL'),
        nullable=True, index=True)
    nota_validacao_id = db.Column(
        db.Integer, db.ForeignKey('nota_nfse.id', ondelete='SET NULL'),
        nullable=True, index=True)
    erro_validacao = db.Column(db.String(500), nullable=True)

    criado_por = db.relationship(
        'Usuario', foreign_keys=[criado_por_id], passive_deletes=True)
    ativado_por = db.relationship(
        'Usuario', foreign_keys=[ativado_por_id], passive_deletes=True)
    nota_validacao = db.relationship(
        'NotaNfse', foreign_keys=[nota_validacao_id], passive_deletes=True)
    campos = db.relationship(
        'CampoContratoNfse', back_populates='contrato',
        cascade='all, delete-orphan', passive_deletes=True)
    incidentes = db.relationship(
        'IncidenteContratoNfse', foreign_keys='IncidenteContratoNfse.contrato_base_id',
        back_populates='contrato_base', cascade='all, delete-orphan',
        passive_deletes=True)
    incidentes_candidata = db.relationship(
        'IncidenteContratoNfse', foreign_keys='IncidenteContratoNfse.contrato_candidato_id',
        back_populates='contrato_candidato', passive_deletes=True)

    def __repr__(self):
        return f'<ContratoNfse v{self.versao} {self.estado}>'


@event.listens_for(ContratoNfse, 'before_insert')
@event.listens_for(ContratoNfse, 'before_update')
def _sincronizar_ativa_unica(mapper, connection, alvo):
    alvo.ativa_unica = 1 if alvo.estado == 'ativa' else None


class CampoContratoNfse(db.Model):
    """Controle declarado em uma versão do contrato da NFS-e."""
    __tablename__ = 'campo_contrato_nfse'
    __table_args__ = (
        db.UniqueConstraint(
            'contrato_id', 'chave_semantica',
            name='uq_campo_contrato_nfse_chave'),
    )

    id = db.Column(db.Integer, primary_key=True)
    contrato_id = db.Column(
        db.Integer, db.ForeignKey('contrato_nfse.id', ondelete='CASCADE'),
        nullable=False, index=True)
    chave_semantica = db.Column(db.String(100), nullable=False)
    etapa = db.Column(db.String(20), nullable=False, index=True)
    seletor_tipo = db.Column(db.String(20), nullable=False)
    seletor = db.Column(db.String(200), nullable=False)
    rotulo = db.Column(db.String(500), nullable=False)
    tipo = db.Column(db.String(30), nullable=False)
    interacao = db.Column(db.String(30), nullable=False)
    obrigatorio = db.Column(db.Boolean, nullable=False, default=False)
    ordem = db.Column(db.Integer, nullable=False, default=0)
    condicao_chave = db.Column(db.String(100), nullable=True)
    condicao_valor = db.Column(db.String(190), nullable=True)
    origem = db.Column(db.String(30), nullable=True)
    fonte = db.Column(db.String(100), nullable=True)
    valor_fixo = db.Column(db.String(500), nullable=True)
    revisao_secao = db.Column(db.String(100), nullable=True)
    revisao_rotulo = db.Column(db.String(500), nullable=True)
    conferivel_automatico = db.Column(
        db.Boolean, nullable=False, default=True)

    contrato = db.relationship('ContratoNfse', back_populates='campos')
    opcoes = db.relationship(
        'OpcaoCampoContratoNfse', back_populates='campo',
        cascade='all, delete-orphan', passive_deletes=True)

    def __repr__(self):
        return f'<CampoContratoNfse {self.chave_semantica}>'


class OpcaoCampoContratoNfse(db.Model):
    """Opção declarada no HTML e aprovada no contrato."""
    __tablename__ = 'opcao_campo_contrato_nfse'
    __table_args__ = (
        db.UniqueConstraint(
            'campo_id', 'valor', name='uq_opcao_campo_contrato_nfse_valor'),
    )

    id = db.Column(db.Integer, primary_key=True)
    campo_id = db.Column(
        db.Integer, db.ForeignKey('campo_contrato_nfse.id', ondelete='CASCADE'),
        nullable=False, index=True)
    valor = db.Column(db.String(190), nullable=False)
    rotulo = db.Column(db.String(500), nullable=False)
    ordem = db.Column(db.Integer, nullable=False, default=0)

    campo = db.relationship('CampoContratoNfse', back_populates='opcoes')


class IncidenteContratoNfse(db.Model):
    """Diferença observada entre um contrato e uma etapa do portal."""
    __tablename__ = 'incidente_contrato_nfse'
    __table_args__ = (
        db.UniqueConstraint(
            'contrato_base_id', 'assinatura',
            name='uq_incidente_contrato_nfse_assinatura'),
    )

    id = db.Column(db.Integer, primary_key=True)
    contrato_base_id = db.Column(
        db.Integer, db.ForeignKey('contrato_nfse.id', ondelete='CASCADE'),
        nullable=False, index=True)
    contrato_candidato_id = db.Column(
        db.Integer, db.ForeignKey('contrato_nfse.id', ondelete='SET NULL'),
        nullable=True, index=True)
    assinatura = db.Column(db.String(64), nullable=False)
    etapa = db.Column(db.String(20), nullable=False, index=True)
    tipo = db.Column(db.String(30), nullable=False)
    severidade = db.Column(db.String(20), nullable=False)
    estado = db.Column(db.String(20), nullable=False, index=True)
    # Posicao do controle na etapa, em ordem de documento. A Central lista na
    # ordem em que o operador percorre a tela; por `id` a lista sai na ordem em
    # que a recon comparou, que nao e a ordem de ninguem.
    ordem_pagina = db.Column(db.Integer, nullable=True)
    chave_esperada = db.Column(db.String(100), nullable=True)
    chave_observada = db.Column(db.String(100), nullable=True)
    rotulo = db.Column(db.String(500), nullable=True)
    tipo_controle = db.Column(db.String(30), nullable=True)
    interacao = db.Column(db.String(30), nullable=True)
    obrigatorio = db.Column(db.Boolean, nullable=True)
    primeira_observacao_em = db.Column(db.DateTime, nullable=False)
    ultima_observacao_em = db.Column(db.DateTime, nullable=False)
    observacoes = db.Column(db.Integer, nullable=False, default=1)
    resolvido_em = db.Column(db.DateTime, nullable=True)
    resolvido_por_id = db.Column(
        db.Integer, db.ForeignKey('usuario.id', ondelete='SET NULL'),
        nullable=True, index=True)
    mensagem = db.Column(db.String(500), nullable=False)
    artefato_sanitizado = db.Column(db.String(500), nullable=True)

    contrato_base = db.relationship(
        'ContratoNfse', foreign_keys=[contrato_base_id],
        back_populates='incidentes')
    contrato_candidato = db.relationship(
        'ContratoNfse', foreign_keys=[contrato_candidato_id],
        back_populates='incidentes_candidata')
    resolvido_por = db.relationship(
        'Usuario', foreign_keys=[resolvido_por_id], passive_deletes=True)
    opcoes = db.relationship(
        'OpcaoIncidenteContratoNfse', back_populates='incidente',
        cascade='all, delete-orphan', passive_deletes=True)

    def __repr__(self):
        return f'<IncidenteContratoNfse {self.assinatura} {self.estado}>'


class OpcaoIncidenteContratoNfse(db.Model):
    """Opção observada no controle que originou um incidente."""
    __tablename__ = 'opcao_incidente_contrato_nfse'

    id = db.Column(db.Integer, primary_key=True)
    incidente_id = db.Column(
        db.Integer,
        db.ForeignKey('incidente_contrato_nfse.id', ondelete='CASCADE'),
        nullable=False, index=True)
    valor = db.Column(db.String(190), nullable=False)
    rotulo = db.Column(db.String(500), nullable=False)
    ordem = db.Column(db.Integer, nullable=False, default=0)

    incidente = db.relationship('IncidenteContratoNfse', back_populates='opcoes')


class ServicoNfse(db.Model):
    """Memoria do que um termo do extrato significa como servico.

    Mesmo desenho da `ApelidoNfse`, para o outro eixo do problema: la o sistema
    aprende que nome do banco e qual cliente, aqui aprende que abreviacao e qual
    servico ('ALT. CONTRATO' -> 'ALTERAÇÃO CONTRATUAL'). Sem essa memoria o
    operador redigitaria a mesma descricao todo mes, e o Pix do banco vem cheio
    de abreviacao improvisada.

    A chave e o termo normalizado (sem acento, caixa alta) e nao a descricao
    inteira do Pix: a descricao carrega o nome do cliente e a competencia, que
    mudam a cada linha — o que se repete e o pedaco que nomeia o servico."""
    __tablename__ = 'servico_nfse'

    id = db.Column(db.Integer, primary_key=True)
    termo_norm = db.Column(db.String(140), unique=True, nullable=False)
    descricao = db.Column(db.String(300), nullable=False)
    criado_em = db.Column(db.DateTime, nullable=False, default=datetime.now)

    def __repr__(self):
        return f'<ServicoNfse {self.termo_norm} -> {self.descricao}>'


# --- Manifestador de NF-e (MANIF-03, MANIF-16) ------------------------------

class EstadoCertificado:
    """Em que pe esta o certificado da empresa no drive de rede.

    Os seis valores nao foram imaginados: sairam do inventario real da carteira
    de empresas (`.specs/features/manifestador-nfe/recon.md`), que produziu
    exatamente estas situacoes. String, nunca db.Enum nativo (AD-016/AD-020).
    """
    # abre com a senha guardada, o CNPJ de dentro bate com o cadastro, e esta
    # dentro da validade — o unico estado que autoriza manifestar
    PRONTO = 'pronto'
    # o .pfx existe mas nenhuma senha conhecida o abre
    SENHA_PENDENTE = 'senha_pendente'
    VENCIDO = 'vencido'
    # abre, mas o titular nao e a empresa: tipicamente so ha e-CPF de socios na
    # pasta, e manifestar por e-CPF exigiria procuracao eletronica
    CNPJ_DIVERGENTE = 'cnpj_divergente'
    SEM_ARQUIVO = 'sem_arquivo'
    SEM_PASTA = 'sem_pasta'


class StatusManifestacao:
    """Ciclo de vida de uma chave na fila.

    PENDENTE -> ENVIANDO -> MANIFESTADA (terminal) | REJEITADA (reprocessavel)
                         -> INDEFINIDA (so acao humana resolve)
    DUPLICATA sai por liberacao explicita e volta para PENDENTE.
    """
    PENDENTE = 'pendente'
    ENVIANDO = 'enviando'
    # a SEFAZ registrou o evento. Terminal: manifestacao e irreversivel la.
    MANIFESTADA = 'manifestada'
    REJEITADA = 'rejeitada'
    # o evento saiu e a resposta nao chegou. NAO vira MANIFESTADA nem volta a
    # PENDENTE: os dois chutes erram em direcoes opostas — um perde um evento
    # que existe, o outro reenvia um ja protocolado (AD-027).
    INDEFINIDA = 'indefinida'
    DUPLICATA = 'duplicata'


class CertificadoEmpresa(db.Model):
    """O certificado A1 da empresa, como o DRIVE o mostra (AD-027).

    Tabela propria e 1:1 com `Empresa`, pelo mesmo motivo da `DadosReceita`
    (AD-024): `Empresa` guarda o que o escritorio cadastrou, esta guarda o que
    foi achado no `Z:` — e a divergencia entre os dois e justamente o que o
    operador precisa ver no pre-voo.

    **Nao guarda o .pfx.** Guarda o caminho e a senha cifrada; o arquivo e lido
    do drive no momento do uso. Assim nao se duplica chave privada, e a
    renovacao anual na pasta e herdada sem recadastro.
    """
    __tablename__ = 'certificado_empresa'

    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'),
                           unique=True, nullable=False)

    caminho = db.Column(db.String(500), nullable=True)
    # Fernet; NULL enquanto nenhuma senha conhecida abriu o arquivo. Nunca vai
    # para log, mensagem de erro ou resposta JSON.
    senha_cifrada = db.Column(db.String(500), nullable=True)

    # O CN tem a forma `RAZAO SOCIAL:CNPJ`. E o CNPJ dali — nao o nome do
    # arquivo, nem o da pasta, nem a razao social — que casa com a empresa: na
    # carteira real ha certificado chamado `CERTIFICADO A VALIDAR NA EMISSAO`, grafias `MARTINS & FILHOS` x `MARTINS E FILHOS`, e uma razao social
    # repetida em 5 CNPJs (AD-027).
    subject_cn = db.Column(db.String(200), nullable=True)
    issuer_cn = db.Column(db.String(200), nullable=True)
    cnpj_certificado = db.Column(db.String(14), nullable=True, index=True)
    not_after = db.Column(db.DateTime, nullable=True)

    estado = db.Column(db.String(20), nullable=False,
                       default=EstadoCertificado.SEM_ARQUIVO, index=True)
    # contexto do estado quando ele sozinho nao explica: no cnpj_divergente,
    # quais CNs foram achados
    detalhe = db.Column(db.String(500), nullable=True)
    verificado_em = db.Column(db.DateTime, nullable=False, default=datetime.now)

    def __repr__(self):
        return f'<CertificadoEmpresa {self.empresa_id} {self.estado}>'


class ChaveManifestacao(db.Model):
    """Uma NF-e a manifestar — e a fila duravel por item (AD-010).

    Nao se cria `TarefaEmissao` aqui: aquela exige `certidao_id`. Esta linha ja
    carrega status, tentativa e desfecho, entao uma segunda tabela de estado
    seria estado paralelo.

    A chave e unica no sistema inteiro porque identifica a NF-e globalmente.
    Reimportar uma existente produz DUPLICATA liberavel, nunca erro (o mesmo
    desenho da ND-004 da NFSe).
    """
    __tablename__ = 'chave_manifestacao'

    id = db.Column(db.Integer, primary_key=True)
    chave = db.Column(db.String(44), unique=True, nullable=False, index=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'),
                           nullable=False, index=True)

    # 'AAAA-MM', derivada dos digitos 3-6 da chave (AAMM = mes de emissao da
    # NF-e). `competencia_ajustada` marca quando o operador sobrescreveu.
    competencia = db.Column(db.String(7), nullable=True, index=True)
    competencia_ajustada = db.Column(db.Boolean, nullable=False, default=False)
    # digitos 7-20 da chave. E o EMITENTE, nao o destinatario — por isso a chave
    # sozinha nao diz de qual empresa da carteira ela e.
    cnpj_emitente = db.Column(db.String(14), nullable=True)
    origem = db.Column(db.String(10), nullable=True)

    status = db.Column(db.String(20), nullable=False,
                       default=StatusManifestacao.PENDENTE, index=True)
    tipo_evento = db.Column(db.String(6), nullable=True)

    # o que a SEFAZ respondeu, cru: parafrasear esconderia o codigo que o
    # operador usa para procurar o motivo
    cstat = db.Column(db.String(3), nullable=True)
    xmotivo = db.Column(db.String(255), nullable=True)
    protocolo = db.Column(db.String(20), nullable=True)
    # fechou por duplicidade de evento: a nota ja estava manifestada. E desfecho
    # de sucesso, nao falha.
    ja_existia = db.Column(db.Boolean, nullable=False, default=False)
    manifestado_em = db.Column(db.DateTime, nullable=True)

    # Reenvios CONSECUTIVOS com a MESMA rejeicao. A SEFAZ bloqueia o CNPJ por 1h
    # quando o mesmo evento volta com a mesma rejeicao mais de 20 vezes
    # (NT 2018.002, consumo indevido / cStat 656) — e continuar enviando durante
    # o bloqueio REINICIA o cronometro, com 50 bloqueios seguidos virando
    # bloqueio permanente. O contador zera quando a rejeicao muda: ai o problema
    # e outro, e a contagem antiga nao diz nada sobre ele.
    tentativas = db.Column(db.Integer, nullable=False, default=0)

    importado_em = db.Column(db.DateTime, nullable=False, default=datetime.now,
                             index=True)
    atualizado_em = db.Column(db.DateTime, nullable=False, default=datetime.now,
                              onupdate=datetime.now)
    liberado_por_id = db.Column(db.Integer, db.ForeignKey('usuario.id'),
                                nullable=True)

    def __repr__(self):
        return f'<ChaveManifestacao {self.chave} {self.status}>'


_COLUNA_POR_TIPO = {
    'Federal': 'a_vencer_dias_federal',
    'FGTS': 'a_vencer_dias_fgts',
    'Estadual': 'a_vencer_dias_estadual',
    'Municipal': 'a_vencer_dias_municipal',
    'Trabalhista': 'a_vencer_dias_trabalhista',
}


def _validar_dias(valor_raw):
    try:
        v = int(valor_raw)
    except (TypeError, ValueError):
        return None
    return v if 1 <= v <= 90 else None


def _get_config_cached():
    """Retorna ConfiguracaoSistema do cache flask.g (1 query por request)."""
    try:
        from flask import g
        if not hasattr(g, '_config_sistema'):
            try:
                g._config_sistema = db.session.get(ConfiguracaoSistema, 1)
            except Exception:
                g._config_sistema = None
        return g._config_sistema
    except RuntimeError:
        # Fora de contexto de request (ex: scripts, testes sem request)
        try:
            return db.session.get(ConfiguracaoSistema, 1)
        except Exception:
            return None


def get_a_vencer_dias(tipo=None, default=7):
    try:
        config = _get_config_cached()
    except Exception:
        return default

    if not config:
        return default

    if tipo is not None:
        chave = tipo.value if hasattr(tipo, 'value') else str(tipo)
        coluna = _COLUNA_POR_TIPO.get(chave)
        if coluna:
            valor_tipo = _validar_dias(getattr(config, coluna, None))
            if valor_tipo is not None:
                return valor_tipo

    valor = _validar_dias(config.a_vencer_dias)
    return valor if valor is not None else default
