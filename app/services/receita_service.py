"""Aplica os dados da Receita sobre o cadastro da empresa (spec 08).

Camada de DOMINIO: decide o que preenche, o que apenas sinaliza e o que nunca
se toca. A rede fica em `receita_client`; aqui nao ha HTTP.

Duas regras mandam neste modulo:

1. **`Empresa.nome` NUNCA e escrito** (DATA-01.8). Ele e a chave de busca da
   pasta no drive de rede (`file_manager.encontrar_pasta_empresa`); trocar pelo
   `razao_social` da Receita quebraria o casamento com as pastas existentes. A
   razao social vive em `DadosReceita.razao_social`, coluna propria.
2. **Campo ja preenchido que difere NAO e sobrescrito** (DATA-01.9), so
   sinalizado para o operador aceitar. A automacao municipal casa cidade por
   string, entao trocar a cidade em silencio quebraria o "Abrir Site" sem
   ninguem perceber.
"""
import time
from datetime import datetime, timedelta

from app import db
from app.models import DadosReceita, Empresa
from app.services import auditoria, receita_client
from app.services.cidade_canonica import canonicalizar
from app.services.execution_logger import log_event
from app.utils import normalizar_cidade

# Situacoes que valem como "empresa viva". Tudo fora daqui (BAIXADA, SUSPENSA,
# INAPTA, NULA) sai do lote automatico.
SITUACAO_ATIVA = 'ATIVA'

# Colunas da Empresa que a Receita tambem conhece. `nome` esta fora de
# proposito — ver regra 1 no topo. A chave e o nome do campo na Empresa; o valor
# extrai e normaliza o correspondente do DTO.
_CAMPOS_CADASTRO = {
    'cidade': lambda dto: canonicalizar(dto.municipio) if dto.municipio else None,
    'estado': lambda dto: (dto.uf or '').strip().upper() or None,
}

# Comparadores por campo: sem eles, 'Porto Alegre' x 'PORTO ALEGRE' viraria uma
# divergencia falsa em toda empresa da carteira.
_COMPARADORES = {
    'cidade': normalizar_cidade,
    'estado': lambda valor: (valor or '').strip().upper(),
}

# Campos do DTO copiados 1:1 para DadosReceita (todos menos os de controle).
_CAMPOS_ESPELHO = (
    'situacao', 'situacao_data', 'situacao_motivo',
    'razao_social', 'nome_fantasia', 'data_inicio_atividade', 'porte',
    'natureza_juridica', 'matriz_filial',
    'logradouro', 'numero', 'complemento', 'bairro', 'cep',
    'municipio_ibge', 'municipio', 'uf',
    'cnae_fiscal', 'cnae_descricao', 'opcao_simples', 'opcao_simples_data',
    'fonte',
)


def _normalizar_situacao(valor):
    from app.file_manager import remover_acentos
    return remover_acentos((valor or '').strip()).upper()


def situacao_ativa(situacao):
    """A situacao cadastral conta como "empresa viva"? (regra unica, sobre a string)

    `None`/'' contam como ATIVA: "ainda nao classificada" e diferente de "morta".
    Sem isso, ligar a feature tiraria a carteira inteira do lote automatico ate o
    recheck terminar de rodar.

    E a MESMA funcao usada pelo filtro de lote (`batch_engine.calc_targets`), que
    recebe a situacao como coluna do join. Uma regra em Python, nenhuma copia em
    SQL que pudesse divergir dela.
    """
    if not situacao:
        return True
    return _normalizar_situacao(situacao) == SITUACAO_ATIVA


def empresa_ativa(empresa):
    """A empresa esta viva na Receita? Le a situacao do espelho e delega a regra."""
    dados = getattr(empresa, 'dados_receita', None)
    return situacao_ativa(dados.situacao if dados is not None else None)


def _aplicar_espelho(dados, dto):
    for campo in _CAMPOS_ESPELHO:
        setattr(dados, campo, getattr(dto, campo))
    dados.verificado_em = datetime.now()   # hora local naive (AD-004)


