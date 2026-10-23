"""
card_service.py — Lógica de negócio para cartões de crédito e faturas.
"""

import calendar as _calendar
from datetime import datetime, date as _date
from sqlalchemy import func, or_, and_
from app.core.types import utcnow
from app.core.db import db
from app.models import Expense, InstallmentGroup, CreditCard, CreditAccount
from app.services.finance import month_offset

_PT_MONTHS = [
    '', 'Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
    'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro',
]


def get_invoice_reference(purchase_date: _date, best_buy_day: int) -> tuple[int, int]:
    """
    Retorna (year, month) da fatura em que a compra será lançada.

    Regra: se o dia da compra é posterior ao melhor dia de compra (best_buy_day),
    ela cai na fatura do próximo mês; caso contrário, cai na fatura do mês atual.
    """
    if purchase_date.day > best_buy_day:
        if purchase_date.month == 12:
            return purchase_date.year + 1, 1
        return purchase_date.year, purchase_date.month + 1
    return purchase_date.year, purchase_date.month


def get_invoice_label(year: int, month: int) -> str:
    """Retorna rótulo legível da fatura, ex: 'Julho 2026'."""
    return f"{_PT_MONTHS[month]} {year}"

# ── Gradientes por banco ────────────────────────────────────────────
BANK_GRADIENTS = {
    'Nubank':                  ('linear-gradient(135deg,#820ad1,#5c0391)', '#fff'),
    'Itaú':                    ('linear-gradient(135deg,#FF6600,#cc4400)', '#fff'),
    'Bradesco':                ('linear-gradient(135deg,#CC0000,#8b0000)', '#fff'),
    'Banco do Brasil':         ('linear-gradient(135deg,#F9C700,#c9a200)', '#212529'),
    'Santander':               ('linear-gradient(135deg,#EC0000,#a80000)', '#fff'),
    'Inter':                   ('linear-gradient(135deg,#FF7A00,#cc5e00)', '#fff'),
    'C6 Bank':                 ('linear-gradient(135deg,#232323,#000)', '#e8c840'),
    'Caixa Econômica Federal': ('linear-gradient(135deg,#005B9A,#003a6b)', '#fff'),
    'BTG Pactual':             ('linear-gradient(135deg,#1a1a2e,#0f0f1a)', '#c9a227'),
    'Sicredi':                 ('linear-gradient(135deg,#006633,#004422)', '#fff'),
    'Sicoob':                  ('linear-gradient(135deg,#006633,#004422)', '#fff'),
    'Safra':                   ('linear-gradient(135deg,#003399,#001f66)', '#fff'),
    'Original':                ('linear-gradient(135deg,#00A859,#006e3a)', '#fff'),
    'Neon':                    ('linear-gradient(135deg,#00C4B4,#008a7e)', '#fff'),
    'PicPay':                  ('linear-gradient(135deg,#21c25e,#128a3e)', '#fff'),
    'XP Investimentos':        ('linear-gradient(135deg,#111,#000)', '#f5c518'),
    'Outros':                  ('linear-gradient(135deg,#4a5568,#2d3748)', '#fff'),
}
_DEFAULT_GRADIENT = ('linear-gradient(135deg,#2d3748,#1a202c)', '#fff')

# ── Ícones por categoria ────────────────────────────────────────────
CATEGORY_ICONS = {
    'Alimentação':  'bi-cart3',
    'Transporte':   'bi-car-front',
    'Saúde':        'bi-heart-pulse',
    'Lazer':        'bi-controller',
    'Moradia':      'bi-house',
    'Educação':     'bi-book',
    'Vestuário':    'bi-bag',
    'Serviços':     'bi-tools',
    'Compras':      'bi-bag-heart',
    'Beleza':       'bi-stars',
    'Internet':     'bi-wifi',
    'Telefone':     'bi-phone',
    'Outros':       'bi-tag',
}

CATEGORY_COLORS = {
    'Alimentação': '#FF6384', 'Beleza': '#f72585', 'Educação': '#FFCE56',
    'Lazer': '#FF9F40',       'Moradia': '#9966FF', 'Saúde': '#4BC0C0',
    'Internet': '#4361ee',    'Telefone': '#43aa8b', 'Transporte': '#36A2EB',
    'Outros': '#C9CBCF',
}


