"""Leitura e montagem offline de uma DPS da NFS-e nacional.

O P2 não cria uma obrigação nova: reproduz uma NFS-e já emitida para o
ambiente restrito. Por isso a data de início da prestação sai exclusivamente
do XML histórico; a competência mensal da fila serve apenas para a descrição
legada quando ela não foi digitada pelo operador.
"""
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from app.models import StatusNotaNfse
from app.services.nfse_api_ensaio import validar_serie
from app.services.nfse_api_xml import NAMESPACE_NFSE


VERSAO_DPS = '1.01'
TP_AMBIENTE_RESTRITO = '2'
TP_EMITENTE_PRESTADOR = '1'
VERSAO_APLICATIVO = 'Zelo'
CAMINHO_SCHEMA_RESTRITO = (
    Path(__file__).resolve().parents[1]
    / 'schemas'
    / 'nfse'
    / 'restrita'
    / 'v1.01-20260727'
    / 'DPS_v1.01.xsd'
)


class NfseApiDpsError(ValueError):
    """Erro controlado de leitura ou montagem de DPS."""


class NotaHistoricaNaoEmitidaError(NfseApiDpsError):
    """A nota selecionada não é uma origem histórica emitida."""


class XmlReferenciaAusenteError(NfseApiDpsError):
    """O XML histórico não foi fornecido."""


class XmlReferenciaInvalidoError(NfseApiDpsError):
    """O XML histórico não tem a estrutura oficial esperada."""


class CampoObrigatorioAusenteError(NfseApiDpsError):
    """Um fato obrigatório não foi encontrado na referência."""


class DocumentoNaoRepresentavelError(NfseApiDpsError):
    """O documento fiscal não cabe no domínio nacional usado pelo P2."""


class IbscbsForaEscopoError(NfseApiDpsError):
    """A referência contém IBS/CBS, ainda fora do primeiro ensaio."""


class ConfiguracaoDpsInvalidaError(NfseApiDpsError):
    """A configuração atual não pode ser representada no leiaute da DPS."""


class MontagemDpsInvalidaError(NfseApiDpsError):
    """A DPS não pôde ser montada com os fatos fornecidos."""


@dataclass(frozen=True)
class ProblemaDps:
    """Falha de leiaute sem reproduzir valor fiscal ou XML na mensagem."""

    caminho: str
    mensagem: str


@dataclass(frozen=True)
class ReferenciaFiscal:
    """Fatos necessários para reproduzir uma NFS-e histórica.

    ``d_compet`` e ``valor_servico_xml`` permanecem como texto original para
    que a montagem não arredonde nem derive valores silenciosamente. O
    elemento interno é um modelo imutável por convenção e nunca aparece no
    ``repr``; ele só serve para copiar os grupos fiscais opcionais da fonte.
    """

    d_compet: str
    c_loc_emi: str
    prestador: str = field(repr=False)
    documento_tomador: str = field(repr=False)
    tipo_documento_tomador: str = field(repr=False)
    nome_tomador: str = field(repr=False)
    valor_servico: Decimal
    valor_servico_xml: str = field(repr=False)
    competencia_mensal: str | None = field(default=None, repr=False)
    descricao_servico: str | None = field(default=None, repr=False)
    descricao_referencia: str = field(default='', repr=False)
    chave_nfse: str | None = field(default=None, repr=False)
    _inf_dps: ET.Element = field(default=None, repr=False, compare=False)


def _tag(nome):
    return f'{{{NAMESPACE_NFSE}}}{nome}'


def _nome_local(elemento):
    if elemento is None or not isinstance(elemento.tag, str):
        return ''
    return elemento.tag.rsplit('}', 1)[-1]


def _filho(elemento, nome):
    if elemento is None:
        return None
    return elemento.find(_tag(nome))


def _texto(elemento, nome):
    filho = _filho(elemento, nome)
    return filho.text if filho is not None and filho.text is not None else ''


