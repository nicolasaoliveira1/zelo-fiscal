r"""Camada conferivel do manifestador: texto e XML viram fila (MANIF-07..11).

**Zero rede, zero Selenium** — mesmo papel que o `nfse_import` tem na NFSe. E
aqui que o erro e barato: chave mal colada descoberta nesta camada nao custa
nada; descoberta na SEFAZ custa uma rejeicao e a duvida sobre o que aconteceu.

Layout da chave de 44 digitos:

    cUF(2) AAMM(4) CNPJ-emitente(14) mod(2) serie(3) nNF(9) tpEmis(1) cNF(8) DV(1)

Duas consequencias que mandam no desenho:

1. A **competencia** sai de `AAMM` — e o mes de emissao da NF-e, que e o que o
   escritorio chama de competencia ("no mes 08 a competencia e 07").
2. O CNPJ ali dentro e do **emitente**, nao do destinatario. Por isso a chave
   sozinha NAO diz de qual empresa da carteira ela e: a colagem e por empresa, e
   so o XML (que traz `dest/CNPJ`) resolve isso sozinho.
"""
import re
from collections import namedtuple

ChaveDecomposta = namedtuple(
    'ChaveDecomposta',
    'cuf aamm cnpj_emitente modelo serie numero tipo_emissao codigo dv')

TAMANHO_CHAVE = 44

# Grupos de digitos da linha. Todo o resto (espaco, ponto, hifen, letra) e
# separador — e as fronteiras que ele deixa sao o unico lugar por onde
# `_chaves_da_linha` tem permissao de recortar.
_BLOCO_DE_DIGITOS = re.compile(r'\d+')


def dv_valido(chave):
    """Digito verificador pelo modulo 11, pesos 2..9 ciclando da direita.

    Conferido contra 3 NF-e reais (`recon.md` §3). E a segunda rede de seguranca
    da extracao: um bloco que virou chave por acidente quase certamente cai
    aqui."""
    if not chave or len(str(chave)) != TAMANHO_CHAVE or not str(chave).isdigit():
        return False

    chave = str(chave)
    peso = 2
    soma = 0
    for digito in reversed(chave[:43]):
        soma += int(digito) * peso
        peso = 2 if peso == 9 else peso + 1

    resto = soma % 11
    esperado = 0 if resto in (0, 1) else 11 - resto
    return esperado == int(chave[43])


def decompor(chave):
    """Os 9 campos da chave. Nao valida — use `dv_valido` antes."""
    chave = str(chave)
    return ChaveDecomposta(
        cuf=chave[0:2], aamm=chave[2:6], cnpj_emitente=chave[6:20],
        modelo=chave[20:22], serie=chave[22:25], numero=chave[25:34],
        tipo_emissao=chave[34:35], codigo=chave[35:43], dv=chave[43:44])


def competencia_da_chave(chave):
    """'AAAA-MM' a partir de `AAMM`, ou None se o mes nao existe.

    O ano de 2 digitos e sempre 20xx: a NF-e comecou em 2006 e a chave nao
    representa datas anteriores."""
    if not chave or len(str(chave)) < 6:
        return None
    aamm = str(chave)[2:6]
    if not aamm.isdigit():
        return None
    ano, mes = int(aamm[:2]), int(aamm[2:])
    if not 1 <= mes <= 12:
        return None
    return f'20{ano:02d}-{mes:02d}'


def _fatiar(digitos):
    """Divide um bloco de 44*n digitos em n chaves."""
    return [digitos[i:i + TAMANHO_CHAVE]
            for i in range(0, len(digitos), TAMANHO_CHAVE)]


def _chaves_da_linha(linha):
    """As chaves de UMA linha, ou [] se a linha nao contem chave.

    O recorte so acontece em fronteira de separador que ja existia no texto —
    nunca numa janela arbitraria. Essa propriedade e o que impede o erro mais
    caro desta camada: uma chave DESALINHADA aponta para a NF-e de outra
    pessoa, e manifestar a nota errada nao tem desfazer.

    Duas passagens:

    1. Junta todos os grupos de digitos da linha. Se o total e multiplo de 44, e
       isso — sem exigir DV, para que uma chave com digito trocado ainda seja
       EXTRAIDA e possa ser recusada com nome na lista (MANIF-08). Aceitar tudo
       nao descarta informacao, entao nao precisa de prova.
    2. Se nao fecha, tenta descartar grupos pela esquerda ('NFe numero 4540
       <chave>', 'Chave: <chave>'). Aqui ha descarte de dados, entao exige-se
       prova: so vale a fatia cujos DVs todos conferem.
    """
    grupos = _BLOCO_DE_DIGITOS.findall(linha)
    if not grupos:
        return []

    tudo = ''.join(grupos)
    if len(tudo) % TAMANHO_CHAVE == 0:
        return _fatiar(tudo)

    acumulado = tudo
    for grupo in grupos[:-1]:
        acumulado = acumulado[len(grupo):]
        if len(acumulado) % TAMANHO_CHAVE:
            continue
        fatias = _fatiar(acumulado)
        if all(dv_valido(c) for c in fatias):
            return fatias
    return []


