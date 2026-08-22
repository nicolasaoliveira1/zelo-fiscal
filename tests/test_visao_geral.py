"""Visao Geral — a pagina inicial que responde "por onde eu comeco hoje".

Comeca pela contagem da carteira (OVER-02), que virou nucleo compartilhado: o
digest por e-mail e a tela fazem a MESMA pergunta, e um numero que diverge entre
os dois nao tem como o operador saber em qual acreditar.
"""
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from app import db
from app.models import (
    Certidao,
    Empresa,
    PapelUsuario,
    StatusEspecial,
    StatusNotaNfse,
    TipoCertidao,
)
from app.services import snapshot_service, visao_geral


def _empresa(nome='EMPRESA VG'):
    emp = Empresa(nome=nome, cnpj=f'00.000.000/000{Empresa.query.count()}-00',
                  estado='RS', cidade='Tramandai')
    db.session.add(emp)
    db.session.commit()
    return emp


def _cert(emp, tipo, *, validade=None, pendente=False):
    # `status` e propriedade derivada da validade (verde/amarelo/vermelho): quem
    # decide a categoria e a DATA, nao um campo que se possa fixar no fixture.
    c = Certidao(tipo=tipo, empresa=emp, data_validade=validade,
                 status_especial=(StatusEspecial.PENDENTE if pendente else None))
    db.session.add(c)
    db.session.commit()
    return c


def test_contagem_separa_vencidas_a_vencer_e_pendentes(app, ids):
    with app.app_context():
        emp = _empresa()
        hoje = date.today()
        _cert(emp, TipoCertidao.FEDERAL, validade=hoje - timedelta(days=2))
        _cert(emp, TipoCertidao.FGTS, validade=hoje + timedelta(days=5))
        _cert(emp, TipoCertidao.ESTADUAL, pendente=True)

        # as 5 do fixture `ids` nao tem data: com tres baldes elas eram
        # invisiveis, e a contagem parecia falar da carteira inteira.
        assert snapshot_service.contagem_carteira() == {
            'vencidas': 1, 'a_vencer': 1, 'pendentes': 1,
            'validas': 0, 'sem_data': 5}


def test_valida_e_sem_data_entram_na_contagem(app, ids):
    """REVERTE a intencao anterior desta funcao, de proposito.

    Ela ja respondia so "o que pede atencao", e por isso classificava em cinco
    baldes para devolver tres. O preco apareceu na tela: um numero em destaque
    sem denominador nao diz se 89 e crise ou terca-feira comum, e quem quisesse
    o total tinha de contar de novo por fora — que e como duas contagens da
    mesma carteira comecam a divergir.

    Agora a contagem devolve os cinco e cada consumidor soma o que lhe importa:
    o e-mail le as tres de atencao (`montar_resumo`), a tela soma quatro e usa a
    soma dos cinco como denominador.
    """
    with app.app_context():
        emp = _empresa()
        _cert(emp, TipoCertidao.FEDERAL,
              validade=date.today() + timedelta(days=200))
        _cert(emp, TipoCertidao.FGTS, validade=None)

        assert snapshot_service.contagem_carteira() == {
            'vencidas': 0, 'a_vencer': 0, 'pendentes': 0,
            'validas': 1, 'sem_data': 6}  # 1 do teste + 5 do fixture


def test_carteira_vazia_nao_quebra(app, ids):
    """Carteira DE FATO vazia — o fixture semeia 5 certidoes sem data, e o nome
    deste teste so era verdadeiro porque a contagem antiga as descartava."""
    with app.app_context():
        Certidao.query.delete()
        db.session.commit()

        assert snapshot_service.contagem_carteira() == {
            'vencidas': 0, 'a_vencer': 0, 'pendentes': 0,
            'validas': 0, 'sem_data': 0}


def test_contagem_aceita_a_data_de_referencia(app, ids):
    """A data e parametro para o chamador nao precisar viajar no tempo: a mesma
    certidao e "a vencer" hoje e "vencida" depois do vencimento."""
    with app.app_context():
        emp = _empresa()
        vence = date.today() + timedelta(days=3)
        _cert(emp, TipoCertidao.FEDERAL, validade=vence)

        assert snapshot_service.contagem_carteira(hoje=date.today())['a_vencer'] == 1
        assert snapshot_service.contagem_carteira(
            hoje=vence + timedelta(days=1))['vencidas'] == 1


def test_resumo_e_tela_leem_a_mesma_funcao(app, ids):
    """A prova da extracao: o resumo nao tem mais laco proprio de contagem."""
    import inspect

    from app.services import notificacoes

    assert not hasattr(notificacoes, '_contagem_carteira')
    assert 'contagem_carteira' in inspect.getsource(notificacoes.montar_resumo)