def _exigir_texto(elemento, nome, caminho):
    valor = _texto(elemento, nome)
    if not valor or not valor.strip():
        raise CampoObrigatorioAusenteError(
            f'O XML histórico não informa o campo obrigatório {caminho}.')
    return valor


def _exigir_grupo(elemento, nome, caminho):
    grupo = _filho(elemento, nome)
    if grupo is None:
        raise CampoObrigatorioAusenteError(
            f'O XML histórico não informa o grupo obrigatório {caminho}.')
    return grupo


def _parsear_xml(xml_real):
    if xml_real is None or xml_real == b'' or xml_real == '':
        raise XmlReferenciaAusenteError(
            'A nota histórica não possui XML oficial para o ensaio.')
    try:
        return ET.fromstring(xml_real)
    except (ET.ParseError, TypeError, ValueError) as exc:
        raise XmlReferenciaInvalidoError(
            'O XML oficial da nota histórica está malformado ou ilegível.') from exc


def _validar_data_completa(valor, caminho):
    if re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', valor) is None:
        raise XmlReferenciaInvalidoError(
            f'O campo {caminho} precisa ser uma data completa AAAA-MM-DD.')
    try:
        date.fromisoformat(valor)
    except ValueError as exc:
        raise XmlReferenciaInvalidoError(
            f'O campo {caminho} não contém uma data válida.') from exc
    return valor


def _documento_do_grupo(grupo, caminho):
    escolhas = []
    for nome in ('CNPJ', 'CPF', 'NIF', 'cNaoNIF'):
        filho = _filho(grupo, nome)
        if filho is not None:
            escolhas.append((nome, filho))
    if not escolhas:
        raise DocumentoNaoRepresentavelError(
            f'O grupo {caminho} não informa um documento representável.')
    if len(escolhas) > 1:
        raise DocumentoNaoRepresentavelError(
            f'O grupo {caminho} informa mais de um tipo de documento.')

    nome, filho = escolhas[0]
    valor = filho.text or ''
    tamanho = 14 if nome == 'CNPJ' else 11 if nome == 'CPF' else None
    if tamanho is None:
        raise DocumentoNaoRepresentavelError(
            f'O documento do grupo {caminho} usa a forma {nome}, fora do escopo do P2.')
    if re.fullmatch(rf'[0-9]{{{tamanho}}}', valor) is None:
        raise DocumentoNaoRepresentavelError(
            f'O {nome} do grupo {caminho} precisa ter {tamanho} algarismos.')
    return nome, valor


def _documento_da_nota(valor):
    bruto = str(valor or '').strip()
    # Aceita somente as máscaras já usadas pelo domínio; letras ou outros
    # caracteres não podem desaparecer numa "normalização".
    if not bruto or re.fullmatch(r'[0-9 .()/\-]+', bruto) is None:
        raise DocumentoNaoRepresentavelError(
            'A nota histórica não possui documento de tomador representável.')
    documento = ''.join(caractere for caractere in bruto if caractere.isdigit())
    if len(documento) not in (11, 14):
        raise DocumentoNaoRepresentavelError(
            'O documento do tomador da nota não tem 11 ou 14 algarismos.')
    return documento


def _valor_decimal(valor, caminho):
    if re.fullmatch(
            r'(?:0|0\.[0-9]{2}|[1-9][0-9]{0,14}(?:\.[0-9]{2})?)', valor) is None:
        raise XmlReferenciaInvalidoError(
            f'O campo {caminho} não está no formato monetário oficial.')
    try:
        decimal = Decimal(valor)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise XmlReferenciaInvalidoError(
            f'O campo {caminho} não contém um valor monetário válido.') from exc
    if not decimal.is_finite() or decimal < 0:
        raise XmlReferenciaInvalidoError(
            f'O campo {caminho} precisa ser finito e não negativo.')
    return decimal