def card_gradient(card: CreditCard) -> tuple[str, str]:
    """Retorna (gradient_css, text_color) para o cartão."""
    return BANK_GRADIENTS.get(card.bank or '', _DEFAULT_GRADIENT)


def invoice_status(card: CreditCard, month: int, year: int) -> str:
    """
    Retorna o status da fatura: 'aberta', 'fechada' ou 'futura'.
    - aberta:  mês atual antes do best_buy_day
    - fechada: mês atual após best_buy_day, ou meses passados
    - futura:  meses futuros
    """
    now = datetime.now()
    cur = (year, month)
    today = (now.year, now.month)

    if cur > today:
        return 'futura'
    if cur == today:
        return 'aberta' if now.day <= card.best_buy_day else 'fechada'
    return 'fechada'


def invoice_total(card_id: int, month: int, year: int,
                  payment_method: str | None = None) -> float:
    """
    Soma despesas do cartão no mês.
    payment_method=None → todas; 'Cartão de Crédito' → só crédito; etc.
    """
    q = (db.session.query(func.sum(Expense.amount))
         .filter(Expense.card_id == card_id,
                 Expense.month == month,
                 Expense.year == year))
    if payment_method:
        q = q.filter(Expense.payment_method == payment_method)
    return float(q.scalar() or 0)


def credit_invoice_total(card_id: int, month: int, year: int) -> float:
    return invoice_total(card_id, month, year, 'Cartão de Crédito')


def debit_total(card_id: int, month: int, year: int) -> float:
    return invoice_total(card_id, month, year, 'Cartão de Débito')


def credit_invoice_status(card: CreditCard, month: int, year: int) -> dict | None:
    """
    Retorna o status de pagamento da fatura de crédito do mês: quanto já foi
    pago, quanto falta, e um status resumido ('pago', 'pendente', 'parcial',
    'sem_despesas'). Considera cartões irmãos da mesma conta (limite
    compartilhado). None se o cartão não tem função de crédito.
    """
    if not card.supports_credit:
        return None

    if card.account_id:
        card_ids = [
            row[0] for row in
            db.session.query(CreditCard.id).filter(CreditCard.account_id == card.account_id).all()
        ]
    else:
        card_ids = [card.id]

    rows = (db.session.query(Expense.paid, func.sum(Expense.amount))
            .filter(Expense.card_id.in_(card_ids),
                    Expense.month == month,
                    Expense.year == year,
                    Expense.payment_method == 'Cartão de Crédito')
            .group_by(Expense.paid)
            .all())

    paid_total = pending_total = 0.0
    for is_paid, total in rows:
        if is_paid:
            paid_total = float(total)
        else:
            pending_total = float(total)

    total = paid_total + pending_total
    if total == 0:
        status = 'sem_despesas'
    elif pending_total == 0:
        status = 'pago'
    elif paid_total == 0:
        status = 'pendente'
    else:
        status = 'parcial'

    return {'paid_total': paid_total, 'pending_total': pending_total,
            'total': total, 'status': status}


def account_credit_usage(account_id: int, month: int, year: int) -> float:
    """
    Soma despesas de crédito de TODOS os cartões da conta no mês.
    Esta é a fonte da verdade para uso de limite compartilhado.
    """
    card_ids = [
        row[0] for row in
        db.session.query(CreditCard.id).filter(CreditCard.account_id == account_id).all()
    ]
    if not card_ids:
        return 0.0
    return float(
        db.session.query(func.sum(Expense.amount))
        .filter(
            Expense.card_id.in_(card_ids),
            Expense.month == month,
            Expense.year == year,
            Expense.payment_method == 'Cartão de Crédito',
        ).scalar() or 0
    )


def future_installments_total(card_id: int, month: int, year: int) -> float:
    """Total de parcelas futuras (do mesmo cartão) além do mês atual."""
    total = (db.session.query(func.sum(Expense.amount))
             .filter(
                 Expense.card_id == card_id,
                 or_(
                     Expense.year > year,
                     and_(Expense.year == year, Expense.month > month),
                 )
             ).scalar() or 0)
    return float(total)


