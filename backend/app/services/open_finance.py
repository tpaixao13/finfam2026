"""Open Finance (Pluggy): leitura de bancos, contas e lançamentos, sem HTTP do FinFam.

Regra de ouro: nada vira despesa sozinho. Os lançamentos que chegam do banco ficam em
`bank_transactions` como `pending` (revisar), `duplicate` (já estava no FinFam) ou
`ignored` (pagamento de fatura, transferência entre contas suas...). Só `approve`
lança — usando o mesmo `create_expense` do site e do bot.
"""
import logging
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.core.db import db
from app.core.security import decrypt_field
from app.core.types import utcnow
from app.models import BankAccount, BankBill, BankConnection, BankTransaction, CreditCard, Expense, PluggyConfig, User
from app.services import pluggy as pg
from app.services.categorizer import categorize
from app.services.expense_service import ExpenseData, create_expense
from app.services.finance import CATEGORIES, CREDIT, DEBIT, brl

log = logging.getLogger('finfam.pluggy')

MAX_PAGES = 20           # páginas de 500: 10 mil lançamentos por conta e leitura bastam
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
    (re.compile(r'total da fatura anterior|saldo (da fatura )?anterior|saldo em atraso', re.I),
     'Saldo da fatura anterior: não é compra.'),
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


def apply_card_limit(acc: BankAccount) -> bool:
    """Cartão do FinFam ligado ao do banco: limite, vencimento e melhor dia (= fechamento) vêm do
    banco. O limite vai na conta de crédito quando o cartão divide limite. Devolve True se mudou."""
    if acc.type != 'CREDIT' or not acc.card_id:
        return False
    card = db.session.get(CreditCard, acc.card_id)
    if card is None:
        return False
    changed = False
    # o FinFam guarda "melhor dia < vencimento" no mesmo mês; fecha 28/vence 5 fica só pelo mês da fatura
    if acc.close_day and acc.due_day and acc.close_day < acc.due_day and \
            (card.best_buy_day, card.due_day) != (acc.close_day, acc.due_day):
        card.best_buy_day, card.due_day = acc.close_day, acc.due_day
        changed = True
    if not acc.credit_limit:
        return changed
    limit = Decimal(str(acc.credit_limit))
    target = card.account if card.account is not None else card
    if target.credit_limit is not None and Decimal(str(target.credit_limit)) == limit:
        return changed
    target.credit_limit = limit
    if target is not card and card.credit_limit is not None:
        card.credit_limit = limit
    return True


def sync_bills(acc: BankAccount, client: pg.PluggyClient) -> None:
    """Faturas do cartão na Pluggy (vencimento e total). Banco que não informa: segue sem."""
    if acc.type != 'CREDIT':
        return
    try:
        r = client.call('GET', f'/bills?accountId={pg.safe_id(acc.pluggy_account_id)}') or {}
    except pg.PluggyError as exc:
        log.info('Faturas da conta %s não vieram: %s', acc.pluggy_account_id, exc)
        return
    for b in (r.get('results') or []) if isinstance(r, dict) else []:
        pid, due = str(b.get('id') or ''), local_date(b.get('dueDate'))
        if not _ID.fullmatch(pid) or due is None:
            continue
        bill = BankBill.query.filter_by(pluggy_id=pid).first()
        if bill is None:
            bill = BankBill(account_id=acc.id, pluggy_id=pid, due_date=due)
            db.session.add(bill)
        elif bill.account_id != acc.id:
            continue
        bill.due_date, bill.total = due, money(b.get('totalAmount'))
    db.session.flush()
    latest = acc.bills.order_by(BankBill.due_date.desc()).first()
    if latest is not None and not acc.due_day:
        acc.due_day = latest.due_date.day


def infer_close_day(acc: BankAccount, raws: list[dict]) -> int | None:
    """Banco que não informa o fechamento (C6, Santander pelo Meu Pluggy): descobre o dia de
    corte pelas compras que vêm com o código da fatura. Só usa se explica 90%+ delas."""
    if acc.type != 'CREDIT' or acc.close_day or not acc.due_day:
        return None
    due_of = {b.pluggy_id: b.due_date for b in acc.bills.all()}
    samples = []
    for r in raws:
        meta = r.get('creditCardMetadata') or {}
        ref = str(meta.get('billId') or '') if isinstance(meta, dict) else ''
        day = local_date(r.get('date'))
        if ref in due_of and day:
            samples.append((day, (due_of[ref].year, due_of[ref].month)))
    if len(samples) < 10:
        return None
    best = max(((sum(bill_of(d, cut, acc.due_day) == b for d, b in samples), cut) for cut in range(1, 29)))
    if best[0] >= 0.9 * len(samples):
        acc.close_day = best[1]
        return best[1]
    return None


def tx_bill(acc: BankAccount, tx: BankTransaction) -> tuple[int, int] | None:
    """Fatura (ano, mês) da compra do cartão: a que o banco diz (billId); senão pelos dias de
    fechamento/vencimento; senão pela lista de faturas (a 1ª que vence 7+ dias depois da compra,
    ou a próxima depois da última conhecida). None quando o banco não dá pista nenhuma."""
    if acc.type != 'CREDIT':
        return None
    if tx.bill_ref:
        bill = BankBill.query.filter_by(pluggy_id=tx.bill_ref, account_id=acc.id).first()
        if bill is not None:
            return bill.due_date.year, bill.due_date.month
    if acc.close_day and acc.due_day:
        return bill_of(tx.date, acc.close_day, acc.due_day)
    bills = acc.bills.order_by(BankBill.due_date).all()
    if not bills:
        return None
    today = date.today()
    if tx.bank_status == 'PENDING':
        # pendente = fatura em aberto: a que ainda vai vencer, ou a seguinte à última fechada
        open_bill = next((b for b in bills if b.due_date > today), None)
        if open_bill is not None:
            return open_bill.due_date.year, open_bill.due_date.month
        nxt = add_months(bills[-1].due_date, 1)
        while nxt <= today:
            nxt = add_months(nxt, 1)
        return nxt.year, nxt.month
    for b in bills:
        if b.due_date >= tx.date + timedelta(days=7):
            return b.due_date.year, b.due_date.month
    last = add_months(bills[-1].due_date, 1)
    while last < tx.date + timedelta(days=7):
        last = add_months(last, 1)
    return last.year, last.month


