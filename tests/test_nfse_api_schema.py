"""Contrato offline dos schemas restritos oficiais da NFS-e."""
from pathlib import Path
from urllib.parse import urlparse

import xmlschema


SCHEMA_DIR = (
    Path(__file__).parents[1]
    / 'app'
    / 'schemas'
    / 'nfse'
    / 'restrita'
    / 'v1.01-20260727'
)


def test_schema_dps_restrito_carrega_imports_e_includes_offline():
    schema = xmlschema.XMLSchema(SCHEMA_DIR / 'DPS_v1.01.xsd')

    assert schema.target_namespace == 'http://www.sped.fazenda.gov.br/nfse'
    assert 'tiposComplexos_v1.01.xsd' in schema.includes
    assert any(
        Path(urlparse(localizacao).path).name == 'xmldsig-core-schema.xsd'
        for localizacao in schema.imports
    )
    assert len(schema.maps.elements) > 0
