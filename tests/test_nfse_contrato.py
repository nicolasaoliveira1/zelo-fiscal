"""Serviço persistente do contrato adaptativo, com dados sintéticos."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import datetime
from threading import Barrier
from types import SimpleNamespace

import pytest

from app import db
from app.automation import nfse as automacao_nfse
from app.automation import nfse_recon as automacao_recon
from app.automation.nfse_recon import OpcaoInventariada
from app.models import IncidenteContratoNfse
from app.services import nfse_contrato
from app.services.nfse_drift import CampoComparavel, Diferenca


def _diferenca():
    observado = CampoComparavel(
        chave_semantica="campo.novo",
        etapa="servico",
        rotulo="Campo sintético novo",
        tipo="select",
        interacao="chosen",
        obrigatorio=True,
        opcoes=(OpcaoInventariada("A", "Opção A", 0),),
    )
    return Diferenca(
        etapa="servico",
        tipo="controle_novo",
        severidade="critica",
        chave_observada="campo.novo",
        observado=observado,
        mensagem="Controle sintético requer decisão.",
    )


def _diferenca_com_chave(chave):
    diferenca = _diferenca()
    observado = replace(
        diferenca.observado,
        chave_semantica=chave,
        rotulo=f'Campo sintético {chave}',
    )
    return replace(
        diferenca,
        chave_observada=chave,
        observado=observado,
    )


def _ativar_contrato_com_avisos():
    ativo = nfse_contrato.garantir_contrato_inicial()
    incidente = nfse_contrato.registrar_incidentes(ativo.id, [_diferenca()])[0]
    candidata = nfse_contrato.configurar_incidente(
        incidente.id, {'origem': 'fixo', 'valor_fixo': 'A'}
    )
    resultado = automacao_nfse.ResultadoAutorrevisao(
        (),
        elegivel_automatico=False,
        avisos_assistidos=('Campo sintético sem leitor.',),
    )
    validada = nfse_contrato.registrar_validacao(candidata.id, None, resultado)
    return nfse_contrato.ativar(validada.id)


def test_garantir_contrato_inicial_eh_idempotente_e_nao_abre_portal(app, ids):
    with app.app_context():
        primeiro = nfse_contrato.garantir_contrato_inicial()
        segundo = nfse_contrato.garantir_contrato_inicial()

        assert segundo.id == primeiro.id
        assert primeiro.estado == "ativa"
        assert primeiro.elegivel_automatico is True
        assert len(primeiro.campos) == len(nfse_contrato.CONTRATO_INICIAL)
        assert [campo.ordem for campo in primeiro.campos] == list(range(len(primeiro.campos)))
        assert {campo.etapa for campo in primeiro.campos} == {
            "pessoas", "servico", "tributacao", "revisao"
        }


def test_carregar_execucao_materializa_opcoes_e_eh_frozen(app, ids):
    with app.app_context():
        contrato = nfse_contrato.garantir_contrato_inicial()
        execucao = nfse_contrato.carregar_execucao(contrato.id)
        campo = execucao.campo("Tomador.LocalDomicilio")

        assert execucao.contrato_id == contrato.id
        assert tuple(opcao.valor for opcao in campo.opcoes) == ("0", "1", "2")
        assert campo.origem == "fixo"
        with pytest.raises(FrozenInstanceError):
            execucao.versao = 99
        with pytest.raises(nfse_contrato.CampoContratoDesconhecidoError):
            execucao.campo("campo.inexistente")


def test_registrar_incidentes_faz_upsert_e_preserva_primeira_observacao(app, ids, monkeypatch):
    eventos = []
    monkeypatch.setattr(
        nfse_contrato,
        "log_event",
        lambda evento, **campos: eventos.append((evento, campos)),
    )
    monkeypatch.setattr(nfse_contrato.auditoria, "registrar", lambda *args, **kwargs: None)
    primeira_hora = datetime(2026, 8, 25, 12, 0)
    segunda_hora = datetime(2026, 8, 25, 12, 5)

    with app.app_context():
        contrato = nfse_contrato.garantir_contrato_inicial()
        primeira = nfse_contrato.registrar_incidentes(
            contrato.id, [_diferenca()], agora=primeira_hora
        )
        segunda = nfse_contrato.registrar_incidentes(
            contrato.id, [_diferenca()], agora=segunda_hora
        )
        incidente = db.session.get(IncidenteContratoNfse, primeira[0].id)

        assert len(primeira) == len(segunda) == 1
        assert segunda[0].id == primeira[0].id
        assert incidente.primeira_observacao_em == primeira_hora
        assert incidente.ultima_observacao_em == segunda_hora
        assert incidente.observacoes == 2
        assert len(incidente.opcoes) == 1
        assert {"campo.novo", "Controle sintético"}.isdisjoint(
            {eventos[-1][1].get("valor"), eventos[-1][1].get("rotulo")}
        )
        assert set(eventos[-1][1]) == {
            "contrato_id", "incidente_id", "etapa", "tipo", "assinatura"
        }


def test_observacoes_concorrentes_nao_perdem_contagem_mysql(app, ids, monkeypatch):
    with app.app_context():
        if db.engine.dialect.name != 'mysql':
            pytest.skip('a contenção real por linha é validada no gate MySQL')
        contrato_id = nfse_contrato.garantir_contrato_inicial().id
    monkeypatch.setattr(nfse_contrato.auditoria, 'registrar', lambda *a, **k: None)
    barreira = Barrier(2)

    def observar():
        with app.app_context():
            barreira.wait()
            resultado = nfse_contrato.registrar_incidentes(
                contrato_id, [_diferenca()]
            )[0].id
            db.session.remove()
            return resultado

    with ThreadPoolExecutor(max_workers=2) as executor:
        ids_incidentes = list(executor.map(lambda _indice: observar(), range(2)))

    with app.app_context():
        incidente = db.session.get(IncidenteContratoNfse, ids_incidentes[0])
        assert len(set(ids_incidentes)) == 1
        assert incidente.observacoes == 2


def test_comparacao_desconhecida_nao_persiste_incidente(app, ids):
    with app.app_context():
        contrato = nfse_contrato.garantir_contrato_inicial()
        resultado = type("Resultado", (), {"diferencas": (), "compatibilidade": "desconhecida"})()

        assert nfse_contrato.registrar_incidentes(contrato.id, resultado) == []
        assert IncidenteContratoNfse.query.count() == 0


def test_falha_de_persistencia_levanta_bloqueio_e_limpa_transacao(app, ids, monkeypatch):
    with app.app_context():
        contrato = nfse_contrato.garantir_contrato_inicial()

        def falhar_commit():
            raise RuntimeError("falha sintética")

        monkeypatch.setattr(db.session, "commit", falhar_commit)
        with pytest.raises(nfse_contrato.PersistenciaContratoError):
            nfse_contrato.registrar_incidentes(contrato.id, [_diferenca()])

        assert not db.session().in_transaction()


def test_fontes_disponiveis_e_resolver_valor_usam_catalogo_fechado():
    nota = SimpleNamespace(
        documento="DOCUMENTO-SINTÉTICO",
        valor_final=123,
        descricao_servico="Descrição sintética",
        competencia="08/2026",
    )
    config = SimpleNamespace(
        regime_apuracao_sn="1",
        municipio_servico_codigo="0000000",
        municipio_servico_nome="Município sintético",
        codigo_tributacao="00.00.00",
        item_nbs="000000000",
        piscofins_situacao="0",
        piscofins_tipo_retencao="0",
        descricao_template="Descrição {competencia}",
    )
    regra = {"origem": "nota", "fonte": "documento"}

    catalogo = nfse_contrato.fontes_disponiveis()

    assert {item["origem"] for item in catalogo} == {
        "fixo", "nota", "derivado", "configuracao", "padrao_portal", "intocavel"
    }
    assert nfse_contrato.resolver_valor(regra, nota, config, datetime(2026, 8, 25)) == (
        "DOCUMENTO-SINTÉTICO"
    )
    assert nfse_contrato.resolver_valor(
        {"origem": "fixo", "valor_fixo": "A"}, nota, config, None
    ) == "A"
    assert nfse_contrato.resolver_valor(
        {"origem": "padrao_portal", "fonte": None}, nota, config, None
    ) is None
    with pytest.raises(nfse_contrato.ConfiguracaoContratoInvalidaError):
        nfse_contrato.resolver_valor(
            {"origem": "nao_permitida", "fonte": "__import__"},
            nota,
            config,
            None,
        )


def test_configurar_incidente_copia_ativo_e_altera_somente_campo_coberto(
    app, ids, monkeypatch
):
    monkeypatch.setattr(nfse_contrato.auditoria, "registrar", lambda *args, **kwargs: None)
    with app.app_context():
        ativo = nfse_contrato.garantir_contrato_inicial()
        campos_ativos = {
            campo.chave_semantica: (campo.origem, campo.fonte, campo.valor_fixo)
            for campo in ativo.campos
        }
        incidente = nfse_contrato.registrar_incidentes(
            ativo.id, [_diferenca()], agora=datetime(2026, 8, 25, 12, 0)
        )[0]

        candidato = nfse_contrato.configurar_incidente(
            incidente.id,
            {"origem": "fixo", "valor_fixo": "A"},
            usuario_id=None,
        )
        db.session.expire_all()
        ativo_depois = db.session.get(type(ativo), ativo.id)

        assert candidato.estado == "candidata"
        assert candidato.elegivel_automatico is False
        assert candidato.id != ativo.id
        assert any(campo.chave_semantica == "campo.novo" for campo in candidato.campos)
        assert {
            campo.chave_semantica: (campo.origem, campo.fonte, campo.valor_fixo)
            for campo in ativo_depois.campos
        } == campos_ativos
        assert incidente.estado == "configurado"
        assert incidente.contrato_candidato_id == candidato.id


def test_configuracoes_sucessivas_formam_candidata_cumulativa(app, ids, monkeypatch):
    monkeypatch.setattr(nfse_contrato.auditoria, 'registrar', lambda *a, **k: None)
    with app.app_context():
        ativo = nfse_contrato.garantir_contrato_inicial()
        primeiro, segundo = nfse_contrato.registrar_incidentes(
            ativo.id,
            [_diferenca_com_chave('campo.novo.a'), _diferenca_com_chave('campo.novo.b')],
        )

        candidata_a = nfse_contrato.configurar_incidente(
            primeiro.id, {'origem': 'fixo', 'valor_fixo': 'A'}
        )
        candidata_b = nfse_contrato.configurar_incidente(
            segundo.id, {'origem': 'fixo', 'valor_fixo': 'A'}
        )
        db.session.refresh(candidata_a)
        db.session.refresh(primeiro)

        assert candidata_a.estado == 'arquivada'
        assert primeiro.contrato_candidato_id == candidata_b.id
        assert segundo.contrato_candidato_id == candidata_b.id
        assert {'campo.novo.a', 'campo.novo.b'} <= {
            campo.chave_semantica for campo in candidata_b.campos
        }
        historico = {
            item['id']: item for item in nfse_contrato.estado_painel()['versoes']
        }
        assert historico[candidata_a.id]['intermediaria'] is True
        assert historico[ativo.id]['intermediaria'] is False
        with pytest.raises(nfse_contrato.ContratoNfseNaoElegivelError):
            nfse_contrato.validar_contrato_automatico(ativo.id)


def test_ativacao_recusa_candidata_que_nao_cobre_incidente_novo(
    app, ids, monkeypatch
):
    monkeypatch.setattr(nfse_contrato.auditoria, 'registrar', lambda *a, **k: None)
    with app.app_context():
        ativo = nfse_contrato.garantir_contrato_inicial()
        primeiro = nfse_contrato.registrar_incidentes(
            ativo.id, [_diferenca_com_chave('campo.novo.a')]
        )[0]
        candidata = nfse_contrato.configurar_incidente(
            primeiro.id, {'origem': 'fixo', 'valor_fixo': 'A'}
        )
        nfse_contrato.registrar_incidentes(
            ativo.id, [_diferenca_com_chave('campo.novo.b')]
        )
        candidata.estado = 'validada'
        db.session.commit()

        with pytest.raises(
            nfse_contrato.ContratoNfseTransicaoInvalidaError,
            match='todos os incidentes',
        ):
            nfse_contrato.ativar(candidata.id)

        assert nfse_contrato.contrato_ativo().id == ativo.id


def test_recomendacao_persistida_no_painel_remapeia_seletor_e_cobre_o_par(
    app, ids, monkeypatch
):
    monkeypatch.setattr(nfse_contrato.auditoria, 'registrar', lambda *a, **k: None)
    with app.app_context():
        ativo = nfse_contrato.garantir_contrato_inicial()
        esperado = CampoComparavel(
            chave_semantica='ServicoPrestado_Descricao',
            etapa='servico',
            rotulo='Descrição do serviço',
            tipo='textarea',
            interacao='textarea',
            obrigatorio=True,
        )
        observado = replace(
            esperado,
            chave_semantica='ServicoPrestado_DescricaoNova',
        )
        removido, novo = nfse_contrato.registrar_incidentes(
            ativo.id,
            [
                Diferenca(
                    etapa='servico',
                    tipo='controle_removido',
                    severidade='critica',
                    chave_esperada=esperado.chave_semantica,
                    esperado=esperado,
                    mensagem='Controle sintético anterior removido.',
                ),
                Diferenca(
                    etapa='servico',
                    tipo='controle_novo',
                    severidade='critica',
                    chave_observada=observado.chave_semantica,
                    observado=observado,
                    mensagem='Controle sintético substituto observado.',
                ),
            ],
        )

        resumo = next(
            item
            for item in nfse_contrato.estado_painel()['incidentes']
            if item['id'] == removido.id
        )
        assert resumo['recomendacao']['inequivoca'] is True
        assert resumo['recomendacao']['chave_observada'] == observado.chave_semantica

        candidata = nfse_contrato.configurar_incidente(
            removido.id,
            {'origem': 'nota', 'fonte': 'descricao'},
            chave_observada=observado.chave_semantica,
            confirmar_recomendacao=True,
        )
        campo = next(
            item
            for item in candidata.campos
            if item.chave_semantica == esperado.chave_semantica
        )

        assert campo.seletor_tipo == 'css'
        assert observado.chave_semantica in campo.seletor
        assert removido.contrato_candidato_id == candidata.id
        assert novo.contrato_candidato_id == candidata.id
        assert removido.estado == novo.estado == 'configurado'


def test_validacao_sem_leitor_ativa_somente_para_modos_assistidos(
    app, ids, monkeypatch
):
    monkeypatch.setattr(nfse_contrato.auditoria, 'registrar', lambda *a, **k: None)
    with app.app_context():
        ativada = _ativar_contrato_com_avisos()

        # A frase base continua, agora seguida do MOTIVO: sem nomear a lacuna,
        # "permite somente modos assistidos" não dizia onde consertar.
        assert ativada.erro_validacao.startswith(
            'a revisão permite somente modos assistidos')
        assert 'Campo sintético sem leitor.' in ativada.erro_validacao
        assert ativada.estado == 'ativa'
        assert ativada.elegivel_automatico is False


def test_operador_pode_liberar_e_revogar_avisos_da_versao_ativa(
    app, ids, monkeypatch
):
    eventos = []
    monkeypatch.setattr(
        nfse_contrato.auditoria,
        'registrar',
        lambda evento, **_dados: eventos.append(evento),
    )
    with app.app_context():
        ativa = _ativar_contrato_com_avisos()

        liberada = nfse_contrato.definir_liberacao_automatica(
            ativa.id, True
        )
        assert liberada.elegivel_automatico is True
        assert 'Campo sintético sem leitor.' in liberada.erro_validacao
        assert nfse_contrato.validar_contrato_automatico().id == ativa.id
        assert nfse_contrato.estado_painel()['ativo'][
            'liberacao_automatica_manual'
        ] is True

        revogada = nfse_contrato.definir_liberacao_automatica(
            ativa.id, False
        )
        assert revogada.elegivel_automatico is False
        assert eventos[-2:] == [
            'nfse.contrato.liberar_automatico',
            'nfse.contrato.revogar_automatico',
        ]


def test_incidente_novo_impede_liberar_avisos_da_versao_ativa(
    app, ids, monkeypatch
):
    monkeypatch.setattr(nfse_contrato.auditoria, 'registrar', lambda *a, **k: None)
    with app.app_context():
        ativa = _ativar_contrato_com_avisos()
        nfse_contrato.registrar_incidentes(
            ativa.id, [_diferenca_com_chave('campo.posterior')]
        )

        with pytest.raises(
            nfse_contrato.ContratoNfseTransicaoInvalidaError,
            match='incidentes pendentes',
        ):
            nfse_contrato.definir_liberacao_automatica(ativa.id, True)


def test_configurar_recusa_opcao_fixa_ausente_sem_criar_candidata(app, ids):
    with app.app_context():
        ativo = nfse_contrato.garantir_contrato_inicial()
        incidente = nfse_contrato.registrar_incidentes(
            ativo.id, [_diferenca()], agora=datetime(2026, 8, 25, 12, 0)
        )[0]

        with pytest.raises(nfse_contrato.ConfiguracaoContratoInvalidaError):
            nfse_contrato.configurar_incidente(
                incidente.id, {"origem": "fixo", "valor_fixo": "B"}
            )

        assert nfse_contrato.ContratoNfse.query.count() == 1
        assert incidente.estado == "aberto"


def test_padrao_portal_obrigatorio_fica_dependente_de_prova(app, ids, monkeypatch):
    monkeypatch.setattr(nfse_contrato.auditoria, "registrar", lambda *args, **kwargs: None)
    with app.app_context():
        ativo = nfse_contrato.garantir_contrato_inicial()
        diferenca = _diferenca()
        diferenca = type(diferenca)(
            **{**diferenca.__dict__, "chave_observada": "campo.padrao"}
        )
        incidente = nfse_contrato.registrar_incidentes(ativo.id, [diferenca])[0]
        candidato = nfse_contrato.configurar_incidente(
            incidente.id, {"origem": "padrao_portal", "fonte": None}
        )

        assert candidato.elegivel_automatico is False
        assert candidato.erro_validacao == "aguarda prova assistida de avanço"


def test_falha_de_auditoria_nao_desfaz_candidata(app, ids, monkeypatch):
    with app.app_context():
        ativo = nfse_contrato.garantir_contrato_inicial()
        incidente = nfse_contrato.registrar_incidentes(ativo.id, [_diferenca()])[0]

        def falhar_auditoria(*args, **kwargs):
            raise RuntimeError("falha sintética")

        monkeypatch.setattr(nfse_contrato.auditoria, "registrar", falhar_auditoria)
        candidato = nfse_contrato.configurar_incidente(
            incidente.id, {"origem": "fixo", "valor_fixo": "A"}
        )

        assert db.session.get(type(candidato), candidato.id) is not None


def _diferenca_de_controle(interacao, chave="Campo.Novo"):
    campo = CampoComparavel(
        chave_semantica=chave, etapa="pessoas",
        rotulo="Informar série e número da DPS", tipo="checkbox",
        interacao=interacao, obrigatorio=True,
        opcoes=(OpcaoInventariada("true", "Informar série e número da DPS", 0),),
    )
    return Diferenca(
        etapa="pessoas", tipo="controle_novo", severidade="critica",
        chave_observada=chave, observado=campo,
        mensagem="O portal passou a exigir um controle que não existe no contrato.",
    )


def _incidente_de_controle(app, *, interacao, chave="Campo.Novo"):
    with app.app_context():
        contrato = nfse_contrato.garantir_contrato_inicial()
        incidente, _ = nfse_contrato._registrar_uma_diferenca(
            contrato.id, _diferenca_de_controle(interacao, chave),
            datetime(2026, 8, 26, 12, 0, 0),
        )
        return incidente.id


@pytest.mark.parametrize("origem", ["intocavel", "padrao_portal"])
def test_nao_tocar_dispensa_adaptador_do_controle(app, ids, origem):
    """O executor deriva o adaptador da própria origem quando ela não interage:
    exigir um adaptador de DOM barrava a decisão de justamente NÃO mexer."""

    incidente_id = _incidente_de_controle(app, interacao="acao")

    with app.app_context():
        candidata = nfse_contrato.configurar_incidente(incidente_id, {"origem": origem})
        campo = next(c for c in candidata.campos if c.chave_semantica == "Campo.Novo")
        assert campo.interacao == origem
        assert campo.interacao in automacao_nfse._ADAPTADORES_CONTRATO


def test_toda_interacao_que_o_inventario_produz_tem_adaptador():
    """Guarda que faltava: o inventário deriva a interação (`texto`), e o mapa
    só conhecia o tipo cru do DOM (`text`). Configurar qualquer campo de texto
    falhava com "não possui adaptador seguro"."""

    produzidas = {
        automacao_recon._interacao("input", tipo, (), revela)
        for tipo in ("text", "date", "number", "email", "tel", "radio", "checkbox")
        for revela in (False, True)
    } | {
        automacao_recon._interacao("textarea", "textarea", ()),
        automacao_recon._interacao("select", "select", ()),
        automacao_recon._interacao("select", "select", ("form-chosen",)),
        automacao_recon._interacao("select", "select", ("select2-hidden-accessible",)),
    }

    for interacao in produzidas:
        assert nfse_contrato._adaptador_observado(interacao) in (
            automacao_nfse._ADAPTADORES_CONTRATO
        ), interacao


def test_reobservar_nao_reabre_incidente_ja_configurado(app, ids):
    """Uma emissão assistida entre configurar e validar reobserva a mesma
    diferença. Reabrir quebraria a cobertura da candidata e obrigaria o
    operador a configurar tudo de novo sem entender por quê."""

    incidente_id = _incidente_de_controle(app, interacao='texto', chave='Campo.Reobs')

    with app.app_context():
        candidata = nfse_contrato.configurar_incidente(
            incidente_id, {'origem': 'intocavel'}
        )
        contrato_id = nfse_contrato.contrato_ativo().id
        incidente = db.session.get(IncidenteContratoNfse, incidente_id)
        observacoes_antes = incidente.observacoes

        nfse_contrato._registrar_uma_diferenca(
            contrato_id, _diferenca_de_controle('texto', 'Campo.Reobs'),
            datetime(2026, 8, 26, 13, 0, 0),
        )

        db.session.refresh(incidente)
        assert incidente.estado == 'configurado'
        assert incidente.contrato_candidato_id == candidata.id
        assert incidente.observacoes > observacoes_antes


def test_incidente_da_validacao_e_gravado_contra_a_versao_ativa(app, ids):
    """Durante a validação o preenchimento carrega a CANDIDATA. Gravar contra
    ela escondia o incidente: a Central lista os da ativa."""

    incidente_id = _incidente_de_controle(app, interacao='texto', chave='Campo.Base')

    with app.app_context():
        candidata = nfse_contrato.configurar_incidente(
            incidente_id, {'origem': 'intocavel'}
        )
        ativo_id = nfse_contrato.contrato_ativo().id
        assert candidata.id != ativo_id

        novos = nfse_contrato.registrar_incidentes(
            candidata.id,
            type('R', (), {
                'diferencas': (_diferenca_de_controle('texto', 'Campo.Drift'),),
            })(),
        )

        assert [item.contrato_base_id for item in novos] == [ativo_id]
        chaves = [
            item['campo']['chave_observada']
            for item in nfse_contrato.estado_painel()['incidentes']
        ]
        assert 'Campo.Drift' in chaves


def test_descartar_incidentes_tira_do_gate_sem_alterar_contrato(app, ids):
    """Recon defeituosa entulha a Central: o incidente persiste e nada o expira."""

    incidente_id = _incidente_de_controle(app, interacao='texto', chave='Campo.Lixo')

    with app.app_context():
        assert nfse_contrato.estado_painel()['incidentes']
        contrato_id = nfse_contrato.contrato_ativo().id

        descartados = nfse_contrato.descartar_incidentes(contrato_id)

        assert descartados >= 1
        assert nfse_contrato.estado_painel()['incidentes'] == []
        assert nfse_contrato.contrato_ativo().id == contrato_id
        assert incidente_id is not None


def test_limite_da_chave_sai_da_largura_da_coluna(app, ids):
    """O seletor gerado tem 17 + 2×len(chave) e a coluna é String(200). O
    SQLite ignora largura de VARCHAR e o MySQL impõe: um estouro passa no gate
    rápido e explode em produção (lição 3 do CLAUDE.md)."""

    limite = nfse_contrato._LIMITE_CHAVE_SELETOR
    assert 17 + 2 * limite <= nfse_contrato._LARGURA_SELETOR

    no_limite = nfse_contrato._seletor_css_identidade("c" * limite)
    assert len(no_limite) <= nfse_contrato._LARGURA_SELETOR

    with pytest.raises(nfse_contrato.ConfiguracaoContratoInvalidaError):
        nfse_contrato._seletor_css_identidade("c" * (limite + 1))

    # A conta do limite vale para chave sem caractere que escapa. Aspa e barra
    # DOBRAM ao escapar: uma chave dentro do limite pode gerar seletor fora
    # dele, e quem tem de recusar e a medida do seletor pronto.
    com_aspas = 'c' * (limite - 4) + '""""'
    assert len(com_aspas) <= limite
    with pytest.raises(nfse_contrato.ConfiguracaoContratoInvalidaError):
        nfse_contrato._seletor_css_identidade(com_aspas)