def bill_of(day: date, close_day: int, due_day: int) -> tuple[int, int]:
    """(ano, mês) da fatura que vence depois do fechamento que contém `day`.
    Ex.: fecha 3/vence 10 → compra 05/10 vai na fatura de novembro; fecha 28/vence 5 → compra
    05/10 fecha 28/10 e vence 05/11 (novembro)."""
    y, m = day.year, day.month
    if day.day > close_day:  # depois do fechamento deste mês: entra no próximo fechamento
        m, y = (1, y + 1) if m == 12 else (m + 1, y)
    if due_day <= close_day:  # vence no mês seguinte ao fechamento
        m, y = (1, y + 1) if m == 12 else (m + 1, y)
    return y, m


def card_days(acc: BankAccount) -> tuple[int, int]:
    """(vencimento, melhor dia de compra) para criar o cartão. O FinFam exige melhor dia antes do
    vencimento; quando o banco fecha num mês e vence no outro (ex.: fecha 28, vence 5), usa a
    véspera do vencimento — a pessoa ajusta no cartão se quiser."""
    due = acc.due_day or 10
    best = acc.close_day or max(1, due - 7)
    if best >= due:
        best = max(1, due - 1)
    return due, best


def create_card_from(acc: BankAccount) -> CreditCard:
    """Cadastra no FinFam o cartão do banco (final, banco, limite, vencimento, melhor dia) e liga."""
    from app.services.card_service import apply_card_fields
    digits = last4(acc.number)
    if acc.type != 'CREDIT' or not digits:
        raise ValueError('Só dá para criar cartão a partir de um cartão de crédito do banco com número.')
    if acc.card_id:
        raise ValueError('Este cartão do banco já está ligado a um cartão do FinFam.')
    conn = acc.connection
    due, best = card_days(acc)
    card = CreditCard(user_id=conn.user_id)
    db.session.add(card)
    apply_card_fields(card, special_type=None, supports_credit=True, supports_debit=False,
                      nickname=(acc.name or '')[:50] or None, last_digits=digits, bank=conn.label[:60],
                      due_day=due, best_buy_day=best,
                      credit_limit=Decimal(str(acc.credit_limit)) if acc.credit_limit else None,
                      is_virtual=False, is_additional=False, monthly_amount=None, renewal_day=None)
    db.session.flush()
    acc.card_id = card.id
    apply_card_limit(acc)
    return card


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
        credit = credit if isinstance(credit, dict) else {}
        acc.credit_limit = money(credit.get('creditLimit'))
        acc.available_limit = money(credit.get('availableCreditLimit'))
        close, due = local_date(credit.get('balanceCloseDate')), local_date(credit.get('balanceDueDate'))
        acc.close_day, acc.due_day = (close.day if close else None), (due.day if due else None)
        _auto_card(conn, acc)
        apply_card_limit(acc)
    for acc in conn.accounts.all():
        if acc.pluggy_account_id not in seen:
            db.session.delete(acc)  # o banco deixou de devolver a conta
    db.session.flush()


def _account_key(acc: BankAccount) -> tuple | None:
    digits = last4(acc.number)
    return (acc.type, digits, (acc.name or '').strip().lower()) if digits else None


def same_accounts_elsewhere(conn: BankConnection) -> bool:
    """A conexão nova traz as mesmas contas de outra conexão da pessoa (mesmo banco duas vezes)?"""
    mine = {k for k in (_account_key(a) for a in conn.accounts.all()) if k}
    if not mine:
        return False
    others = (BankAccount.query.join(BankConnection)
              .filter(BankConnection.user_id == conn.user_id, BankConnection.id != conn.id).all())
    return any(_account_key(a) in mine for a in others)


# ── lançamentos ──────────────────────────────────────────────────────────────

def _expense_date(e: Expense) -> date:
    return date(e.purchase_year or e.year, e.purchase_month or e.month, max(1, min(e.day or 1, 28)))


def target_date(tx: BankTransaction) -> date:
    """Data que o FinFam usa: 1ª parcela/à vista = data da compra; parcela k>1 = data em que
    ela entrou na fatura (a "data da compra original" que alguns bancos mandam não é confiável)."""
    if tx.installment_number and tx.installment_number > 1:
        return tx.date
    return tx.purchase_date or tx.date


def card_family(card_id: int | None) -> set[int]:
    """O cartão e os que vêm junto no mesmo cartão do banco: os da mesma conta de crédito e os
    de crédito da família no mesmo banco (adicional no nome de outra pessoa fica noutra conta)."""
    if not card_id:
        return set()
    card = db.session.get(CreditCard, card_id)
    if card is None:
        return {card_id}
    ids = {card.id}
    if card.account_id:
        ids |= {c.id for c in CreditCard.query.filter_by(account_id=card.account_id).all()}
    if card.bank:
        uids = family_of(card.user_id)[0]
        ids |= {c.id for c in CreditCard.query.filter(CreditCard.user_id.in_(uids)).all()
                if (c.card_type == 'credit' or c.supports_credit) and (c.bank or '').casefold() == card.bank.casefold()}
    return ids


def card_used(tx: BankTransaction) -> CreditCard | None:
    """Cartão do FinFam usado na compra: o do final que o banco informa (virtual ou adicional
    da mesma conta de crédito do cartão ligado); senão, o cartão ligado."""
    acc = tx.account
    if acc.type != 'CREDIT' or not acc.card_id:
        return None
    if tx.card_digits:
        uids, _ = family_of(tx.user_id)
        siblings = card_family(acc.card_id)
        for c in CreditCard.query.filter(CreditCard.user_id.in_(uids), CreditCard.last_digits == tx.card_digits).all():
            if c.id in siblings:
                return c
    return db.session.get(CreditCard, acc.card_id)


def take_bank_amount(e: Expense, tx: BankTransaction) -> None:
    """Parcela reconhecida com centavos de diferença: fica com o valor que o banco cobrou."""
    bank = Decimal(str(tx.amount)) * (-1 if tx.kind == 'refund' else 1)
    if e.installment_number and Decimal(str(e.amount)) != bank and abs(Decimal(str(e.amount)) - bank) <= Decimal('0.10'):
        e.amount = bank


