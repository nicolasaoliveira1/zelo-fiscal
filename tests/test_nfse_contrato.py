"""Serviço persistente do contrato adaptativo, com dados sintéticos."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import datetime
from threading import Barrier
from types import SimpleNamespace

import pytest

from app import db
from app.automation import nfse as automacao_nfse
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
        ativo = nfse_contrato.garantir_contrato_inicial()
        incidente = nfse_contrato.registrar_incidentes(
            ativo.id, [_diferenca()]
        )[0]
        candidata = nfse_contrato.configurar_incidente(
            incidente.id, {'origem': 'fixo', 'valor_fixo': 'A'}
        )
        resultado = automacao_nfse.ResultadoAutorrevisao(
            (),
            elegivel_automatico=False,
            avisos_assistidos=('Campo sintético sem leitor.',),
        )

        validada = nfse_contrato.registrar_validacao(
            candidata.id, None, resultado
        )
        ativada = nfse_contrato.ativar(validada.id)

        assert validada.erro_validacao == 'a revisão permite somente modos assistidos'
        assert ativada.estado == 'ativa'
        assert ativada.elegivel_automatico is False


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
