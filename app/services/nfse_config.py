"""Configuracao dos campos fixos da NFSe (NFSE-08/09).

Registro unico (id=1) no padrao de `ConfiguracaoSistema`. Todos os defaults
vieram da recon do Emissor Nacional (ver `recon.md` da feature), nao de
suposicao: sao os valores que o portal realmente aceita nos selects.

O template da descricao e o unico campo com regra propria — precisa conter o
placeholder `{competencia}`, senao toda nota sairia com a mesma descricao e o
mes de referencia se perderia.
"""
from app import db
from app.models import ConfiguracaoNfse

PLACEHOLDER_COMPETENCIA = '{competencia}'

# Rotulos para a mensagem de erro apontar o campo pelo nome que o operador ve.
CAMPOS_OBRIGATORIOS = {
    'regime_apuracao_sn': 'Regime de apuração dos tributos no Simples Nacional',
    'municipio_servico_codigo': 'Código do município do serviço',
    'municipio_servico_nome': 'Município do serviço',
    'codigo_tributacao': 'Código de tributação nacional',
    'item_nbs': 'Item da NBS',
    'descricao_template': 'Descrição do serviço',
    'piscofins_situacao': 'Situação tributária do PIS/COFINS',
    'piscofins_tipo_retencao': 'Tipo de retenção do PIS/COFINS/CSLL',
    'categoria_extrato': 'Categoria dos recebimentos no extrato',
}


class ConfiguracaoInvalidaError(ValueError):
    """Configuracao recusada: campo obrigatorio vazio ou template sem o
    placeholder. Carrega o nome do campo para a UI destacar."""

    def __init__(self, mensagem, campo=None):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.campo = campo


def get_config_nfse():
    """Devolve o registro unico, criando-o com os defaults da recon se faltar.

    Espelha o padrao de `ConfiguracaoSistema`: a aplicacao nunca fica sem
    configuracao, e o operador ajusta pela tela o que mudar."""
    config = db.session.get(ConfiguracaoNfse, 1)
    if config is None:
        config = ConfiguracaoNfse(id=1)
        db.session.add(config)
        db.session.commit()
    return config


def validar(valores):
    """Valida um dicionario de campos vindos do formulario.

    Levanta `ConfiguracaoInvalidaError` no primeiro problema, com o rotulo do
    campo — mensagem acionavel em vez de 'dados invalidos'."""
    for campo, rotulo in CAMPOS_OBRIGATORIOS.items():
        if campo not in valores:
            continue
        if not str(valores.get(campo) or '').strip():
            raise ConfiguracaoInvalidaError(
                f'O campo "{rotulo}" nao pode ficar em branco.', campo=campo)

    template = valores.get('descricao_template')
    if template is not None and PLACEHOLDER_COMPETENCIA not in template:
        raise ConfiguracaoInvalidaError(
            'A descrição do serviço precisa conter '
            f'{PLACEHOLDER_COMPETENCIA}, que e substituido pelo mes de '
            'referencia de cada nota (ex.: 06/2026). Sem ele todas as notas '
            'sairiam com a mesma descricao.',
            campo='descricao_template')


def salvar(valores):
    """Valida e grava. Nada e escrito se a validacao recusar."""
    validar(valores)
    config = get_config_nfse()
    for campo in CAMPOS_OBRIGATORIOS:
        if campo in valores:
            setattr(config, campo, str(valores[campo]).strip())
    db.session.commit()
    return config


def renderizar_descricao(config, competencia):
    """Descricao de HONORARIOS, com a competencia daquela nota (MM/AAAA)."""
    if not competencia:
        raise ValueError('competencia obrigatoria para renderizar a descricao')
    return config.descricao_template.replace(PLACEHOLDER_COMPETENCIA, competencia)


def descricao_da_nota(config, nota):
    """Descricao que vai para o portal — as duas origens, num lugar so.

    Nota de servico avulso ('ALTERAÇÃO CONTRATUAL', 'BAIXA DE EMPRESA') traz o
    texto pronto em `descricao_servico` e NAO recebe competencia: a competencia
    gravada nela existe para agrupar e filtrar a lista, nao para descrever o
    servico — dizer "alteração contratual referente ao mês de 07/2026" seria
    falso, a alteracao nao e mensal.

    Honorarios (`descricao_servico` nulo, o caso comum e o unico que existia
    antes do extrato do Inter) segue vindo do template com a competencia.
    """
    if nota.descricao_servico:
        return nota.descricao_servico
    return renderizar_descricao(config, nota.competencia)
