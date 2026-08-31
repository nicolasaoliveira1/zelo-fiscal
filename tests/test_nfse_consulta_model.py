"""Persistência do intervalo de uma consulta completa do portal."""
from datetime import date, datetime

from app import db
from app.models import ConsultaEmitidaNfse


def test_consulta_emitida_guarda_intervalo_e_carimbo(app):
    with app.app_context():
        db.create_all()
        consulta = ConsultaEmitidaNfse(
            inicio=date(2026, 8, 10), fim=date(2026, 8, 20),
            consultado_em=datetime(2026, 8, 31, 15, 10))
        db.session.add(consulta)
        db.session.commit()

        salva = db.session.get(ConsultaEmitidaNfse, consulta.id)
        assert salva.inicio == date(2026, 8, 10)
        assert salva.fim == date(2026, 8, 20)
        assert salva.consultado_em == datetime(2026, 8, 31, 15, 10)

        db.session.remove()
        db.drop_all()


def test_consultas_de_intervalos_diferentes_sao_registros_distintos(app):
    with app.app_context():
        db.create_all()
        primeira = ConsultaEmitidaNfse(
            inicio=date(2026, 8, 1), fim=date(2026, 8, 31))
        segunda = ConsultaEmitidaNfse(
            inicio=date(2026, 8, 10), fim=date(2026, 8, 20))
        db.session.add_all([primeira, segunda])
        db.session.commit()

        consultas = ConsultaEmitidaNfse.query.order_by(ConsultaEmitidaNfse.id).all()
        assert [(c.inicio, c.fim) for c in consultas] == [
            (date(2026, 8, 1), date(2026, 8, 31)),
            (date(2026, 8, 10), date(2026, 8, 20)),
        ]

        db.session.remove()
        db.drop_all()