def test_tipo_e_interacao_do_incidente_cabem_na_coluna(app, ids):
    """`tipo_controle` e `interacao` são String(30)."""

    with app.app_context():
        contrato = nfse_contrato.garantir_contrato_inicial()
        diferenca = _diferenca_de_controle("texto", "Campo.Largo")
        observado = replace(
            diferenca.observado, tipo="t" * 80, interacao="i" * 80,
        )
        incidente, _ = nfse_contrato._registrar_uma_diferenca(
            contrato.id, replace(diferenca, observado=observado),
            datetime(2026, 8, 26, 12, 0, 0),
        )

        assert len(incidente.tipo_controle) <= 30
        assert len(incidente.interacao) <= 30


def test_so_uma_candidata_recebe_o_resultado_da_validacao(app, ids):
    """Sem esta guarda, uma validação em curso ressuscitava a versão que
    `configurar_incidente` acabara de arquivar — a Central passava a oferecer
    "Ativar" numa arquivada — e nada impedia a mesma chamada de tirar o
    contrato ATIVO do estado `ativa`."""

    incidente_id = _incidente_de_controle(app, interacao='texto', chave='Campo.Val')

    with app.app_context():
        primeira = nfse_contrato.configurar_incidente(
            incidente_id, {'origem': 'intocavel'}
        )
        ativo_id = nfse_contrato.contrato_ativo().id

        # A ativa nunca recebe resultado de validação.
        with pytest.raises(nfse_contrato.ContratoNfseTransicaoInvalidaError):
            nfse_contrato.registrar_validacao(ativo_id, None, ())

        # Uma candidata arquivada no meio do caminho também não.
        nfse_contrato.descartar_candidata(primeira.id)
        with pytest.raises(nfse_contrato.ContratoNfseTransicaoInvalidaError):
            nfse_contrato.registrar_validacao(primeira.id, None, ())


