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

        assert snapshot_service.contagem_carteira() == {
            'vencidas': 1, 'a_vencer': 1, 'pendentes': 1}


def test_valida_e_sem_data_ficam_fora_da_contagem(app, ids):
    """A contagem responde "o que pede atencao", nao "quantas certidoes existem":
    valida nao pede nada, e sem data nao e afirmacao sobre validade."""
    with app.app_context():
        emp = _empresa()
        _cert(emp, TipoCertidao.FEDERAL,
              validade=date.today() + timedelta(days=200))
        _cert(emp, TipoCertidao.FGTS, validade=None)

        assert snapshot_service.contagem_carteira() == {
            'vencidas': 0, 'a_vencer': 0, 'pendentes': 0}


def test_carteira_vazia_nao_quebra(app, ids):
    with app.app_context():
        assert snapshot_service.contagem_carteira() == {
            'vencidas': 0, 'a_vencer': 0, 'pendentes': 0}


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


def test_digest_e_tela_leem_a_mesma_funcao(app, ids):
    """A prova da extracao: o digest nao tem mais laco proprio de contagem."""
    import inspect

    from app.services import notificacoes

    assert not hasattr(notificacoes, '_contagem_carteira')
    assert 'contagem_carteira' in inspect.getsource(notificacoes.montar_digest)


def _usuario(papel=PapelUsuario.OPERADOR):
    return SimpleNamespace(papel=papel)


def _nota(status, *, grupo=None, grupo_descartado=False, grupo_confirmado=False):
    return SimpleNamespace(
        status=status,
        grupo_sugerido=grupo,
        grupo_descartado=grupo_descartado,
        grupo_confirmado=grupo_confirmado,
    )


def _configurar_fontes(monkeypatch, *, contagem=None, estados=None, itens=None,
                        notas=None, grupos=None, breakers=None):
    monkeypatch.setattr(
        visao_geral.snapshot_service,
        'contagem_carteira',
        lambda: contagem or {'vencidas': 0, 'a_vencer': 0, 'pendentes': 0},
    )
    monkeypatch.setattr(
        visao_geral.manifestador_cofre,
        'estado_da_carteira',
        lambda: estados or {},
    )
    monkeypatch.setattr(
        visao_geral.manifestador_cofre,
        'certificados_a_vencer',
        lambda: itens or [],
    )
    monkeypatch.setattr(
        visao_geral,
        'NotaNfse',
        SimpleNamespace(query=SimpleNamespace(all=lambda: notas or [])),
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
        contagem={'vencidas': 2, 'a_vencer': 1, 'pendentes': 3},
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
    )

    blocos = visao_geral.montar(_usuario())

    assert blocos['certidoes'] == {
        'vencidas': 2, 'a_vencer': 1, 'pendentes': 3, 'vazio': False}
    assert blocos['certificados'] == {
        'itens': certificados, 'inventariado': True, 'vazio': False}
    assert blocos['nfse'] == {
        'prontas': 2, 'pendentes': 2, 'grupos_pendentes': 1, 'vazio': False}
    assert blocos['fila'] == {
        'falhas': 2, 'motivo': 'Tempo esgotado', 'grupos': falhas,
        'breakers': [breaker], 'vazio': False}


def test_blocos_vazios_sao_diferentes_de_blocos_com_erro(monkeypatch):
    _configurar_fontes(monkeypatch, estados={'pronto': 1})

    blocos = visao_geral.montar(_usuario())

    assert all(bloco['vazio'] is True for bloco in blocos.values())
    assert all('erro' not in bloco for bloco in blocos.values())


def test_cofre_sem_inventario_nao_significa_zero_certificados(monkeypatch):
    _configurar_fontes(monkeypatch)

    bloco = visao_geral.montar(_usuario())['certificados']

    assert bloco == {'itens': [], 'inventariado': False, 'vazio': False}


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
                        'certificados_a_vencer', falhar)
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

    monkeypatch.setattr(
        visao_geral,
        'NotaNfse',
        SimpleNamespace(query=SimpleNamespace(all=falhar)),
    )

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