def _usuario(papel=PapelUsuario.OPERADOR):
    return SimpleNamespace(papel=papel)


def _nota(status, *, grupo=None, grupo_descartado=False, grupo_confirmado=False):
    return SimpleNamespace(
        status=status,
        grupo_sugerido=grupo,
        grupo_descartado=grupo_descartado,
        grupo_confirmado=grupo_confirmado,
    )


def _contagem_nfse(notas):
    """Traduz as notas de fixture para o formato que `contagem_fila` devolve —
    os testes de composicao continuam falando de notas, que e o vocabulario do
    caso; a contagem em si tem teste proprio, contra banco de verdade."""
    from app.models import StatusNotaNfse
    from app.services import nfse_grupos
    return {
        'prontas': sum(n.status == StatusNotaNfse.PRONTA for n in notas),
        'pendentes': sum(n.status in (StatusNotaNfse.EMPRESA_PENDENTE,
                                      StatusNotaNfse.DESCRICAO_PENDENTE)
                         for n in notas),
        'grupos_pendentes': len({n.grupo_sugerido for n in notas
                                 if nfse_grupos.tem_proposta_pendente(n)}),
    }


def _configurar_fontes(monkeypatch, *, contagem=None, estados=None, itens=None,
                        notas=None, grupos=None, breakers=None,
                        com_vencimento=0):
    monkeypatch.setattr(
        visao_geral.snapshot_service,
        'contagem_carteira',
        lambda: contagem or {'vencidas': 0, 'a_vencer': 0, 'pendentes': 0,
                             'validas': 0, 'sem_data': 0},
    )
    monkeypatch.setattr(
        visao_geral.manifestador_cofre,
        'estado_da_carteira',
        lambda: estados or {},
    )
    monkeypatch.setattr(
        visao_geral.manifestador_cofre,
        'resumo_de_vencimento',
        lambda: {'itens': itens or [], 'com_vencimento': com_vencimento,
                 'janela_dias': 30},
    )
    # A contagem da fila de NFSe agora e uma funcao do dominio da NFSe
    # (`nfse_import.contagem_fila`), como a da carteira e do snapshot_service:
    # aqui ela e uma FONTE, patchada igual as outras.
    monkeypatch.setattr(
        visao_geral.nfse_import,
        'contagem_fila',
        lambda: _contagem_nfse(notas or []),
    )
    monkeypatch.setattr(
        visao_geral.fila_emissao,
        'agrupar_falhas',
        lambda: grupos or [],
    )
    monkeypatch.setattr(
        visao_geral.circuit_breaker,
        'abertos',
        lambda: breakers or [],
    )


def test_montar_reune_blocos_preenchidos_das_fontes_existentes(monkeypatch):
    certificados = [{'empresa_id': 3, 'causa': 'vencido'}]
    falhas = [{'total': 2, 'titulo': 'Tempo esgotado'}]
    breaker = {'alvo': 'FGTS', 'motivo': 'timeout'}
    _configurar_fontes(
        monkeypatch,
        contagem={'vencidas': 2, 'a_vencer': 1, 'pendentes': 3,
                  'validas': 40, 'sem_data': 4},
        estados={'pronto': 5},
        itens=certificados,
        notas=[
            _nota(StatusNotaNfse.PRONTA),
            _nota(StatusNotaNfse.EMPRESA_PENDENTE),
            _nota(StatusNotaNfse.DESCRICAO_PENDENTE, grupo='grupo-1'),
            _nota(StatusNotaNfse.PRONTA, grupo='grupo-1'),
        ],
        grupos=falhas,
        breakers=[breaker],
        com_vencimento=27,
    )

    blocos = visao_geral.montar(_usuario())

    assert blocos['certidoes'] == {
        'vencidas': 2, 'a_vencer': 1, 'pendentes': 3, 'validas': 40,
        'sem_data': 4, 'total': 50, 'atencao': 10, 'vazio': False}
    assert blocos['certificados'] == {
        'itens': certificados, 'inventariado': True, 'vazio': False,
        'com_vencimento': 27, 'janela_dias': 30}
    assert blocos['nfse'] == {
        'prontas': 2, 'pendentes': 2, 'grupos_pendentes': 1, 'vazio': False}
    assert blocos['fila'] == {
        'falhas': 2, 'motivo': 'Tempo esgotado', 'grupos': falhas,
        'breakers': [breaker], 'vazio': False}