def find_existing(tx: BankTransaction, card_id: int | None) -> Expense | None:
    """Despesa da pessoa que já representa este lançamento (mesmo valor, data perto;
    cartão igual quando o cartão do banco está ligado; mesma parcela)."""
    when = target_date(tx)
    n = tx.installment_number if tx.total_installments and tx.total_installments > 1 else None
    tolerance = 40 if n and n > 1 else 3
    account = tx.account or db.session.get(BankAccount, tx.account_id)  # recém-criado ainda sem o vínculo
    credit = account.type == 'CREDIT'
    siblings = card_family(card_id)
    # no cartão, a compra pode estar no nome de outra pessoa da família (adicional/virtual da conta)
    uids = family_of(tx.user_id)[0] if credit and siblings else [tx.user_id]
    used = {row[0] for row in db.session.query(BankTransaction.expense_id)
            .filter(BankTransaction.user_id.in_(uids), BankTransaction.expense_id.isnot(None),
                    BankTransaction.id != (tx.id or 0))}
    amount = Decimal(str(tx.amount)) * (-1 if tx.kind == 'refund' else 1)
    # a mesma parcela da mesma compra pode vir com centavos de diferença (o banco põe o
    # arredondamento na 1ª parcela: 120,94 + 120,92 + ...): parcela igual aceita até R$ 0,10
    slack = Decimal('0.10') if n and n > 1 else Decimal('0')
    q = Expense.query.filter(Expense.user_id.in_(uids), Expense.amount.between(amount - slack, amount + slack),
                             Expense.year.between(when.year - 1, when.year + 1))
    best = None
    for e in q.all():
        if e.id in used:
            continue
        if Decimal(str(e.amount)) != amount and e.installment_number != n:
            continue  # centavos de diferença só valem para a mesma parcela
        if e.user_id != tx.user_id and e.card_id not in siblings:
            continue  # de outra pessoa só vale no mesmo cartão/conta
        allowed = (CREDIT,) if credit else ('PIX', DEBIT)
        if e.payment_method not in allowed:
            continue  # cartão só com crédito; conta só com PIX/débito (dinheiro e VR/VA não saem do banco)
        if siblings and e.card_id and e.card_id not in siblings:
            continue
        if n and n > 1 and e.installment_number and e.installment_number != n:
            continue
        gap = abs((_expense_date(e) - when).days)
        # parcela com o mesmo número: o FinFam guarda no mês da fatura (até ~2 meses depois do lançamento)
        limit = 70 if n and n > 1 and e.installment_number == n else tolerance
        if gap <= limit and (best is None or gap < best[0]):
            best = (gap, e)
    return best[1] if best else None


def reconcile_bill_payment(tx: BankTransaction, bills_only: bool = False, max_gap: int = 20) -> str | None:
    """Pagamento de fatura saindo da conta: acha a fatura do FinFam com o mesmo total (mês do
    pagamento, o anterior ou o seguinte) e marca as compras dela como pagas. Devolve o motivo."""
    from app.services.finance import MONTH_NAMES_SHORT, month_offset
    uids, _ = family_of(tx.user_id)
    amount = Decimal(str(tx.amount))
    # 1) pela fatura que o banco informa (mesmo total, vencimento até 20 dias do pagamento)
    best = None
    paying = tx.account or db.session.get(BankAccount, tx.account_id)
    for b in (BankBill.query.join(BankAccount).join(BankConnection)
              .filter(BankConnection.user_id.in_(uids), BankBill.total.isnot(None)).all()):
        gap = abs((b.due_date - tx.date).days)
        if abs(Decimal(str(b.total)) - amount) > Decimal('0.05') or gap > max_gap:
            continue
        # sem "pagamento/fatura" no texto, só vale se cita o banco do cartão ou sai do mesmo banco
        # (C6 paga a fatura do C6 com a descrição "C6 BANK"; outra conta paga "BANCO SANTANDER")
        if bills_only and not (_BILL_LIKE.search(tx.description) or b.account.connection_id == paying.connection_id
                               or _issuer_in(b.account, tx.description)):
            continue
        if best is None or gap < best[0]:
            best = (gap, b)
    if best:
        bill, acc = best[1], best[1].account
        y, m = bill.due_date.year, bill.due_date.month
        name = f"{acc.connection.label} •••• {last4(acc.number) or ''}".strip()
        if not acc.card_id:
            return (f'Pagamento da fatura {name} de {MONTH_NAMES_SHORT[m - 1]}/{y}. Ligue esse cartão do banco a '
                    'um cartão do FinFam para a fatura ficar marcada como paga.')
        (Expense.query.filter(Expense.card_id.in_(card_family(acc.card_id)), Expense.payment_method == CREDIT,
                              Expense.year == y, Expense.month == m)
         .update({'paid': True}, synchronize_session=False))
        return f'Pagamento da fatura {name} de {MONTH_NAMES_SHORT[m - 1]}/{y}: fatura marcada como paga.'
    if bills_only:
        return None
    # 2) sem faturas do banco: pela fatura do FinFam com o mesmo total
    for delta in (0, -1, 1):
        m, y = month_offset(tx.date.month, tx.date.year, delta)
        rows = Expense.query.filter(Expense.user_id.in_(uids), Expense.payment_method == CREDIT,
                                    Expense.year == y, Expense.month == m, Expense.card_id.isnot(None)).all()
        groups: dict[tuple, list[Expense]] = {}
        for e in rows:
            card = db.session.get(CreditCard, e.card_id)
            key = ('a', card.account_id) if card and card.account_id else ('c', e.card_id)
            groups.setdefault(key, []).append(e)
        for items in groups.values():
            total = sum(Decimal(str(e.amount)) for e in items)
            if abs(total - amount) <= Decimal('0.05'):
                for e in items:
                    e.paid = True
                card = db.session.get(CreditCard, items[0].card_id)
                label = f"{card.bank or 'cartão'} •••• {card.last_digits}" if card else 'do cartão'
                return (f'Pagamento da fatura {label} de {MONTH_NAMES_SHORT[m - 1]}/{y}: '
                        'fatura marcada como paga (as compras já vêm pelo cartão).')
    return None


_BILL_LIKE = re.compile(r'boleto|pagamento|pgto|pagto|fatura|cart[aã]o', re.I)
_GENERIC = {'banco', 'bank', 'bco', 'cartao', 'credito', 'meupluggy', 'pluggy', 'conta', 'sa', 's', 'a', 'do', 'de', 'da'}


def _words(text: str | None) -> list[str]:
    import unicodedata
    t = unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode().lower()
    return re.findall(r'[a-z0-9]+', t)


