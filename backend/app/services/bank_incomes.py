"""Entradas de dinheiro que chegam do banco (Open Finance) → Rendas, depois da revisão.

Cada função faz uma coisa:
  is_income            o lançamento da Pluggy é entrada na conta corrente?
  sync_account         grava/atualiza as entradas de uma conta e classifica as novas
  classify             ignora resgate e transferência entre contas suas; reconhece o que já está em Rendas
  pair_with_income     uma saída que é transferência para outra conta sua some junto com a entrada
  suggestion           tipo (Salário, PIX recebido, Rendimento…), fixa/variável, fonte e renda prevista do mês
  approve              vira renda (ou marca a renda prevista do mês como recebida)

Nada vira renda sem a pessoa aprovar.
"""
import re
import unicodedata
from datetime import date, timedelta
from decimal import Decimal

from app.core.db import db
from app.core.types import utcnow
from app.models import BankAccount, BankConnection, BankIncome, BankTransaction, Salary, User

INCOME_TYPES = ('fixa', 'variavel')
TRANSFER_REASON = 'Transferência entre as suas contas.'
_SKIP = (
    (re.compile(r'same person transfer|transfer.*same ownership|mesma titularidade|entre contas', re.I), TRANSFER_REASON),
    (re.compile(r'resgate|^investments?$|aplica[cç][aã]o', re.I), 'Resgate de investimento: não é renda.'),
    (re.compile(r'estorno|devolu[cç][aã]o|reembolso|cashback', re.I), None),  # fica, mas como "Estorno/reembolso"
)
_KINDS = (  # (rótulo, fixa/variável, padrão na descrição/categoria do banco)
    ('Salário', 'fixa', re.compile(r'sal[aá]rio|salary|folha|proventos|vencimentos|pagto sal|payroll|pro.?labore', re.I)),
    ('Rendimento', 'variavel', re.compile(r'rendimento|juros|remunera[cç][aã]o|dividend|interest|proventos? de a[cç]', re.I)),
    ('Estorno/reembolso', 'variavel', re.compile(r'estorno|devolu[cç][aã]o|reembolso|cashback|refund', re.I)),
    ('PIX recebido', 'variavel', re.compile(r'\bpix\b', re.I)),
    ('Transferência recebida', 'variavel', re.compile(r'\bted\b|\bdoc\b|transf', re.I)),
)
_NOISE = re.compile(r'\b(pix|recebid[oa]|recebimento|ted|doc|transf\w*|cr[eé]dito|credito em conta|de|da|do|'
                    r'received|transfer|inbound)\b|[-–:]', re.I)


def _norm(text) -> str:
    return unicodedata.normalize('NFKD', str(text or '')).encode('ascii', 'ignore').decode().lower().strip()


def _money(value) -> float | None:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


# ── leitura ──────────────────────────────────────────────────────────────────

def is_income(acc: BankAccount, raw: dict) -> bool:
    """Entrada na conta corrente/poupança (no cartão, crédito é estorno e vai para as saídas)."""
    amount = _money(raw.get('amount')) or 0
    return acc.type == 'BANK' and (raw.get('type') == 'CREDIT' or (raw.get('type') is None and amount > 0))


def _apply(inc: BankIncome, raw: dict) -> None:
    from app.services.open_finance import local_date
    pay = raw.get('paymentData') if isinstance(raw.get('paymentData'), dict) else {}
    payer = pay.get('payer') if isinstance(pay.get('payer'), dict) else {}
    inc.date = local_date(raw.get('date')) or inc.date or date.today()
    inc.description = re.sub(r'\s+', ' ', str(raw.get('description') or raw.get('descriptionRaw') or 'Entrada')).strip()[:200]
    inc.amount = abs(_money(raw.get('amount')) or 0)
    inc.counterpart = (str(payer.get('name') or '').strip()[:120] or None)
    inc.payment_hint = str(pay.get('paymentMethod') or '')[:16] or None
    inc.pluggy_category = str(raw.get('category') or '')[:80] or None