def _conciliar(empresa, dto, preencher_vazios):
    """Compara cadastro x Receita campo a campo. Nucleo compartilhado por
    `enriquecer` (que preenche vazio) e `divergencias_atuais` (que so le).

    Retorna (preenchidos, divergencias). Com `preencher_vazios=False` nada e
    escrito — e o modo que a tela usa para listar divergencias sem rechecar.
    """
    preenchidos, divergencias = [], []
    for campo, extrair in _CAMPOS_CADASTRO.items():
        valor_api = extrair(dto)
        if not valor_api:
            continue

        atual = getattr(empresa, campo, None)
        if not (atual or '').strip():
            if preencher_vazios:
                setattr(empresa, campo, valor_api)
                preenchidos.append(campo)
            continue

        comparar = _COMPARADORES.get(campo, lambda valor: valor)
        if comparar(atual) != comparar(valor_api):
            divergencias.append(
                {'campo': campo, 'atual': atual, 'receita': valor_api})
    return preenchidos, divergencias


def _dto_do_espelho(dados):
    """DTO montado a partir do que ja esta gravado, para comparar sem rede."""
    return receita_client.DadosReceitaDTO(municipio=dados.municipio, uf=dados.uf)


def divergencias_atuais(empresa):
    """Divergencias entre o cadastro e o espelho JA gravado. So le, nao escreve —
    a tela de detalhe chama isto a cada render."""
    dados = getattr(empresa, 'dados_receita', None)
    if dados is None:
        return []
    return _conciliar(empresa, _dto_do_espelho(dados), preencher_vazios=False)[1]


def enriquecer(empresa, dto):
    """Grava o espelho da Receita e concilia com o cadastro.

    Campo vazio e preenchido; campo preenchido que difere vira divergencia, sem
    sobrescrever. Retorna
    `{'ok', 'preenchidos': [...], 'divergencias': [...], 'erro'}`.
    """
    resultado = {'ok': False, 'preenchidos': [], 'divergencias': [], 'erro': None}
    if dto is None:
        resultado['erro'] = 'Nenhum dado da Receita para aplicar.'
        return resultado

    dados = empresa.dados_receita
    if dados is None:
        dados = DadosReceita(empresa_id=empresa.id)
        empresa.dados_receita = dados

    situacao_anterior = dados.situacao
    _aplicar_espelho(dados, dto)

    preenchidos, divergencias = _conciliar(empresa, dto, preencher_vazios=True)
    resultado['preenchidos'] = preenchidos
    resultado['divergencias'] = divergencias

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        log_event('receita_enriquecer_db_error', level='ERROR',
                  empresa_id=getattr(empresa, 'id', None), error=str(exc))
        resultado['erro'] = f'Não foi possível salvar os dados da Receita: {exc}'
        return resultado

    resultado['ok'] = True
    resultado['situacao_anterior'] = situacao_anterior
    resultado['situacao'] = dados.situacao
    return resultado


def aceitar_divergencia(empresa, campo):
    """Aplica no cadastro o valor que a Receita informa para um campo (DATA-01.9).

    Só aceita campos de `_CAMPOS_CADASTRO`: `nome` e qualquer outro atributo
    ficam de fora por construção, senão a rota viraria um "escreva qualquer
    coluna da Empresa". Retorna (ok, erro).
    """
    if campo not in _CAMPOS_CADASTRO:
        return False, f'Campo "{campo}" não pode ser atualizado pela Receita.'

    dados = empresa.dados_receita
    if dados is None:
        return False, 'Esta empresa ainda não tem dados da Receita.'

    valor = _CAMPOS_CADASTRO[campo](_dto_do_espelho(dados))
    if not valor:
        return False, f'A Receita não informou {campo} para esta empresa.'

    setattr(empresa, campo, valor)
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return False, f'Não foi possível atualizar {campo}: {exc}'

    auditoria.registrar('empresa.receita_aceitar', alvo_tipo='empresa',
                        alvo_id=empresa.id, detalhe=f'{campo}={valor}')
    return True, None


def consultar_para_formulario(cnpj):
    """Consulta a Receita e devolve os campos prontos para a tela de cadastro.

    NAO persiste nada: o operador confere e corrige antes de salvar (DATA-01.3).
    Retorna (dados_dict, erro).
    """
    dto, erro = receita_client.consultar(cnpj)
    if dto is None:
        return None, erro

    return {
        'nome': dto.nome_fantasia or dto.razao_social,
        'razao_social': dto.razao_social,
        'nome_fantasia': dto.nome_fantasia,
        'cidade': canonicalizar(dto.municipio) if dto.municipio else None,
        'estado': (dto.uf or '').strip().upper() or None,
        'situacao': dto.situacao,
        # regra UNICA: `_normalizar_situacao(...) == ATIVA` diverge de
        # `situacao_ativa` quando a situacao vem vazia (a tela avisaria que a
        # empresa sai do lote, mas o calc_targets a inclui).
        'ativa': situacao_ativa(dto.situacao),
        'logradouro': dto.logradouro,
        'numero': dto.numero,
        'bairro': dto.bairro,
        'cep': dto.cep,
        'cnae_descricao': dto.cnae_descricao,
        'fonte': dto.fonte,
    }, None


