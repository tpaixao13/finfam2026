"""Open Finance (Pluggy): leitura de bancos, contas e lançamentos, sem HTTP do FinFam.

Regra de ouro: nada vira despesa sozinho. Os lançamentos que chegam do banco ficam em
`bank_transactions` como `pending` (revisar), `duplicate` (já estava no FinFam) ou
`ignored` (pagamento de fatura, transferência entre contas suas...). Só `approve`
lança — usando o mesmo `create_expense` do site e do bot.
"""
import logging
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.core.db import db
from app.core.security import decrypt_field
from app.core.types import utcnow
from app.models import BankAccount, BankConnection, BankTransaction, CreditCard, Expense, PluggyConfig
from app.services import pluggy as pg
from app.services.categorizer import categorize
from app.services.expense_service import ExpenseData, create_expense
from app.services.finance import CATEGORIES, CREDIT, DEBIT

log = logging.getLogger('finfam.pluggy')

PAGE_SIZE = 500
MAX_PAGES = 20           # 10 mil lançamentos por conta e leitura é mais que suficiente
RESYNC_DAYS = 40         # a cada leitura, relê os últimos 40 dias (pendentes viram lançados)
WINDOW_MONTHS = (1, 3, 6, 12)
_BRT = timezone(timedelta(hours=-3))
_ID = re.compile(r'[A-Za-z0-9-]{1,64}')

# Saídas que não são gasto novo (as compras já vêm pelo cartão / é dinheiro seu mudando de lugar)
_SKIP = (
    (re.compile(r'credit card payment|pagamento de fatura|pgto fatura|pag fatura|pagto cartao|pagamento cartao', re.I),
     'Pagamento de fatura: as compras já vêm pelo cartão.'),
    (re.compile(r'same person transfer|transfer.*same ownership|mesma titularidade|entre contas', re.I),
     'Transferência entre contas suas.'),
    (re.compile(r'^investments?$|aplica[cç][aã]o|resgate|investimento', re.I),
     'Aplicação/investimento.'),
)


# ── conversões ───────────────────────────────────────────────────────────────

def client_for(user_id: int) -> pg.PluggyClient | None:
    cfg = PluggyConfig.query.filter_by(user_id=user_id).first()
    return pg.PluggyClient(cfg.client_id, decrypt_field(cfg.client_secret)) if cfg else None