def test_campo_novo_entra_no_fim_da_propria_etapa_sem_colidir(app, ids):
    """`ordem` é a sequência de APLICAÇÃO do contrato inteiro, não a posição na
    tela. Copiar `ordem_pagina` — um índice por etapa — para dentro dela
    produzia valor duplicado e posição sem sentido."""

    incidente_id = _incidente_de_controle(app, interacao='texto', chave='Campo.Ordem')

    with app.app_context():
        incidente = db.session.get(IncidenteContratoNfse, incidente_id)
        # Posição na tela pequena de propósito: é o caso que colidia.
        incidente.ordem_pagina = 2
        db.session.commit()
        antes = nfse_contrato.carregar_execucao()
        ultima_de_pessoas = max(
            campo.ordem for campo in antes.campos if campo.etapa == 'pessoas'
        )

        candidata = nfse_contrato.configurar_incidente(
            incidente_id, {'origem': 'intocavel'}
        )

        ordens = [campo.ordem for campo in candidata.campos]
        assert len(ordens) == len(set(ordens)), 'nenhuma ordem duplicada'
        novo = next(c for c in candidata.campos if c.chave_semantica == 'Campo.Ordem')
        assert novo.ordem == ultima_de_pessoas + 1
        assert novo.etapa == 'pessoas'
        # E continua depois de todos os campos de Pessoas que já existiam.
        assert all(
            campo.ordem < novo.ordem
            for campo in candidata.campos
            if campo.etapa == 'pessoas' and campo.chave_semantica != 'Campo.Ordem'
        )


