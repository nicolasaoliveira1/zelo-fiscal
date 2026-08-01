"""Configuracao dos campos fixos da NFSe (NFSE-08/09).

Os defaults nao sao arbitrarios: cada um foi lido do Emissor Nacional durante a
recon (T0). Se algum mudar sem que o portal tenha mudado, a nota sai com dado
errado — por isso os valores estao fixados em teste.
"""
import pytest

from app import db
from app.models import ConfiguracaoNfse
from app.services import nfse_config as cfg


@pytest.fixture()
def banco(app):
    """Schema limpo por teste (o `app` do conftest e session-scoped e nao cria
    schema). `db.session.remove()` antes do `drop_all()` por causa do MySQL
    (AD-020)."""
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


# --- criacao preguicosa e defaults da recon --------------------------------

def test_cria_o_registro_unico_quando_nao_existe(banco):
    assert ConfiguracaoNfse.query.count() == 0
    config = cfg.get_config_nfse()
    assert config.id == 1
    assert ConfiguracaoNfse.query.count() == 1


def test_chamadas_repetidas_nao_duplicam_o_registro(banco):
    cfg.get_config_nfse()
    cfg.get_config_nfse()
    assert ConfiguracaoNfse.query.count() == 1


def test_defaults_sao_os_valores_lidos_do_portal_na_recon(banco):
    config = cfg.get_config_nfse()
    assert config.regime_apuracao_sn == '1'
    assert config.municipio_servico_codigo == '4310330'
    assert config.municipio_servico_nome == 'Imbé/RS'
    assert config.codigo_tributacao == '17.19.01'
    assert config.item_nbs == '113022100'
    assert config.piscofins_situacao == '0'
    assert config.piscofins_tipo_retencao == '0'
    assert config.descricao_template == (
        'HONORÁRIOS PROFISSIONAIS REFERENTES AO MÊS DE {competencia}')


def test_configuracao_nao_guarda_modo_de_emissao(banco):
    """O modo (assistido individual, assistido em lote, automatico) e escolhido
    ao iniciar a fila, na pagina. Persistir tambem um flag daria dois controles
    para a mesma coisa — e o operador nao saberia qual venceu."""
    assert not hasattr(cfg.get_config_nfse(), 'emissao_automatica')


# --- validacao do template (NFSE-09) ---------------------------------------

def test_template_sem_placeholder_e_recusado():
    with pytest.raises(cfg.ConfiguracaoInvalidaError) as exc:
        cfg.validar({'descricao_template': 'HONORARIOS PROFISSIONAIS'})
    assert exc.value.campo == 'descricao_template'
    assert '{competencia}' in exc.value.mensagem


def test_mensagem_do_template_explica_a_consequencia():
    with pytest.raises(cfg.ConfiguracaoInvalidaError) as exc:
        cfg.validar({'descricao_template': 'SEM PLACEHOLDER'})
    assert 'mesma descricao' in exc.value.mensagem.lower()


def test_template_com_placeholder_passa():
    cfg.validar({'descricao_template': 'HONORARIOS DE {competencia}'})


# --- campos obrigatorios ---------------------------------------------------

@pytest.mark.parametrize('campo', sorted(set(cfg.CAMPOS_OBRIGATORIOS) - {'descricao_template'}))
def test_campo_obrigatorio_vazio_e_recusado_com_o_rotulo(campo):
    with pytest.raises(cfg.ConfiguracaoInvalidaError) as exc:
        cfg.validar({campo: '   '})
    assert exc.value.campo == campo
    assert cfg.CAMPOS_OBRIGATORIOS[campo] in exc.value.mensagem


def test_campo_ausente_do_formulario_nao_e_validado():
    # salvamento parcial nao pode exigir campo que o formulario nem enviou
    cfg.validar({'codigo_tributacao': '17.19.01'})


# --- salvar ----------------------------------------------------------------

def test_salvar_grava_os_campos_enviados(banco):
    cfg.salvar({'codigo_tributacao': '17.19.02', 'item_nbs': '113022200'})
    config = cfg.get_config_nfse()
    assert config.codigo_tributacao == '17.19.02'
    assert config.item_nbs == '113022200'


def test_salvar_invalido_nao_grava_nada(banco):
    cfg.get_config_nfse()
    with pytest.raises(cfg.ConfiguracaoInvalidaError):
        cfg.salvar({'codigo_tributacao': '17.19.99', 'descricao_template': 'SEM'})
    assert cfg.get_config_nfse().codigo_tributacao == '17.19.01'


def test_salvar_faz_strip_dos_valores(banco):
    cfg.salvar({'codigo_tributacao': '  17.19.03  '})
    assert cfg.get_config_nfse().codigo_tributacao == '17.19.03'


# --- renderizacao ----------------------------------------------------------

def test_renderiza_a_descricao_com_a_competencia_da_nota(banco):
    config = cfg.get_config_nfse()
    assert cfg.renderizar_descricao(config, '06/2026') == (
        'HONORÁRIOS PROFISSIONAIS REFERENTES AO MÊS DE 06/2026')


def test_renderizar_sem_competencia_levanta(banco):
    with pytest.raises(ValueError):
        cfg.renderizar_descricao(cfg.get_config_nfse(), '')
