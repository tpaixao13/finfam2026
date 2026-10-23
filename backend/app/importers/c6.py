"""
Parser do extrato de conta corrente C6 Bank (PDF).

Formato do PDF:
    Colunas: Data lançamento | Data contábil | Tipo | Descrição | Valor
    Valores negativos (-R$ X,XX) = saídas (despesas)
    Valores positivos (R$ X,XX)  = entradas (ignorados)
"""
import io
import re

import pdfplumber
from . import BankTransaction

# Tipos que representam despesas e o método de pagamento correspondente
_TIPO_PAYMENT = {
    'Débito de Cartão': 'Cartão de Débito',
    'Saída PIX':        'PIX',
    'Pagamento':        'PIX',
}

# Padrões de descrição que devem ser ignorados (não são despesas reais)
_SKIP_DESC = re.compile(
    r'PGTO FAT CARTAO|PAGAMENTO DE FATURA|PAGAMENTO FATURA',
    re.IGNORECASE,
)

_DATE_CELL = re.compile(r'^(\d{2})/(\d{2})$')
_AMOUNT_NEG = re.compile(r'^-R\$\s*([\d.]+,\d{2})$')   # ex: -R$ 48,00
_YEAR_RE    = re.compile(r'\b(20\d{2})\b')



# Alias para compatibilidade com código existente
C6Transaction = BankTransaction


def _to_float(s: str) -> float:
    return float(s.replace('.', '').replace(',', '.'))


def _from_table_row(row: list, year: int) -> 'C6Transaction | None':
    """
    Linha esperada: [data_lanc, data_contab, tipo, descricao, valor]
    Aceita também linhas com colunas fundidas (pdfplumber às vezes une colunas).
    """
    if not row:
        return None
    cells = [str(c or '').strip() for c in row]

    # Primeira célula deve ser DD/MM
    dm = _DATE_CELL.match(cells[0])
    if not dm:
        return None

    day, month = int(dm.group(1)), int(dm.group(2))

    # Detecta tipo e valor conforme número de colunas extraídas
    tipo = description = valor_str = ''

    if len(cells) >= 5:
        tipo        = cells[2]
        description = cells[3]
        valor_str   = cells[4]
    elif len(cells) == 4:
        tipo      = cells[2]
        valor_str = cells[3]
    elif len(cells) == 3:
        # Tipo + descrição podem estar fundidos
        tipo_desc = cells[1]
        valor_str = cells[2]
        for t in _TIPO_PAYMENT:
            if tipo_desc.startswith(t):
                tipo = t
                description = tipo_desc[len(t):].strip()
                break
    else:
        return None

    if tipo not in _TIPO_PAYMENT:
        return None

    am = _AMOUNT_NEG.match(valor_str)
    if not am:
        return None

    amount = _to_float(am.group(1))
    if amount <= 0:
        return None

    if _SKIP_DESC.search(description):
        return None

    return C6Transaction(
        day=day, month=month, year=year,
        description=description,
        amount=amount,
        payment_method=_TIPO_PAYMENT[tipo],
    )


# Regex para linha de texto: DD/MM DD/MM <Tipo> <Desc> -R$ X,XX
_LINE_RE = re.compile(
    r'^(\d{2})/(\d{2})\s+\d{2}/\d{2}\s+(.+?)\s+-R\$\s*([\d.]+,\d{2})\s*$'
)


def _from_text_line(line: str, year: int) -> 'C6Transaction | None':
    m = _LINE_RE.match(line.strip())
    if not m:
        return None

    day   = int(m.group(1))
    month = int(m.group(2))
    middle       = m.group(3)   # "Tipo Descrição" combinados
    amount_str   = m.group(4)

    tipo = description = ''
    for t in _TIPO_PAYMENT:
        if middle.startswith(t):
            tipo        = t
            description = middle[len(t):].strip()
            break

    if not tipo:
        return None

    if _SKIP_DESC.search(description):
        return None

    amount = _to_float(amount_str)
    if amount <= 0:
        return None

    return C6Transaction(
        day=day, month=month, year=year,
        description=description,
        amount=amount,
        payment_method=_TIPO_PAYMENT[tipo],
    )


def parse_c6_pdf(file_bytes: bytes, ref_year: int | None = None, password: str | None = None) -> list[C6Transaction]:
    from datetime import datetime
    if ref_year is None:
        ref_year = datetime.now().year

    transactions: list[C6Transaction] = []

    open_kwargs = {'password': password} if password else {}
    with pdfplumber.open(io.BytesIO(file_bytes), **open_kwargs) as pdf:
        # Detecta o ano no cabeçalho do PDF
        first_text = pdf.pages[0].extract_text() or '' if pdf.pages else ''
        ym = _YEAR_RE.search(first_text)
        if ym:
            ref_year = int(ym.group(1))

        for page in pdf.pages:
            # Estratégia 1: extração de tabelas
            for table in (page.extract_tables() or []):
                for row in table:
                    t = _from_table_row(row, ref_year)
                    if t:
                        transactions.append(t)

            # Estratégia 2: texto linha a linha (fallback)
            text = page.extract_text() or ''
            for line in text.splitlines():
                t = _from_text_line(line, ref_year)
                if t:
                    transactions.append(t)

    # Remove duplicatas (tabela + texto podem se sobrepor)
    seen: set[tuple] = set()
    unique: list[C6Transaction] = []
    for t in transactions:
        key = (t.day, t.month, t.year, t.description, t.amount)
        if key not in seen:
            seen.add(key)
            unique.append(t)

    unique.sort(key=lambda t: (t.month, t.day))
    return unique