def vr_va_balance(card: CreditCard, month: int, year: int) -> dict | None:
    """
    Retorna saldo de um cartão VR/VA com carry-over: saldo não usado
    de meses anteriores acumula no mês atual.
    """
    ct = card.card_type or 'credit'
    if ct not in ('vr', 'va') or not card.monthly_amount:
        return None

    monthly = float(card.monthly_amount)

    # Ponto de partida: mês de criação do cartão
    from datetime import datetime as _dt
    created = card.created_at or utcnow()
    start_m, start_y = created.month, created.year

    carry_over = 0.0
    if (start_y, start_m) < (year, month):
        # Busca todos os gastos anteriores ao mês atual (numa só query)
        hist = (
            db.session.query(
                Expense.year, Expense.month,
                func.sum(Expense.amount).label('total')
            )
            .filter(
                Expense.card_id == card.id,
                or_(
                    Expense.year < year,
                    and_(Expense.year == year, Expense.month < month),
                )
            )
            .group_by(Expense.year, Expense.month)
            .all()
        )
        spent_by_month = {(r.year, r.month): float(r.total) for r in hist}

        # Calcula carry-over acumulado mês a mês
        cur_m, cur_y = start_m, start_y
        while (cur_y, cur_m) < (year, month):
            spent_m   = spent_by_month.get((cur_y, cur_m), 0.0)
            available = monthly + carry_over
            carry_over = max(0.0, available - spent_m)
            cur_m, cur_y = month_offset(cur_m, cur_y, 1)

    # Mês atual
    spent = float(
        db.session.query(func.sum(Expense.amount))
        .filter(Expense.card_id == card.id,
                Expense.month == month,
                Expense.year == year)
        .scalar() or 0
    )
    available = monthly + carry_over
    remaining = max(0.0, available - spent)
    pct_used  = min(spent / available * 100, 100) if available > 0 else 0

    return {
        'monthly':    round(monthly, 2),
        'carry_over': round(carry_over, 2),
        'available':  round(available, 2),
        'spent':      round(spent, 2),
        'remaining':  round(remaining, 2),
        'pct_used':   round(pct_used, 1),
    }