def empresas_para_recheck(idade_dias, limite):
    """Empresas com verificação mais velha que `idade_dias`, as mais velhas
    primeiro. Nunca verificada (`verificado_em` nulo) vem antes de todas."""
    corte = datetime.now() - timedelta(days=idade_dias)
    return (Empresa.query
            .outerjoin(DadosReceita, DadosReceita.empresa_id == Empresa.id)
            .filter(db.or_(DadosReceita.id.is_(None),
                           DadosReceita.verificado_em.is_(None),
                           DadosReceita.verificado_em < corte))
            .order_by(DadosReceita.verificado_em.is_(None).desc(),
                      DadosReceita.verificado_em.asc(),
                      Empresa.id.asc())
            .limit(limite)
            .all())


def _marcar_tentativa(empresa):
    """Carimba `verificado_em` sem tocar na situacao — usado quando a consulta
    falhou de forma definitiva. Manda a empresa para o fim da fila do recheck
    sem afirmar nada sobre ela estar viva ou morta."""
    dados = empresa.dados_receita
    if dados is None:
        dados = DadosReceita(empresa_id=empresa.id)
        empresa.dados_receita = dados
    dados.verificado_em = datetime.now()
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        log_event('receita_recheck_carimbo_falhou', level='WARNING',
                  empresa_id=getattr(empresa, 'id', None), error=str(exc))


def rechecar_lote(idade_dias=30, limite=50, pausa_s=1.0):
    """Recheca a situação cadastral de uma fatia da carteira (DATA-02.6).

    Fatiado de propósito: a ReceitaWS aceita ~3 req/min, então a carteira gira
    em alguns dias em vez de estourar cota num dia só. Falha de uma empresa não
    derruba as demais.

    Retorna `{'verificadas', 'falhas', 'baixadas': [(empresa_id, nome, situacao)]}`
    — `baixadas` lista as transições ATIVA → não-ativa, que alimentam o alerta.
    """
    alvos = empresas_para_recheck(idade_dias, limite)
    resumo = {'verificadas': 0, 'falhas': 0, 'baixadas': []}

    for indice, empresa in enumerate(alvos):
        if indice and pausa_s:
            time.sleep(pausa_s)
        try:
            dto, erro = receita_client.consultar(empresa.cnpj)
            if dto is None:
                resumo['falhas'] += 1
                log_event('receita_recheck_sem_dados', level='WARNING',
                          empresa_id=empresa.id, motivo=erro)
                # Falha DEFINITIVA (o CNPJ nao existe na base) carimba mesmo assim:
                # `empresas_para_recheck` poe as nunca-verificadas na frente, entao
                # sem o carimbo esses CNPJs reocupariam a cabeca da fila todo dia e
                # o resto da carteira nunca seria rechecado. Falha TRANSITORIA
                # (API fora) nao carimba — tem de ser retentada amanha.
                if erro == receita_client.ERRO_NAO_ENCONTRADO:
                    _marcar_tentativa(empresa)
                continue

            resultado = enriquecer(empresa, dto)
            if not resultado['ok']:
                resumo['falhas'] += 1
                continue

            resumo['verificadas'] += 1
            antes = _normalizar_situacao(resultado.get('situacao_anterior'))
            agora = _normalizar_situacao(resultado.get('situacao'))
            # So a TRANSICAO alerta: empresa que ja estava baixada nao volta a
            # avisar todo dia, e ligar a feature nao dispara alerta retroativo
            # para a carteira inteira.
            if antes == SITUACAO_ATIVA and agora and agora != SITUACAO_ATIVA:
                resumo['baixadas'].append(
                    (empresa.id, empresa.nome, resultado.get('situacao')))
        except Exception as exc:
            db.session.rollback()
            resumo['falhas'] += 1
            log_event('receita_recheck_falhou', level='ERROR',
                      empresa_id=getattr(empresa, 'id', None), error=str(exc))

    log_event('receita_recheck_fim', **{k: v for k, v in resumo.items()
                                        if k != 'baixadas'},
              baixadas=len(resumo['baixadas']))
    return resumo