def test_carteira_so_com_validas_deixa_o_cartao_vazio(monkeypatch):
    """`vazio` fala de ATENCAO, nao da existencia de certidoes.

    E a armadilha da mudanca: enquanto a contagem devolvia tres baldes,
    `not any(contagem.values())` era uma forma correta de dizer "nada pede
    atencao". Com `validas` no dict ela passa a ser falsa em toda carteira
    saudavel — e o estado vazio do cartao sumiria sem nenhum teste ficar
    vermelho, porque nenhuma fixture tinha certidao valida.
    """
    _configurar_fontes(monkeypatch, contagem={
        'vencidas': 0, 'a_vencer': 0, 'pendentes': 0,
        'validas': 1240, 'sem_data': 0})

    bloco = visao_geral.montar(_usuario())['certidoes']

    assert bloco['vazio'] is True
    assert bloco['atencao'] == 0
    assert bloco['total'] == 1240


def test_sem_data_conta_como_atencao(monkeypatch):
    """Certidao sem validade e desconhecida, e desconhecido pede conferencia —
    a mesma leitura do chip `nao_definida` na tela de Certidoes."""
    _configurar_fontes(monkeypatch, contagem={
        'vencidas': 0, 'a_vencer': 0, 'pendentes': 0,
        'validas': 3, 'sem_data': 7})

    bloco = visao_geral.montar(_usuario())['certidoes']

    assert bloco['atencao'] == 7
    assert bloco['vazio'] is False


def test_carteira_sem_nenhuma_certidao_tem_total_zero(monkeypatch):
    _configurar_fontes(monkeypatch)

    bloco = visao_geral.montar(_usuario())['certidoes']

    assert bloco['total'] == 0
    assert bloco['atencao'] == 0


def test_blocos_vazios_sao_diferentes_de_blocos_com_erro(monkeypatch):
    _configurar_fontes(monkeypatch, estados={'pronto': 1})

    blocos = visao_geral.montar(_usuario())

    assert all(bloco['vazio'] is True for bloco in blocos.values())
    assert all('erro' not in bloco for bloco in blocos.values())


def test_cofre_sem_inventario_nao_significa_zero_certificados(monkeypatch):
    _configurar_fontes(monkeypatch)

    bloco = visao_geral.montar(_usuario())['certificados']

    assert bloco == {'itens': [], 'inventariado': False, 'vazio': False,
                     'com_vencimento': 0, 'janela_dias': 30}


def test_visualizador_nao_recebe_blocos_de_operador(monkeypatch):
    _configurar_fontes(monkeypatch)

    blocos = visao_geral.montar(_usuario(PapelUsuario.LEITURA))

    assert set(blocos) == {'certidoes', 'certificados'}


def test_falha_de_uma_fonte_preserva_os_outros_blocos(monkeypatch):
    _configurar_fontes(
        monkeypatch,
        contagem={'vencidas': 1, 'a_vencer': 0, 'pendentes': 0},
        estados={'pronto': 1},
        itens=[{'empresa_id': 1}],
        notas=[_nota(StatusNotaNfse.PRONTA)],
        grupos=[{'total': 1, 'titulo': 'Timeout'}],
    )
    logger = MagicMock()
    monkeypatch.setattr(visao_geral, 'log_event', logger)

    def falhar():
        raise RuntimeError('fonte indisponivel')

    monkeypatch.setattr(visao_geral.snapshot_service, 'contagem_carteira', falhar)

    blocos = visao_geral.montar(_usuario())

    assert blocos['certidoes'] == {'erro': True, 'nome': 'certidoes'}
    assert blocos['certificados']['itens'] == [{'empresa_id': 1}]
    assert blocos['nfse']['prontas'] == 1
    assert blocos['fila']['falhas'] == 1
    logger.assert_called_once_with(
        'visao_geral_bloco_falhou',
        level='ERROR',
        bloco='certidoes',
        error='fonte indisponivel',
    )


def test_fontes_quebradas_ficam_isoladas_no_proprio_bloco(monkeypatch):
    _configurar_fontes(monkeypatch)

    def falhar():
        raise RuntimeError('indisponivel')

    monkeypatch.setattr(visao_geral.manifestador_cofre,
                        'resumo_de_vencimento', falhar)
    monkeypatch.setattr(visao_geral.fila_emissao, 'agrupar_falhas', falhar)

    blocos = visao_geral.montar(_usuario())

    assert blocos['certificados'] == {'erro': True, 'nome': 'certificados'}
    assert blocos['fila'] == {'erro': True, 'nome': 'fila'}
    assert blocos['certidoes']['vazio'] is True
    assert blocos['nfse']['vazio'] is True