def sync_account(acc: BankAccount, raws: list[dict], start: date, complete: bool) -> int:
    """Grava as entradas da conta desde `start`. Devolve quantas novas ficaram para revisar."""
    conn = acc.connection
    seen, new = set(), 0
    for raw in raws:
        pid = str(raw.get('id') or '')
        seen.add(pid)
        inc = BankIncome.query.filter_by(pluggy_id=pid).first()
        if inc is not None and (inc.account_id != acc.id or inc.reviewed_at is not None or inc.status == 'imported'):
            continue  # de outra conta, ou você já mexeu
        fresh = inc is None
        if fresh:
            inc = BankIncome(account_id=acc.id, user_id=conn.user_id, pluggy_id=pid, status='pending')
            db.session.add(inc)
        _apply(inc, raw)
        if inc.date < start or not inc.amount:
            if fresh:
                db.session.expunge(inc)
            continue
        db.session.flush()
        classify(inc)
        new += fresh and inc.status == 'pending'
    if complete:  # o banco deixou de mandar (pendente que virou lançado com outro id)
        for inc in acc.incomes.filter(BankIncome.date >= start, BankIncome.status.in_(('pending', 'duplicate'))).all():
            if inc.pluggy_id not in seen and inc.reviewed_at is None:
                db.session.delete(inc)
    db.session.flush()
    return new


# ── classificação ────────────────────────────────────────────────────────────

def _pair_banks(user_id: int):
    """Bancos onde procurar o outro lado da transferência: os da pessoa e os que a família compartilhou
    (banco privado de outra pessoa não é mexido)."""
    return (BankConnection.user_id == user_id) | (BankConnection.shared.is_(True) & BankConnection.user_id.in_(_family(user_id)))


def _family(user_id: int) -> list[int]:
    user = db.session.get(User, user_id)
    if user is None or not user.tenant_id:
        return [user_id]
    return [u.id for u in User.query.filter_by(tenant_id=user.tenant_id).all()]


def classify(inc: BankIncome) -> None:
    """Para revisar, ou ignorada (resgate, transferência entre contas suas), ou já em Rendas."""
    inc.status, inc.reason, inc.salary_id = 'pending', None, None
    text = f'{inc.description} {inc.pluggy_category or ""}'
    for pattern, why in _SKIP:
        if why and pattern.search(text):
            inc.status, inc.reason = 'ignored', why
            return
    if _pair(inc):
        return
    received = (Salary.query.filter(Salary.user_id == inc.user_id, Salary.year == inc.date.year,
                                    Salary.month == inc.date.month, Salary.received.is_(True),
                                    Salary.amount == Decimal(str(inc.amount)))
                .filter(~Salary.id.in_(db.session.query(BankIncome.salary_id).filter(BankIncome.salary_id.isnot(None),
                                                                                     BankIncome.id != inc.id)))
                .first())
    if received is not None:
        inc.status, inc.salary_id, inc.reason = 'duplicate', received.id, 'Já estava em Rendas.'


def _counterpart_out(inc: BankIncome):
    """Saída do mesmo valor, até 1 dia de diferença, em OUTRA conta corrente da família."""
    return (BankTransaction.query.join(BankAccount).join(BankConnection)
            .filter(_pair_banks(inc.user_id), BankAccount.type == 'BANK', BankAccount.id != inc.account_id,
                    BankTransaction.kind == 'expense', BankTransaction.amount == Decimal(str(inc.amount)),
                    BankTransaction.date.between(inc.date - timedelta(days=1), inc.date + timedelta(days=1)),
                    BankTransaction.reviewed_at.is_(None),
                    (BankTransaction.status == 'pending')
                    | ((BankTransaction.status == 'ignored') & BankTransaction.reason.like('Transfer%')))
            .order_by(BankTransaction.date).first())


def _mark_pair(inc: BankIncome, tx: BankTransaction) -> None:
    inc.status, inc.reason, inc.transfer_tx_id = 'ignored', TRANSFER_REASON, tx.id
    tx.status, tx.reason = 'ignored', TRANSFER_REASON


def _pair(inc: BankIncome) -> bool:
    tx = _counterpart_out(inc)
    if tx is None:
        return False
    _mark_pair(inc, tx)
    return True


def pair_with_income(tx: BankTransaction) -> bool:
    """A saída chegou depois da entrada: se for transferência para outra conta da família, some junto."""
    if tx.kind != 'expense' or tx.account.type != 'BANK' or tx.status != 'pending':
        return False
    inc = (BankIncome.query.join(BankAccount).join(BankConnection)
           .filter(_pair_banks(tx.user_id), BankIncome.account_id != tx.account_id,
                   BankIncome.amount == Decimal(str(tx.amount)), BankIncome.status == 'pending',
                   BankIncome.reviewed_at.is_(None),
                   BankIncome.date.between(tx.date - timedelta(days=1), tx.date + timedelta(days=1)))
           .first())
    if inc is None:
        return False
    _mark_pair(inc, tx)
    return True


