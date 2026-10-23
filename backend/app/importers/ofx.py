"""
Parser OFX — cobre Itaú, Bradesco, Santander, BB, Caixa, Nubank (NuConta),
Banco Inter, BTG, XP, Sicoob, Sicredi e qualquer banco que exporte OFX.

Suporta:
  • OFX SGML (versões 1.x — sem tags de fechamento nas folhas)
  • OFX XML  (versões 2.x — XML válido)

Importa apenas saídas (TRNAMT < 0). Entradas são ignoradas.
"""
import re
from xml.etree import ElementTree as ET
from . import BankTransaction

# ── Mapeamento TRNTYPE → método de pagamento ─────────────────────────────────
_TRNTYPE_PAYMENT = {
    'POS':          'Cartão de Débito',
    'DEBIT':        'Cartão de Débito',
    'ATM':          'Cartão de Débito',
    'CHECK':        'Cartão de Débito',
    'PAYMENT':      'PIX',
    'XFER':         'PIX',
    'CASH':         'Dinheiro',
    'DIRECTDEBIT':  'PIX',
    'REPEATPMT':    'PIX',
    'OTHER':        'PIX',
}
# Tipos que representam ENTRADAS — sempre ignorados
_INCOME_TYPES = {'CREDIT', 'INT', 'DIV', 'DEP', 'DIRECTDEP'}

_YEAR_RE   = re.compile(r'^(\d{4})(\d{2})(\d{2})')
_FIELD_RE  = re.compile(r'<([A-Z0-9]+)>\s*([^\n<]+)')   # SGML: <TAG>value


def _decode(file_bytes: bytes) -> str:
    for enc in ('utf-8-sig', 'utf-8', 'latin-1', 'cp1252'):
        try:
            return file_bytes.decode(enc)
        except UnicodeDecodeError:
            continue
    return file_bytes.decode('latin-1', errors='replace')


def _parse_date(dtstr: str) -> tuple[int, int, int] | None:
    """YYYYMMDD[HHMMSS[tz]] → (day, month, year)"""
    m = _YEAR_RE.match(dtstr.strip())
    if not m:
        return None
    try:
        return int(m.group(3)), int(m.group(2)), int(m.group(1))
    except ValueError:
        return None


def _payment_from(trntype: str, memo: str) -> str:
    if 'pix' in memo.lower():
        return 'PIX'
    return _TRNTYPE_PAYMENT.get(trntype.upper(), 'PIX')


def _build(fields: dict) -> BankTransaction | None:
    # Valor
    try:
        amount = float(fields.get('TRNAMT', '0').replace(',', '.'))
    except ValueError:
        return None
    if amount >= 0:
        return None   # entrada — ignora

    # Tipo
    trntype = fields.get('TRNTYPE', 'OTHER').strip().upper()
    if trntype in _INCOME_TYPES:
        return None

    # Data
    dtstr = fields.get('DTPOSTED') or fields.get('DTTRADE') or ''
    date = _parse_date(dtstr)
    if not date:
        return None
    day, month, year = date

    # Descrição
    desc = (fields.get('MEMO') or fields.get('NAME') or '').strip()
    # Remove código de referência comum (ex: "SUPERMERCADO #123456")
    desc = re.sub(r'\s*#\d{6,}$', '', desc).strip()
    if not desc:
        return None

    return BankTransaction(
        day=day, month=month, year=year,
        description=desc,
        amount=abs(amount),
        payment_method=_payment_from(trntype, desc),
    )


# ── SGML ──────────────────────────────────────────────────────────────────────

def _parse_sgml(content: str) -> list[BankTransaction]:
    transactions: list[BankTransaction] = []
    blocks = re.findall(r'<STMTTRN>(.*?)</STMTTRN>', content, re.DOTALL | re.IGNORECASE)
    for block in blocks:
        fields = {m.group(1).upper(): m.group(2).strip()
                  for m in _FIELD_RE.finditer(block)}
        t = _build(fields)
        if t:
            transactions.append(t)
    return transactions


# ── XML ───────────────────────────────────────────────────────────────────────

def _parse_xml(content: str) -> list[BankTransaction]:
    transactions: list[BankTransaction] = []
    # Remove namespace prefixes que alguns bancos incluem
    content = re.sub(r'<(/?)[\w]+:', r'<\1', content)
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return []
    for stmttrn in root.iter('STMTTRN'):
        fields = {child.tag.upper(): (child.text or '').strip()
                  for child in stmttrn}
        t = _build(fields)
        if t:
            transactions.append(t)
    return transactions


# ── Entrada pública ───────────────────────────────────────────────────────────

def parse_ofx(file_bytes: bytes) -> list[BankTransaction]:
    content = _decode(file_bytes).strip()

    # Detecta formato: XML começa com '<?xml' ou '<OFX>'/'<ofx>'
    if re.match(r'<\?xml', content, re.IGNORECASE) or re.match(r'<OFX>', content, re.IGNORECASE):
        transactions = _parse_xml(content)
    else:
        transactions = _parse_sgml(content)
        # Fallback para XML se SGML não retornou nada
        if not transactions:
            transactions = _parse_xml(content)

    # Deduplica por FITID ou (data+desc+valor)
    seen: set[tuple] = set()
    unique: list[BankTransaction] = []
    for t in transactions:
        key = (t.day, t.month, t.year, t.description, t.amount)
        if key not in seen:
            seen.add(key)
            unique.append(t)

    unique.sort(key=lambda t: (t.month, t.day))
    return unique