def extrair_chaves(texto):
    """Toda chave de 44 digitos do texto, na ordem, com repetidas preservadas.

    Trabalha LINHA A LINHA: a colagem do operador vem do scanner de codigo de
    barras ou de copiar-colar de PDF, e nos dois a unidade e a linha. Juntar
    linhas deixaria o final de uma emendar no comeco da outra.

    Repetidas NAO sao deduplicadas aqui: quem decide o que fazer com duplicata e
    a importacao (MANIF-11), e sumir com elas silenciosamente esconderia do
    operador que ele colou o mesmo bloco duas vezes."""
    if not texto:
        return []

    achadas = []
    for linha in str(texto).splitlines():
        achadas.extend(_chaves_da_linha(linha))
    return achadas


# --- importacao (MANIF-09, MANIF-11) ----------------------------------------

ORIGEM_COLAGEM = 'colagem'
ORIGEM_XML = 'xml'

NS_NFE = 'http://www.portalfiscal.inf.br/nfe'

_COMPETENCIA = re.compile(r'^(20\d{2})-(0[1-9]|1[0-2])$')

# Estados em que a chave ja "aconteceu": um conflito de empresa descoberto
# depois nao pode desfaze-los. Manifestacao registrada e fato fiscal.
_TERMINAIS = ('manifestada', 'rejeitada', 'indefinida')


class Balanco:
    """O que entrou e o que ficou de fora, com as chaves NOMEADAS.

    Contar nao basta: "3 recusadas" manda o operador caçar quais; "estas 3" ele
    resolve na hora."""

    def __init__(self):
        self.aceitas = []
        self.dv_invalido = []
        self.competencia_invalida = []
        self.duplicatas = []
        self.sem_empresa = []
        self.total_lidas = 0

    def __repr__(self):
        return (f'<Balanco lidas={self.total_lidas} aceitas={len(self.aceitas)} '
                f'dv={len(self.dv_invalido)} comp={len(self.competencia_invalida)} '
                f'dup={len(self.duplicatas)} sem_empresa={len(self.sem_empresa)}>')

    def como_dict(self):
        return {
            'total_lidas': self.total_lidas,
            'aceitas': list(self.aceitas),
            'dv_invalido': list(self.dv_invalido),
            'competencia_invalida': list(self.competencia_invalida),
            'duplicatas': list(self.duplicatas),
            'sem_empresa': list(self.sem_empresa),
        }


def _registrar_duplicata(balanco, existente, empresa):
    """Reporta a chave repetida e, havendo conflito de empresa, tira-a da fila.

    O conflito — a mesma NF-e importada para duas empresas — e o caso perigoso:
    manifestar sob a empresa errada usa o certificado errado e cria um evento
    que nao tem desfazer. Entao a chave sai da fila (status DUPLICATA) e espera
    o operador dizer de quem ela e.

    Chave ja em estado terminal NAO e derrubada: a manifestacao ja aconteceu, e
    reescrever o desfecho apagaria o registro do que de fato saiu."""
    from app import db
    from app.models import StatusManifestacao

    conflito = bool(empresa is not None and existente.empresa_id != empresa.id)
    if conflito and existente.status not in _TERMINAIS:
        existente.status = StatusManifestacao.DUPLICATA
        db.session.commit()

    balanco.duplicatas.append({
        'chave': existente.chave,
        'status': existente.status,
        'empresa': existente.empresa.nome if existente.empresa else None,
        'conflito': conflito,
    })