def _issuer_in(card_acc: BankAccount, description: str) -> bool:
    """A descrição cita o banco do cartão? ("C6 BANK", "BANCO SANTANDER (BRASIL) S.A.")"""
    conn = card_acc.connection
    card = db.session.get(CreditCard, card_acc.card_id) if card_acc.card_id else None
    names = [conn.display_name, conn.bank_name, card_acc.name, card.bank if card else None]
    marks = {w for n in names for w in _words(n)[:1] if w not in _GENERIC and len(w) >= 2}
    return bool(marks & set(_words(description)))


def reconcile_payments(user_id: int) -> int:
    """Refaz a conciliação dos pagamentos de fatura classificados sozinhos (as compras do cartão
    podem ter sido lançadas depois do pagamento chegar). Só mexe no banco de dados do FinFam."""
    uids, _ = family_of(user_id)
    n = 0
    rows = (BankTransaction.query.join(BankAccount)
            .filter(BankTransaction.user_id.in_(uids), BankTransaction.status == 'ignored',
                    BankTransaction.reviewed_at.is_(None), BankAccount.type == 'BANK',
                    BankTransaction.reason.like('Pagamento d%fatura%')).all())
    for tx in rows:
        why = reconcile_bill_payment(tx)
        if why and why != tx.reason:
            tx.reason, n = why, n + 1
    # boleto ainda "para revisar" que tem o total de uma fatura do banco (a fatura chegou depois)
    for tx in (BankTransaction.query.join(BankAccount)
               .filter(BankTransaction.user_id.in_(uids), BankTransaction.status == 'pending',
                       BankTransaction.reviewed_at.is_(None), BankAccount.type == 'BANK').all()):
        why = reconcile_bill_payment(tx, bills_only=True, max_gap=10)
        if why:
            tx.status, tx.reason, n = 'ignored', why, n + 1
    # pagamento de fatura que já foi lançado como despesa (antes de o FinFam reconhecer): desfaz,
    # senão a fatura conta duas vezes (as compras pelo cartão e o pagamento pela conta)
    for tx in (BankTransaction.query.join(BankAccount)
               .filter(BankTransaction.user_id.in_(uids), BankTransaction.status == 'imported',
                       BankTransaction.reason.is_(None), BankAccount.type == 'BANK').all()):
        why = reconcile_bill_payment(tx, bills_only=True, max_gap=10)
        if not why:
            continue
        e = db.session.get(Expense, tx.expense_id) if tx.expense_id else None
        if e is not None and e.payment_method != CREDIT and abs(Decimal(str(e.amount)) - Decimal(str(tx.amount))) <= Decimal('0.05'):
            db.session.delete(e)
        tx.status, tx.expense_id, tx.reviewed_at = 'ignored', None, None
        tx.reason, n = why + ' A despesa lançada antes foi removida (era pagamento de fatura).', n + 1
    db.session.flush()
    return n


def _skip_reason(description: str, category: str | None) -> str | None:
    debit_buy = bool(_DEBIT_CARD.search(description))  # C6 "DÉBITO DE CARTÃO": compra no débito, não fatura
    for rx, why in _SKIP:
        if debit_buy and why.startswith('Pagamento de fatura') and not rx.search(description):
            continue
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
    ref = str(meta.get('billId') or '')
    tx.bill_ref = ref if _ID.fullmatch(ref) else None
    tx.card_digits = last4(meta.get('cardNumber'))


_CARD_PAYMENT = re.compile(r'pagamento|pagto|pgto|pag fatura|pag\.? ?boleto|pagamento recebido|cr[eé]dito de pagamento', re.I)


def tx_kind(acc: BankAccount, raw: dict) -> str | None:
    """'expense' (saída de dinheiro), 'refund' (estorno/crédito no cartão, abate a fatura) ou None.
    No cartão, valor positivo é compra; negativo é pagamento da fatura (fica de fora) ou estorno."""
    amount = money(raw.get('amount')) or 0
    if acc.type == 'CREDIT':
        if amount > 0 and raw.get('type', 'DEBIT') != 'CREDIT':
            return 'expense'
        if amount and not _CARD_PAYMENT.search(str(raw.get('description') or '')):
            return 'refund'
        return None
    return 'expense' if raw.get('type') == 'DEBIT' or (raw.get('type') is None and amount < 0) else None


def _is_expense(acc: BankAccount, raw: dict) -> bool:
    return tx_kind(acc, raw) is not None


_NEXT = re.compile(r'\?[A-Za-z0-9=&%._:+\-]{1,1000}')


def _fetch(client: pg.PluggyClient, account_id: str, since: date) -> tuple[list[dict], bool]:
    """GET /v2/transactions com cursor (o /transactions antigo foi desligado pela Pluggy).
    `next` vem pronto ("?accountId=...&after=...") e é colado no caminho.
    Devolve (lançamentos, completo?) — incompleto se bateu no limite de páginas."""
    out = []
    path = f'/v2/transactions?accountId={pg.safe_id(account_id)}&dateFrom={since.isoformat()}T00:00:00.000Z'
    for _ in range(MAX_PAGES):
        r = client.call('GET', path) or {}
        out.extend(r.get('results') or [])
        nxt = r.get('next')
        if not nxt or not isinstance(nxt, str) or not _NEXT.fullmatch(nxt):
            return out, True
        path = '/v2/transactions' + nxt
    return out, False


def _drop_vanished(acc: BankAccount, since: date, seen: set[str]) -> None:
    """O banco deixou de mandar (ex.: compra pendente que virou lançada com outro id).
    Pendente some da revisão; já lançado/duplicado perde só o vínculo (a despesa fica e
    pode casar com o lançamento novo, sem lançar duas vezes)."""
    for tx in acc.transactions.filter(BankTransaction.date >= since).all():
        if tx.pluggy_id in seen:
            continue
        if tx.status in ('pending', 'imported', 'duplicate'):
            db.session.delete(tx)
    db.session.flush()


def move_to_bill(e: Expense, bill: tuple[int, int]) -> bool:
    """Põe a despesa do cartão na fatura (ano, mês) que o banco diz; numa série, as parcelas
    seguintes andam junto (o banco pode pular um mês — ex.: fatura de junho zerada)."""
    from app.services.finance import month_offset
    delta = (bill[0] * 12 + bill[1]) - (e.year * 12 + e.month)
    if not delta or e.payment_method != CREDIT:
        return False
    rows = [e]
    if e.installment_group_id:
        rows = (Expense.query.filter(Expense.installment_group_id == e.installment_group_id,
                                     Expense.installment_number >= (e.installment_number or 0)).all())
    for row in rows:
        row.month, row.year = month_offset(row.month, row.year, delta)
        row.paid = False  # mudou de fatura: a conciliação diz de novo se essa fatura foi paga
    return True


