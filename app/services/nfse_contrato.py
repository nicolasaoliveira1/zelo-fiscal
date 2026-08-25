"""Persistência e snapshot imutável do contrato adaptativo da NFS-e."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable

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


__all__ = [
    "CONTRATO_INICIAL",
    "CampoContratoDesconhecidoError",
    "CampoExecucaoNfse",
    "ContratoExecucaoNfse",
    "ContratoNfseError",
    "ContratoNfseNaoEncontradoError",
    "OpcaoExecucaoNfse",
    "PersistenciaContratoError",
    "carregar_execucao",
    "contrato_ativo",
    "garantir_contrato_inicial",
    "registrar_incidentes",
]