def _validar_fatos_obrigatorios(inf_dps):
    """Confere os grupos que a DPS montada precisa carregar da referência."""
    prest = _exigir_grupo(inf_dps, 'prest', 'DPS/infDPS/prest')
    tipo_prestador, prestador = _documento_do_grupo(
        prest, 'DPS/infDPS/prest')
    if tipo_prestador != 'CNPJ':
        raise DocumentoNaoRepresentavelError(
            'O prestador histórico precisa ser um CNPJ para o ensaio restrito.')
    reg_trib = _exigir_grupo(
        prest, 'regTrib', 'DPS/infDPS/prest/regTrib')
    _exigir_texto(reg_trib, 'opSimpNac', 'DPS/infDPS/prest/regTrib/opSimpNac')
    _exigir_texto(reg_trib, 'regEspTrib', 'DPS/infDPS/prest/regTrib/regEspTrib')

    toma = _exigir_grupo(inf_dps, 'toma', 'DPS/infDPS/toma')
    _documento_do_grupo(toma, 'DPS/infDPS/toma')
    _exigir_texto(toma, 'xNome', 'DPS/infDPS/toma/xNome')

    serv = _exigir_grupo(inf_dps, 'serv', 'DPS/infDPS/serv')
    local = _exigir_grupo(serv, 'locPrest', 'DPS/infDPS/serv/locPrest')
    _exigir_texto(
        local, 'cLocPrestacao',
        'DPS/infDPS/serv/locPrest/cLocPrestacao')
    c_serv = _exigir_grupo(serv, 'cServ', 'DPS/infDPS/serv/cServ')
    _exigir_texto(c_serv, 'cTribNac', 'DPS/infDPS/serv/cServ/cTribNac')
    _exigir_texto(c_serv, 'xDescServ', 'DPS/infDPS/serv/cServ/xDescServ')

    valores = _exigir_grupo(inf_dps, 'valores', 'DPS/infDPS/valores')
    v_serv_prest = _exigir_grupo(
        valores, 'vServPrest', 'DPS/infDPS/valores/vServPrest')
    valor_servico_xml = _exigir_texto(
        v_serv_prest, 'vServ', 'DPS/infDPS/valores/vServPrest/vServ')
    valor_servico = _valor_decimal(
        valor_servico_xml, 'DPS/infDPS/valores/vServPrest/vServ')

    trib = _exigir_grupo(valores, 'trib', 'DPS/infDPS/valores/trib')
    trib_mun = _exigir_grupo(
        trib, 'tribMun', 'DPS/infDPS/valores/trib/tribMun')
    _exigir_texto(
        trib_mun, 'tribISSQN',
        'DPS/infDPS/valores/trib/tribMun/tribISSQN')
    _exigir_texto(
        trib_mun, 'tpRetISSQN',
        'DPS/infDPS/valores/trib/tribMun/tpRetISSQN')
    tot_trib = _exigir_grupo(
        trib, 'totTrib', 'DPS/infDPS/valores/trib/totTrib')
    if not any(_filho(tot_trib, nome) is not None for nome in (
            'vTotTrib', 'pTotTrib', 'indTotTrib', 'pTotTribSN')):
        raise CampoObrigatorioAusenteError(
            'O XML histórico não informa uma opção em '
            'DPS/infDPS/valores/trib/totTrib.')
    return prestador, toma, serv, valores, valor_servico, valor_servico_xml


