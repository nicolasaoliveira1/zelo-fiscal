"""Provas do protocolo neutro usado pelos adaptadores de certidões."""
import inspect
from unittest.mock import MagicMock

import pytest

from app.automation import trabalhista_recon
from app.services import contrato_portal_drift
from app.services.contrato_portal_protocol import (
    AdaptadorPortal,
    ElementoInventariado,
    InventarioPortal,
)
from app.services.contrato_portal_registry import (
    AdaptadorRecon,
    RegistroAdaptadores,
)


def _adaptador(*, fluxo='municipal', alvo='alvo-sintetico', seguro=False):
    return AdaptadorRecon(
        fluxo=fluxo,
        alvo=alvo,
        nome='Portal sintético',
        chave_health='Portal sintético',
        observar=MagicMock(),
        recon_passivo_seguro=seguro,
    )


def test_tipos_do_inventario_vivem_no_protocolo_e_o_piloto_os_reexporta():
    assert ElementoInventariado.__module__ == (
        'app.services.contrato_portal_protocol')
    assert InventarioPortal.__module__ == (
        'app.services.contrato_portal_protocol')
    assert contrato_portal_drift.ElementoInventariado is ElementoInventariado
    assert trabalhista_recon.ElementoInventariado is ElementoInventariado
    assert trabalhista_recon.InventarioPortal is InventarioPortal


def test_comparador_nao_importa_automacao_de_um_portal():
    codigo = inspect.getsource(contrato_portal_drift)

    assert 'app.automation' not in codigo
    assert 'trabalhista_recon' not in codigo


def test_inventario_desconhecido_continua_fail_closed_no_tipo_generico():
    inventario = InventarioPortal.desconhecido(
        'etapa-sintetica', 'driver indisponível')

    assert inventario.conhecido is False
    assert inventario.estado == 'desconhecida'
    assert inventario.etapa == 'etapa-sintetica'
    assert inventario.motivo == 'driver indisponível'


def test_registro_indexa_por_fluxo_e_alvo_e_expõe_ausencia():
    adaptador = _adaptador(seguro=True)
    registro = RegistroAdaptadores((adaptador,))

    assert registro.todos() == (adaptador,)
    assert registro.seguros() == (adaptador,)
    assert registro.por_alvo('municipal', 'alvo-sintetico') is adaptador
    assert registro.por_alvo('fgts', 'alvo-sintetico') is None


def test_registro_recusa_dois_adaptadores_para_o_mesmo_alvo():
    primeiro = _adaptador()
    segundo = _adaptador()

    with pytest.raises(ValueError, match='Já existe adaptador'):
        RegistroAdaptadores((primeiro, segundo))


def test_adaptador_declarado_satisfaz_o_protocolo_estrutural():
    assert isinstance(_adaptador(), AdaptadorPortal)
