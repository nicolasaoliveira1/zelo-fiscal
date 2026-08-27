"""Persistência e snapshot imutável do contrato adaptativo da NFS-e."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import selectinload

from app import db
from app.automation import nfse_recon
from app.automation.capture import salvar_artefato_sanitizado
from app.models import (
    CampoContratoNfse,
    ContratoNfse,
    IncidenteContratoNfse,
    OpcaoCampoContratoNfse,
    OpcaoIncidenteContratoNfse,
)
from app.services import auditoria
from app.services.execution_logger import log_event
from app.services.nfse_drift import (
    CONTROLE_NOVO,
    CONTROLE_REMOVIDO,
    CampoComparavel,
    Diferenca,
    ResultadoComparacao,
    assinar_incidente,
    comparar,
    recomendar_remapeamentos,
)
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
    """Dados do operador não pertencem ao catálogo seguro do contrato.

    Carrega `campo` para a rota marcar o controle certo na tela: sem isso a
    interface só sabe dizer "revise a configuração", e o operador procura o
    erro no lugar errado.
    """

    def __init__(self, mensagem, campo="origem"):
        super().__init__(mensagem)
        self.campo = campo


class ContratoNfseNaoElegivelError(ContratoNfseError):
    """A versão não pode conduzir o modo automático."""


class ContratoNfseTransicaoInvalidaError(ContratoNfseError):
    """A versão não está pronta para a transição solicitada."""


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


@dataclass(frozen=True)
class Observacao:
    """Resultado seguro de uma leitura estrutural da tela atual."""

    contrato_id: int
    etapa: str
    momento: str
    estado: str
    compatibilidade: str
    diferencas: tuple[dict[str, Any], ...] = ()
    evidencias: tuple[str, ...] = ()
    incidentes: int = 0


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
    if contrato.estado != "ativa":
        raise ContratoNfseNaoElegivelError(
            "somente o contrato ativo pode conduzir o modo automático"
        )
    if not contrato.elegivel_automatico:
        raise ContratoNfseNaoElegivelError(
            "o contrato da NFS-e ainda não é elegível para o modo automático"
        )
    incidente = (
        IncidenteContratoNfse.query
        .filter(
            IncidenteContratoNfse.contrato_base_id == contrato.id,
            IncidenteContratoNfse.estado.in_(("aberto", "configurado")),
        )
        .first()
    )
    if incidente is not None:
        raise ContratoNfseNaoElegivelError(
            "há incidente fiscal pendente no contrato da NFS-e"
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


def campos_da_etapa(contrato, etapa):
    """Campos que o contrato declara para uma etapa. Fonte única."""

    return tuple(campo for campo in contrato.campos if campo.etapa == etapa)


def _diferenca_segura(diferenca: Diferenca) -> dict[str, Any]:
    return {
        "etapa": diferenca.etapa,
        "tipo": diferenca.tipo,
        "severidade": diferenca.severidade,
        "chave_esperada": diferenca.chave_esperada,
        "chave_observada": diferenca.chave_observada,
        "rotulo": diferenca.rotulo,
        "mensagem": diferenca.mensagem,
    }


def comparar_e_registrar(
    contrato_execucao,
    etapa,
    momento,
    inventario,
    *,
    observacao_final=False,
    execution_id=None,
):
    """Compara a etapa, persiste o que pede decisão e guarda a evidência.

    Núcleo compartilhado pela recon assistida (`observar`) e pela fronteira do
    preenchimento (`nfse_service._observar_fronteira_contrato`). O que difere
    entre as duas é POLÍTICA — uma devolve, a outra levanta —, e política é do
    chamador. O que não pode divergir é isto: qual diferença vira incidente,
    contra qual versão, e que evidência fica.

    Devolve `(resultado, incidentes, html_seguro)`; `html_seguro` é `None`
    quando não houve nada a registrar.
    """

    resultado = comparar(
        etapa,
        campos_da_etapa(contrato_execucao, etapa),
        inventario,
        observacao_final=observacao_final,
    )
    if not resultado.diferencas_acionaveis:
        return resultado, (), None

    incidentes = ()
    if contrato_execucao.contrato_id:
        incidentes = tuple(
            registrar_incidentes(contrato_execucao.contrato_id, resultado)
        )
    html_seguro = nfse_recon.inventario_para_html(inventario)
    salvar_artefato_sanitizado(
        f"nfse_{etapa}_{momento}", html_seguro, execution_id=execution_id,
    )
    return resultado, incidentes, html_seguro


def observar(
    driver,
    contrato_execucao: ContratoExecucaoNfse,
    etapa,
    momento,
    *,
    modo="assistido",
    execution_id=None,
    inventario=None,
    observacao_final=False,
) -> Observacao:
    """Observa a tela atual, compara metadados e registra drift seguro.

    `inventario` permite comparar contra a união dos passes já observados nesta
    etapa, em vez do instantâneo da tela: o formulário é progressivo e revela
    campos conforme é preenchido.
    """

    if modo not in {"assistido", "automatico"}:
        raise ValueError("modo de observação inválido")
    if inventario is None:
        inventario = nfse_recon.inventariar(driver, etapa)
    if inventario.estado != "ok":
        motivo = inventario.motivo or "observação inconclusiva"
        log_event(
            "nfse_recon_desconhecida",
            level="WARNING",
            contrato_id=contrato_execucao.contrato_id,
            etapa=etapa,
            momento=momento,
            motivo=motivo,
            execution_id=execution_id,
        )
        return Observacao(
            contrato_id=contrato_execucao.contrato_id,
            etapa=etapa,
            momento=momento,
            estado="desconhecida",
            compatibilidade="desconhecida",
            evidencias=(
                f"não foi possível observar a etapa com segurança: {motivo}",
            ),
        )

    # A revisão é composta por pares dt/dd e precisa de uma nota para que os
    # leitores declarativos consigam conferir valores. A recon assistida não
    # recebe nota nem lê conteúdo preenchido; portanto, registra somente que a
    # tela foi alcançada, sem fabricar um incidente estrutural.
    if etapa == "revisao":
        return Observacao(
            contrato_id=contrato_execucao.contrato_id,
            etapa=etapa,
            momento=momento,
            estado="observada",
            compatibilidade="desconhecida",
            evidencias=("a revisão exige conferência vinculada a uma nota",),
        )

    resultado, incidentes, _html = comparar_e_registrar(
        contrato_execucao,
        etapa,
        momento,
        inventario,
        observacao_final=observacao_final,
        execution_id=execution_id,
    )
    return Observacao(
        contrato_id=contrato_execucao.contrato_id,
        etapa=etapa,
        momento=momento,
        estado="observada",
        compatibilidade=resultado.compatibilidade,
        diferencas=tuple(
            _diferenca_segura(diferenca) for diferenca in resultado.diferencas
        ),
        evidencias=tuple(resultado.evidencias),
        incidentes=len(incidentes),
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


def _ordem_no_fim_da_etapa(campos, etapa):
    """Primeira `ordem` livre depois do último campo desta etapa.

    Mantém o agrupamento por etapa que a sequência de aplicação já tem, sem
    inventar uma posição dentro dela.
    """

    da_etapa = [campo.ordem for campo in campos if campo.etapa == etapa]
    if da_etapa:
        return max(da_etapa) + 1
    return max((campo.ordem for campo in campos), default=-1) + 1


def _ordem_da_diferenca(diferenca: Diferenca):
    """Posição do controle na etapa, em ordem de documento.

    O observado manda: é ele que reflete a tela de agora. Um controle que sumiu
    só tem a ordem que o contrato guardou, e é a melhor aproximação disponível.
    """

    for campo in (diferenca.observado, diferenca.esperado):
        if campo is not None and campo.ordem is not None:
            return int(campo.ordem)
    return None


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
        rotulo=(campo.rotulo or "")[:500] if campo else None,
        # String(30) nas duas: o inventario deriva nomes curtos, mas um tipo
        # inesperado do portal nao pode virar DataError so no MySQL.
        tipo_controle=(campo.tipo or "")[:30] if campo else None,
        interacao=(campo.interacao or "")[:30] if campo else None,
        obrigatorio=campo.obrigatorio if campo else None,
        ordem_pagina=_ordem_da_diferenca(diferenca),
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
    for tentativa in range(3):
        existente = (
            IncidenteContratoNfse.query
            .filter_by(
                contrato_base_id=contrato_id,
                assinatura=assinatura,
            )
            .with_for_update()
            .first()
        )
        if existente is not None:
            existente.ultima_observacao_em = agora
            existente.observacoes = (existente.observacoes or 0) + 1
            # Reabrir um incidente já configurado quebraria a cobertura da
            # candidata: `ativar` exige que todo pendente esteja `configurado`
            # e apontando para ela. Observar de novo a mesma diferença não
            # desfaz a decisão do operador — só o descarte explícito desfaz.
            if existente.estado not in {"configurado", "resolvido", "descartado"}:
                existente.estado = "aberto"
            # A posição não entra na assinatura (o campo continua o mesmo se o
            # portal o move de lugar), mas a lista precisa refletir a tela atual.
            ordem = _ordem_da_diferenca(diferenca)
            if ordem is not None:
                existente.ordem_pagina = ordem
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
        except (IntegrityError, OperationalError) as exc:
            db.session.rollback()
            args = getattr(getattr(exc, "orig", None), "args", None) or (None,)
            codigo = args[0]
            concorrencia = isinstance(exc, IntegrityError) or codigo in {1205, 1213}
            if tentativa < 2 and concorrencia:
                continue
            if not concorrencia:
                _persistencia_falhou("registrar_incidente_nfse", exc)
            raise PersistenciaContratoError(
                "não foi possível reconciliar a observação concorrente do incidente"
            ) from exc
        except Exception as exc:
            _persistencia_falhou("registrar_incidente_nfse", exc)
    raise PersistenciaContratoError("não foi possível registrar o incidente da NFS-e")


def contrato_base_de_incidentes(contrato_id=None) -> int | None:
    """Contra qual versão um incidente observado deve ser gravado.

    Sempre a ATIVA. Durante a validação de uma candidata o preenchimento carrega
    a candidata, e gravar contra ela escondia o incidente: a Central lista os da
    versão ativa, e `configurar_incidente` exige base ativa. O portal podia mudar
    no meio do teste e o operador não ficava sabendo.
    """

    try:
        return contrato_ativo().id
    except ContratoNfseError:
        return contrato_id


# A etapa ordena antes da posição: percorrer a Central deve ser percorrer o
# formulário, e o formulário tem uma ordem própria entre as telas.
_ORDEM_DAS_ETAPAS = ("pessoas", "servico", "tributacao", "revisao")


def _ordenar_por_tela(incidentes):
    """Ordena como o operador percorre: etapa, depois posição na página.

    Incidente sem posição (registrado antes da coluna existir, ou vindo de um
    campo que o contrato não localizou) vai para o fim da própria etapa.
    """

    def chave(incidente):
        try:
            etapa = _ORDEM_DAS_ETAPAS.index(incidente.etapa or "")
        except ValueError:
            etapa = len(_ORDEM_DAS_ETAPAS)
        ordem = incidente.ordem_pagina
        return (etapa, ordem is None, ordem if ordem is not None else 0, incidente.id)

    return sorted(incidentes, key=chave)


def descartar_incidentes(contrato_id=None, usuario_id=None, *, agora=None) -> int:
    """Descarta os incidentes abertos de uma versão, sem tocar no contrato.

    Serve para o caso em que a própria observação estava errada: os incidentes
    persistem por upsert de assinatura e nada os expira, então uma recon
    defeituosa deixa a Central entulhada para sempre. Descartar é escriturar
    uma decisão — o incidente fica no banco, com autor e instante.

    Recusa se houver incidente `configurado`: esse já pertence a uma candidata,
    e descartá-lo esvaziaria a cobertura que a ativação exige.
    """

    contrato_id = contrato_id if contrato_id is not None else contrato_ativo().id
    pendentes = (
        IncidenteContratoNfse.query
        .filter(
            IncidenteContratoNfse.contrato_base_id == contrato_id,
            IncidenteContratoNfse.estado.in_(("aberto", "configurado")),
        )
        .with_for_update()
        .all()
    )
    if any(incidente.estado == "configurado" for incidente in pendentes):
        raise ContratoNfseTransicaoInvalidaError(
            "há incidentes já configurados numa candidata; descarte a candidata antes"
        )
    agora = agora or utcnow_naive()
    for incidente in pendentes:
        incidente.estado = "descartado"
        incidente.resolvido_em = agora
        incidente.resolvido_por_id = usuario_id
    try:
        db.session.commit()
    except Exception as exc:
        _persistencia_falhou("descartar_incidentes_nfse", exc)
    if pendentes:
        log_event("nfse_incidentes_descartados", level="WARNING",
                  contrato_id=contrato_id, quantidade=len(pendentes))
        auditoria.registrar(
            "nfse.incidente.descartar", alvo_tipo="contrato_nfse",
            alvo_id=contrato_id,
            detalhe=f"contrato_id={contrato_id};quantidade={len(pendentes)}",
        )
    return len(pendentes)


def descartar_candidata(contrato_id, usuario_id=None, *, agora=None) -> int:
    """Arquiva uma candidata e devolve seus incidentes ao estado aberto.

    É o inverso exato da sequência de configurações que a construiu: cada
    `configurar_incidente` reconstrói a candidata inteira a partir da anterior,
    então não existe "meia candidata" para preservar. Não apaga nada: a versão
    fica `arquivada`, com rastro em auditoria.
    """

    candidata = db.session.get(ContratoNfse, contrato_id)
    if candidata is None:
        raise ContratoNfseNaoEncontradoError("a versão candidata da NFS-e não existe")
    if candidata.estado not in {"candidata", "validada"}:
        raise ContratoNfseTransicaoInvalidaError(
            "somente uma versão candidata ou validada pode ser descartada"
        )
    presos = (
        IncidenteContratoNfse.query
        .filter(IncidenteContratoNfse.contrato_candidato_id == candidata.id)
        .with_for_update()
        .all()
    )
    agora = agora or utcnow_naive()
    for incidente in presos:
        incidente.contrato_candidato_id = None
        # Só o que ela prendeu volta a pedir decisão; incidente já resolvido por
        # uma ativação anterior continua resolvido.
        if incidente.estado == "configurado":
            incidente.estado = "aberto"
            incidente.resolvido_em = None
            incidente.resolvido_por_id = None
    candidata.estado = "arquivada"
    candidata.validado_em = None
    candidata.elegivel_automatico = False
    candidata.erro_validacao = None
    try:
        db.session.commit()
    except Exception as exc:
        _persistencia_falhou("descartar_candidata_nfse", exc)
    log_event("nfse_candidata_descartada", level="WARNING",
              contrato_id=candidata.id, quantidade=len(presos))
    auditoria.registrar(
        "nfse.contrato.descartar", alvo_tipo="contrato_nfse", alvo_id=candidata.id,
        detalhe=(f"contrato_id={candidata.id};usuario_id={usuario_id};"
                 f"incidentes_reabertos={len(presos)}"),
    )
    return len(presos)


def registrar_incidentes(contrato_id, resultado, *, agora=None):
    """Faz upsert por assinatura, preservando o primeiro instante observado."""

    # `hasattr`, nunca `or`: tupla vazia de acionáveis é a resposta certa e o
    # `or` cairia no fallback, registrando justamente o que se quer ignorar.
    if hasattr(resultado, "diferencas_acionaveis"):
        diferencas: Iterable[Diferenca] = resultado.diferencas_acionaveis
    else:
        diferencas = getattr(resultado, "diferencas", resultado or ())
    agora = agora or utcnow_naive()
    contrato_id = contrato_base_de_incidentes(contrato_id)
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
                "valor fixo excede o limite permitido", campo="valor_fixo",
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
    chave = incidente.chave_esperada or incidente.chave_observada
    return next((campo for campo in campos if campo.chave_semantica == chave), None)


# Origens que NÃO tocam o controle. Para elas o executor deriva o adaptador da
# própria origem e ignora a interação (`automation/nfse.py`), então exigir um
# adaptador de DOM aqui barra uma decisão que não interage com nada.
ORIGENS_SEM_INTERACAO = frozenset({"intocavel", "padrao_portal"})


def _adaptador_observado(interacao):
    # O mapa recebe DUAS famílias e precisa aceitar as duas: o tipo cru do DOM
    # (`text`, `date`) e o adaptador que o inventário já derivou (`texto`), que
    # é o que o incidente guarda. Faltava a identidade de `texto`, e por isso
    # configurar qualquer campo de texto falhava.
    adaptadores = {
        "texto": "texto",
        "text": "texto",
        "date": "texto",
        "number": "texto",
        "email": "texto",
        "tel": "texto",
        "textarea": "textarea",
        "radio": "radio",
        # Checkbox e o controle que só revela um bloco são clique, e o clique
        # do portal é sempre por `input[name][value]` — nunca por id, que os
        # grupos repetem entre as opções (`_marcar_radio`).
        "checkbox": "radio",
        "acao": "radio",
        "select": "select_direto",
        "select2": "select_busca",
        "select_direto": "select_direto",
        "select_busca": "select_busca",
        "chosen": "chosen",
    }
    adaptador = adaptadores.get(str(interacao or "").lower())
    if adaptador is None:
        raise ConfiguracaoContratoInvalidaError(
            "o controle observado não possui adaptador seguro"
        )
    return adaptador


# `CampoContratoNfse.seletor` e String(200) e o seletor gerado tem
# 17 + 2*len(chave) caracteres. O limite da chave sai DA COLUNA, nunca de um
# numero redondo: o SQLite ignora largura de VARCHAR e o MySQL impoe, entao um
# estouro passa no gate rapido e explode em producao (licao 3 do CLAUDE.md).
_LARGURA_SELETOR = 200
_LIMITE_CHAVE_SELETOR = (_LARGURA_SELETOR - 17) // 2


def _seletor_css_identidade(chave):
    chave = str(chave or "")
    if not chave or len(chave) > _LIMITE_CHAVE_SELETOR:
        raise ConfiguracaoContratoInvalidaError(
            "o controle observado não possui identidade estável"
        )
    escapada = chave.replace("\\", "\\\\").replace('"', '\\"')
    return f'[name="{escapada}"], [id="{escapada}"]'


def _substituir_opcoes(campo, incidente):
    campo.opcoes.clear()
    campo.opcoes.extend(
        OpcaoCampoContratoNfse(
            valor=opcao.valor,
            rotulo=opcao.rotulo,
            ordem=opcao.ordem,
        )
        for opcao in incidente.opcoes
    )


def _aplicar_estrutura_observada(campo, incidente, *, remapeamento=False):
    if incidente.chave_observada and remapeamento:
        campo.seletor_tipo = "css"
        campo.seletor = _seletor_css_identidade(incidente.chave_observada)
    if incidente.rotulo:
        campo.rotulo = incidente.rotulo
    if incidente.tipo_controle:
        campo.tipo = incidente.tipo_controle
    if incidente.interacao:
        campo.interacao = _adaptador_observado(incidente.interacao)
    if incidente.obrigatorio is not None:
        campo.obrigatorio = bool(incidente.obrigatorio)
    _substituir_opcoes(campo, incidente)


def _novo_campo_do_incidente(incidente, origem, fonte, valor_fixo, ordem):
    chave = incidente.chave_observada or incidente.chave_esperada
    if not chave:
        raise ConfiguracaoContratoInvalidaError(
            "o incidente não possui identidade de campo configurável"
        )
    campo = CampoContratoNfse(
        chave_semantica=chave,
        etapa=incidente.etapa,
        seletor_tipo="css",
        seletor=_seletor_css_identidade(chave),
        rotulo=incidente.rotulo or chave,
        tipo=incidente.tipo_controle or "text",
        interacao=(
            origem
            if origem in ORIGENS_SEM_INTERACAO
            else _adaptador_observado(incidente.interacao or "text")
        ),
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


def _campo_comparavel_incidente(incidente):
    return CampoComparavel(
        chave_semantica=(
            incidente.chave_observada or incidente.chave_esperada or ""
        ),
        etapa=incidente.etapa,
        rotulo=incidente.rotulo or "",
        tipo=incidente.tipo_controle or "",
        interacao=incidente.interacao or "",
        obrigatorio=bool(incidente.obrigatorio),
        opcoes=tuple(incidente.opcoes),
    )


def _recomendacoes_incidentes(incidentes):
    por_etapa = {}
    for incidente in incidentes:
        if incidente.estado != "aberto" or incidente.tipo not in {
            CONTROLE_REMOVIDO,
            CONTROLE_NOVO,
        }:
            continue
        campo = _campo_comparavel_incidente(incidente)
        diferenca = Diferenca(
            etapa=incidente.etapa,
            tipo=incidente.tipo,
            severidade=incidente.severidade,
            chave_esperada=incidente.chave_esperada,
            chave_observada=incidente.chave_observada,
            esperado=campo if incidente.tipo == CONTROLE_REMOVIDO else None,
            observado=campo if incidente.tipo == CONTROLE_NOVO else None,
            mensagem=incidente.mensagem,
        )
        por_etapa.setdefault(incidente.etapa, []).append(diferenca)
    recomendacoes = []
    for etapa, diferencas in por_etapa.items():
        recomendacoes.extend(
            recomendar_remapeamentos(
                ResultadoComparacao(
                    etapa=etapa,
                    compatibilidade="incompativel",
                    diferencas=tuple(diferencas),
                )
            )
        )
    return recomendacoes


def recomendacao_incidente(incidente, incidentes=None):
    if incidente.tipo != CONTROLE_REMOVIDO:
        return None
    if incidentes is None:
        incidentes = IncidenteContratoNfse.query.filter_by(
            contrato_base_id=incidente.contrato_base_id
        ).all()
    return next(
        (
            recomendacao
            for recomendacao in _recomendacoes_incidentes(incidentes)
            if recomendacao.chave_esperada == incidente.chave_esperada
            and recomendacao.etapa == incidente.etapa
        ),
        None,
    )


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
            raise ConfiguracaoContratoInvalidaError(
            "origem fixa exige um valor", campo="valor_fixo",
        )
        if len(valor_fixo) > 500:
            raise ConfiguracaoContratoInvalidaError(
                "valor fixo excede o limite permitido"
            )
        valores_opcoes = {opcao.valor for opcao in incidente.opcoes}
        if valores_opcoes and valor_fixo not in valores_opcoes:
            raise ConfiguracaoContratoInvalidaError(
                "o valor fixo não pertence às opções observadas",
                campo="valor_fixo",
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


def configurar_incidente(
    incidente_id,
    dados,
    usuario_id=None,
    *,
    chave_observada=None,
    confirmar_recomendacao=False,
) -> ContratoNfse:
    """Cria uma candidata a partir de um incidente, sem alterar o ativo."""

    incidente = (
        IncidenteContratoNfse.query
        .filter(IncidenteContratoNfse.id == incidente_id)
        .with_for_update()
        .first()
    )
    if incidente is None:
        raise ContratoNfseNaoEncontradoError("o incidente solicitado não existe")
    if incidente.estado != "aberto":
        raise ContratoNfseTransicaoInvalidaError(
            "o incidente já recebeu uma decisão"
        )
    origem, fonte, valor_fixo = _validar_configuracao(incidente, dados)
    ativo_id = contrato_ativo().id
    ativo = _contrato_com_campos(ativo_id)
    if ativo is None:
        raise ContratoNfseNaoEncontradoError("não há contrato ativo para copiar")
    if incidente.contrato_base_id != ativo.id:
        raise ContratoNfseTransicaoInvalidaError(
            "o incidente pertence a uma versão que não está mais ativa"
        )

    pendentes = IncidenteContratoNfse.query.filter_by(
        contrato_base_id=ativo.id
    ).all()
    recomendacao = recomendacao_incidente(incidente, pendentes)
    incidente_observado = None
    if recomendacao is not None:
        escolhida = chave_observada or recomendacao.chave_observada
        if not confirmar_recomendacao:
            raise ConfiguracaoContratoInvalidaError(
                "confirme explicitamente a recomendação antes de salvar",
                campo="confirmar_recomendacao",
            )
        if escolhida not in recomendacao.candidatos:
            raise ConfiguracaoContratoInvalidaError(
                "escolha um dos controles recomendados", campo="chave_observada",
            )
        incidente_observado = next(
            (
                item
                for item in pendentes
                if item.estado == "aberto"
                and item.tipo == CONTROLE_NOVO
                and item.etapa == incidente.etapa
                and item.chave_observada == escolhida
            ),
            None,
        )
        if incidente_observado is None:
            raise ContratoNfseTransicaoInvalidaError(
                "o controle recomendado não está mais pendente"
            )
    elif chave_observada is not None or confirmar_recomendacao:
        # Chega aqui quando o cliente manda escolha/confirmação para um
        # incidente que não tem recomendação — estado que muda entre o desenho
        # da tela e o envio, e por isso é erro do cliente, não do servidor.
        raise ConfiguracaoContratoInvalidaError(
            "o incidente não possui recomendação aplicável",
            campo="chave_observada",
        )

    agora = utcnow_naive()
    candidata_base = (
        ContratoNfse.query
        .join(
            IncidenteContratoNfse,
            IncidenteContratoNfse.contrato_candidato_id == ContratoNfse.id,
        )
        .filter(
            IncidenteContratoNfse.contrato_base_id == ativo.id,
            ContratoNfse.estado.in_(("candidata", "validada")),
        )
        .order_by(ContratoNfse.versao.desc())
        .first()
    )
    origem_campos = (
        _contrato_com_campos(candidata_base.id)
        if candidata_base is not None
        else ativo
    )
    campos = [_copiar_campo(campo) for campo in origem_campos.campos]
    campo_alvo = _campo_do_incidente(incidente, campos)
    remover_campo = (
        incidente.tipo == CONTROLE_REMOVIDO and incidente_observado is None
    )
    if remover_campo:
        if origem != "intocavel" or campo_alvo is None:
            raise ConfiguracaoContratoInvalidaError(
                "um controle removido sem remapeamento exige a decisão de não tocar"
            )
        campos.remove(campo_alvo)
    elif campo_alvo is None:
        # `ordem` e a sequencia de APLICACAO do contrato inteiro (0..N pelas
        # quatro etapas), nao a posicao na tela. Copiar `ordem_pagina` — que e
        # um indice por etapa — para dentro dela produzia valor duplicado e
        # posicao sem sentido. O campo novo entra no fim da SUA etapa, e o que
        # vem depois e renumerado; quem destrava a etapa e o retry de
        # `preencher_etapa_pessoas`, nao a ordem.
        proxima_ordem = _ordem_no_fim_da_etapa(campos, incidente.etapa)
        for campo in campos:
            if campo.ordem >= proxima_ordem:
                campo.ordem += 1
        campo_alvo = _novo_campo_do_incidente(
            incidente, origem, fonte, valor_fixo, proxima_ordem
        )
        campos.append(campo_alvo)
    else:
        campo_alvo.origem = origem
        campo_alvo.fonte = fonte
        campo_alvo.valor_fixo = valor_fixo
        estrutura = incidente_observado or incidente
        _aplicar_estrutura_observada(
            campo_alvo,
            estrutura,
            remapeamento=incidente_observado is not None,
        )

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
    if candidata_base is not None:
        herdados = IncidenteContratoNfse.query.filter_by(
            contrato_candidato_id=candidata_base.id,
            contrato_base_id=ativo.id,
            estado="configurado",
        ).all()
        candidata_base.estado = "arquivada"
        for herdado in herdados:
            herdado.contrato_candidato = candidato
    incidente.contrato_candidato = candidato
    incidente.estado = "configurado"
    if incidente_observado is not None:
        incidente_observado.contrato_candidato = candidato
        incidente_observado.estado = "configurado"
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
    # Só uma candidata se valida. Sem esta guarda, uma validação em curso
    # ressuscitava a versão que `configurar_incidente` acabara de arquivar
    # (a Central passava a oferecer "Ativar" numa arquivada), e nada impedia
    # a mesma chamada de tirar o contrato ATIVO do estado `ativa`.
    if contrato.estado != "candidata":
        raise ContratoNfseTransicaoInvalidaError(
            "somente uma versão candidata pode receber o resultado da validação"
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


def ativar(contrato_id, usuario_id=None, *, agora=None):
    """Promove uma versão validada e resolve somente seus incidentes."""

    candidato = (
        ContratoNfse.query
        .filter(ContratoNfse.id == contrato_id)
        .with_for_update()
        .first()
    )
    if candidato is None:
        raise ContratoNfseNaoEncontradoError(
            "a versão de contrato solicitada não existe"
        )
    if candidato.estado != "validada":
        raise ContratoNfseTransicaoInvalidaError(
            "a versão precisa ser validada antes da ativação"
        )
    ativo = (
        ContratoNfse.query
        .filter(ContratoNfse.estado == "ativa")
        .order_by(ContratoNfse.versao.desc())
        .with_for_update()
        .first()
    )
    if ativo is None:
        raise ContratoNfseTransicaoInvalidaError(
            "não há versão ativa para validar a base da candidata"
        )
    incidentes = IncidenteContratoNfse.query.filter_by(
        contrato_candidato_id=contrato_id,
        estado="configurado",
    ).all()
    if not incidentes or any(
        incidente.contrato_base_id != ativo.id for incidente in incidentes
    ):
        raise ContratoNfseTransicaoInvalidaError(
            "a candidata foi criada sobre uma versão que não está mais ativa"
        )
    pendentes = (
        IncidenteContratoNfse.query
        .filter(
            IncidenteContratoNfse.contrato_base_id == ativo.id,
            IncidenteContratoNfse.estado.in_(("aberto", "configurado")),
        )
        .with_for_update()
        .all()
    )
    if any(
        incidente.estado != "configurado"
        or incidente.contrato_candidato_id != candidato.id
        for incidente in pendentes
    ):
        raise ContratoNfseTransicaoInvalidaError(
            "a candidata não cobre todos os incidentes pendentes da versão ativa"
        )
    agora = agora or utcnow_naive()
    ativo.estado = "arquivada"
    outras = ContratoNfse.query.filter(
        ContratoNfse.id != candidato.id,
        ContratoNfse.estado.in_(("candidata", "validada")),
    ).all()
    for outra in outras:
        outra.estado = "arquivada"
    candidato.estado = "ativa"
    candidato.ativado_em = agora
    candidato.ativado_por_id = usuario_id
    for incidente in incidentes:
        incidente.estado = "resolvido"
        incidente.resolvido_em = agora
        incidente.resolvido_por_id = usuario_id
    try:
        db.session.commit()
    except Exception as exc:
        _persistencia_falhou("ativar_contrato_nfse", exc)
    try:
        auditoria.registrar(
            "nfse.contrato.ativar",
            alvo_tipo="contrato_nfse",
            alvo_id=candidato.id,
            detalhe=(
                f"contrato_id={candidato.id};usuario_id={usuario_id};"
                f"incidentes_resolvidos={len(incidentes)}"
            ),
        )
    except Exception as exc:
        log_event(
            "nfse_contrato_auditoria_falhou",
            level="WARNING",
            contrato_id=candidato.id,
            error_type=type(exc).__name__,
        )
    return candidato


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


def _resumo_recomendacao(recomendacao):
    if recomendacao is None:
        return None
    return {
        "chave_observada": recomendacao.chave_observada,
        "confianca": recomendacao.confianca,
        "evidencias": list(recomendacao.evidencias),
        "candidatos": list(recomendacao.candidatos),
        "ambigua": bool(recomendacao.ambigua),
        "inequivoca": bool(recomendacao.inequivoca),
    }


def _resumo_incidente(incidente, recomendacao=None):
    campo = {
        "chave_esperada": incidente.chave_esperada,
        "chave_observada": incidente.chave_observada,
        "rotulo": incidente.rotulo,
        "tipo": incidente.tipo_controle,
        "interacao": incidente.interacao,
        "obrigatorio": incidente.obrigatorio,
    }
    resumo = {
        "id": incidente.id,
        "etapa": incidente.etapa,
        "tipo": incidente.tipo,
        "severidade": incidente.severidade,
        "estado": incidente.estado,
        # O desfazer da Central precisa saber qual candidata descartar; sem
        # isto o botão nascia sem alvo e não fazia nada, em silêncio.
        "contrato_candidato_id": incidente.contrato_candidato_id,
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
    if recomendacao is not None:
        resumo["recomendacao"] = _resumo_recomendacao(recomendacao)
    return resumo


def _estado_visual(ativo, incidentes):
    """Traduz para a tela o mesmo fato que fecha o gate do automático.

    `bloqueado` é exatamente o que `validar_contrato_automatico` recusa: versão
    não elegível, ou qualquer incidente pendente. Havia divergência aqui — as
    cópias de tela chamavam de "aviso" um incidente pendente `informativa` que
    o servidor já tratava como bloqueio.
    """

    if ativo is None:
        return "desconhecido"
    if not ativo.elegivel_automatico or incidentes:
        return "bloqueado"
    return "compativel"


def estado_painel():
    """Serializa o estado persistido sem dados da nota ou do DOM."""

    ativo = contrato_ativo()
    candidatos = (
        ContratoNfse.query
        .filter(ContratoNfse.estado.in_(("candidata", "validada")))
        .order_by(ContratoNfse.versao.desc())
        .all()
    )
    # Só o que está pendente — o mesmo conjunto que fecha o gate do automático.
    # Sem o filtro, incidente resolvido ou descartado fica na Central para
    # sempre, e o painel deixa de dizer o que ainda precisa de decisão.
    incidentes = _ordenar_por_tela(
        IncidenteContratoNfse.query
        .filter(
            IncidenteContratoNfse.contrato_base_id == ativo.id,
            IncidenteContratoNfse.estado.in_(("aberto", "configurado")),
        )
        .all()
    )
    recomendacoes = {
        (item.etapa, item.chave_esperada): item
        for item in _recomendacoes_incidentes(incidentes)
    }
    resumos = [
        _resumo_incidente(
            item, recomendacoes.get((item.etapa, item.chave_esperada)),
        )
        for item in incidentes
    ]
    return {
        "ativo": _resumo_contrato(ativo),
        # Fonte única do estado visual. A regra vivia em quatro lugares — este
        # painel, o Jinja da primeira pintura, `estadoVisual` e
        # `contratoPermiteAutomatico` — e as cópias já discordavam: a faixa
        # dizia "aviso" ao lado do rádio do automático desabilitado, sem
        # explicação. Quem decide é `validar_contrato_automatico`; aqui só se
        # traduz o mesmo fato para a tela.
        "estado_visual": _estado_visual(ativo, incidentes),
        "candidatas": [_resumo_contrato(item) for item in candidatos],
        "incidentes": resumos,
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
        _resumo_incidente(item) for item in _ordenar_por_tela(contrato.incidentes)
    ]
    return dados


__all__ = [
    "CONTRATO_INICIAL",
    "CampoContratoDesconhecidoError",
    "CampoExecucaoNfse",
    "ConfiguracaoContratoInvalidaError",
    "ContratoNfseNaoElegivelError",
    "ContratoNfseTransicaoInvalidaError",
    "ContratoExecucaoNfse",
    "ContratoNfseError",
    "ContratoNfseNaoEncontradoError",
    "OpcaoExecucaoNfse",
    "Observacao",
    "PersistenciaContratoError",
    "carregar_execucao",
    "contrato_ativo",
    "configurar_incidente",
    "contrato_inicial_execucao",
    "ativar",
    "detalhe_contrato",
    "estado_painel",
    "fontes_disponiveis",
    "garantir_contrato_inicial",
    "observar",
    "registrar_validacao",
    "registrar_incidentes",
    "recomendacao_incidente",
    "resolver_valor",
    "validar_contrato_automatico",
]