def ler_referencia(nota, xml_real):
    """Lê os fatos fiscais de uma nota já emitida, sem rede ou persistência."""
    if getattr(nota, 'status', None) != StatusNotaNfse.EMITIDA:
        raise NotaHistoricaNaoEmitidaError(
            'O P2 aceita somente uma nota histórica com status emitida.')

    raiz = _parsear_xml(xml_real)
    if _nome_local(raiz) != 'NFSe' or raiz.tag != _tag('NFSe'):
        raise XmlReferenciaInvalidoError(
            'O XML histórico precisa ter a raiz oficial NFSe.')
    if any(_nome_local(elemento) == 'IBSCBS' for elemento in raiz.iter()):
        raise IbscbsForaEscopoError(
            'O primeiro ensaio restrito ainda não aceita referências com IBS/CBS.')

    inf_nfse = _exigir_grupo(raiz, 'infNFSe', 'NFSe/infNFSe')
    dps = _exigir_grupo(inf_nfse, 'DPS', 'NFSe/infNFSe/DPS')
    inf_dps = _exigir_grupo(dps, 'infDPS', 'NFSe/infNFSe/DPS/infDPS')

    d_compet = _validar_data_completa(
        _exigir_texto(inf_dps, 'dCompet', 'DPS/infDPS/dCompet'),
        'DPS/infDPS/dCompet')
    c_loc_emi = _exigir_texto(inf_dps, 'cLocEmi', 'DPS/infDPS/cLocEmi')
    if re.fullmatch(r'[0-9]{7}', c_loc_emi) is None:
        raise XmlReferenciaInvalidoError(
            'O município de emissão da DPS precisa ter 7 algarismos.')

    prestador, toma, serv, _valores, valor_servico, valor_servico_xml = (
        _validar_fatos_obrigatorios(inf_dps))
    tipo_tomador, documento_tomador = _documento_do_grupo(
        toma, 'DPS/infDPS/toma')
    documento_nota = _documento_da_nota(getattr(nota, 'documento', None))
    if documento_nota != documento_tomador:
        raise DocumentoNaoRepresentavelError(
            'O tomador do XML histórico não corresponde ao documento da nota.')

    c_serv = _filho(serv, 'cServ')
    chave_nfse = _texto(inf_nfse, 'Id') or inf_nfse.get('Id')
    return ReferenciaFiscal(
        d_compet=d_compet,
        c_loc_emi=c_loc_emi,
        prestador=prestador,
        documento_tomador=documento_tomador,
        tipo_documento_tomador=tipo_tomador,
        nome_tomador=_exigir_texto(
            toma, 'xNome', 'DPS/infDPS/toma/xNome'),
        valor_servico=valor_servico,
        valor_servico_xml=valor_servico_xml,
        competencia_mensal=getattr(nota, 'competencia', None),
        descricao_servico=getattr(nota, 'descricao_servico', None) or None,
        descricao_referencia=_exigir_texto(
            c_serv, 'xDescServ', 'DPS/infDPS/serv/cServ/xDescServ'),
        chave_nfse=chave_nfse,
        _inf_dps=inf_dps,
    )


def _atributo_config(config, nome):
    if config is None:
        raise ConfiguracaoDpsInvalidaError(
            'A configuração da NFS-e não foi fornecida.')
    valor = getattr(config, nome, None)
    if valor is None:
        return None
    texto = str(valor).strip()
    if not texto:
        raise ConfiguracaoDpsInvalidaError(
            f'O campo de configuração {nome} não pode ficar em branco.')
    return texto


def _definir_texto(elemento, nome, valor, *, indice=None):
    filho = _filho(elemento, nome)
    if filho is None:
        filho = ET.Element(_tag(nome))
        if indice is None:
            elemento.append(filho)
        else:
            elemento.insert(indice, filho)
    filho.text = valor
    return filho


def _codigo_tributacao(config):
    valor = _atributo_config(config, 'codigo_tributacao')
    if valor is None:
        return None
    if re.fullmatch(r'[0-9]{6}', valor):
        return valor
    if re.fullmatch(r'[0-9]{2}\.[0-9]{2}\.[0-9]{2}', valor):
        return valor.replace('.', '')
    raise ConfiguracaoDpsInvalidaError(
        'O código de tributação deve ter seis algarismos ou o formato '
        'AA.AA.AA.')