def get_invoice(card: CreditCard, month: int, year: int,
                sibling_cards: list | None = None) -> dict:
    """
    Retorna estrutura completa de uma fatura.
    sibling_cards: lista de todos os cartões do mesmo account (incluindo o principal).
    Quando fornecido, as despesas de crédito de todos os cartões são consolidadas.
    """
    sc = bool(card.supports_credit)
    sd = bool(card.supports_debit)

    # IDs de crédito: todos os cartões do grupo (ou só o atual)
    credit_cards = sibling_cards if sibling_cards else [card]
    credit_ids   = [c.id for c in credit_cards]

    credit_q = Expense.query.filter(
        Expense.card_id.in_(credit_ids),
        Expense.month == month,
        Expense.year == year,
    )
    debit_q = Expense.query.filter(
        Expense.card_id == card.id,
        Expense.month == month,
        Expense.year == year,
    )

    # Despesas de crédito (todos os cartões do grupo)
    if sc:
        credit_expenses = (credit_q
            .filter(Expense.payment_method == 'Cartão de Crédito')
            .order_by(Expense.day, Expense.created_at).all())
    elif not sd:
        # Cartão sem flags (legado) — trata tudo como crédito
        credit_expenses = credit_q.order_by(Expense.day, Expense.created_at).all()
    else:
        credit_expenses = []

    # Despesas de débito (apenas cartão principal — débito é por cartão físico)
    debit_expenses = (debit_q
        .filter(Expense.payment_method == 'Cartão de Débito')
        .order_by(Expense.day, Expense.created_at).all()) if sd else []

    expenses = credit_expenses  # retrocompatibilidade

    total       = sum(float(e.amount) for e in credit_expenses)
    paid_total  = sum(float(e.amount) for e in credit_expenses if e.paid)
    debit_total = sum(float(e.amount) for e in debit_expenses)

    # Agrupar por dia (visão unificada)
    by_day: dict[int, list] = {}
    for e in credit_expenses:
        by_day.setdefault(e.day, []).append(e)

    debit_by_day: dict[int, list] = {}
    for e in debit_expenses:
        debit_by_day.setdefault(e.day, []).append(e)

    # Totais por categoria — crédito
    cat_totals: dict[str, float] = {}
    for e in credit_expenses:
        cat_totals[e.category] = cat_totals.get(e.category, 0) + float(e.amount)

    chart = {
        'labels': list(cat_totals.keys()),
        'data':   list(cat_totals.values()),
        'colors': [CATEGORY_COLORS.get(c, '#C9CBCF') for c in cat_totals],
    } if cat_totals else None

    # Totais por categoria — débito
    debit_cat_totals: dict[str, float] = {}
    for e in debit_expenses:
        debit_cat_totals[e.category] = debit_cat_totals.get(e.category, 0) + float(e.amount)

    debit_chart = {
        'labels': list(debit_cat_totals.keys()),
        'data':   list(debit_cat_totals.values()),
        'colors': [CATEGORY_COLORS.get(c, '#C9CBCF') for c in debit_cat_totals],
    } if debit_cat_totals else None

    # Parcelas futuras somadas de todos os cartões do grupo
    future_total = sum(future_installments_total(cid, month, year) for cid in credit_ids)

    prev_m, prev_y = month_offset(month, year, -1)
    next_m, next_y = month_offset(month, year,  1)

    # Agrupamento por cartão (quando há múltiplos cartões no grupo)
    expenses_by_card = []
    if len(credit_cards) > 1:
        for c in credit_cards:
            card_exps = [e for e in credit_expenses if e.card_id == c.id]
            if not card_exps:
                continue
            c_by_day: dict[int, list] = {}
            for e in card_exps:
                c_by_day.setdefault(e.day, []).append(e)
            c_total = sum(float(e.amount) for e in card_exps)
            c_paid  = sum(float(e.amount) for e in card_exps if e.paid)
            expenses_by_card.append({
                'card':       c,
                'expenses':   card_exps,
                'by_day':     c_by_day,
                'total':      c_total,
                'paid_total': c_paid,
                'pending':    c_total - c_paid,
            })

    return {
        'expenses':          credit_expenses,
        'by_day':            by_day,
        'total':             total,
        'credit_total':      total,
        'paid_total':        paid_total,
        'pending_total':     total - paid_total,
        'cat_totals':        cat_totals,
        'chart':             chart,
        'future_total':      future_total,
        'status':            invoice_status(card, month, year),
        'prev_month': prev_m, 'prev_year': prev_y,
        'next_month': next_m, 'next_year': next_y,
        # débito
        'debit_expenses':    debit_expenses,
        'debit_total':       debit_total,
        'debit_by_day':      debit_by_day,
        'debit_cat_totals':  debit_cat_totals,
        'debit_chart':       debit_chart,
        # agrupamento por cartão (não vazio apenas quando múltiplos cartões)
        'expenses_by_card':  expenses_by_card,
    }


def credit_limit_usage(cards: list, month: int, year: int) -> list[tuple]:
    """
    Uso de limite por linha de crédito, sem duplicar contas compartilhadas.
    Retorna [(cartão_representante, usado, limite), ...] apenas para cartões
    de crédito com limite definido.
    """
    result, seen_accounts = [], set()
    for card in cards:
        if not card.supports_credit:
            continue
        limit = card.effective_limit
        if not limit:
            continue
        if card.account_id:
            if card.account_id in seen_accounts:
                continue
            seen_accounts.add(card.account_id)
            used = account_credit_usage(card.account_id, month, year)
        else:
            used = credit_invoice_total(card.id, month, year)
        result.append((card, used, limit))
    return result


# ── Cadastro de cartões ─────────────────────────────────────────────────────

def get_or_create_account(user_id: int, bank: str | None, credit_limit=None) -> CreditAccount | None:
    """Conta de crédito do (usuário, banco); criada se não existir."""
    if not bank:
        return None
    account = CreditAccount.query.filter_by(user_id=user_id, bank=bank).first()
    if account is None:
        account = CreditAccount(user_id=user_id, bank=bank, credit_limit=credit_limit or None)
        db.session.add(account)
        db.session.flush()
    elif credit_limit:
        account.credit_limit = credit_limit
    return account