def test_segunda_versao_ativa_esbarra_no_banco(app, ids):
    """O invariante "só uma ativa" agora é do banco, não só do serviço.

    O `with_for_update()` de `ativar()` é no-op no SQLite — o dialeto descarta
    `FOR UPDATE` em silêncio — e SQLite é o padrão quando `DATABASE_URL` não
    está definida. A sentinela `ativa_unica` faz a segunda ativa esbarrar na
    constraint em qualquer um dos dois bancos.
    """

    from sqlalchemy.exc import IntegrityError

    from app.models import ContratoNfse

    with app.app_context():
        ativo = nfse_contrato.garantir_contrato_inicial()
        assert ativo.estado == 'ativa'

        intrusa = ContratoNfse(
            versao=ativo.versao + 1,
            estado='ativa',
            fingerprint='f' * 64,
        )
        db.session.add(intrusa)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_sentinela_acompanha_o_estado_sem_o_chamador_saber(app, ids):
    """Quem mantém a coluna é o listener: nenhum write de `estado` precisa
    lembrar de atualizar duas colunas, e é por isso que ela não vaza para os
    serviços."""

    from app.models import ContratoNfse

    with app.app_context():
        contrato = nfse_contrato.garantir_contrato_inicial()
        assert contrato.ativa_unica == 1

        contrato.estado = 'arquivada'
        db.session.commit()
        db.session.refresh(contrato)
        assert contrato.ativa_unica is None

        outra = ContratoNfse(
            versao=contrato.versao + 1, estado='ativa', fingerprint='a' * 64,
        )
        db.session.add(outra)
        db.session.commit()
        db.session.refresh(outra)
        assert outra.ativa_unica == 1


