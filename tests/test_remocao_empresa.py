"""Remoção de empresa com registros pendurados (achado em produção).

A empresa não saía pela tela e a mensagem não dizia por quê: `TarefaEmissao`
aponta para `empresa` E para `certidao`, e não tinha `relationship` em nenhum
dos dois lados. O SQLAlchemy apagava as certidões em cascata, o InnoDB barrava
na FK da fila (errno 1451) e a rota mostrava o traceback cru do driver.

NFS-e é o caso oposto e está aqui para travar a decisão: nota de honorários é
documento fiscal e NÃO se apaga por remoção de cadastro — o vínculo é que se
desfaz, e a coluna já é `nullable`.
"""
from datetime import date

from app import db
from app.models import (ApelidoNfse, Certidao, Empresa, LoteNfse, NotaNfse,
                        TarefaEmissao, TipoCertidao)


def _tarefa(empresa_id, certidao_id, status='pendente'):
    return TarefaEmissao(tipo=TipoCertidao.FGTS.value, empresa_id=empresa_id,
                         certidao_id=certidao_id, status=status)


def test_remove_empresa_com_tarefa_na_fila(client, ids, app):
    """O caso que travava: fila pendente segurava a empresa inteira."""
    with app.app_context():
        db.session.add(_tarefa(ids['empresa'], ids['fgts'], status='retry'))
        db.session.commit()

    resp = client.post(f'/empresa/{ids["empresa"]}/remover',
                       data={'confirm': '1'}, follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        assert db.session.get(Empresa, ids['empresa']) is None
        # a fila some junto: emitir certidão de empresa removida não faz sentido
        assert TarefaEmissao.query.filter_by(empresa_id=ids['empresa']).count() == 0
        assert Certidao.query.filter_by(empresa_id=ids['empresa']).count() == 0


def test_remove_certidao_leva_a_tarefa_junto(app, ids):
    """A FK da fila é dupla; a cascata pela certidão também precisa existir."""
    with app.app_context():
        db.session.add(_tarefa(ids['empresa'], ids['fgts']))
        db.session.commit()
        db.session.delete(db.session.get(Certidao, ids['fgts']))
        db.session.commit()
        assert TarefaEmissao.query.filter_by(certidao_id=ids['fgts']).count() == 0


def test_remocao_bem_sucedida_avisa_sucesso(client, ids):
    """A tela precisa dizer que deu certo — e dizer o nome de quem saiu."""
    resp = client.post(f'/empresa/{ids["empresa"]}/remover',
                       data={'confirm': '1'}, follow_redirects=True)
    corpo = resp.get_data(as_text=True)
    assert 'removida com sucesso' in corpo
    assert 'Empresa Teste' in corpo
    assert 'Erro ao remover' not in corpo


def test_remove_empresa_preserva_nota_de_honorarios(client, ids, app):
    """Documento fiscal não se apaga por remoção de cadastro: desfaz o vínculo."""
    with app.app_context():
        lote = LoteNfse(nome_arquivo='extrato.csv', total=1)
        db.session.add(lote)
        db.session.flush()
        db.session.add(NotaNfse(lote_id=lote.id, empresa_id=ids['empresa'],
                                nome_csv='NOME NO EXTRATO',
                                data_pagamento=date(2026, 1, 10)))
        db.session.commit()
        lote_id = lote.id

    client.post(f'/empresa/{ids["empresa"]}/remover', data={'confirm': '1'})

    with app.app_context():
        assert db.session.get(Empresa, ids['empresa']) is None
        notas = NotaNfse.query.filter_by(lote_id=lote_id).all()
        assert len(notas) == 1, 'a nota nao pode ser apagada junto com a empresa'
        assert notas[0].empresa_id is None, 'o vinculo e que se desfaz'
        assert notas[0].nome_csv == 'NOME NO EXTRATO'


def test_remove_empresa_preserva_apelido(client, ids, app):
    """O apelido guarda o documento e segue poupando digitação sem a empresa."""
    with app.app_context():
        db.session.add(ApelidoNfse(nome_norm='nome no extrato',
                                   empresa_id=ids['empresa'],
                                   documento='11.222.333/0001-81'))
        db.session.commit()

    client.post(f'/empresa/{ids["empresa"]}/remover', data={'confirm': '1'})

    with app.app_context():
        ap = ApelidoNfse.query.filter_by(nome_norm='nome no extrato').first()
        assert ap is not None, 'o apelido nao pode sumir com a empresa'
        assert ap.empresa_id is None
        assert ap.documento == '11.222.333/0001-81'


def test_erro_de_vinculo_nao_despeja_traceback_na_tela(client, ids, app, monkeypatch):
    """Se o banco recusar, a tela explica; o traceback vai para a auditoria.

    Antes, a rota dava `flash(f'Erro ao remover empresa: {exc}')` — a mensagem do
    driver, que expõe tabela e constraint e não diz o que fazer.
    """
    from sqlalchemy.exc import IntegrityError

    from app.routes import empresas as rota

    def recusa(*_a, **_k):
        raise IntegrityError(
            'DELETE FROM empresa', {},
            Exception("(1451, 'Cannot delete or update a parent row: a foreign key "
                      "constraint fails (`x`.`tarefa_emissao`)')"))

    monkeypatch.setattr(rota.db.session, 'commit', recusa)
    resp = client.post(f'/empresa/{ids["empresa"]}/remover',
                       data={'confirm': '1'}, follow_redirects=True)
    corpo = resp.get_data(as_text=True)

    assert 'Empresa Teste' in corpo
    assert 'registros de outras' in corpo
    assert 'Não foi possível remover' in corpo
    assert 'detalhe técnico ficou na auditoria' in corpo
    assert '1451' not in corpo, 'erro cru do driver nao vai para a tela'
    assert 'tarefa_emissao' not in corpo