def test_falha_da_fonte_nfse_fica_no_bloco_nfse(monkeypatch):
    _configurar_fontes(monkeypatch)

    def falhar():
        raise RuntimeError('consulta indisponivel')

    monkeypatch.setattr(visao_geral.nfse_import, 'contagem_fila', falhar)

    blocos = visao_geral.montar(_usuario())

    assert blocos['nfse'] == {'erro': True, 'nome': 'nfse'}
    assert blocos['certidoes']['vazio'] is True
    assert blocos['certificados']['vazio'] is False
    assert blocos['fila']['vazio'] is True


def test_falha_do_breaker_fica_no_bloco_fila(monkeypatch):
    _configurar_fontes(monkeypatch)

    def falhar():
        raise RuntimeError('breaker indisponivel')

    monkeypatch.setattr(visao_geral.circuit_breaker, 'abertos', falhar)

    blocos = visao_geral.montar(_usuario())

    assert blocos['fila'] == {'erro': True, 'nome': 'fila'}
    assert blocos['certidoes']['vazio'] is True
    assert blocos['certificados']['vazio'] is False
    assert blocos['nfse']['vazio'] is True


def test_montar_nao_inventaria_nem_verifica_rede_do_cofre(monkeypatch):
    _configurar_fontes(monkeypatch)

    def chamada_de_rede():
        raise AssertionError('nao deve tocar rede na renderizacao')

    monkeypatch.setattr(visao_geral.manifestador_cofre,
                        'rede_disponivel', chamada_de_rede)
    monkeypatch.setattr(visao_geral.manifestador_cofre,
                        'inventariar', chamada_de_rede)

    assert visao_geral.montar(_usuario())['certificados']['inventariado'] is False


# --- T3: a faixa "o que trava", derivada dos blocos -------------------------

def _blocos(**kw):
    """Blocos ja montados, em memoria — a derivacao nao consulta nada."""
    base = {
        'certidoes': {'vencidas': 0, 'a_vencer': 0, 'pendentes': 0, 'vazio': True},
        'certificados': {'itens': [], 'inventariado': True, 'vazio': True},
        'nfse': {'prontas': 0, 'pendentes': 0, 'grupos_pendentes': 0, 'vazio': True},
        'fila': {'falhas': 0, 'motivo': None, 'grupos': [], 'breakers': [],
                 'vazio': True},
    }
    base.update(kw)
    return base


def test_certificado_vencido_trava_e_vencendo_nao():
    """Vencido e parede: a manifestacao daquela empresa nao roda ate renovar.
    Vencendo e aviso — ele aparece no cartao, com os dias restantes."""
    blocos = _blocos(certificados={'inventariado': True, 'vazio': False, 'itens': [
        {'empresa_nome': 'ACME', 'causa': 'vencido'},
        {'empresa_nome': 'BETA', 'causa': 'vencendo', 'dias_restantes': 4},
    ]})

    itens = visao_geral.itens_que_travam(blocos)

    assert len(itens) == 1
    assert itens[0]['quantidade'] == 1
    assert itens[0]['nomes'] == ['ACME']


def test_breaker_aberto_trava():
    blocos = _blocos(fila={'falhas': 0, 'breakers': [{'alvo': 'FGTS'}], 'vazio': False})

    itens = visao_geral.itens_que_travam(blocos)

    assert len(itens) == 1
    assert itens[0]['nomes'] == ['FGTS']


def test_grupo_aguardando_confirmacao_trava():
    blocos = _blocos(nfse={'prontas': 0, 'pendentes': 0, 'grupos_pendentes': 2,
                           'vazio': False})

    itens = visao_geral.itens_que_travam(blocos)

    assert len(itens) == 1
    assert itens[0]['quantidade'] == 2
    assert 'Grupos de notas' in itens[0]['titulo']


def test_trabalho_pendente_nao_e_trava():
    """Certidao vencida, nota a emitir e tarefa em falha sao TRABALHO — e
    trabalho e o que a tela toda ja mostra. Se tudo virasse trava, a faixa
    perderia a unica funcao que tem: dizer que num dia calmo nao ha nada."""
    blocos = _blocos(
        certidoes={'vencidas': 18, 'a_vencer': 42, 'pendentes': 7, 'vazio': False},
        nfse={'prontas': 12, 'pendentes': 4, 'grupos_pendentes': 0, 'vazio': False},
        fila={'falhas': 5, 'motivo': 'CAPTCHA', 'breakers': [], 'vazio': False},
    )

    assert visao_geral.itens_que_travam(blocos) == []


