"""Persistência e snapshot imutável do contrato adaptativo da NFS-e."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app import db
from app.models import (
    CampoContratoNfse,
    ContratoNfse,
    IncidenteContratoNfse,
    OpcaoCampoContratoNfse,
    OpcaoIncidenteContratoNfse,
)
from app.services import auditoria
from app.services.execution_logger import log_event
from app.services.nfse_drift import Diferenca, assinar_incidente
from app.utils import utcnow_naive


class ContratoNfseError(RuntimeError):
    """Erro seguro da camada de contrato."""


class ContratoNfseNaoEncontradoError(ContratoNfseError):
    """A versão solicitada não existe ou não está disponível."""


class CampoContratoDesconhecidoError(ContratoNfseError):
    """O snapshot não possui a chave solicitada pelo executor."""


class PersistenciaContratoError(ContratoNfseError):
    """Falha que exige bloqueio conservador do fluxo."""


class ConfiguracaoContratoInvalidaError(ValueError):
    """Dados do operador não pertencem ao catálogo seguro do contrato."""


class ContratoNfseNaoElegivelError(ContratoNfseError):
    """A versão não pode conduzir o modo automático."""


def _campo(
    chave_semantica,
    etapa,
    seletor_tipo,
    seletor,
    rotulo,
    tipo,
    interacao,
    *,
    obrigatorio=False,
    origem=None,
    fonte=None,
    valor_fixo=None,
    opcoes=(),
    revisao_secao=None,
    revisao_rotulo=None,
    conferivel_automatico=True,
):
    return {
        "chave_semantica": chave_semantica,
        "etapa": etapa,
        "seletor_tipo": seletor_tipo,
        "seletor": seletor,
        "rotulo": rotulo,
        "tipo": tipo,
        "interacao": interacao,
        "obrigatorio": obrigatorio,
        "ordem": 0,
        "condicao_chave": None,
        "condicao_valor": None,
        "origem": origem,
        "fonte": fonte,
        "valor_fixo": valor_fixo,
        "revisao_secao": revisao_secao,
        "revisao_rotulo": revisao_rotulo,
        "conferivel_automatico": conferivel_automatico,
        "opcoes": tuple(opcoes),
    }


def _opcao(valor, rotulo, ordem):
    return {"valor": valor, "rotulo": rotulo, "ordem": ordem}


# A versão inicial reproduz somente os campos e adaptadores que já existem
# no fluxo atual. Valores de opções são declarações do formulário, nunca
# seleção ou conteúdo da nota.
CONTRATO_INICIAL = (
    _campo(
        "DataCompetencia", "pessoas", "id", "DataCompetencia",
        "Data de competência", "text", "texto", obrigatorio=True,
        origem="derivado", fonte="data_emissao",
    ),
    _campo(
        "SimplesNacional_RegimeApuracaoTributosSN", "pessoas", "id",
        "SimplesNacional_RegimeApuracaoTributosSN",
        "Regime de apuração dos tributos no Simples Nacional", "select",
        "chosen", obrigatorio=True, origem="configuracao",
        fonte="regime_apuracao_sn",
    ),
    _campo(
        "Tomador.LocalDomicilio", "pessoas", "name", "Tomador.LocalDomicilio",
        "Domicílio do tomador", "radio", "radio", obrigatorio=True,
        origem="fixo", valor_fixo="1",
        opcoes=(
            _opcao("0", "Não informado", 0),
            _opcao("1", "Brasil", 1),
            _opcao("2", "Exterior", 2),
        ),
    ),
    _campo(
        "Tomador_Inscricao", "pessoas", "id", "Tomador_Inscricao",
        "CPF/CNPJ do tomador", "text", "texto", obrigatorio=True,
        origem="nota", fonte="documento",
    ),
    _campo(
        "Prestador_Inscricao", "pessoas", "id", "Prestador_Inscricao",
        "Inscrição do prestador", "text", "intocavel", origem="intocavel",
    ),
    _campo(
        "Tomador_Nome", "pessoas", "id", "Tomador_Nome", "Nome do tomador",
        "text", "intocavel", origem="intocavel",
    ),
    _campo(
        "LocalPrestacao_CodigoMunicipioPrestacao", "servico", "id",
        "LocalPrestacao_CodigoMunicipioPrestacao", "Município do serviço",
        "select", "select_busca", obrigatorio=True, origem="configuracao",
        fonte="municipio_servico_codigo",
    ),
    _campo(
        "LocalPrestacao_CodigoPaisPrestacao", "servico", "id",
        "LocalPrestacao_CodigoPaisPrestacao", "País do serviço", "select",
        "intocavel", origem="intocavel",
    ),
    _campo(
        "ServicoPrestado_CodigoTributacaoNacional", "servico", "id",
        "ServicoPrestado_CodigoTributacaoNacional", "Código de tributação nacional",
        "select", "select_busca", obrigatorio=True, origem="configuracao",
        fonte="codigo_tributacao",
    ),
    _campo(
        "ServicoPrestado.HaExportacaoImunidadeNaoIncidencia", "servico", "name",
        "ServicoPrestado.HaExportacaoImunidadeNaoIncidencia",
        "Exportação, imunidade ou não incidência", "radio", "radio",
        obrigatorio=True, origem="fixo", valor_fixo="0",
        opcoes=(_opcao("0", "Não", 0), _opcao("1", "Sim", 1)),
    ),
    _campo(
        "ServicoPrestado_Descricao", "servico", "id",
        "ServicoPrestado_Descricao", "Descrição do serviço", "textarea",
        "textarea", obrigatorio=True, origem="nota", fonte="descricao",
    ),
    _campo(
        "ServicoPrestado_CodigoNBS", "servico", "id",
        "ServicoPrestado_CodigoNBS", "Item da NBS", "select", "chosen",
        obrigatorio=True, origem="configuracao", fonte="item_nbs",
    ),
    _campo(
        "Valores_ValorServico", "tributacao", "id", "Valores_ValorServico",
        "Valor do serviço", "text", "texto", obrigatorio=True,
        origem="nota", fonte="valor_final",
    ),
    _campo(
        "ISSQN.HaRetencao", "tributacao", "name", "ISSQN.HaRetencao",
        "Retenção do ISSQN", "radio", "radio", obrigatorio=True,
        origem="fixo", valor_fixo="0",
        opcoes=(_opcao("0", "Não", 0), _opcao("1", "Sim", 1)),
    ),
    _campo(
        "TributacaoFederal_PISCofins_SituacaoTributaria", "tributacao", "id",
        "TributacaoFederal_PISCofins_SituacaoTributaria",
        "Situação tributária do PIS/COFINS", "select", "chosen",
        obrigatorio=True, origem="configuracao", fonte="piscofins_situacao",
    ),
    _campo(
        "TributacaoFederal_PISCofins_TipoRetencao", "tributacao", "id",
        "TributacaoFederal_PISCofins_TipoRetencao",
        "Tipo de retenção do PIS/COFINS/CSLL", "select", "chosen",
        obrigatorio=True, origem="configuracao", fonte="piscofins_tipo_retencao",
    ),
    _campo(
        "ISSQN.HaSuspensao", "tributacao", "name", "ISSQN.HaSuspensao",
        "Suspensão do ISSQN", "radio", "intocavel", origem="intocavel",
    ),
    _campo(
        "ISSQN.HaBeneficioMunicipal", "tributacao", "name",
        "ISSQN.HaBeneficioMunicipal", "Benefício municipal", "radio",
        "intocavel", origem="intocavel",
    ),
    _campo(
        "ISSQN_TributacaoISSQN", "tributacao", "id", "ISSQN_TributacaoISSQN",
        "Tributação do ISSQN", "select", "intocavel", origem="intocavel",
    ),
    _campo(
        "ISSQN_RegimeEspecial", "tributacao", "id", "ISSQN_RegimeEspecial",
        "Regime especial", "select", "intocavel", origem="intocavel",
    ),
    _campo(
        "ValorTributos.TipoValorTributos", "tributacao", "name",
        "ValorTributos.TipoValorTributos", "Tipo do valor dos tributos",
        "radio", "intocavel", origem="intocavel",
    ),
    _campo(
        "ISSQN_BaseDeCalculo", "tributacao", "id", "ISSQN_BaseDeCalculo",
        "Base de cálculo do ISSQN", "text", "intocavel", origem="intocavel",
    ),
    _campo(
        "ISSQN_Valor", "tributacao", "id", "ISSQN_Valor", "Valor do ISSQN",
        "text", "intocavel", origem="intocavel",
    ),
    _campo(
        "ISSQN_Aliquota", "tributacao", "id", "ISSQN_Aliquota",
        "Alíquota do ISSQN", "text", "intocavel", origem="intocavel",
    ),
    _campo(
        "revisao.tomador.documento", "revisao", "css",
        ".emissao-titulo + .emissao-conteudo", "CPF/CNPJ do tomador",
        "revisao", "revisao", obrigatorio=True, origem="nota", fonte="documento",
        revisao_secao="Tomador do Serviço", revisao_rotulo="CPF/CNPJ",
    ),
    _campo(
        "revisao.valores.valor_servico", "revisao", "css",
        ".emissao-titulo + .emissao-conteudo", "Valor do serviço", "revisao",
        "revisao", obrigatorio=True, origem="nota", fonte="valor_final",
        revisao_secao="Valores do Serviço Prestado", revisao_rotulo="Valor do serviço",
    ),
    _campo(
        "revisao.servico.descricao", "revisao", "css",
        ".emissao-titulo + .emissao-conteudo", "Descrição do serviço", "revisao",
        "revisao", obrigatorio=True, origem="nota", fonte="descricao",
        revisao_secao="Serviço Prestado", revisao_rotulo="Descrição do serviço",
    ),
)

# A ordem é de aplicação e não participa da comparação semântica; ainda assim
# o snapshot precisa oferecer uma sequência estável ao executor.
CONTRATO_INICIAL = tuple(
    {**dados, "ordem": ordem}
    for ordem, dados in enumerate(CONTRATO_INICIAL)
)


@dataclass(frozen=True)
class OpcaoExecucaoNfse:
    valor: str
    rotulo: str
    ordem: int


@dataclass(frozen=True)
class CampoExecucaoNfse:
    chave_semantica: str
    etapa: str
    seletor_tipo: str
    seletor: str
    rotulo: str
    tipo: str
    interacao: str
    obrigatorio: bool
    ordem: int
    condicao_chave: str | None
    condicao_valor: str | None
    origem: str | None
    fonte: str | None
    valor_fixo: str | None
    revisao_secao: str | None
    revisao_rotulo: str | None
    conferivel_automatico: bool
    opcoes: tuple[OpcaoExecucaoNfse, ...]


@dataclass(frozen=True)
class ContratoExecucaoNfse:
    contrato_id: int
    versao: int
    estado: str
    fingerprint: str
    elegivel_automatico: bool
    campos: tuple[CampoExecucaoNfse, ...]

    def campo(self, chave: str) -> CampoExecucaoNfse:
        for campo in self.campos:
            if campo.chave_semantica == chave:
                return campo
        raise CampoContratoDesconhecidoError(
            "o contrato de execução não possui o campo solicitado"
        )


def _forma_inicial() -> list[dict[str, Any]]:
    resultado = []
    for campo in CONTRATO_INICIAL:
        resultado.append(
            {
                chave: valor
                for chave, valor in campo.items()
                if chave != "opcoes"
            }
            | {
                "opcoes": tuple(
                    (opcao["valor"], opcao["rotulo"])
                    for opcao in sorted(campo["opcoes"], key=lambda item: item["ordem"])
                )
            }
        )
    return resultado


def _fingerprint_inicial() -> str:
    forma = json.dumps(
        _forma_inicial(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(forma.encode("utf-8")).hexdigest()


def _contrato_inicial(criado_por_id=None, agora=None) -> ContratoNfse:
    agora = agora or utcnow_naive()
    contrato = ContratoNfse(
        versao=1,
        estado="ativa",
        fingerprint=_fingerprint_inicial(),
        elegivel_automatico=True,
        criado_em=agora,
        criado_por_id=criado_por_id,
    )
    for dados in CONTRATO_INICIAL:
        campo = CampoContratoNfse(
            **{
                chave: valor
                for chave, valor in dados.items()
                if chave != "opcoes"
            }
        )
        campo.opcoes.extend(
            OpcaoCampoContratoNfse(
                valor=opcao["valor"], rotulo=opcao["rotulo"], ordem=opcao["ordem"]
            )
            for opcao in dados["opcoes"]
        )
        contrato.campos.append(campo)
    return contrato


def _persistencia_falhou(operacao: str, exc: Exception):
    try:
        db.session.rollback()
    finally:
        log_event(
            "nfse_contrato_persistencia_falhou",
            level="ERROR",
            operacao=operacao,
            error_type=type(exc).__name__,
        )
    raise PersistenciaContratoError(
        "não foi possível persistir o contrato da NFS-e; a execução deve ser bloqueada"
    ) from exc


def garantir_contrato_inicial(criado_por_id=None):
    """Cria a versão inicial uma única vez, sem acessar o portal."""

    existente = ContratoNfse.query.order_by(ContratoNfse.versao).first()
    if existente is not None:
        return existente
    contrato = _contrato_inicial(criado_por_id=criado_por_id)
    db.session.add(contrato)
    try:
        db.session.commit()
        log_event(
            "nfse_contrato_inicial_criado",
            contrato_id=contrato.id,
            versao=contrato.versao,
        )
        return contrato
    except IntegrityError:
        db.session.rollback()
        existente = ContratoNfse.query.order_by(ContratoNfse.versao).first()
        if existente is not None:
            return existente
        raise PersistenciaContratoError(
            "não foi possível criar o contrato inicial da NFS-e"
        )
    except Exception as exc:
        _persistencia_falhou("garantir_contrato_inicial", exc)


def contrato_ativo():
    contrato = (
        ContratoNfse.query
        .filter(ContratoNfse.estado == "ativa")
        .order_by(ContratoNfse.versao.desc())
        .first()
    )
    return contrato if contrato is not None else garantir_contrato_inicial()


def validar_contrato_automatico(contrato_id=None):
    """Valida o gate automático sem alterar o contrato selecionado."""

    contrato = (
        _contrato_com_campos(contrato_id)
        if contrato_id is not None
        else contrato_ativo()
    )
    if contrato is None:
        raise ContratoNfseNaoElegivelError(
            "não há contrato da NFS-e disponível para o modo automático"
        )
    if not contrato.elegivel_automatico:
        raise ContratoNfseNaoElegivelError(
            "o contrato da NFS-e ainda não é elegível para o modo automático"
        )
    incidente = IncidenteContratoNfse.query.filter_by(
        contrato_base_id=contrato.id,
        estado="aberto",
        severidade="fiscal",
    ).first()
    if incidente is not None:
        raise ContratoNfseNaoElegivelError(
            "há incidente fiscal aberto no contrato da NFS-e"
        )
    return contrato


def _contrato_com_campos(contrato_id):
    return (
        ContratoNfse.query.options(
            selectinload(ContratoNfse.campos).selectinload(CampoContratoNfse.opcoes)
        )
        .filter(ContratoNfse.id == contrato_id)
        .first()
    )


def _opcao_execucao(opcao) -> OpcaoExecucaoNfse:
    return OpcaoExecucaoNfse(
        valor=opcao.valor,
        rotulo=opcao.rotulo,
        ordem=opcao.ordem,
    )


def _campo_execucao(campo) -> CampoExecucaoNfse:
    return CampoExecucaoNfse(
        chave_semantica=campo.chave_semantica,
        etapa=campo.etapa,
        seletor_tipo=campo.seletor_tipo,
        seletor=campo.seletor,
        rotulo=campo.rotulo,
        tipo=campo.tipo,
        interacao=campo.interacao,
        obrigatorio=bool(campo.obrigatorio),
        ordem=campo.ordem,
        condicao_chave=campo.condicao_chave,
        condicao_valor=campo.condicao_valor,
        origem=campo.origem,
        fonte=campo.fonte,
        valor_fixo=campo.valor_fixo,
        revisao_secao=campo.revisao_secao,
        revisao_rotulo=campo.revisao_rotulo,
        conferivel_automatico=bool(campo.conferivel_automatico),
        opcoes=tuple(_opcao_execucao(opcao) for opcao in campo.opcoes),
    )


def carregar_execucao(contrato_id=None) -> ContratoExecucaoNfse:
    """Carrega uma versão e materializa todos os campos e opções em tuplas."""

    if contrato_id is None:
        contrato = contrato_ativo()
        contrato = _contrato_com_campos(contrato.id) or contrato
    else:
        contrato = _contrato_com_campos(contrato_id)
        if contrato is None:
            raise ContratoNfseNaoEncontradoError(
                "a versão de contrato solicitada não existe"
            )
    campos = tuple(_campo_execucao(campo) for campo in contrato.campos)
    return ContratoExecucaoNfse(
        contrato_id=contrato.id,
        versao=contrato.versao,
        estado=contrato.estado,
        fingerprint=contrato.fingerprint,
        elegivel_automatico=bool(contrato.elegivel_automatico),
        campos=campos,
    )


def contrato_inicial_execucao() -> ContratoExecucaoNfse:
    """Materializa a mesma constante inicial para testes sem banco."""

    campos = []
    for dados in CONTRATO_INICIAL:
        campos.append(
            CampoExecucaoNfse(
                chave_semantica=dados["chave_semantica"],
                etapa=dados["etapa"],
                seletor_tipo=dados["seletor_tipo"],
                seletor=dados["seletor"],
                rotulo=dados["rotulo"],
                tipo=dados["tipo"],
                interacao=dados["interacao"],
                obrigatorio=bool(dados["obrigatorio"]),
                ordem=dados["ordem"],
                condicao_chave=dados["condicao_chave"],
                condicao_valor=dados["condicao_valor"],
                origem=dados["origem"],
                fonte=dados["fonte"],
                valor_fixo=dados["valor_fixo"],
                revisao_secao=dados["revisao_secao"],
                revisao_rotulo=dados["revisao_rotulo"],
                conferivel_automatico=bool(dados["conferivel_automatico"]),
                opcoes=tuple(
                    OpcaoExecucaoNfse(
                        valor=opcao["valor"],
                        rotulo=opcao["rotulo"],
                        ordem=opcao["ordem"],
                    )
                    for opcao in dados["opcoes"]
                ),
            )
        )
    return ContratoExecucaoNfse(
        contrato_id=0,
        versao=1,
        estado="ativa",
        fingerprint=_fingerprint_inicial(),
        elegivel_automatico=True,
        campos=tuple(campos),
    )


def _campo_da_diferenca(diferenca: Diferenca):
    return diferenca.observado or diferenca.esperado


def _incidente_novo(contrato_id, diferenca: Diferenca, assinatura: str, agora):
    campo = _campo_da_diferenca(diferenca)
    incidente = IncidenteContratoNfse(
        contrato_base_id=contrato_id,
        assinatura=assinatura,
        etapa=diferenca.etapa,
        tipo=diferenca.tipo,
        severidade=diferenca.severidade,
        estado="aberto",
        chave_esperada=diferenca.chave_esperada,
        chave_observada=diferenca.chave_observada,
        rotulo=campo.rotulo if campo else None,
        tipo_controle=campo.tipo if campo else None,
        interacao=campo.interacao if campo else None,
        obrigatorio=campo.obrigatorio if campo else None,
        primeira_observacao_em=agora,
        ultima_observacao_em=agora,
        observacoes=1,
        mensagem=(diferenca.mensagem or "Diferença estrutural requer revisão.")[:500],
    )
    campo_opcoes = diferenca.observado or diferenca.esperado
    if campo_opcoes is not None:
        incidente.opcoes.extend(
            OpcaoIncidenteContratoNfse(
                valor=opcao.valor,
                rotulo=opcao.rotulo,
                ordem=opcao.ordem,
            )
            for opcao in campo_opcoes.opcoes
        )
    return incidente


def _registrar_uma_diferenca(contrato_id, diferenca: Diferenca, agora):
    assinatura = assinar_incidente(contrato_id, diferenca)
    for tentativa in range(2):
        existente = IncidenteContratoNfse.query.filter_by(
            contrato_base_id=contrato_id,
            assinatura=assinatura,
        ).first()
        if existente is not None:
            existente.ultima_observacao_em = agora
            existente.observacoes = (existente.observacoes or 0) + 1
            existente.estado = "aberto"
            try:
                db.session.commit()
                return existente, assinatura
            except Exception as exc:
                _persistencia_falhou("atualizar_incidente_nfse", exc)
        db.session.add(_incidente_novo(contrato_id, diferenca, assinatura, agora))
        try:
            db.session.commit()
            incidente = IncidenteContratoNfse.query.filter_by(
                contrato_base_id=contrato_id,
                assinatura=assinatura,
            ).first()
            return incidente, assinatura
        except IntegrityError:
            db.session.rollback()
            if tentativa == 0:
                continue
            raise PersistenciaContratoError(
                "não foi possível reconciliar a observação concorrente do incidente"
            )
        except Exception as exc:
            _persistencia_falhou("registrar_incidente_nfse", exc)
    raise PersistenciaContratoError("não foi possível registrar o incidente da NFS-e")


def registrar_incidentes(contrato_id, resultado, *, agora=None):
    """Faz upsert por assinatura, preservando o primeiro instante observado."""

    diferencas: Iterable[Diferenca] = getattr(resultado, "diferencas", resultado or ())
    agora = agora or utcnow_naive()
    persistidos = []
    for diferenca in diferencas:
        if not isinstance(diferenca, Diferenca):
            continue
        incidente, assinatura = _registrar_uma_diferenca(contrato_id, diferenca, agora)
        persistidos.append(incidente)
        log_event(
            "nfse_incidente_observado",
            contrato_id=contrato_id,
            incidente_id=incidente.id,
            etapa=diferenca.etapa,
            tipo=diferenca.tipo,
            assinatura=assinatura,
        )
        auditoria.registrar(
            "nfse.incidente.observar",
            alvo_tipo="incidente_nfse",
            alvo_id=incidente.id,
            detalhe=(
                f"contrato_id={contrato_id};etapa={diferenca.etapa};"
                f"tipo={diferenca.tipo};assinatura={assinatura}"
            ),
        )
    return persistidos


def fontes_disponiveis() -> list[dict[str, str | None]]:
    """Devolve o catálogo fechado de origens aceitas pelo contrato."""

    return [
        {"origem": "fixo", "fonte": None, "rotulo": "Valor fixo"},
        {"origem": "nota", "fonte": "documento", "rotulo": "Documento da nota"},
        {"origem": "nota", "fonte": "valor_final", "rotulo": "Valor final da nota"},
        {"origem": "nota", "fonte": "descricao", "rotulo": "Descrição da nota"},
        {"origem": "derivado", "fonte": "data_emissao", "rotulo": "Data de emissão"},
        {
            "origem": "derivado",
            "fonte": "competencia_descricao",
            "rotulo": "Competência da descrição",
        },
        {
            "origem": "configuracao",
            "fonte": "regime_apuracao_sn",
            "rotulo": "Regime de apuração configurado",
        },
        {
            "origem": "configuracao",
            "fonte": "municipio_servico_codigo",
            "rotulo": "Código do município configurado",
        },
        {
            "origem": "configuracao",
            "fonte": "municipio_servico_nome",
            "rotulo": "Nome do município configurado",
        },
        {
            "origem": "configuracao",
            "fonte": "codigo_tributacao",
            "rotulo": "Código de tributação configurado",
        },
        {"origem": "configuracao", "fonte": "item_nbs", "rotulo": "Item NBS configurado"},
        {
            "origem": "configuracao",
            "fonte": "piscofins_situacao",
            "rotulo": "Situação PIS/COFINS configurada",
        },
        {
            "origem": "configuracao",
            "fonte": "piscofins_tipo_retencao",
            "rotulo": "Tipo de retenção configurado",
        },
        {"origem": "padrao_portal", "fonte": None, "rotulo": "Manter padrão do portal"},
        {"origem": "intocavel", "fonte": None, "rotulo": "Não tocar no campo"},
    ]


def _valor_regra(regra: Any, chave: str, padrao=None):
    if isinstance(regra, Mapping):
        return regra.get(chave, padrao)
    if isinstance(regra, (CampoExecucaoNfse, CampoContratoNfse)):
        valores = {
            "origem": regra.origem,
            "fonte": regra.fonte,
            "valor_fixo": regra.valor_fixo,
        }
        return valores.get(chave, padrao)
    raise ConfiguracaoContratoInvalidaError("regra de contrato inválida")


def _nota_documento(nota, _config, _hoje):
    return nota.documento


def _nota_valor(nota, _config, _hoje):
    return nota.valor_final


def _nota_descricao(nota, config, _hoje):
    from app.services import nfse_config

    return nfse_config.descricao_da_nota(config, nota)


def _nota_competencia(nota, _config, _hoje):
    return nota.competencia


def _config_regime(_nota, config, _hoje):
    return config.regime_apuracao_sn


def _config_municipio_codigo(_nota, config, _hoje):
    return config.municipio_servico_codigo


def _config_municipio_nome(_nota, config, _hoje):
    return config.municipio_servico_nome


def _config_tributacao(_nota, config, _hoje):
    return config.codigo_tributacao


def _config_nbs(_nota, config, _hoje):
    return config.item_nbs


def _config_piscofins_situacao(_nota, config, _hoje):
    return config.piscofins_situacao


def _config_piscofins_retencao(_nota, config, _hoje):
    return config.piscofins_tipo_retencao


_RESOLVEDORES_FONTES = {
    "nota": {
        "documento": _nota_documento,
        "valor_final": _nota_valor,
        "descricao": _nota_descricao,
    },
    "derivado": {
        "data_emissao": lambda _nota, _config, hoje: hoje,
        "competencia_descricao": _nota_competencia,
    },
    "configuracao": {
        "regime_apuracao_sn": _config_regime,
        "municipio_servico_codigo": _config_municipio_codigo,
        "municipio_servico_nome": _config_municipio_nome,
        "codigo_tributacao": _config_tributacao,
        "item_nbs": _config_nbs,
        "piscofins_situacao": _config_piscofins_situacao,
        "piscofins_tipo_retencao": _config_piscofins_retencao,
    },
}


def _catalogo_tem(origem: str, fonte: str | None) -> bool:
    return any(
        item["origem"] == origem and item["fonte"] == fonte
        for item in fontes_disponiveis()
    )


def resolver_valor(regra, nota, config, hoje):
    """Resolve uma fonte do catálogo sem executar código configurável."""

    origem = _valor_regra(regra, "origem")
    fonte = _valor_regra(regra, "fonte")
    if origem == "fixo":
        valor = _valor_regra(regra, "valor_fixo")
        if valor is None or not str(valor).strip():
            raise ConfiguracaoContratoInvalidaError(
                "origem fixa exige um valor"
            )
        if len(str(valor)) > 500:
            raise ConfiguracaoContratoInvalidaError(
                "valor fixo excede o limite permitido"
            )
        return valor
    if origem in {"padrao_portal", "intocavel"} and fonte is None:
        return None
    resolveres = _RESOLVEDORES_FONTES.get(origem, {})
    resolver = resolveres.get(fonte)
    if resolver is None:
        raise ConfiguracaoContratoInvalidaError(
            "origem ou fonte não pertence ao catálogo seguro"
        )
    return resolver(nota, config, hoje)


def _copiar_campo(campo):
    copia = CampoContratoNfse(
        chave_semantica=campo.chave_semantica,
        etapa=campo.etapa,
        seletor_tipo=campo.seletor_tipo,
        seletor=campo.seletor,
        rotulo=campo.rotulo,
        tipo=campo.tipo,
        interacao=campo.interacao,
        obrigatorio=campo.obrigatorio,
        ordem=campo.ordem,
        condicao_chave=campo.condicao_chave,
        condicao_valor=campo.condicao_valor,
        origem=campo.origem,
        fonte=campo.fonte,
        valor_fixo=campo.valor_fixo,
        revisao_secao=campo.revisao_secao,
        revisao_rotulo=campo.revisao_rotulo,
        conferivel_automatico=campo.conferivel_automatico,
    )
    copia.opcoes.extend(
        OpcaoCampoContratoNfse(
            valor=opcao.valor,
            rotulo=opcao.rotulo,
            ordem=opcao.ordem,
        )
        for opcao in campo.opcoes
    )
    return copia


def _campo_do_incidente(incidente, campos):
    chave = incidente.chave_observada or incidente.chave_esperada
    return next((campo for campo in campos if campo.chave_semantica == chave), None)


def _novo_campo_do_incidente(incidente, origem, fonte, valor_fixo, ordem):
    chave = incidente.chave_observada or incidente.chave_esperada
    if not chave:
        raise ConfiguracaoContratoInvalidaError(
            "o incidente não possui identidade de campo configurável"
        )
    campo = CampoContratoNfse(
        chave_semantica=chave,
        etapa=incidente.etapa,
        seletor_tipo="name",
        seletor=chave,
        rotulo=incidente.rotulo or chave,
        tipo=incidente.tipo_controle or "text",
        interacao=incidente.interacao or "texto",
        obrigatorio=bool(incidente.obrigatorio),
        ordem=ordem,
        origem=origem,
        fonte=fonte,
        valor_fixo=valor_fixo,
        conferivel_automatico=False,
    )
    campo.opcoes.extend(
        OpcaoCampoContratoNfse(
            valor=opcao.valor,
            rotulo=opcao.rotulo,
            ordem=opcao.ordem,
        )
        for opcao in incidente.opcoes
    )
    return campo


def _fingerprint_contrato(campos) -> str:
    forma = []
    for campo in sorted(campos, key=lambda item: item.chave_semantica):
        forma.append(
            {
                "chave": campo.chave_semantica,
                "etapa": campo.etapa,
                "seletor_tipo": campo.seletor_tipo,
                "seletor": campo.seletor,
                "rotulo": campo.rotulo,
                "tipo": campo.tipo,
                "interacao": campo.interacao,
                "obrigatorio": bool(campo.obrigatorio),
                "origem": campo.origem,
                "fonte": campo.fonte,
                "valor_fixo": campo.valor_fixo,
                "opcoes": sorted((opcao.valor, opcao.rotulo) for opcao in campo.opcoes),
            }
        )
    texto = json.dumps(forma, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _validar_configuracao(incidente, dados):
    if not isinstance(dados, Mapping):
        raise ConfiguracaoContratoInvalidaError("dados de configuração inválidos")
    permitidos = {"origem", "fonte", "valor_fixo"}
    if set(dados) - permitidos:
        raise ConfiguracaoContratoInvalidaError(
            "a configuração contém campos não permitidos"
        )
    origem = str(dados.get("origem") or "")
    fonte = dados.get("fonte")
    fonte = str(fonte) if fonte is not None else None
    valor_fixo = dados.get("valor_fixo")
    if valor_fixo is not None:
        valor_fixo = str(valor_fixo)
    if not _catalogo_tem(origem, fonte):
        raise ConfiguracaoContratoInvalidaError(
            "origem ou fonte não pertence ao catálogo seguro"
        )
    if origem == "fixo":
        if valor_fixo is None or not valor_fixo.strip():
            raise ConfiguracaoContratoInvalidaError("origem fixa exige um valor")
        if len(valor_fixo) > 500:
            raise ConfiguracaoContratoInvalidaError(
                "valor fixo excede o limite permitido"
            )
        valores_opcoes = {opcao.valor for opcao in incidente.opcoes}
        if valores_opcoes and valor_fixo not in valores_opcoes:
            raise ConfiguracaoContratoInvalidaError(
                "o valor fixo não pertence às opções observadas"
            )
    elif valor_fixo is not None:
        raise ConfiguracaoContratoInvalidaError(
            "valor fixo só pode ser usado com a origem fixa"
        )
    return origem, fonte, valor_fixo


def _auditar_configuracao(candidato, incidente, usuario_id):
    try:
        auditoria.registrar(
            "nfse.contrato.configurar",
            alvo_tipo="contrato_nfse",
            alvo_id=candidato.id,
            detalhe=(
                f"incidente_id={incidente.id};usuario_id={usuario_id};"
                f"contrato_id={candidato.id}"
            ),
        )
    except Exception as exc:
        log_event(
            "nfse_contrato_auditoria_falhou",
            level="WARNING",
            contrato_id=candidato.id,
            incidente_id=incidente.id,
            error_type=type(exc).__name__,
        )


def configurar_incidente(incidente_id, dados, usuario_id=None) -> ContratoNfse:
    """Cria uma candidata a partir de um incidente, sem alterar o ativo."""

    incidente = db.session.get(IncidenteContratoNfse, incidente_id)
    if incidente is None:
        raise ContratoNfseNaoEncontradoError("o incidente solicitado não existe")
    origem, fonte, valor_fixo = _validar_configuracao(incidente, dados)
    ativo = _contrato_com_campos(contrato_ativo().id)
    if ativo is None:
        raise ContratoNfseNaoEncontradoError("não há contrato ativo para copiar")

    agora = utcnow_naive()
    campos = [_copiar_campo(campo) for campo in ativo.campos]
    campo_alvo = _campo_do_incidente(incidente, campos)
    if campo_alvo is None:
        proxima_ordem = max((campo.ordem for campo in campos), default=-1) + 1
        campo_alvo = _novo_campo_do_incidente(
            incidente, origem, fonte, valor_fixo, proxima_ordem
        )
        campos.append(campo_alvo)
    else:
        campo_alvo.origem = origem
        campo_alvo.fonte = fonte
        campo_alvo.valor_fixo = valor_fixo

    maior_versao = db.session.query(db.func.max(ContratoNfse.versao)).scalar() or 0
    candidato = ContratoNfse(
        versao=maior_versao + 1,
        estado="candidata",
        fingerprint=_fingerprint_contrato(campos),
        elegivel_automatico=False,
        criado_em=agora,
        criado_por_id=usuario_id,
        erro_validacao=(
            "aguarda prova assistida de avanço"
            if incidente.obrigatorio and origem in {"padrao_portal", "intocavel"}
            else None
        ),
    )
    candidato.campos.extend(campos)
    incidente.contrato_candidato = candidato
    incidente.estado = "configurado"
    db.session.add(candidato)
    try:
        db.session.commit()
    except Exception as exc:
        _persistencia_falhou("configurar_incidente_nfse", exc)
    _auditar_configuracao(candidato, incidente, usuario_id)
    return candidato


def registrar_validacao(
    contrato_id, nota_id, resultado, usuario_id=None, *, agora=None
) -> ContratoNfse:
    """Registra a revisão da candidata sem persistir valores da nota."""

    contrato = db.session.get(ContratoNfse, contrato_id)
    if contrato is None:
        raise ContratoNfseNaoEncontradoError(
            "a versão candidata da NFS-e não existe"
        )
    divergencias = tuple(resultado or ())
    elegivel = bool(getattr(resultado, "elegivel_automatico", False))
    agora = agora or utcnow_naive()
    contrato.nota_validacao_id = nota_id
    contrato.elegivel_automatico = elegivel and not divergencias
    if divergencias:
        contrato.estado = "candidata"
        contrato.validado_em = None
        contrato.erro_validacao = "a revisão da validação apresentou divergências"
    else:
        contrato.estado = "validada"
        contrato.validado_em = agora
        contrato.erro_validacao = (
            None
            if contrato.elegivel_automatico
            else "a revisão permite somente modos assistidos"
        )
    try:
        db.session.commit()
    except Exception as exc:
        _persistencia_falhou("registrar_validacao_nfse", exc)
    try:
        auditoria.registrar(
            "nfse.contrato.validar",
            alvo_tipo="contrato_nfse",
            alvo_id=contrato.id,
            detalhe=(
                f"contrato_id={contrato.id};nota_id={nota_id};"
                f"usuario_id={usuario_id};divergencias={len(divergencias)}"
            ),
        )
    except Exception as exc:
        log_event(
            "nfse_contrato_auditoria_falhou",
            level="WARNING",
            contrato_id=contrato.id,
            error_type=type(exc).__name__,
        )
    return contrato


def _data_iso(valor):
    return valor.isoformat() if valor is not None else None


def _resumo_contrato(contrato):
    return {
        "id": contrato.id,
        "versao": contrato.versao,
        "estado": contrato.estado,
        "elegivel_automatico": bool(contrato.elegivel_automatico),
        "criado_em": _data_iso(contrato.criado_em),
        "validado_em": _data_iso(contrato.validado_em),
        "ativado_em": _data_iso(contrato.ativado_em),
        "erro_validacao": contrato.erro_validacao,
    }


def _resumo_incidente(incidente):
    campo = {
        "chave_esperada": incidente.chave_esperada,
        "chave_observada": incidente.chave_observada,
        "rotulo": incidente.rotulo,
        "tipo": incidente.tipo_controle,
        "interacao": incidente.interacao,
        "obrigatorio": incidente.obrigatorio,
    }
    return {
        "id": incidente.id,
        "etapa": incidente.etapa,
        "tipo": incidente.tipo,
        "severidade": incidente.severidade,
        "estado": incidente.estado,
        "campo": campo,
        "observacoes": incidente.observacoes,
        "primeira_observacao_em": _data_iso(incidente.primeira_observacao_em),
        "ultima_observacao_em": _data_iso(incidente.ultima_observacao_em),
        "mensagem": incidente.mensagem,
        "opcoes": [
            {"valor": opcao.valor, "rotulo": opcao.rotulo}
            for opcao in sorted(incidente.opcoes, key=lambda item: item.ordem)
        ],
    }


def estado_painel():
    """Serializa o estado persistido sem dados da nota ou do DOM."""

    ativo = contrato_ativo()
    candidatos = (
        ContratoNfse.query
        .filter(ContratoNfse.estado.in_(("candidata", "validada")))
        .order_by(ContratoNfse.versao.desc())
        .all()
    )
    incidentes = (
        IncidenteContratoNfse.query
        .filter(IncidenteContratoNfse.contrato_base_id == ativo.id)
        .order_by(IncidenteContratoNfse.id.desc())
        .all()
    )
    return {
        "ativo": _resumo_contrato(ativo),
        "candidatas": [_resumo_contrato(item) for item in candidatos],
        "incidentes": [_resumo_incidente(item) for item in incidentes],
        "fontes": fontes_disponiveis(),
    }


def detalhe_contrato(contrato_id):
    """Devolve uma versão e seus campos sem expor seletores internos."""

    contrato = _contrato_com_campos(contrato_id)
    if contrato is None:
        raise ContratoNfseNaoEncontradoError("a versão de contrato solicitada não existe")
    dados = _resumo_contrato(contrato)
    dados["campos"] = [
        {
            "chave_semantica": campo.chave_semantica,
            "etapa": campo.etapa,
            "rotulo": campo.rotulo,
            "tipo": campo.tipo,
            "interacao": campo.interacao,
            "obrigatorio": bool(campo.obrigatorio),
            "ordem": campo.ordem,
            "origem": campo.origem,
            "fonte": campo.fonte,
            "valor_fixo": campo.valor_fixo,
            "conferivel_automatico": bool(campo.conferivel_automatico),
            "opcoes": [
                {"valor": opcao.valor, "rotulo": opcao.rotulo}
                for opcao in sorted(campo.opcoes, key=lambda item: item.ordem)
            ],
        }
        for campo in sorted(contrato.campos, key=lambda item: item.ordem)
    ]
    dados["incidentes"] = [
        _resumo_incidente(item)
        for item in sorted(contrato.incidentes, key=lambda item: item.id)
    ]
    return dados


__all__ = [
    "CONTRATO_INICIAL",
    "CampoContratoDesconhecidoError",
    "CampoExecucaoNfse",
    "ConfiguracaoContratoInvalidaError",
    "ContratoNfseNaoElegivelError",
    "ContratoExecucaoNfse",
    "ContratoNfseError",
    "ContratoNfseNaoEncontradoError",
    "OpcaoExecucaoNfse",
    "PersistenciaContratoError",
    "carregar_execucao",
    "contrato_ativo",
    "configurar_incidente",
    "contrato_inicial_execucao",
    "detalhe_contrato",
    "estado_painel",
    "fontes_disponiveis",
    "garantir_contrato_inicial",
    "registrar_validacao",
    "registrar_incidentes",
    "resolver_valor",
    "validar_contrato_automatico",
]