def _aplicar_configuracao(inf_dps, referencia, config):
    serv = _exigir_grupo(inf_dps, 'serv', 'DPS/infDPS/serv')
    local = _exigir_grupo(serv, 'locPrest', 'DPS/infDPS/serv/locPrest')
    municipio = _atributo_config(config, 'municipio_servico_codigo')
    if municipio is not None:
        if re.fullmatch(r'[0-9]{7}', municipio) is None:
            raise ConfiguracaoDpsInvalidaError(
                'O código do município do serviço deve ter 7 algarismos.')
        pais = _filho(local, 'cPaisPrestacao')
        if pais is not None:
            local.remove(pais)
        _definir_texto(local, 'cLocPrestacao', municipio)

    c_serv = _exigir_grupo(serv, 'cServ', 'DPS/infDPS/serv/cServ')
    codigo = _codigo_tributacao(config)
    if codigo is not None:
        _definir_texto(c_serv, 'cTribNac', codigo)
    nbs = _atributo_config(config, 'item_nbs')
    if nbs is not None:
        if re.fullmatch(r'[0-9]{9}', nbs) is None:
            raise ConfiguracaoDpsInvalidaError(
                'O item da NBS deve ter 9 algarismos.')
        _definir_texto(c_serv, 'cNBS', nbs)

    descricao = referencia.descricao_servico
    if not descricao:
        template = _atributo_config(config, 'descricao_template')
        if template is not None:
            if '{competencia}' not in template:
                raise ConfiguracaoDpsInvalidaError(
                    'O template da descrição precisa conter {competencia}.')
            if not referencia.competencia_mensal:
                raise ConfiguracaoDpsInvalidaError(
                    'A competência mensal é necessária para a descrição.')
            descricao = template.replace(
                '{competencia}', str(referencia.competencia_mensal))
        else:
            descricao = referencia.descricao_referencia
    if not descricao or not str(descricao).strip():
        raise CampoObrigatorioAusenteError(
            'A DPS não pode ser montada sem descrição do serviço.')
    _definir_texto(c_serv, 'xDescServ', str(descricao))

    prest = _exigir_grupo(inf_dps, 'prest', 'DPS/infDPS/prest')
    reg_trib = _exigir_grupo(
        prest, 'regTrib', 'DPS/infDPS/prest/regTrib')
    regime = _atributo_config(config, 'regime_apuracao_sn')
    if regime is not None:
        if regime not in {'1', '2', '3'}:
            raise ConfiguracaoDpsInvalidaError(
                'O regime de apuração deve ser 1, 2 ou 3.')
        if _texto(reg_trib, 'opSimpNac') == '3':
            reg_esp = _filho(reg_trib, 'regEspTrib')
            indice = list(reg_trib).index(reg_esp) if reg_esp is not None else None
            _definir_texto(reg_trib, 'regApTribSN', regime, indice=indice)

    valores = _exigir_grupo(inf_dps, 'valores', 'DPS/infDPS/valores')
    trib = _exigir_grupo(valores, 'trib', 'DPS/infDPS/valores/trib')
    situacao = _atributo_config(config, 'piscofins_situacao')
    retencao = _atributo_config(config, 'piscofins_tipo_retencao')
    if situacao is not None or retencao is not None:
        if situacao is None or retencao is None:
            raise ConfiguracaoDpsInvalidaError(
                'Situação e retenção de PIS/COFINS devem ser configuradas juntas.')
        if re.fullmatch(r'[0-9]{1,2}', situacao) is None:
            raise ConfiguracaoDpsInvalidaError(
                'A situação tributária de PIS/COFINS deve ter até 2 algarismos.')
        if re.fullmatch(r'[0-9]', retencao) is None:
            raise ConfiguracaoDpsInvalidaError(
                'A retenção de PIS/COFINS deve ser um algarismo.')
        situacao = situacao.zfill(2)
        trib_fed = _filho(trib, 'tribFed')
        if trib_fed is None:
            trib_mun = _filho(trib, 'tribMun')
            indice = list(trib).index(trib_mun) + 1
            trib_fed = ET.Element(_tag('tribFed'))
            trib.insert(indice, trib_fed)
        piscofins = _filho(trib_fed, 'piscofins')
        if piscofins is None:
            piscofins = ET.Element(_tag('piscofins'))
            trib_fed.insert(0, piscofins)
        _definir_texto(piscofins, 'CST', situacao)
        _definir_texto(piscofins, 'tpRetPisCofins', retencao)