def parse_when(value) -> datetime | None:
    """ISO da Pluggy → datetime UTC "naive" (como o resto do banco)."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def local_date(value) -> date | None:
    """Data do lançamento no fuso de Brasília (a Pluggy manda em UTC)."""
    dt = parse_when(value)
    return (dt.replace(tzinfo=timezone.utc).astimezone(_BRT).date()) if dt else None


def money(value) -> float | None:
    try:
        return round(float(value), 2) if value is not None else None
    except (TypeError, ValueError):
        return None


def last4(number) -> str | None:
    digits = re.sub(r'\D', '', str(number or ''))
    return digits[-4:] if len(digits) >= 4 else None


def months_back(today: date, months: int) -> date:
    m, y = today.month - months, today.year
    while m <= 0:
        m, y = m + 12, y - 1
    return date(y, m, min(today.day, 28))


def add_months(d: date, months: int) -> date:
    m, y = d.month + months, d.year
    while m > 12:
        m, y = m - 12, y + 1
    return date(y, m, min(d.day, 28))


# ── banco e contas ───────────────────────────────────────────────────────────

def apply_item(conn: BankConnection, item: dict) -> None:
    connector = item.get('connector') or {}
    color = str(connector.get('primaryColor') or '').lstrip('#')
    conn.bank_name = str(connector.get('name') or conn.bank_name or 'Banco')[:120]
    conn.bank_color = f'#{color[:6]}' if re.fullmatch(r'[0-9a-fA-F]{6}', color[:6]) else conn.bank_color
    conn.status = str(item.get('status') or '')[:32] or conn.status
    conn.execution_status = str(item.get('executionStatus') or '')[:48] or None
    err = item.get('error') or {}
    conn.error_message = (str(err.get('message') or '')[:300] or None) if isinstance(err, dict) else None
    conn.last_updated_at = parse_when(item.get('lastUpdatedAt')) or conn.last_updated_at


def _auto_card(conn: BankConnection, acc: BankAccount) -> None:
    """Liga o cartão do banco ao cartão de crédito da pessoa com os mesmos 4 últimos dígitos."""
    digits = last4(acc.number)
    if acc.type != 'CREDIT' or acc.card_id or not digits:
        return
    cards = [c for c in CreditCard.query.filter_by(user_id=conn.user_id, last_digits=digits).all()
             if c.card_type == 'credit' or c.supports_credit]
    if len(cards) == 1:
        acc.card_id = cards[0].id


def sync_accounts(conn: BankConnection, client: pg.PluggyClient) -> None:
    apply_item(conn, client.item(conn.item_id))
    seen = set()
    for a in client.accounts(conn.item_id):
        pid = str(a.get('id') or '')
        if not _ID.fullmatch(pid):
            continue
        seen.add(pid)
        acc = BankAccount.query.filter_by(pluggy_account_id=pid).first()
        if acc is not None and acc.connection_id != conn.id:
            continue  # pertence a outra conexão: nunca mistura
        if acc is None:
            acc = BankAccount(connection_id=conn.id, pluggy_account_id=pid, type='BANK')
            db.session.add(acc)
        credit = a.get('creditData') or {}
        acc.type = 'CREDIT' if a.get('type') == 'CREDIT' else 'BANK'
        acc.subtype = str(a.get('subtype') or '')[:32] or None
        acc.name = str(a.get('marketingName') or a.get('name') or '')[:120] or None
        acc.number = str(a.get('number') or '')[:32] or None
        acc.balance = money(a.get('balance'))
        acc.currency = str(a.get('currencyCode') or 'BRL')[:3]
        acc.credit_limit = money(credit.get('creditLimit')) if isinstance(credit, dict) else None
        _auto_card(conn, acc)
    for acc in conn.accounts.all():
        if acc.pluggy_account_id not in seen:
            db.session.delete(acc)  # o banco deixou de devolver a conta
    db.session.flush()


# ── lançamentos ──────────────────────────────────────────────────────────────

def _expense_date(e: Expense) -> date:
    return date(e.purchase_year or e.year, e.purchase_month or e.month, max(1, min(e.day or 1, 28)))


def target_date(tx: BankTransaction) -> date:
    """Data de compra que o FinFam usa ao lançar (parcela k → mês da compra + k-1)."""
    if tx.installment_number and tx.installment_number > 1 and tx.purchase_date:
        return add_months(tx.purchase_date, tx.installment_number - 1)
    return tx.purchase_date or tx.date


def find_existing(tx: BankTransaction, card_id: int | None) -> Expense | None:
    """Despesa da pessoa que já representa este lançamento (mesmo valor, data perto;
    cartão igual quando o cartão do banco está ligado; mesma parcela)."""
    when = target_date(tx)
    n = tx.installment_number if tx.total_installments and tx.total_installments > 1 else None
    tolerance = 40 if n and n > 1 else 3
    used = {row[0] for row in db.session.query(BankTransaction.expense_id)
            .filter(BankTransaction.user_id == tx.user_id, BankTransaction.expense_id.isnot(None),
                    BankTransaction.id != (tx.id or 0))}
    q = Expense.query.filter(Expense.user_id == tx.user_id, Expense.amount == Decimal(str(tx.amount)),
                             Expense.year.between(when.year - 1, when.year + 1))
    best = None
    for e in q.all():
        if e.id in used:
            continue
        if card_id and e.card_id and e.card_id != card_id:
            continue
        if n and n > 1 and e.installment_number and e.installment_number != n:
            continue
        gap = abs((_expense_date(e) - when).days)
        if gap <= tolerance and (best is None or gap < best[0]):
            best = (gap, e)
    return best[1] if best else None


def _skip_reason(description: str, category: str | None) -> str | None:
    for rx, why in _SKIP:
        if rx.search(category or '') or rx.search(description):
            return why
    return None


def _apply_tx(tx: BankTransaction, raw: dict) -> None:
    meta = raw.get('creditCardMetadata') or {}
    meta = meta if isinstance(meta, dict) else {}
    pay = raw.get('paymentData') or {}
    tx.date = local_date(raw.get('date')) or tx.date or date.today()
    tx.purchase_date = local_date(meta.get('purchaseDate'))
    tx.description = re.sub(r'\s+', ' ', str(raw.get('description') or raw.get('descriptionRaw') or 'Lançamento')).strip()[:200]
    tx.amount = abs(money(raw.get('amount')) or 0)
    tx.installment_number = meta.get('installmentNumber') if isinstance(meta.get('installmentNumber'), int) else None
    tx.total_installments = meta.get('totalInstallments') if isinstance(meta.get('totalInstallments'), int) else None
    tx.total_amount = abs(money(meta.get('totalAmount')) or 0) or None
    tx.pluggy_category = str(raw.get('category') or '')[:80] or None
    tx.payment_hint = (str(pay.get('paymentMethod') or '')[:16] or None) if isinstance(pay, dict) else None
    tx.bank_status = str(raw.get('status') or '')[:12] or None


def _is_expense(acc: BankAccount, raw: dict) -> bool:
    """Saída de dinheiro. No cartão, valor positivo é compra; negativo é pagamento/estorno."""
    amount = money(raw.get('amount')) or 0
    if acc.type == 'CREDIT':
        return amount > 0 and raw.get('type', 'DEBIT') != 'CREDIT'
    return raw.get('type') == 'DEBIT' or (raw.get('type') is None and amount < 0)


def _fetch(client: pg.PluggyClient, account_id: str, since: date) -> list[dict]:
    out, page = [], 1
    while page <= MAX_PAGES:
        r = client.call('GET', f'/transactions?accountId={pg.safe_id(account_id)}'
                               f'&from={since.isoformat()}&pageSize={PAGE_SIZE}&page={page}') or {}
        out.extend(r.get('results') or [])
        if page >= int(r.get('totalPages') or 1):
            break
        page += 1
    return out


def sync_transactions(conn: BankConnection, client: pg.PluggyClient, today: date | None = None) -> int:
    """Traz os lançamentos novos de todas as contas. Devolve quantos ficaram para revisar."""
    today = today or date.today()
    start = conn.import_from or months_back(today, 1)
    since = start if conn.tx_synced_at is None else max(start, today - timedelta(days=RESYNC_DAYS))
    new_pending = 0
    for acc in conn.accounts.all():
        seen = set()
        for raw in _fetch(client, acc.pluggy_account_id, since):
            pid = str(raw.get('id') or '')
            if not _ID.fullmatch(pid) or not _is_expense(acc, raw):
                continue
            seen.add(pid)
            tx = BankTransaction.query.filter_by(pluggy_id=pid).first()
            if tx is not None and tx.account_id != acc.id:
                continue
            fresh = tx is None
            if fresh:
                tx = BankTransaction(account_id=acc.id, user_id=conn.user_id, pluggy_id=pid, status='pending')
                db.session.add(tx)
            elif tx.status != 'pending':
                continue  # já revisado: não mexe
            _apply_tx(tx, raw)
            if tx.date < start or not tx.amount:
                if fresh:
                    db.session.expunge(tx)
                continue
            why = _skip_reason(tx.description, tx.pluggy_category)
            match = None if why else find_existing(tx, acc.card_id)
            if why:
                tx.status, tx.reason = 'ignored', why
            elif match:
                tx.status, tx.expense_id = 'duplicate', match.id
                tx.reason = 'Já estava no FinFam.'
            elif fresh:
                new_pending += 1
        # pendente que o banco deixou de mandar (ex.: compra pendente que virou outra) some da revisão
        for tx in acc.transactions.filter(BankTransaction.status == 'pending', BankTransaction.date >= since).all():
            if tx.pluggy_id not in seen:
                db.session.delete(tx)
    conn.tx_synced_at = utcnow()
    db.session.flush()
    return new_pending


def sync(conn: BankConnection, client: pg.PluggyClient, today: date | None = None) -> int:
    """Relê banco, contas e lançamentos."""
    sync_accounts(conn, client)
    count = sync_transactions(conn, client, today)
    conn.needs_sync = False
    return count


def sync_stale(user_id: int | None = None, max_age_hours: int | None = None) -> int:
    """Relê as conexões com novidade (webhook) — e, com max_age_hours, também as antigas.
    Falha de um banco não impede os outros. Devolve quantos lançamentos novos chegaram."""
    q = BankConnection.query
    if user_id is not None:
        q = q.filter_by(user_id=user_id)
    total = 0
    limit = utcnow() - timedelta(hours=max_age_hours) if max_age_hours else None
    for conn in q.all():
        stale = conn.needs_sync or (limit is not None and (conn.tx_synced_at is None or conn.tx_synced_at < limit))
        if not stale:
            continue
        client = client_for(conn.user_id)
        if client is None:
            continue
        try:
            total += sync(conn, client)
            db.session.commit()
        except pg.PluggyError as exc:
            db.session.rollback()
            log.warning('Sincronização do item %s falhou: %s', conn.item_id, exc)
    return total


# ── revisão ──────────────────────────────────────────────────────────────────

def suggested_method(tx: BankTransaction) -> str:
    if tx.account.type == 'CREDIT':
        return CREDIT
    return 'PIX' if (tx.payment_hint or '').upper() == 'PIX' or 'PIX' in tx.description.upper() else DEBIT


def suggestion(tx: BankTransaction, uids: list, tenant_id: int | None) -> dict:
    """Categoria e forma de pagamento sugeridas para o lançamento."""
    category, source = categorize(tx.description, uids, tenant_id) if tenant_id else (None, None)
    return {'category': category or 'Outros', 'category_source': source, 'payment_method': suggested_method(tx)}


def approve(tx: BankTransaction, uids: list, category: str, description: str | None = None,
            payment_method: str | None = None) -> Expense:
    """Lança a despesa (mesma regra do site: fatura, parcelas) e marca como importado."""
    acc, conn = tx.account, tx.account.connection
    when = target_date(tx)
    desc = (description or tx.description).strip()[:200] or tx.description
    n, k = tx.total_installments or 0, tx.installment_number or 0
    credit = acc.type == 'CREDIT'
    method = CREDIT if credit else (payment_method if payment_method in ('PIX', DEBIT) else suggested_method(tx))
    installments = None
    amount = Decimal(str(tx.amount))
    if credit and n > 1:
        if k <= 1:  # 1ª parcela: lança a compra inteira parcelada, como no site
            installments = n
            amount = Decimal(str(tx.total_amount)) if tx.total_amount else amount * n
        else:       # parcela do meio sem a compra original: lança só esta parcela
            desc = f'{desc} ({k}/{n})'
    data = ExpenseData(user_id=tx.user_id, description=desc, amount=amount,
                       category=category if category else 'Outros', payment_method=method,
                       day=when.day, month=when.month, year=when.year,
                       bank=conn.bank_name, card_id=acc.card_id if credit else None,
                       paid=not credit, installments=installments)
    result = create_expense(uids, data)
    first = result['expenses'][0]
    tx.status, tx.expense_id, tx.reason, tx.reviewed_at = 'imported', first.id, None, utcnow()
    return first


def valid_category(category: str | None, allowed: list[str]) -> str:
    category = (category or '').strip()[:60]
    return category if category in allowed or category in CATEGORIES else 'Outros'
