"""Snapshot diário das contagens de certidões por tipo × status (spec 02).

Extraído de `routes.py` para ser chamável por um job real do agendador (SCHED-07)
além do caminho lazy das páginas. Idempotente: uma foto por dia (unique
constraint `uq_snapshot_dia_tipo_status` + checagem). O cache de módulo evita
requerir o banco a cada request no mesmo dia.
"""
from datetime import date

from app import db
from app.models import Certidao, SnapshotCertidao, StatusEspecial
from app.services.execution_logger import log_event


def classificar_status_certidao(certidao, hoje):
    """Classifica uma certidão em uma das 5 categorias de status usadas nos
    relatórios/snapshot: pendentes | sem_data | vencidas | a_vencer | validas."""
    if certidao.status_especial == StatusEspecial.PENDENTE:
        return 'pendentes'
    if not certidao.data_validade:
        return 'sem_data'
    if (certidao.data_validade - hoje).days < 0:
        return 'vencidas'
    if certidao.status == 'amarelo':
        return 'a_vencer'
    return 'validas'


def contagem_carteira(hoje=None):
    """A carteira inteira classificada nos CINCO baldes de HOJE.

    Nucleo compartilhado: o digest por e-mail e a Visao Geral fazem a mesma
    pergunta, e faziam por caminhos diferentes. Um numero que diverge entre a
    tela e o e-mail nao tem como o operador saber qual dos dois acreditar.

    Devolve os cinco porque ha duas perguntas legitimas sobre a mesma contagem:
    o e-mail quer o que pede atencao (e le so as tres chaves que lhe importam),
    a tela quer TAMBEM o denominador — e um numero em destaque sem a sua escala
    nao diz se 89 e crise ou terca-feira comum. Classificar em cinco e devolver
    tres era o que obrigava quem quisesse o total a contar de novo, por fora.

    Uma consulta so, classificada em Python pela MESMA
    `classificar_status_certidao` do painel — nenhuma copia da regra em SQL.
    """
    hoje = hoje or date.today()
    contagem = {'a_vencer': 0, 'vencidas': 0, 'pendentes': 0,
                'validas': 0, 'sem_data': 0}
    for certidao in Certidao.query.all():
        contagem[classificar_status_certidao(certidao, hoje)] += 1
    return contagem


_ULTIMO_SNAPSHOT_DIA = None


def garantir_snapshot_diario():
    """Grava (uma vez por dia) a foto das contagens por tipo × status.

    Chamável tanto de forma lazy (1ª visita do dia às páginas) quanto por um job
    do agendador. Best-effort — uma falha nunca deve quebrar a página/job.
    Retorna True se o snapshot do dia existe ao final (criado agora ou antes)."""
    global _ULTIMO_SNAPSHOT_DIA
    hoje = date.today()
    if _ULTIMO_SNAPSHOT_DIA == hoje:
        return True
    try:
        if db.session.query(SnapshotCertidao.id).filter_by(data=hoje).first():
            _ULTIMO_SNAPSHOT_DIA = hoje
            return True
        contagens = {}
        for certidao in Certidao.query.all():
            chave = (certidao.tipo.value, classificar_status_certidao(certidao, hoje))
            contagens[chave] = contagens.get(chave, 0) + 1
        for (tipo_valor, status_key), qtd in contagens.items():
            db.session.add(SnapshotCertidao(
                data=hoje, tipo=tipo_valor, status=status_key, quantidade=qtd))
        db.session.commit()
        _ULTIMO_SNAPSHOT_DIA = hoje
        return True
    except Exception as e:
        db.session.rollback()
        log_event('snapshot_certidao_falhou', level='WARNING', error=str(e))
        return False