def apply_card_fields(card: CreditCard, *, special_type: str | None, supports_credit: bool,
                      supports_debit: bool, nickname: str | None, last_digits: str,
                      bank: str | None, due_day: int | None, best_buy_day: int | None,
                      credit_limit, is_virtual: bool, is_additional: bool,
                      monthly_amount, renewal_day: int | None) -> None:
    """Preenche o cartão a partir dos dados do formulário/API, com as mesmas regras
    do FinFam original (VR/VA não têm crédito/débito; crédito exige melhor dia < vencimento)."""
    is_vr_va = special_type in ('vr', 'va')
    sc = bool(supports_credit) and not is_vr_va
    sd = bool(supports_debit) and not is_vr_va
    due, best = due_day or 1, best_buy_day or 1
    if sc and best >= due:
        raise ValueError('O melhor dia de compra deve ser anterior ao dia de vencimento.')

    card.nickname = nickname or None
    card.last_digits = last_digits
    card.bank = bank or None
    card.due_day = due
    card.best_buy_day = best
    card.credit_limit = credit_limit or None
    card.card_type = special_type if is_vr_va else ('credit' if sc else 'debit')
    card.supports_credit = sc
    card.supports_debit = sd
    card.is_virtual = bool(is_virtual)
    card.is_additional = bool(is_additional)
    card.monthly_amount = monthly_amount if is_vr_va else None
    card.renewal_day = renewal_day if is_vr_va else None

    account = get_or_create_account(card.user_id, card.bank, card.credit_limit) if sc else None
    card.account_id = account.id if account else None


def sibling_cards(card: CreditCard, uids: list) -> list[CreditCard] | None:
    """Cartões de crédito da mesma conta (fatura consolidada). None se só há um."""
    if not (card.account_id and card.supports_credit):
        return None
    siblings = (CreditCard.query
                .filter(CreditCard.account_id == card.account_id, CreditCard.user_id.in_(uids),
                        CreditCard.supports_credit == True)  # noqa: E712
                .order_by(CreditCard.is_virtual, CreditCard.is_additional, CreditCard.last_digits)
                .all())
    return siblings if len(siblings) > 1 else None


def card_usage(card: CreditCard, month: int, year: int) -> dict:
    """Resumo do cartão no mês, para a listagem."""
    sc, sd = bool(card.supports_credit), bool(card.supports_debit)
    if sc and card.account_id:
        cr_total = account_credit_usage(card.account_id, month, year)
    elif sc:
        cr_total = credit_invoice_total(card.id, month, year)
    else:
        cr_total = 0.0
    db_total = debit_total(card.id, month, year) if sd else 0.0
    total = cr_total if sc else (db_total if sd else invoice_total(card.id, month, year))
    limit = card.effective_limit
    gradient, text_color = card_gradient(card)
    return {
        'total': round(total, 2),
        'credit_total': round(cr_total, 2),
        'debit_total': round(db_total, 2),
        'limit': limit,
        'pct': round(cr_total / limit * 100, 1) if (limit and sc) else None,
        'available': round(limit - cr_total, 2) if (limit and sc) else None,
        'vr_balance': vr_va_balance(card, month, year),
        'account_cards': card.account.cards.count() if card.account_id and card.account else 1,
        'payment_status': credit_invoice_status(card, month, year),
        'status': invoice_status(card, month, year),
        'gradient': gradient,
        'text_color': text_color,
    }


_CHART_COLORS = ['#0d6efd', '#dc3545', '#198754', '#ffc107', '#0dcaf0', '#6f42c1']


def history_chart(cards: list, month: int, year: int, months: int = 6) -> dict:
    """Barras dos últimos N meses por cartão (cartão misto vira 2 séries)."""
    from app.services.finance import month_label
    periods = [month_offset(month, year, -i) for i in range(months - 1, -1, -1)]
    series, idx = [], 0

    def add(label, fn):
        nonlocal idx
        series.append({'label': label, 'data': [round(fn(m, y), 2) for m, y in periods],
                       'color': _CHART_COLORS[idx % len(_CHART_COLORS)]})
        idx += 1

    for card in cards:
        if card.supports_credit and card.supports_debit:
            add(f'{card.label} — Crédito', lambda m, y, c=card: credit_invoice_total(c.id, m, y))
            add(f'{card.label} — Débito', lambda m, y, c=card: debit_total(c.id, m, y))
        else:
            add(card.label, lambda m, y, c=card: invoice_total(c.id, m, y))
    return {'labels': [month_label(m, y) for m, y in periods], 'series': series}
