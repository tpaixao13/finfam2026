"""
csv_generic.py — Parser CSV genérico.

Auto-detecta colunas de data, descrição e valor em qualquer CSV.
Suporta formatos brasileiros (DD/MM/AAAA, vírgula decimal) e
internacionais (YYYY-MM-DD, ponto decimal).
"""

import csv
import io
import re
from datetime import datetime
from app.importers import BankTransaction

# ── Padrões de data suportados ──────────────────────────────────────
_DATE_FMTS = [
    (re.compile(r'^\d{4}-\d{2}-\d{2}$'), '%Y-%m-%d'),
    (re.compile(r'^\d{2}/\d{2}/\d{4}$'), '%d/%m/%Y'),
    (re.compile(r'^\d{2}-\d{2}-\d{4}$'), '%d-%m-%Y'),
    (re.compile(r'^\d{2}/\d{2}/\d{2}$'), '%d/%m/%y'),
]

# Palavras em nomes de coluna que indicam data / valor / descrição
_DATE_HINTS    = {'data', 'date', 'dt', 'dia', 'lancamento', 'lançamento', 'competencia', 'competência'}
_AMOUNT_HINTS  = {'valor', 'value', 'amount', 'debito', 'débito', 'saída', 'saida', 'vlr'}
_DESC_HINTS    = {'descricao', 'descrição', 'description', 'historico', 'histórico',
                  'memo', 'detail', 'detalhe', 'estabelecimento', 'complemento'}


def _parse_date(s: str):
    """Tenta parsear uma string de data. Retorna (day, month, year) ou None."""
    s = s.strip().strip('"').strip("'")
    # Remove qualquer hora (ex: "2026-06-01 00:00:00")
    s = s.split(' ')[0].split('T')[0]
    for pattern, fmt in _DATE_FMTS:
        if pattern.match(s):
            try:
                dt = datetime.strptime(s, fmt)
                return dt.day, dt.month, dt.year
            except ValueError:
                pass
    return None


def _parse_amount(s: str):
    """Tenta parsear valor monetário. Retorna float ou None."""
    s = s.strip().strip('"').replace('R$', '').replace('\xa0', '').replace(' ', '')
    if not s or s in ('-', '+', '.', ','):
        return None

    has_dot   = '.' in s
    has_comma = ',' in s

    if has_dot and has_comma:
        # Ex: -1.234,56 (BR) ou -1,234.56 (EN)
        # Se a vírgula vem antes do ponto → formato EN (-1,234.56)
        if s.index(',') < s.index('.'):
            try:
                return float(s.replace(',', ''))
            except ValueError:
                pass
        # Senão → formato BR (-1.234,56)
        try:
            return float(s.replace('.', '').replace(',', '.'))
        except ValueError:
            pass

    elif has_comma and not has_dot:
        # -1234,56 → formato BR com vírgula decimal
        try:
            return float(s.replace(',', '.'))
        except ValueError:
            pass

    else:
        # -1234.56 ou -1234 → formato EN ou inteiro
        try:
            return float(s)
        except ValueError:
            pass

    return None


def _col_hint_score(name: str, hint_set: set) -> int:
    """Retorna 1 se o nome da coluna (lower, sem espaços/underlines) está nos hints."""
    normalized = re.sub(r'[\s_\-]', '', name.lower())
    return 1 if normalized in hint_set else 0


def _detect_columns(header: list[str], sample: list[list[str]]) -> dict | None:
    """
    Detecta índices de colunas de data, valor e descrição.
    Retorna {'date': int, 'amount': int, 'desc': int} ou None.
    """
    n = len(header)
    if n < 2:
        return None

    date_scores   = [0] * n
    amount_scores = [0] * n
    desc_scores   = [0] * n

    # Pontuação por nome de coluna
    for i, h in enumerate(header):
        date_scores[i]   += _col_hint_score(h, _DATE_HINTS) * 3
        amount_scores[i] += _col_hint_score(h, _AMOUNT_HINTS) * 3
        desc_scores[i]   += _col_hint_score(h, _DESC_HINTS) * 3

    # Pontuação por conteúdo
    for row in sample:
        for i in range(min(n, len(row))):
            v = row[i]
            if _parse_date(v):
                date_scores[i] += 1
            amt = _parse_amount(v)
            if amt is not None and v.strip():
                amount_scores[i] += 1
            if len(v.strip()) > 5:
                desc_scores[i] += 1

    # Selecionar melhor coluna de data (com maioria de valores parseáveis)
    date_col = max(range(n), key=lambda i: date_scores[i])
    if date_scores[date_col] == 0:
        return None

    # Selecionar melhor coluna de valor (exclui data, tem negativos)
    best_amt, best_neg = -1, -1
    amount_col = None
    for i in range(n):
        if i == date_col:
            continue
        negs = sum(1 for row in sample
                   if i < len(row) and (a := _parse_amount(row[i])) is not None and a < 0)
        score = amount_scores[i] + negs * 2
        if score > best_amt:
            best_amt, amount_col = score, i

    if amount_col is None:
        return None

    # Selecionar melhor coluna de descrição (exclui data e valor, maior texto médio)
    desc_col = None
    best_len = -1
    for i in range(n):
        if i in (date_col, amount_col):
            continue
        avg = sum(len(row[i].strip()) for row in sample if i < len(row)) / max(len(sample), 1)
        score = desc_scores[i] * 5 + avg
        if score > best_len:
            best_len, desc_col = score, i

    if desc_col is None:
        return None

    return {'date': date_col, 'amount': amount_col, 'desc': desc_col}


def parse_csv_generic(file_bytes: bytes) -> list[BankTransaction]:
    """
    Lê um arquivo CSV genérico e retorna lista de BankTransaction.
    Apenas despesas (valor < 0) são incluídas.
    """
    text = None
    for enc in ('utf-8-sig', 'utf-8', 'latin-1', 'cp1252'):
        try:
            text = file_bytes.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            pass
    if text is None:
        return []

    # Detectar delimitador
    for delim in (',', ';', '\t', '|'):
        try:
            reader = list(csv.reader(io.StringIO(text), delimiter=delim))
        except csv.Error:
            continue

        rows = [r for r in reader if any(c.strip() for c in r)]
        if len(rows) < 2:
            continue

        header = [h.strip().strip('"') for h in rows[0]]
        data   = rows[1:]

        if len(header) < 2:
            continue

        sample = data[:30]
        cols   = _detect_columns(header, sample)
        if not cols:
            continue

        transactions: list[BankTransaction] = []
        seen: set = set()

        for row in data:
            max_idx = max(cols.values())
            if len(row) <= max_idx:
                continue

            date_str   = row[cols['date']].strip()
            amount_str = row[cols['amount']].strip()
            desc_str   = row[cols['desc']].strip().strip('"').strip("'")

            parsed_date   = _parse_date(date_str)
            parsed_amount = _parse_amount(amount_str)

            if not parsed_date or parsed_amount is None:
                continue
            if parsed_amount >= 0:
                continue   # Só despesas (débitos)
            if not desc_str:
                continue

            day, month, year = parsed_date
            amount = abs(parsed_amount)

            key = (day, month, year, desc_str[:40], round(amount, 2))
            if key in seen:
                continue
            seen.add(key)

            transactions.append(BankTransaction(
                day=day, month=month, year=year,
                description=desc_str[:200],
                amount=amount,
                payment_method='PIX',   # Formato genérico — não tem info de método
            ))

        if transactions:
            return transactions

    return []
