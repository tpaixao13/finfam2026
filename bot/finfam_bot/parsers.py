"""Interpretação local de valores, datas e frases curtas (sem IA)."""
import re
import unicodedata
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


_WEEKDAYS = {'segunda': 0, 'terca': 1, 'quarta': 2, 'quinta': 3, 'sexta': 4, 'sabado': 5, 'domingo': 6}


def parse_date(text: str, today: date) -> date | None:
    """Datas digitadas: hoje, ontem, anteontem, 05/10, 5/10/2026, 05-10-26, "dia 5" ou só "5"
    (último dia 5 que já passou), dia da semana ("sexta", "sábado": o último que passou)."""
    t = unicodedata.normalize('NFKD', (text or '').strip().lower()).encode('ascii', 'ignore').decode()
    t = re.sub(r'\s+', ' ', t).removeprefix('dia ').removesuffix('-feira').removesuffix(' feira').strip()
    relative = {'hoje': 0, 'h': 0, 'ontem': 1, 'o': 1, 'anteontem': 2}
    if t in relative:
        return today - timedelta(days=relative[t])
    if t in _WEEKDAYS:
        return today - timedelta(days=(today.weekday() - _WEEKDAYS[t]) % 7)
    if re.fullmatch(r'\d{1,2}', t):  # só o dia: este mês, ou o mês passado se ainda não chegou
        day = int(t)
        for back in (0, 1, 2):  # dia 31 em outubro (dia 23) → 31 de agosto
            month, year = today.month - back, today.year
            if month <= 0:
                month, year = month + 12, year - 1
            try:
                d = date(year, month, day)
            except ValueError:
                continue
            if d <= today:
                return d
        return None
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


def _plain(text: str) -> str:
    return unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode().lower()


def pick_person(text: str, members: list[dict]) -> tuple[dict | None, str]:
    """"farmácia 30 pra Greyce" → (membro Greyce, "farmácia 30"). Vale o nome completo ou o primeiro nome,
    depois de pra/para/pro/da/do/de. Família de uma pessoa só: nada muda."""
    if len(members) < 2:
        return None, text
    flat = ''.join((_plain(ch) or ' ')[:1] for ch in text)  # mesmo tamanho do original: dá para cortar nele
    for m in sorted(members, key=lambda m: -len(m['name'])):
        names = {_plain(m['name']).strip(), _plain(m['name']).split()[0] if m['name'].strip() else ''} - {''}
        for name in sorted(names, key=len, reverse=True):
            pattern = rf'\b(?:pra|para|pro|da|do|de)\s+(?:a\s+|o\s+)?{re.escape(name)}\b'
            found = re.search(pattern, flat)
            if found:
                rest = (text[:found.start()] + text[found.end():]).strip()
                return m, re.sub(r'\s{2,}', ' ', rest)
    return None, text
