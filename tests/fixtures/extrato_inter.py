"""Gera um PDF com o layout do extrato do Banco Inter, para os testes.

Gerado em vez de commitado: o extrato real e da conta do escritorio, com nomes
de clientes e o CNPJ do emitente. Aqui os nomes sao ficticios e o layout e o
que importa — as coordenadas reproduzem as do extrato real (medidas com
`pdfplumber` sobre o arquivo do banco), porque e delas que a leitura depende:

    Data 24.8 | Nome 101.1 | Descrição 279.1 | Ref. 471.4 | Identif. 559.6
    Entrada x1=633.3 | Saída x1=683.7 | Saldo x1=728.9   (alinhadas a direita)

As linhas exercitam de proposito o que o texto corrido nao resolveria: data que
so aparece na primeira linha do dia, saldo que so aparece na ultima, saida entre
parenteses e os rotulos do grafico de saldo, que o pdfplumber le junto.
"""
from fpdf import FPDF

# x0 das colunas alinhadas a esquerda; x1 das alinhadas a direita.
X_DATA, X_NOME, X_DESCRICAO, X_REF, X_IDENTIF = 24.8, 101.1, 279.1, 471.4, 559.6
X1_ENTRADA, X1_SAIDA, X1_SALDO = 633.3, 683.7, 728.9
LARGURA_VALOR = 60.0

CATEGORIA = 'HONORÁRIOS - CLIENTES'

# (data, nome, descricao, entrada, saida, saldo)
LINHAS = [
    ('30/06/26', '', 'Saldo anterior', '', '', '2.056,63'),
    ('06/07/26', CATEGORIA, 'Pix - Alfa Comercio Ltda - honor. 06/2026',
     '1.806,00', '', '3.862,63'),
    # tres linhas do mesmo dia: so a primeira traz a data, so a ultima o saldo
    ('07/07/26', CATEGORIA, 'Pix - Beta Servicos Ltda - 06/2026', '325,00', '', ''),
    ('', CATEGORIA, 'Pix GAMA SAUDE - ALT. CONTRATO - PARTE', '684,00', '', ''),
    ('', CATEGORIA, 'Pix recebido - Gama Saude Produtos Ltda', '2.000,00', '', '6.871,63'),
    # estorno: nome fora da categoria, valor na coluna Saida
    ('08/07/26', 'GAMA SAUDE', 'GAMA SAUDE - ESTORNO', '', '(1.784,00)', '5.087,63'),
    ('09/07/26', CATEGORIA, 'Pix - Delta Transportes Ltda -06/2026', '487,00', '', '5.574,63'),
    # saida que nao e estorno de cliente nenhum
    ('20/07/26', 'CAIXA ECONOMICA FEDERAL', 'Guia de recolhimento do fgts',
     '', '(1.475,42)', '4.098,79'),
    ('23/07/26', CATEGORIA, 'Pix - baixa Epsilon Matriz e Epsilon Filial',
     '1.000,00', '', '5.098,79'),
]


def _cabecalho(pdf, topo):
    pdf.set_xy(X_DATA, topo)
    pdf.cell(60, 10, 'Data')
    pdf.set_xy(X_NOME, topo)
    pdf.cell(160, 10, 'Nome')
    pdf.set_xy(X_DESCRICAO, topo)
    pdf.cell(180, 10, 'Descrição')
    pdf.set_xy(X_REF, topo)
    pdf.cell(80, 10, 'Ref.')
    pdf.set_xy(X_IDENTIF, topo)
    pdf.cell(40, 10, 'Identif.')
    for x1, rotulo in ((X1_ENTRADA, 'Entrada'), (X1_SAIDA, 'Saída'),
                       (X1_SALDO, 'Saldo')):
        pdf.set_xy(x1 - LARGURA_VALOR, topo)
        pdf.cell(LARGURA_VALOR, 10, rotulo, align='R')


def gerar(caminho):
    """Escreve o PDF sintetico em `caminho` e devolve os bytes."""
    pdf = FPDF(orientation='L', unit='pt', format=(792, 612))
    pdf.add_page()
    pdf.set_font('helvetica', size=8)

    # rotulos do eixo do grafico de saldo: ruido que a leitura precisa descartar
    pdf.set_xy(77, 60)
    pdf.cell(300, 10, '2/07 4/07 6/07 8/07 10/07 12/07')

    _cabecalho(pdf, 100)

    topo = 120
    for data, nome, descricao, entrada, saida, saldo in LINHAS:
        if data:
            pdf.set_xy(X_DATA, topo)
            pdf.cell(60, 10, data)
        if nome:
            pdf.set_xy(X_NOME, topo)
            pdf.cell(170, 10, nome)
        pdf.set_xy(X_DESCRICAO, topo)
        pdf.cell(190, 10, descricao)
        for x1, valor in ((X1_ENTRADA, entrada), (X1_SAIDA, saida),
                          (X1_SALDO, saldo)):
            if not valor:
                continue
            pdf.set_xy(x1 - LARGURA_VALOR, topo)
            pdf.cell(LARGURA_VALOR, 10, valor, align='R')
        topo += 22

    conteudo = bytes(pdf.output())
    if caminho is not None:
        with open(caminho, 'wb') as arquivo:
            arquivo.write(conteudo)
    return conteudo