def test_dia_calmo_nao_tem_trava():
    assert visao_geral.itens_que_travam(_blocos()) == []


def test_bloco_com_erro_nao_gera_trava_nem_quebra():
    """Nao saber se ha trava e diferente de nao haver trava — quem diz isso e o
    proprio bloco, na sua area da tela."""
    blocos = _blocos(certificados={'erro': True, 'nome': 'certificados'},
                     fila={'erro': True, 'nome': 'fila'},
                     nfse={'erro': True, 'nome': 'nfse'})

    assert visao_geral.itens_que_travam(blocos) == []


def test_travas_de_frentes_diferentes_somam_na_mesma_faixa():
    blocos = _blocos(
        certificados={'inventariado': True, 'vazio': False,
                      'itens': [{'empresa_nome': 'ACME', 'causa': 'vencido'}]},
        fila={'falhas': 0, 'breakers': [{'alvo': 'FGTS'}], 'vazio': False},
        nfse={'prontas': 0, 'pendentes': 0, 'grupos_pendentes': 1, 'vazio': False},
    )

    itens = visao_geral.itens_que_travam(blocos)

    assert len(itens) == 3
    assert {i['destino'] for i in itens} == {
        'main.manifestador_painel', 'main.diagnostico', 'main.nfse_painel'}


def test_derivacao_nao_consulta_banco(monkeypatch):
    """Sem app context nenhum: se a funcao tocasse o banco, estouraria aqui."""
    blocos = _blocos(certificados={'inventariado': True, 'vazio': False,
                                   'itens': [{'empresa_nome': 'X', 'causa': 'vencido'}]})

    assert len(visao_geral.itens_que_travam(blocos)) == 1


def test_dez_certificados_vencidos_sao_UMA_linha_da_faixa():
    """Dez vencidos são um problema com dez casos, não dez problemas: mesma
    ação (renovar), mesmo destino, uma decisão só. Uma linha por caso fazia a
    faixa tomar a tela e empurrar para fora os cartões que dizem o que fazer."""
    blocos = _blocos(certificados={'inventariado': True, 'vazio': False, 'itens': [
        {'empresa_nome': f'EMPRESA {i}', 'causa': 'vencido'} for i in range(10)
    ]})

    itens = visao_geral.itens_que_travam(blocos)

    assert len(itens) == 1
    assert itens[0]['quantidade'] == 10
    assert visao_geral.total_de_travas(itens) == 10


def test_faixa_cita_alguns_nomes_e_resume_o_resto():
    """Com 30 casos a faixa continua com uma linha: cita os primeiros e diz
    quantos sobraram. Quem precisa da lista inteira clica e vai para a tela."""
    blocos = _blocos(certificados={'inventariado': True, 'vazio': False, 'itens': [
        {'empresa_nome': f'EMPRESA {i}', 'causa': 'vencido'} for i in range(30)
    ]})

    grupo = visao_geral.itens_que_travam(blocos)[0]

    assert grupo['quantidade'] == 30
    assert len(grupo['nomes']) == 8
    assert grupo['restantes'] == 22


def test_total_da_faixa_e_a_soma_do_que_esta_listado():
    """O número do cabeçalho e os números das linhas não podem discordar: um é
    a soma dos outros, por construção."""
    blocos = _blocos(
        certificados={'inventariado': True, 'vazio': False, 'itens': [
            {'empresa_nome': 'A', 'causa': 'vencido'},
            {'empresa_nome': 'B', 'causa': 'vencido'}]},
        fila={'falhas': 0, 'breakers': [{'alvo': 'FGTS'}], 'vazio': False},
        nfse={'prontas': 0, 'pendentes': 0, 'grupos_pendentes': 3, 'vazio': False},
    )

    grupos = visao_geral.itens_que_travam(blocos)

    assert len(grupos) == 3                      # três tipos de problema
    assert visao_geral.total_de_travas(grupos) == 6   # 2 + 1 + 3 casos
    assert sum(g['quantidade'] for g in grupos) == visao_geral.total_de_travas(grupos)


def test_um_caso_so_fala_no_singular():
    blocos = _blocos(certificados={'inventariado': True, 'vazio': False, 'itens': [
        {'empresa_nome': 'ACME', 'causa': 'vencido'}]})

    grupo = visao_geral.itens_que_travam(blocos)[0]

    assert grupo['titulo'] == 'Certificado A1 vencido'
    assert 'dessa empresa' in grupo['detalhe']


def test_total_de_travas_com_lista_vazia():
    assert visao_geral.total_de_travas([]) == 0
    assert visao_geral.total_de_travas(None) == 0