def test_erro_de_validacao_diz_o_que_reprovou_sem_dado_da_nota():
    """A contagem ia para o log e a coluna guardava sempre a mesma frase — que
    a interface nem mostrava. Para o operador a validação falhava sem sintoma.

    E o que a divergência cita da tela é dado de cliente: `erro_validacao` vive
    no CONTRATO, que sobrevive à nota e aparece na Central."""

    divergencias = [
        'O tomador na tela e 44.556.677/0001-86, e a nota e de 11.222.333/0001-81.',
        'O valor na tela e 1.234,56, e a nota e de 649,00.',
    ]
    resumo = nfse_contrato.resumo_das_divergencias(
        divergencias, ('44.556.677/0001-86', '649,00', 'Honorários contábeis'),
    )

    assert resumo.startswith('2 divergência(s) na revisão: ')
    # A frase sobrevive: é ela que diz QUAL conferência reprovou.
    assert 'O tomador na tela' in resumo
    assert 'O valor na tela' in resumo
    # Os números não sobrevivem — nem o que foi declarado sensível, nem o que
    # tem forma de documento e escapou da lista.
    assert '44.556.677/0001-86' not in resumo
    assert '11.222.333/0001-81' not in resumo
    assert '649,00' not in resumo


def test_resumo_da_validacao_cabe_na_coluna():
    """`erro_validacao` é String(500): o SQLite ignora largura de VARCHAR e o
    MySQL levanta DataError (lição 3)."""

    resumo = nfse_contrato.resumo_das_divergencias(['x' * 400] * 5)

    assert len(resumo) <= nfse_contrato._LARGURA_ERRO_VALIDACAO
    assert resumo.endswith('…')


