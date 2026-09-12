"""Contrato público de LinhaEmitida após a promoção para services."""
from dataclasses import fields

from app.automation.nfse_emitidas import LinhaEmitida as LinhaLegada
from app.services.nfse_emitidas import LinhaEmitida


def test_automacao_reexporta_a_mesma_linha_do_servico():
    assert LinhaLegada is LinhaEmitida
    assert [(campo.name, campo.default) for campo in fields(LinhaEmitida)] == [
        ('chave', ''),
        ('data_geracao', None),
        ('documento', ''),
        ('nome_tomador', ''),
        ('competencia', ''),
        ('municipio', ''),
        ('valor', None),
        ('situacao', ''),
    ]