def merge_duplicate_series(user_id: int) -> int:
    """Junta séries de parcelas duplicadas da mesma compra (criadas quando a parcela seguinte do
    banco vinha com centavos de diferença e não era reconhecida). Mesma loja, mesmo número de
    parcelas, mesmo cartão e valor até R$ 0,10: fica a série com mais parcelas vindas do banco;
    de cada número de parcela sobra uma, de preferência a do banco. Se as duas séries têm a
    MESMA parcela vinda do banco, são duas compras de verdade e nada muda. Devolve quantas
    despesas foram apagadas."""
    from app.models import InstallmentGroup
    uids, _ = family_of(user_id)
    linked = {i for (i,) in db.session.query(BankTransaction.expense_id)
              .filter(BankTransaction.expense_id.isnot(None)).all()}
    buckets: dict[tuple, list] = {}
    for g in InstallmentGroup.query.filter(InstallmentGroup.user_id.in_(uids)).order_by(InstallmentGroup.id).all():
        rows = Expense.query.filter_by(installment_group_id=g.id).all()
        rows = [r for r in rows if r.payment_method == CREDIT]
        if not rows:
            continue
        word = next(iter(_words(rows[0].description)), '')
        cards = frozenset(card_family(rows[0].card_id)) if rows[0].card_id else frozenset()
        buckets.setdefault((word, g.num_installments, cards), []).append((g, rows))
    removed = 0
    for groups in buckets.values():
        if len(groups) < 2:
            continue
        keep_g, keep_rows = max(groups, key=lambda gr: (sum(r.id in linked for r in gr[1]), -gr[0].id))
        by_num = {r.installment_number: r for r in keep_rows}
        typical = sorted(Decimal(str(r.amount)) for r in keep_rows)[len(keep_rows) // 2]
        for g, rows in groups:
            if g.id == keep_g.id:
                continue
            other = sorted(Decimal(str(r.amount)) for r in rows)[len(rows) // 2]
            clash = any(r.id in linked and by_num.get(r.installment_number) is not None
                        and by_num[r.installment_number].id in linked for r in rows)
            if abs(other - typical) > Decimal('0.10') or clash:
                continue  # outra compra
            for r in rows:
                mine = by_num.get(r.installment_number)
                if mine is None:
                    r.installment_group_id = keep_g.id  # parcela que só a outra série tinha
                    by_num[r.installment_number] = r
                elif r.id in linked and mine.id not in linked:
                    db.session.delete(mine)  # fica a que veio do banco
                    r.installment_group_id = keep_g.id
                    by_num[r.installment_number] = r
                    removed += 1
                else:
                    db.session.delete(r)
                    removed += 1
            db.session.flush()
            if not Expense.query.filter_by(installment_group_id=g.id).count():
                db.session.delete(g)
    db.session.flush()
    return removed


def align_to_bank(acc: BankAccount) -> int:
    """A fatura que o banco informa manda: cada compra do cartão já lançada (ou reconhecida como
    já lançada) vai para a fatura dela, da mais antiga para a mais nova. Só banco de dados."""
    if acc.type != 'CREDIT' or not ((acc.close_day and acc.due_day) or acc.bills.count()):
        return 0
    moved = 0
    rows = (acc.transactions.filter(BankTransaction.status.in_(('imported', 'duplicate')),
                                    BankTransaction.expense_id.isnot(None))
            .order_by(BankTransaction.date, BankTransaction.installment_number).all())
    for tx in rows:
        bill = tx_bill(acc, tx)
        if not bill:
            continue
        tx.bill_year, tx.bill_month = bill
        e = db.session.get(Expense, tx.expense_id)
        if e is not None and move_to_bill(e, bill):
            moved += 1
    db.session.flush()
    return moved


def sync_transactions(conn: BankConnection, client: pg.PluggyClient, today: date | None = None) -> int:
    """Traz os lançamentos novos de todas as contas. Devolve quantos ficaram para revisar."""
    today = today or date.today()
    start = conn.import_from or months_back(today, 1)
    since = start if conn.tx_synced_at is None else max(start, today - timedelta(days=RESYNC_DAYS))
    new_pending = 0
    for acc in conn.accounts.all():
        # cartão: o período conta por FATURA (compra de setembro que vence em outubro entra em "desde outubro")
        sync_bills(acc, client)
        by_bill = acc.type == 'CREDIT' and bool((acc.close_day and acc.due_day) or acc.bills.count())
        acc_since = since
        if by_bill:
            acc_since = months_back(start, 2) if conn.tx_synced_at is None else \
                max(months_back(start, 2), today - timedelta(days=RESYNC_DAYS + 62))
        raws, complete = _fetch(client, acc.pluggy_account_id, acc_since)
        raws = [r for r in raws if _ID.fullmatch(str(r.get('id') or '')) and _is_expense(acc, r)]
        if infer_close_day(acc, raws):
            apply_card_limit(acc)  # o cartão ligado recebe o fechamento descoberto
            by_bill = True
        if complete:
            _drop_vanished(acc, acc_since, {str(r['id']) for r in raws})
        for raw in raws:
            pid = str(raw['id'])
            tx = BankTransaction.query.filter_by(pluggy_id=pid).first()
            if tx is not None and tx.account_id != acc.id:
                continue
            fresh = tx is None
            if fresh:
                tx = BankTransaction(account_id=acc.id, user_id=conn.user_id, pluggy_id=pid, status='pending')
                db.session.add(tx)
            elif tx.status == 'imported':
                _apply_tx(tx, raw)  # a fatura é acertada no fim (align_to_bank)
                continue
            elif tx.reviewed_at is not None:
                continue  # você já mexeu: não reclassifica
            else:  # classificado sozinho antes: classifica de novo (regras podem ter melhorado)
                tx.status, tx.reason, tx.expense_id = 'pending', None, None
            _apply_tx(tx, raw)
            tx.kind = tx_kind(acc, raw) or 'expense'
            bill = tx_bill(acc, tx) if by_bill else None
            tx.bill_year, tx.bill_month = bill if bill else (None, None)
            # cartão: fica se a fatura vence a partir do início; conta: se a data é a partir do início
            too_old = (date(bill[0], bill[1], min(acc.due_day or 10, 28)) < start) if bill else tx.date < start
            if too_old or not tx.amount:
                if fresh:
                    db.session.expunge(tx)
                continue
            why = _skip_reason(tx.description, tx.pluggy_category) if tx.kind == 'expense' else None
            if why and acc.type == 'BANK' and why.startswith('Pagamento de fatura'):
                why = reconcile_bill_payment(tx) or why
            elif not why and acc.type == 'BANK':
                why = reconcile_bill_payment(tx, bills_only=True, max_gap=10)  # boleto/"C6 BANK" que paga fatura
            match = None if why else find_existing(tx, acc.card_id)
            if why:
                tx.status, tx.reason = 'ignored', why
            elif match:
                tx.status, tx.expense_id = 'duplicate', match.id
                tx.reason = 'Já estava no FinFam.'
                take_bank_amount(match, tx)
            elif fresh:
                new_pending += 1
            db.session.flush()
        align_to_bank(acc)  # lançado antes de o FinFam saber a fatura certa, ou série com mês pulado
    conn.tx_synced_at = utcnow()
    db.session.flush()
    return new_pending


def family_of(user_id: int) -> tuple[list[int], int | None]:
    """(ids da família, tenant) de quem conectou o banco — para validar cartão e categoria."""
    user = db.session.get(User, user_id)
    if user is None or not user.tenant_id:
        return [user_id], None
    return [u.id for u in User.query.filter_by(tenant_id=user.tenant_id).all()], user.tenant_id


def auto_approve(conn: BankConnection) -> int:
    """Modo "lançar automaticamente": o que ficou para revisar vira despesa com a categoria
    sugerida (aparece em Lançados para corrigir). Duplicados e ignorados ficam de fora."""
    db.session.commit()  # o que a leitura trouxe fica salvo mesmo se um lançamento falhar
    uids, tenant_id = family_of(conn.user_id)
    done = 0
    rows = (BankTransaction.query.join(BankAccount)
            .filter(BankAccount.connection_id == conn.id, BankTransaction.status == 'pending',
                    BankTransaction.reviewed_at.is_(None))
            .order_by(BankTransaction.date, BankTransaction.id).all())
    for tx in rows:
        sug = suggestion(tx, uids, tenant_id)
        try:
            approve(tx, uids, sug['category'])
        except ValueError as exc:  # ExpenseError: deixa para revisar à mão
            db.session.rollback()
            log.warning('Lançamento automático %s ficou para revisar: %s', tx.pluggy_id, exc)
            continue
        tx.reason = 'Lançado automaticamente.'
        done += 1
    db.session.flush()
    return done


def sync(conn: BankConnection, client: pg.PluggyClient, today: date | None = None) -> int:
    """Relê banco, contas e lançamentos. Se só os lançamentos falharem, banco e contas
    ficam salvos e a próxima leitura tenta de novo (needs_sync continua ligado).
    Devolve quantos lançamentos novos chegaram (para revisar ou lançados sozinhos)."""
    sync_accounts(conn, client)
    try:
        with db.session.begin_nested():
            count = sync_transactions(conn, client, today)
    except pg.PluggyError as exc:
        log.warning('Lançamentos do item %s não vieram: %s', conn.item_id, exc)
        conn.needs_sync = True
        return 0
    conn.needs_sync = False
    merge_duplicate_series(conn.user_id)
    reconcile_payments(conn.user_id)  # antes do automático: boleto que paga fatura não vira despesa
    if conn.auto_import:
        auto_approve(conn)
        reconcile_payments(conn.user_id)
    return count


def sync_stale(user_id: int | None = None, max_age_hours: int | None = None, notify: bool = False) -> int:
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
        before = {i for (i,) in db.session.query(BankTransaction.id).join(BankAccount)
                  .filter(BankAccount.connection_id == conn.id).all()}
        try:
            total += sync(conn, client)
            db.session.commit()
        except pg.PluggyError as exc:
            db.session.rollback()
            log.warning('Sincronização do item %s falhou: %s', conn.item_id, exc)
            continue
        if notify:
            notify_new(conn, before)
    return total


def notify_new(conn: BankConnection, before: set[int]) -> bool:
    """Avisa no Telegram o que chegou do banco nesta leitura (para revisar ou já lançado).
    Só nas leituras sozinhas (webhook/cron): quem está na página já vê."""
    from app.core.config import settings
    from app.services import telegram as tg
    cfg = PluggyConfig.query.filter_by(user_id=conn.user_id).first()
    if cfg is None or not cfg.notify or not settings.telegram_bot_token:
        return False
    new = (BankTransaction.query.join(BankAccount)
           .filter(BankAccount.connection_id == conn.id, BankTransaction.status.in_(('pending', 'imported')),
                   ~BankTransaction.id.in_(before or {0})).all())
    if not new:
        return False
    chats = tg.linked_chats([conn.user_id])
    if not chats:
        return False
    pending = [t for t in new if t.status == 'pending']
    launched = [t for t in new if t.status == 'imported']
    lines = [f'🏦 <b>{tg._h(conn.label)}</b>: {len(new)} lançamento(s) novo(s) do banco.']
    if launched:
        lines.append(f'✅ {len(launched)} lançado(s) automaticamente ({brl(sum(float(t.amount) for t in launched))}).')
    if pending:
        lines.append(f'📝 {len(pending)} para revisar ({brl(sum(float(t.amount) for t in pending))}):')
        lines += [f'• {tg._h(t.description[:40])} — {brl(float(t.amount))}' for t in sorted(pending, key=lambda t: t.date)[-5:]]
    base = settings.public_url
    if base.startswith('https://'):
        lines.append(f'{base}/bank/review')
    text = '\n'.join(lines)
    sent = False
    for chat in chats:
        ok, err = tg.send_message(settings.telegram_bot_token, chat, text)
        sent = sent or ok
        if not ok:
            log.warning('Aviso do Open Finance não foi para o Telegram: %s', err)
    return sent


# ── revisão ──────────────────────────────────────────────────────────────────

_DEBIT_CARD = re.compile(r'compra.{0,12}(cart|deb|elo|visa|master)|cart[aã]o de d[eé]bito|debito (visa|elo|master)'
                         r'|d[eé]bito (de|no|em) cart[aã]o'
                         r'|\bdeb(ito)? (cart|visa|elo)|maestro|visa electron', re.I)


def suggested_method(tx: BankTransaction) -> str:
    """Cartão → crédito. Conta: compra com cartão de débito → débito; o resto (PIX, boleto, débito
    automático, transferência) → PIX, que é como o FinFam registra conta paga pela conta."""
    if tx.account.type == 'CREDIT':
        return CREDIT
    hint = (tx.payment_hint or '').upper()
    if hint == 'PIX' or 'PIX' in tx.description.upper():
        return 'PIX'
    return DEBIT if hint in ('DEBIT_CARD', 'DEBITO') or _DEBIT_CARD.search(tx.description) else 'PIX'


# categoria pela descrição (português) e pela categoria que o banco manda (inglês, Pluggy)
_CATEGORY_WORDS = (
    ('Moradia', r'presta[cç][aã]o hab|habitac|aluguel|condom[ií]nio|\biptu\b|energia|\bluz\b|enel|cemig|copel|light\b|sabesp|'
                r'[aá]gua e esgoto|\bg[aá]s\b|comgas|housing|rent|utilities|electricity|water'),
    ('Alimentação', r'ifood|rappi|restaur|lanchon|padaria|panific|mercado|supermerc|atacad|assa[ií]|carrefour|'
                    r'p[aã]o de a[cç]|hortifrut|a[cç]ougue|pizz|burger|mc ?donald|bk\b|groceries|food|eating out|restaurant'),
    ('Transporte', r'\buber\b|\b99\s?(app|pop|taxi)|taxi|posto|shell|ipiranga|petrobras|br mania|combust|estacion|'
                   r'ped[aá]gio|sem parar|veloe|conectcar|metr[oô]|\bcptm\b|bilhete [uú]nico|transport|gas station|parking|tolls'),
    ('Saúde', r'farm[aá]cia|drogaria|droga ?raia|drogasil|pague menos|hospital|cl[ií]nica|laborat|m[eé]dic|odonto|'
              r'unimed|amil|bradesco sa[uú]de|sulam[eé]rica|health|pharmacy|hospital'),
    ('Telefone', r'\bvivo\b|\btim\b|\bclaro\b|\boi\b|telefon|celular|mobile|telecommunication'),
    ('Internet', r'internet|fibra|net virtua|\bnet\b|provedor|broadband'),
    ('Lazer', r'netflix|spotify|disney|hbo|\bmax\b|prime video|amazon prime|youtube|deezer|cinema|ingresso|'
              r'steam|playstation|xbox|nintendo|nuuvem|entertainment|leisure|streaming|games?\b'),
    ('Educação', r'escola|col[eé]gio|faculdade|universid|curso|udemy|alura|livraria|education|school|tuition'),
    ('Beleza', r'sal[aã]o|barbear|barba\b|cabeleir|est[eé]tica|manicure|botic[aá]rio|natura\b|sephora|beauty|personal care'),
)
_CATEGORY_RX = [(name, re.compile(rx, re.I)) for name, rx in _CATEGORY_WORDS]


def guess_category(tx: BankTransaction) -> str | None:
    text = f'{tx.description} {tx.pluggy_category or ""}'
    for name, rx in _CATEGORY_RX:
        if rx.search(text):
            return name
    return None


def suggestion(tx: BankTransaction, uids: list, tenant_id: int | None) -> dict:
    """Categoria e forma de pagamento sugeridas: regras e histórico da família primeiro; sem
    nada, pela descrição e pela categoria que o banco manda."""
    category, source = categorize(tx.description, uids, tenant_id) if tenant_id else (None, None)
    if tx.kind == 'refund':  # estorno: a categoria da compra que ele devolve
        orig = (Expense.query.filter(Expense.user_id.in_(uids), Expense.amount > 0,
                                     Expense.description.ilike(f'%{tx.description[:20]}%'))
                .order_by(Expense.year.desc(), Expense.month.desc()).first())
        if orig is not None:
            category, source = orig.category, 'compra'
    if not category:
        category = guess_category(tx)
        source = 'banco' if category else None
    return {'category': category or 'Outros', 'category_source': source, 'payment_method': suggested_method(tx)}


def approve(tx: BankTransaction, uids: list, category: str, description: str | None = None,
            payment_method: str | None = None) -> Expense:
    """Lança a despesa (mesma regra do site: fatura, parcelas) e marca como importado.
    Parcela do meio (k/n, k>1) lança k..n: as que faltam aparecem nas faturas seguintes, como
    numa compra parcelada lançada à mão. Se já existe (outra parcela lançada no mesmo lote
    criou), vira "já no FinFam" em vez de duplicar."""
    from app.models import InstallmentGroup
    from app.services.finance import month_offset
    acc, conn = tx.account, tx.account.connection
    credit = acc.type == 'CREDIT'
    if credit and not (tx.bill_year and tx.bill_month):
        bill = tx_bill(acc, tx)  # as faturas do banco podem ter chegado depois da classificação
        tx.bill_year, tx.bill_month = bill if bill else (None, None)
    existing = find_existing(tx, acc.card_id)
    if existing is not None:
        tx.status, tx.expense_id, tx.reason, tx.reviewed_at = 'duplicate', existing.id, 'Já estava no FinFam.', utcnow()
        if credit and tx.bill_year and tx.bill_month:
            move_to_bill(existing, (tx.bill_year, tx.bill_month))
        take_bank_amount(existing, tx)
        return existing
    when = target_date(tx)
    desc = (description or tx.description).strip()[:200] or tx.description
    n, k = tx.total_installments or 0, tx.installment_number or 0
    method = CREDIT if credit else (payment_method if payment_method in ('PIX', DEBIT) else suggested_method(tx))
    installments = None
    amount = Decimal(str(tx.amount))
    if tx.kind == 'refund':  # estorno/crédito no cartão: entra negativo e abate a fatura
        amount, n, k = -amount, 0, 0
        if not desc.lower().startswith('estorno'):
            desc = f'Estorno: {desc}'[:200]
    if credit and n > 1 and k <= 1:  # 1ª parcela: lança a compra inteira parcelada, como no site
        installments = n
        amount = Decimal(str(tx.total_amount)) if tx.total_amount else amount * n
    card = card_used(tx) if credit else None
    owner = card.user_id if card is not None and card.user_id in uids else tx.user_id  # adicional da Greyce → dela
    data = ExpenseData(user_id=owner, description=desc, amount=amount,
                       category=category if category else 'Outros', payment_method=method,
                       day=when.day, month=when.month, year=when.year,
                       bank=conn.label, card_id=card.id if card is not None else None,
                       paid=not credit, installments=installments)
    result = create_expense(uids, data)
    first = result['expenses'][0]
    if credit and tx.bill_year and tx.bill_month:  # a fatura que o banco diz, não a calculada
        delta = (tx.bill_year * 12 + tx.bill_month) - (first.year * 12 + first.month)
        if delta:
            for e in result['expenses']:
                e.month, e.year = month_offset(e.month, e.year, delta)
    if credit and n > 1 and k > 1:  # parcela do meio: completa a série k..n
        group = InstallmentGroup(user_id=first.user_id, description=first.description, total_amount=amount * n,
                                 num_installments=n, bank=first.bank or '')
        db.session.add(group)
        db.session.flush()
        first.installment_group_id, first.installment_number = group.id, k
        for j in range(k + 1, n + 1):
            m, y = month_offset(first.month, first.year, j - k)
            db.session.add(Expense(
                user_id=first.user_id, description=first.description, amount=amount, category=first.category,
                payment_method=CREDIT, bank=first.bank, year=y, month=m, day=first.day,
                purchase_month=m, purchase_year=y, card_id=first.card_id, paid=False,
                installment_group_id=group.id, installment_number=j))
        db.session.flush()
    tx.status, tx.expense_id, tx.reason, tx.reviewed_at = 'imported', first.id, None, utcnow()
    return first


def valid_category(category: str | None, allowed: list[str]) -> str:
    """Categoria escolhida na revisão: uma da lista ou uma nova (como no lançamento do site).
    Reaproveita a grafia de uma existente ("moradia" → "Moradia")."""
    category = re.sub(r'\s+', ' ', category or '').strip()[:50]
    if not category or any(ord(ch) < 32 for ch in category):
        return 'Outros'
    known = {c.casefold(): c for c in [*CATEGORIES, *allowed]}
    return known.get(category.casefold(), category)


# ── leitura em segundo plano (disparada pelo webhook) ────────────────────────

BACKGROUND = True        # os testes desligam
DEBOUNCE_SECONDS = 5     # a Pluggy manda vários avisos seguidos: junta numa leitura só
_bg_lock = threading.Lock()
_bg_state: dict[int, str] = {}   # user_id → 'running' | 'again'


def schedule_sync(user_id: int) -> None:
    """Relê os bancos da pessoa numa thread (o webhook precisa responder em até 10 s)."""
    if not BACKGROUND:
        return
    with _bg_lock:
        if user_id in _bg_state:
            _bg_state[user_id] = 'again'
            return
        _bg_state[user_id] = 'running'
    threading.Thread(target=_bg_run, args=(user_id,), daemon=True, name=f'pluggy-sync-{user_id}').start()


def _bg_run(user_id: int) -> None:
    from app.core.db import session  # thread nova = sessão própria
    try:
        while True:
            time.sleep(DEBOUNCE_SECONDS)
            try:
                sync_stale(user_id, notify=True)
            except Exception:  # noqa: BLE001 — nunca derruba a API
                log.exception('Leitura em segundo plano falhou (user %s)', user_id)
            finally:
                session.remove()
            with _bg_lock:
                if _bg_state.get(user_id) == 'again':
                    _bg_state[user_id] = 'running'
                    continue
                _bg_state.pop(user_id, None)
                return
    except BaseException:
        with _bg_lock:
            _bg_state.pop(user_id, None)
        raise


# ── conferir fatura (FinFam × banco) ─────────────────────────────────────────

def invoice_check(user_id: int, month: int, year: int) -> list[dict]:
    """Para cada cartão do banco ligado: total da fatura no banco (a fatura que ele informa, ou a
    soma dos lançamentos até agora) × total no FinFam, e o que está só de um lado."""
    out = []
    accs = (BankAccount.query.join(BankConnection)
            .filter(BankConnection.user_id == user_id, BankAccount.type == 'CREDIT').all())
    for acc in accs:
        conn = acc.connection
        label = f"{conn.label} •••• {last4(acc.number) or ''}".strip()
        bill = next((b for b in acc.bills.all() if (b.due_date.year, b.due_date.month) == (year, month)), None)
        txs = acc.transactions.filter(BankTransaction.bill_year == year, BankTransaction.bill_month == month,
                                      BankTransaction.status != 'ignored').all()
        items_total = round(sum(float(t.amount) * (-1 if t.kind == 'refund' else 1) for t in txs), 2)
        knows_bills = bool((acc.close_day and acc.due_day) or acc.bills.count())
        if bill is not None and bill.total:
            bank_total, source = round(float(bill.total), 2), 'fatura'
        elif knows_bills:
            bank_total, source = items_total, 'lancamentos'
        else:  # o banco não diz em que fatura cada compra está: não dá para comparar
            bank_total, source = None, 'sem_fatura'
        row = {'account_id': acc.id, 'label': label, 'linked': bool(acc.card_id), 'bank_total': bank_total,
               'bank_source': source, 'bank_items_total': items_total, 'finfam_total': None, 'diff': None,
               'only_bank': [], 'only_finfam': [],
               'due_date': bill.due_date.isoformat() if bill is not None else None}
        if acc.card_id:
            cards = card_family(acc.card_id)
            exps = Expense.query.filter(Expense.card_id.in_(cards), Expense.payment_method == CREDIT,
                                        Expense.year == year, Expense.month == month).all()
            linked = {i for (i,) in db.session.query(BankTransaction.expense_id)
                      .filter(BankTransaction.expense_id.in_([e.id for e in exps] or [0])).all()}
            row['finfam_total'] = round(sum(float(e.amount) for e in exps), 2)
            row['diff'] = round(row['finfam_total'] - bank_total, 2) if bank_total is not None else None
            row['only_finfam'] = [{'id': e.id, 'description': e.description, 'amount': float(e.amount),
                                   'date': _expense_date(e).isoformat(), 'installment': e.installment_number}
                                  for e in exps if e.id not in linked]
        row['only_bank'] = [{'id': t.id, 'description': t.description,
                             'amount': float(t.amount) * (-1 if t.kind == 'refund' else 1),
                             'date': t.date.isoformat(), 'status': t.status} for t in txs if t.status == 'pending']
        out.append(row)
    return out