def importar_colagem(empresa, texto, origem=ORIGEM_COLAGEM):
    """Cola um bloco de texto na fila de uma empresa. Devolve o `Balanco`.

    Nada de rede aqui: so extracao, validacao e persistencia."""
    from app import db
    from app.models import ChaveManifestacao

    balanco = Balanco()

    for chave in extrair_chaves(texto):
        balanco.total_lidas += 1

        if not dv_valido(chave):
            balanco.dv_invalido.append(chave)
            continue

        competencia = competencia_da_chave(chave)
        if competencia is None:
            balanco.competencia_invalida.append(chave)
            continue

        # Cobre tambem a chave repetida DENTRO do mesmo bloco: o commit abaixo
        # acontece antes da proxima volta, entao a segunda ocorrencia ja
        # encontra a linha aqui.
        existente = ChaveManifestacao.query.filter_by(chave=chave).first()
        if existente is not None:
            _registrar_duplicata(balanco, existente, empresa)
            continue

        partes = decompor(chave)
        db.session.add(ChaveManifestacao(
            chave=chave, empresa_id=empresa.id, competencia=competencia,
            cnpj_emitente=partes.cnpj_emitente, origem=origem))
        db.session.commit()
        balanco.aceitas.append(chave)

    return balanco


def _chave_e_destinatario(conteudo):
    """(chave, cnpj do destinatario) lidos do XML, ou (None, None).

    O `Id` do `infNFe` vem prefixado com 'NFe'. O destinatario e `dest/CNPJ`:
    `dest/CPF` (pessoa fisica) e a ausencia de `dest` (NFC-e modelo 65, venda no
    balcao) significam a mesma coisa aqui — nao ha empresa da carteira para
    manifestar."""
    import xml.etree.ElementTree as ET

    try:
        raiz = ET.fromstring(conteudo)
    except (ET.ParseError, ValueError, TypeError):
        return None, None

    inf = raiz.find(f'.//{{{NS_NFE}}}infNFe')
    if inf is None:
        return None, None

    chave = re.sub(r'\D', '', inf.get('Id') or '')
    cnpj_no = inf.find(f'{{{NS_NFE}}}dest/{{{NS_NFE}}}CNPJ')
    destinatario = (cnpj_no.text or '').strip() if cnpj_no is not None else None
    return (chave or None), (destinatario or None)


def importar_xmls(arquivos):
    """Importa a partir dos XML das NF-e. `arquivos` = [(nome, bytes)].

    Cada recusa nomeia o ARQUIVO, nao a chave: no XML o operador nao viu a chave
    — ele viu um arquivo, e e por ele que vai procurar o problema."""
    from app import db
    from app.models import ChaveManifestacao, Empresa

    balanco = Balanco()

    for nome, conteudo in arquivos:
        balanco.total_lidas += 1

        chave, destinatario = _chave_e_destinatario(conteudo)
        if not chave:
            balanco.sem_empresa.append(nome)
            continue

        if not dv_valido(chave):
            balanco.dv_invalido.append(chave)
            continue

        competencia = competencia_da_chave(chave)
        if competencia is None:
            balanco.competencia_invalida.append(chave)
            continue

        empresa = None
        if destinatario:
            empresa = Empresa.query.filter(
                db.func.replace(db.func.replace(db.func.replace(
                    Empresa.cnpj, '.', ''), '/', ''), '-', '') == destinatario
            ).first()
        if empresa is None:
            balanco.sem_empresa.append(nome)
            continue

        existente = ChaveManifestacao.query.filter_by(chave=chave).first()
        if existente is not None:
            _registrar_duplicata(balanco, existente, empresa)
            continue

        partes = decompor(chave)
        db.session.add(ChaveManifestacao(
            chave=chave, empresa_id=empresa.id, competencia=competencia,
            cnpj_emitente=partes.cnpj_emitente, origem=ORIGEM_XML))
        db.session.commit()
        balanco.aceitas.append(chave)

    return balanco


def liberar_duplicata(chave_linha, empresa=None, ator_id=None, confirmar=False):
    """Devolve uma chave a fila. False quando falta confirmacao.

    Chave em estado terminal exige `confirmar=True`: reenviar e inofensivo (a
    SEFAZ responde duplicidade de evento), mas devolver a fila uma nota que ja
    saiu, sem ninguem pedir, esconderia do operador que ela ja foi manifestada."""
    from app import db
    from app.models import StatusManifestacao

    if chave_linha.status in _TERMINAIS and not confirmar:
        return False

    if empresa is not None:
        chave_linha.empresa_id = empresa.id
    chave_linha.status = StatusManifestacao.PENDENTE
    chave_linha.liberado_por_id = ator_id
    db.session.commit()
    return True


def ajustar_competencia(chave_linha, valor):
    """Sobrescreve a competencia derivada. False se o formato nao for AAAA-MM."""
    from app import db

    if not valor or not _COMPETENCIA.match(str(valor)):
        return False

    chave_linha.competencia = str(valor)
    chave_linha.competencia_ajustada = True
    db.session.commit()
    return True