# ── sugestão ─────────────────────────────────────────────────────────────────

def _kind(inc: BankIncome) -> tuple[str, str]:
    text = f'{inc.description} {inc.pluggy_category or ""} {inc.payment_hint or ""}'
    return next(((label, kind) for label, kind, pattern in _KINDS if pattern.search(text)), ('Entrada', 'variavel'))


def _source(inc: BankIncome) -> str:
    """De quem veio: o pagador que o banco informa, ou a descrição sem "PIX RECEBIDO" e afins."""
    if inc.counterpart:
        return inc.counterpart.title()[:60]
    clean = re.sub(r'\s+', ' ', _NOISE.sub(' ', inc.description)).strip()
    return (clean.title() if clean.isupper() else clean)[:60] or inc.description[:60]


def _looks_like_me(inc: BankIncome) -> bool:
    """O pagador tem o nome da própria pessoa: dinheiro dela vindo de outro banco (não conectado)."""
    user = db.session.get(User, inc.user_id)
    words = _norm(user.name).split() if user else []
    who = _norm(inc.counterpart or inc.description)
    return len(words) >= 2 and words[0] in who.split() and words[-1] in who.split()


def _previous_month(inc: BankIncome) -> tuple[int, int]:
    first = inc.date.replace(day=1) - timedelta(days=1)
    return first.year, first.month


def _same_source(a: str | None, b: str | None) -> bool:
    a, b = _norm(a), _norm(b)
    return bool(a and b) and (a == b or a.split()[0] == b.split()[0])


def _repeats(inc: BankIncome, source: str) -> bool:
    """No mês passado entrou algo parecido (mesma fonte, valor até 20% diferente): renda fixa."""
    year, month = _previous_month(inc)
    amount = float(inc.amount)
    for s in Salary.query.filter_by(user_id=inc.user_id, year=year, month=month).all():
        if _same_source(s.company, source) and abs(float(s.amount) - amount) <= 0.2 * amount:
            return True
    return False


def _expected(inc: BankIncome) -> Salary | None:
    """Renda do mês ainda não recebida, de valor parecido (até 15%): é ela que chegou."""
    amount = float(inc.amount)
    rows = Salary.query.filter(Salary.user_id == inc.user_id, Salary.year == inc.date.year,
                               Salary.month == inc.date.month, Salary.received.isnot(True)).all()
    close = [s for s in rows if abs(float(s.amount) - amount) <= 0.15 * amount]
    return min(close, key=lambda s: abs(float(s.amount) - amount)) if close else None


def suggestion(inc: BankIncome) -> dict:
    label, kind = _kind(inc)
    source = _source(inc)
    repeats = _repeats(inc, source)
    if repeats:
        kind = 'fixa'
    expected = _expected(inc)
    mine = _looks_like_me(inc)
    return {'label': 'Transferência sua?' if mine else label, 'income_type': kind, 'company': source,
            'recurring': repeats, 'own_transfer_hint': mine,
            'expected': ({'id': expected.id, 'company': expected.company, 'amount': float(expected.amount)}
                         if expected is not None else None)}


# ── aprovar ──────────────────────────────────────────────────────────────────

def approve(inc: BankIncome, uids: list[int], income_type: str, company: str | None,
            use_expected: bool = True) -> tuple[Salary, bool]:
    """Vira renda do mês da entrada, recebida. Com renda prevista parecida no mês, marca ela.
    Devolve (renda, True se marcou uma prevista em vez de criar)."""
    if inc.status == 'imported' and inc.salary_id:
        return db.session.get(Salary, inc.salary_id), False
    expected = _expected(inc) if use_expected else None
    if expected is not None and expected.user_id in uids:
        expected.received, expected.amount = True, Decimal(str(inc.amount))
        salary = expected
        marked = True
    else:
        marked = False
        salary = Salary(user_id=inc.user_id, year=inc.date.year, month=inc.date.month, amount=Decimal(str(inc.amount)),
                        company=(company or _source(inc)).strip()[:200] or None,
                        income_type=income_type if income_type in INCOME_TYPES else 'variavel',
                        payment_day=inc.date.day if income_type == 'fixa' else None,
                        payment_day_type='fixo' if income_type == 'fixa' else None, received=True)
        db.session.add(salary)
        db.session.flush()
    inc.status, inc.salary_id, inc.reason, inc.reviewed_at = 'imported', salary.id, None, utcnow()
    return salary, marked