def test_sem_divergencia_legivel_ainda_sobra_uma_frase():
    """Mascarar tudo não pode devolver string vazia: a coluna é o único sinal
    de que a validação reprovou."""

    assert nfse_contrato.resumo_das_divergencias(['', '   ']) != ''


def test_validacao_reprovada_grava_o_motivo_na_candidata(app, ids):
    from app.models import ContratoNfse

    with app.app_context():
        nfse_contrato.garantir_contrato_inicial()
        incidente_id = None
        candidata = None
        diferenca = _diferenca_de_controle('texto', 'Campo.Motivo')
        contrato = nfse_contrato.contrato_ativo()
        incidente, _ = nfse_contrato._registrar_uma_diferenca(
            contrato.id, diferenca, datetime(2026, 8, 27, 12, 0, 0),
        )
        db.session.commit()
        incidente_id = incidente.id
        candidata = nfse_contrato.configurar_incidente(
            incidente_id, {'origem': 'intocavel'}
        )

        nfse_contrato.registrar_validacao(
            candidata.id, None,
            ['O valor na tela e 1.234,56, e a nota e de 649,00.'],
            valores_sensiveis=('649,00',),
        )

        gravada = db.session.get(ContratoNfse, candidata.id)
        assert gravada.estado == 'candidata'
        assert '1 divergência(s) na revisão' in gravada.erro_validacao
        assert '649,00' not in gravada.erro_validacao