def _formatar_dh_emi(agora):
    if agora is None:
        agora = datetime.now().astimezone()
    if not isinstance(agora, datetime) or agora.tzinfo is None \
            or agora.utcoffset() is None:
        raise MontagemDpsInvalidaError(
            'dhEmi precisa receber um datetime com fuso horário explícito.')
    # O XSD não representa frações de segundo. O instante continua injetável;
    # apenas a precisão que o leiaute não comporta é descartada.
    agora = agora.replace(microsecond=0)
    deslocamento = agora.utcoffset()
    if deslocamento.total_seconds() % 3600:
        raise MontagemDpsInvalidaError(
            'O fuso horário de dhEmi precisa usar deslocamento em horas inteiras.')
    horas = deslocamento.total_seconds() / 3600
    if horas < -11 or horas > 12:
        raise MontagemDpsInvalidaError(
            'O fuso horário de dhEmi está fora do intervalo oficial.')
    return agora.isoformat(timespec='seconds')


def _numero_dps(numero):
    if isinstance(numero, bool):
        raise MontagemDpsInvalidaError('O número da DPS precisa ser positivo.')
    texto = str(numero)
    if re.fullmatch(r'[1-9][0-9]{0,14}', texto) is None:
        raise MontagemDpsInvalidaError(
            'O número da DPS deve ter de 1 a 15 algarismos e ser positivo.')
    return texto


def _inf_dps_de(dps):
    if dps is None:
        raise MontagemDpsInvalidaError('A DPS não foi fornecida.')
    if _nome_local(dps) == 'infDPS':
        return dps
    if dps.tag != _tag('DPS'):
        raise MontagemDpsInvalidaError(
            'O elemento fornecido não é uma DPS do namespace oficial.')
    return _exigir_grupo(dps, 'infDPS', 'DPS/infDPS')


def _dps_de(dps):
    if dps is None or dps.tag != _tag('DPS'):
        raise MontagemDpsInvalidaError(
            'A validação exige o elemento raiz DPS do namespace oficial.')
    return dps


@lru_cache(maxsize=1)
def carregar_esquema_restrito():
    """Carrega somente o XSD restrito versionado no repositório."""
    try:
        import xmlschema
        return xmlschema.XMLSchema(CAMINHO_SCHEMA_RESTRITO)
    except (OSError, ImportError, ValueError) as exc:
        raise ConfiguracaoDpsInvalidaError(
            'O esquema XSD restrito versionado não pôde ser carregado.') from exc


def _caminho_legivel(caminho):
    if not caminho:
        return '/DPS'
    partes = re.findall(r'(?:^|/)(?:\{[^}]+\})?([^/{]+)', caminho)
    return '/' + '/'.join(partes)


def _mensagem_validacao(erro):
    nome_validador = type(getattr(erro, 'validator', None)).__name__
    if 'Pattern' in nome_validador:
        return 'O valor não atende ao formato oficial do leiaute.'
    if 'Enumeration' in nome_validador:
        return 'O valor não pertence às opções permitidas pelo leiaute.'
    if 'Length' in nome_validador or 'Digits' in nome_validador:
        return 'O tamanho ou a escala não atende ao formato oficial.'
    if 'Date' in nome_validador:
        return 'A data não atende ao formato oficial do leiaute.'
    return 'O campo está ausente, fora da ordem ou incompatível com o leiaute.'


def validar(dps, esquema_restrito=None):
    """Valida uma DPS com um esquema já carregado, sem resolver schema na rede.

    A lista devolvida é segura para apresentar ao operador: contém apenas o
    caminho estrutural e uma explicação genérica, nunca o XML nem o valor
    fiscal que causou a falha.
    """
    dps = _dps_de(dps)
    esquema = (
        carregar_esquema_restrito()
        if esquema_restrito is None else esquema_restrito)
    iter_erros = getattr(esquema, 'iter_errors', None)
    if iter_erros is None:
        raise ConfiguracaoDpsInvalidaError(
            'A validação exige um esquema XSD restrito carregado localmente.')
    return [
        ProblemaDps(
            caminho=_caminho_legivel(getattr(erro, 'path', None)),
            mensagem=_mensagem_validacao(erro),
        )
        for erro in iter_erros(dps)
    ]


