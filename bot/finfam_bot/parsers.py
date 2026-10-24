"""Interpretação local de valores, datas e frases curtas (sem IA)."""
import re
from datetime import date, timedelta

from finfam_bot.consts import CREDIT, DEBIT


def parse_amount(text: str) -> float | None:
    s = re.sub(r'[^\d,.]', '', text or '')
    if ',' in s:
        s = s.replace('.', '').replace(',', '.')
    elif s.count('.') > 1 or re.fullmatch(r'\d{1,3}\.\d{3}', s):  # "1.000" é mil, não 1,000
        s = s.replace('.', '')
    try:
        value = round(float(s), 2)
    except ValueError:
        return None
    return value if 0 < value < 10_000_000 else None


def parse_date(text: str, today: date) -> date | None:
    t = (text or '').strip().lower()
    if t in ('hoje', 'h'):
        return today
    if t in ('ontem', 'o'):
        return today - timedelta(days=1)
    m = re.fullmatch(r'(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?', t)
    if not m:
        return None
    day, month, year = int(m[1]), int(m[2]), int(m[3] or today.year)
    year += 2000 if year < 100 else 0
    try:
        return date(year, month, day)
    except ValueError:
        return None


_STOP = {'gastei', 'paguei', 'comprei', 'gasto', 'compra', 'lancar', 'lança', 'lanca', 'no', 'na', 'nos',
         'nas', 'de', 'do', 'da', 'em', 'com', 'um', 'uma', 'o', 'a', 'e', 'pelo', 'pela', 'reais', 'real', 'r$',
         'hoje', 'ontem', 'cartao', 'cartão', 'via', 'pago', 'paga'}
_METHOD_WORDS = [(('credito', 'crédito'), CREDIT), (('debito', 'débito'), DEBIT), (('pix',), 'PIX'),
                 (('dinheiro', 'especie', 'espécie'), 'Dinheiro'), (('vr',), 'VR'), (('va',), 'VA')]


def quick_parse(text: str, today: date) -> dict:
    """Entende frases simples sem IA: "gastei 25 no almoço no pix", "uber 18,90 ontem".
    Devolve só o que encontrou (description, amount, payment_method, installments, date)."""
    out: dict = {}
    rest = []
    for w in (text or '').split():
        low = w.lower().strip('.,;!?')
        if 'amount' not in out and re.fullmatch(r'(r\$)?\d[\d.]*(,\d{1,2})?', low):
            value = parse_amount(low)
            if value:
                out['amount'] = value
                continue
        if m := re.fullmatch(r'(\d{1,2})x', low):
            out['installments'] = int(m[1])
            continue
        method = next((mth for keys, mth in _METHOD_WORDS if low in keys), None)
        if method and 'payment_method' not in out:
            out['payment_method'] = method
            continue
        if low in ('hoje', 'ontem'):
            out['date'] = parse_date(low, today).isoformat()
        if low not in _STOP:
            rest.append(w.strip('.,;!?'))
    if out.get('installments') and out.get('payment_method', CREDIT) != CREDIT:
        out.pop('installments')
    elif out.get('installments'):
        out['payment_method'] = CREDIT
    if rest:
        desc = ' '.join(rest)[:200]
        out['description'] = desc[0].upper() + desc[1:]
    return out