def test_editar_uma_linha_preserva_as_decisoes_das_outras(app, ids):
    """O botão da linha desfazia a candidata INTEIRA: corrigir um campo custava
    a configuração de todos os outros. Aconteceu de verdade."""

    with app.app_context():
        contrato = nfse_contrato.garantir_contrato_inicial()
        agora = datetime(2026, 8, 27, 12, 0, 0)
        primeiro, _ = nfse_contrato._registrar_uma_diferenca(
            contrato.id, _diferenca_de_controle('texto', 'Campo.Um'), agora,
        )
        segundo, _ = nfse_contrato._registrar_uma_diferenca(
            contrato.id, _diferenca_de_controle('texto', 'Campo.Dois'), agora,
        )
        db.session.commit()
        id_um, id_dois = primeiro.id, segundo.id

        nfse_contrato.configurar_incidente(id_um, {'origem': 'intocavel'})
        nfse_contrato.configurar_incidente(id_dois, {'origem': 'padrao_portal'})

        candidata = nfse_contrato.reabrir_incidente(id_um)

        # O desfeito volta a pedir decisão...
        assert db.session.get(IncidenteContratoNfse, id_um).estado == 'aberto'
        # ...e o outro continua configurado, com a MESMA decisão.
        assert db.session.get(IncidenteContratoNfse, id_dois).estado == 'configurado'
        chaves = {c.chave_semantica: c.origem for c in candidata.campos}
        assert chaves.get('Campo.Dois') == 'padrao_portal'
        assert 'Campo.Um' not in chaves


def test_editar_a_unica_decisao_nao_deixa_candidata(app, ids):
    with app.app_context():
        contrato = nfse_contrato.garantir_contrato_inicial()
        incidente, _ = nfse_contrato._registrar_uma_diferenca(
            contrato.id, _diferenca_de_controle('texto', 'Campo.Solo'),
            datetime(2026, 8, 27, 12, 0, 0),
        )
        db.session.commit()
        incidente_id = incidente.id
        nfse_contrato.configurar_incidente(incidente_id, {'origem': 'intocavel'})

        assert nfse_contrato.reabrir_incidente(incidente_id) is None
        assert db.session.get(IncidenteContratoNfse, incidente_id).estado == 'aberto'


def test_editar_recusa_incidente_que_nao_esta_configurado(app, ids):
    with app.app_context():
        contrato = nfse_contrato.garantir_contrato_inicial()
        incidente, _ = nfse_contrato._registrar_uma_diferenca(
            contrato.id, _diferenca_de_controle('texto', 'Campo.Aberto'),
            datetime(2026, 8, 27, 12, 0, 0),
        )
        db.session.commit()

        with pytest.raises(nfse_contrato.ContratoNfseTransicaoInvalidaError):
            nfse_contrato.reabrir_incidente(incidente.id)


def test_edicao_de_linha_nao_loga_como_descarte_do_operador(app, ids, monkeypatch):
    """`descartar_candidata` tem dois chamadores com significados opostos. Sem
    distinguir, editar uma linha logava "candidata descartada" em WARNING e
    parecia ter apagado a configuração inteira — foi o que gerou a dúvida."""

    eventos = []
    monkeypatch.setattr(
        nfse_contrato, 'log_event',
        lambda nome, **campos: eventos.append((nome, campos)),
    )

    with app.app_context():
        contrato = nfse_contrato.garantir_contrato_inicial()
        agora = datetime(2026, 8, 27, 12, 0, 0)
        um, _ = nfse_contrato._registrar_uma_diferenca(
            contrato.id, _diferenca_de_controle('texto', 'Campo.A'), agora)
        dois, _ = nfse_contrato._registrar_uma_diferenca(
            contrato.id, _diferenca_de_controle('texto', 'Campo.B'), agora)
        db.session.commit()
        id_um, id_dois = um.id, dois.id
        nfse_contrato.configurar_incidente(id_um, {'origem': 'intocavel'})
        nfse_contrato.configurar_incidente(id_dois, {'origem': 'intocavel'})

        eventos.clear()
        # A candidata anterior foi arquivada: a viva é a que `reabrir` devolve.
        candidata = nfse_contrato.reabrir_incidente(id_um)

        descartes = [c for nome, c in eventos if nome == 'nfse_candidata_descartada']
        assert descartes, 'o passo interno continua registrado'
        assert all(c['motivo'] == 'edicao_de_linha' for c in descartes)
        assert all(c.get('level') != 'WARNING' for c in descartes)

        # E o descarte pedido pelo operador continua sendo WARNING.
        eventos.clear()
        nfse_contrato.descartar_candidata(candidata.id)
        descartes = [c for nome, c in eventos if nome == 'nfse_candidata_descartada']
        assert descartes[0]['motivo'] == 'descarte'
        assert descartes[0]['level'] == 'WARNING'