def _identificador_dos_campos(inf_dps):
    c_loc_emi = _exigir_texto(inf_dps, 'cLocEmi', 'DPS/infDPS/cLocEmi')
    if re.fullmatch(r'[0-9]{7}', c_loc_emi) is None:
        raise MontagemDpsInvalidaError(
            'O município de emissão precisa ter 7 algarismos.')
    prest = _exigir_grupo(inf_dps, 'prest', 'DPS/infDPS/prest')
    tipo, inscricao = _documento_do_grupo(prest, 'DPS/infDPS/prest')
    if tipo == 'CPF':
        tipo_inscricao = '2'
        inscricao = '000' + inscricao
    elif tipo == 'CNPJ':
        tipo_inscricao = '1'
    else:
        raise DocumentoNaoRepresentavelError(
            'O prestador da DPS não possui documento representável.')
    serie = _exigir_texto(inf_dps, 'serie', 'DPS/infDPS/serie')
    numero = _exigir_texto(inf_dps, 'nDPS', 'DPS/infDPS/nDPS')
    if re.fullmatch(r'[0-9]{1,5}', serie) is None:
        raise MontagemDpsInvalidaError('A série da DPS não é numérica.')
    if re.fullmatch(r'[1-9][0-9]{0,14}', numero) is None:
        raise MontagemDpsInvalidaError('O número da DPS não é positivo.')
    identificador = (
        f'DPS{c_loc_emi}{tipo_inscricao}{inscricao}'
        f'{serie.zfill(5)}{numero.zfill(15)}')
    if len(identificador) != 45:
        raise MontagemDpsInvalidaError(
            'O identificador determinístico da DPS não tem 45 posições.')
    return identificador


def identificador(dps):
    """Calcula o identificador oficial de 45 posições da DPS."""
    return _identificador_dos_campos(_inf_dps_de(dps))


def montar(referencia, config, *, serie, numero, agora=None):
    """Monta uma DPS restrita, sem receber ambiente como argumento público."""
    if not isinstance(referencia, ReferenciaFiscal):
        raise MontagemDpsInvalidaError(
            'A montagem exige uma referência fiscal lida pelo serviço.')
    serie = validar_serie(serie)
    numero = _numero_dps(numero)
    dh_emi = _formatar_dh_emi(agora)

    dps = ET.Element(_tag('DPS'), {'versao': VERSAO_DPS})
    inf_dps = ET.SubElement(dps, _tag('infDPS'))
    _definir_texto(inf_dps, 'tpAmb', TP_AMBIENTE_RESTRITO)
    _definir_texto(inf_dps, 'dhEmi', dh_emi)
    _definir_texto(inf_dps, 'verAplic', VERSAO_APLICATIVO)
    _definir_texto(inf_dps, 'serie', serie)
    _definir_texto(inf_dps, 'nDPS', numero)
    _definir_texto(inf_dps, 'dCompet', referencia.d_compet)
    _definir_texto(inf_dps, 'tpEmit', TP_EMITENTE_PRESTADOR)
    _definir_texto(inf_dps, 'cLocEmi', referencia.c_loc_emi)

    elementos_tecnicos = {
        'tpAmb', 'dhEmi', 'verAplic', 'serie', 'nDPS', 'dCompet', 'tpEmit',
        'cMotivoEmisTI', 'chNFSeRej', 'cLocEmi', 'IBSCBS',
    }
    for filho in list(referencia._inf_dps):
        if _nome_local(filho) not in elementos_tecnicos:
            inf_dps.append(deepcopy(filho))

    _aplicar_configuracao(inf_dps, referencia, config)
    inf_dps.set('Id', identificador(inf_dps))
    return dps
