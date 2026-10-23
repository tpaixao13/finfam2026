"""
Parser Nubank CSV — fatura do cartão de crédito.

Formatos suportados:

  Formato A (fatura cartão — mais comum):
    date,category,title,amount
    2026-04-15,supermarket,SUPERMERCADO,150.0

  Formato B (NuConta / extrato):
    "Data","Valor","Identificador","Descrição"
    "15/04/2026","-150,00","abc","SUPERMERCADO"

Em ambos, importa apenas saídas (amount > 0 no A; amount < 0 no B).
"""
import csv
import io
from datetime import datetime
from . import BankTransaction


def _decode(file_bytes: bytes) -> str:
    for enc in ('utf-8-sig', 'utf-8', 'latin-1'):
        try:
            return file_bytes.decode(enc)
        except UnicodeDecodeError:
            continue
    return file_bytes.decode('latin-1', errors='replace')


def _parse_date(s: str) -> tuple[int, int, int] | None:
    s = s.strip()
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d/%m/%y'):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.day, dt.month, dt.year
        except ValueError:
            continue
    return None


def _to_float(s: str) -> float | None:
    s = s.strip()
    if not s:
        return None
    try:
        if ',' in s:
            # Formato brasileiro: 1.234,56
            return float(s.replace('.', '').replace(',', '.'))
        # Formato inglês: 150.0 ou 1234.56
        return float(s)
    except ValueError:
        return None


def parse_nubank_csv(file_bytes: bytes) -> list[BankTransaction]:
    content = _decode(file_bytes)
    reader = csv.DictReader(io.StringIO(content))
    headers = [h.strip().lower() for h in (reader.fieldnames or [])]

    transactions: list[BankTransaction] = []

    # Detecta formato pelo cabeçalho
    if 'title' in headers and 'amount' in headers:
        # Formato A: fatura do cartão
        for row in reader:
            date = _parse_date(row.get('date', ''))
            amount = _to_float(row.get('amount', '0') or '0')
            desc = str(row.get('title', '')).strip()

            if not date or amount is None or not desc:
                continue
            if amount <= 0:       # reembolso — ignora
                continue

            transactions.append(BankTransaction(
                day=date[0], month=date[1], year=date[2],
                description=desc,
                amount=amount,
                payment_method='Cartão de Crédito',
            ))

    elif 'descrição' in headers or 'descricao' in headers:
        # Formato B: NuConta extrato
        desc_key = 'Descrição' if 'descrição' in headers else 'Descricao'
        for row in reader:
            raw_keys = list(row.keys())
            # Encontra chaves reais (preservando case)
            data_key  = next((k for k in raw_keys if k.strip().lower() == 'data'), None)
            valor_key = next((k for k in raw_keys if k.strip().lower() == 'valor'), None)
            desc_key2 = next((k for k in raw_keys if k.strip().lower() in ('descrição', 'descricao', 'descrição')), None)

            if not all([data_key, valor_key, desc_key2]):
                continue

            date   = _parse_date(row.get(data_key, ''))
            amount = _to_float(row.get(valor_key, '0') or '0')
            desc   = str(row.get(desc_key2, '')).strip()

            if not date or amount is None or not desc:
                continue
            if amount >= 0:       # entrada — ignora
                continue

            transactions.append(BankTransaction(
                day=date[0], month=date[1], year=date[2],
                description=desc,
                amount=abs(amount),
                payment_method='Cartão de Crédito',
            ))
    else:
        # Tentativa genérica: procura coluna de data + valor
        for row in reader:
            vals = list(row.values())
            if len(vals) < 2:
                continue
            # Última coluna numérica como valor
            for i in range(len(vals) - 1, -1, -1):
                amount = _to_float(vals[i] or '0')
                if amount is not None and amount != 0:
                    break
            else:
                continue
            date_str = vals[0]
            desc     = ' '.join(v for v in vals[1:len(vals) - 1] if v).strip()
            date     = _parse_date(date_str)
            if not date or not desc:
                continue
            if amount > 0:
                transactions.append(BankTransaction(
                    day=date[0], month=date[1], year=date[2],
                    description=desc,
                    amount=amount,
                    payment_method='Cartão de Crédito',
                ))

    transactions.sort(key=lambda t: (t.month, t.day))
    return transactions