def test_controle_ja_contratado_em_outra_etapa_e_recusado(app, ids):
    """O portal é ASP.NET: o MESMO input é `Valores.ValorServico` por `name` e
    `Valores_ValorServico` por `id`. Sem canonizar o ponto, as duas formas
    passam por controles diferentes — e o valor do serviço acabou contratado em
    duas etapas, deixando o seletor sem alvo único e o incidente oscilando
    entre "novo" e "removido"."""

    with app.app_context():
        contrato = nfse_contrato.garantir_contrato_inicial()
        ja_contratado = next(
            c for c in contrato.campos
            if c.chave_semantica == 'Valores_ValorServico'
        )
        diferenca = _diferenca_de_controle('texto', 'Valores.ValorServico')
        incidente, _ = nfse_contrato._registrar_uma_diferenca(
            contrato.id, diferenca, datetime(2026, 8, 27, 12, 0, 0),
        )
        db.session.commit()

        with pytest.raises(
            nfse_contrato.ConfiguracaoContratoInvalidaError
        ) as erro:
            nfse_contrato.configurar_incidente(
                incidente.id, {'origem': 'nota', 'fonte': 'valor_final'},
            )

        # A mensagem precisa dizer ONDE ele já está, senão o operador não tem
        # como saber que decisão tomar.
        assert ja_contratado.etapa in str(erro.value)


def test_controle_removido_explica_a_unica_saida(app, ids):
    """"Exige a decisão de não tocar" nomeava a regra sem dizer o que fazer nem
    por que as outras origens somem."""

    with app.app_context():
        contrato = nfse_contrato.garantir_contrato_inicial()
        alvo = next(
            c for c in contrato.campos
            if c.chave_semantica == 'ServicoPrestado_Descricao'
        )
        diferenca = Diferenca(
            etapa=alvo.etapa,
            tipo=nfse_contrato.CONTROLE_REMOVIDO,
            severidade='fiscal',
            chave_esperada=alvo.chave_semantica,
            esperado=CampoComparavel(
                chave_semantica=alvo.chave_semantica,
                etapa=alvo.etapa,
                rotulo=alvo.rotulo,
                tipo=alvo.tipo,
                interacao=alvo.interacao,
                obrigatorio=alvo.obrigatorio,
            ),
            observado=None,
            mensagem='O controle esperado não foi encontrado.',
        )
        incidente, _ = nfse_contrato._registrar_uma_diferenca(
            contrato.id, diferenca, datetime(2026, 8, 27, 12, 0, 0),
        )
        db.session.commit()

        with pytest.raises(
            nfse_contrato.ConfiguracaoContratoInvalidaError
        ) as erro:
            nfse_contrato.configurar_incidente(
                incidente.id, {'origem': 'nota', 'fonte': 'descricao'},
            )

        mensagem = str(erro.value)
        assert 'Não tocar' in mensagem
        assert 'não está mais na tela' in mensagem

        # E a decisão possível continua funcionando.
        candidata = nfse_contrato.configurar_incidente(
            incidente.id, {'origem': 'intocavel'},
        )
        assert all(
            c.chave_semantica != alvo.chave_semantica for c in candidata.campos
        )


def test_candidata_assistida_diz_qual_lacuna_a_prende(app, ids):
    """Os avisos assistidos eram calculados e jogados fora: a candidata dizia
    "permite somente modos assistidos" sem dizer por quê, e a lacuna do
    contrato ficava invisível, portanto permanente."""

    from app.models import ContratoNfse

    class _Resultado(list):
        elegivel_automatico = False
        avisos_assistidos = (
            'O contrato não declara onde conferir "Tomador_Nome" na revisão; '
            'confira à vista.',
        )

    with app.app_context():
        contrato = nfse_contrato.garantir_contrato_inicial()
        incidente, _ = nfse_contrato._registrar_uma_diferenca(
            contrato.id, _diferenca_de_controle('texto', 'Campo.Assistido'),
            datetime(2026, 8, 27, 12, 0, 0),
        )
        db.session.commit()
        candidata = nfse_contrato.configurar_incidente(
            incidente.id, {'origem': 'intocavel'})

        nfse_contrato.registrar_validacao(candidata.id, None, _Resultado())

        gravada = db.session.get(ContratoNfse, candidata.id)
        assert gravada.estado == 'validada'
        assert 'somente modos assistidos' in gravada.erro_validacao
        assert 'Tomador_Nome' in gravada.erro_validacao
        assert len(gravada.erro_validacao) <= nfse_contrato._LARGURA_ERRO_VALIDACAO
