"""Conversão de models para dicionários JSON (respostas da API)."""
import calendar
from datetime import date as _date, datetime

from app.models import CreditCard, Expense, Goal, Investment, Salary, Tenant, User
from app.services.finance import CREDIT


def money(value) -> float:
    return round(float(value or 0), 2)


def iso(value) -> str | None:
    return value.isoformat() if value else None


def avatar_url(user: User) -> str | None:
    return f'/api/v1/avatars/{user.avatar}' if user.avatar else None


def user_out(user: User, color: str | None = None) -> dict:
    data = {'id': user.id, 'name': user.name, 'email': user.email, 'avatar_url': avatar_url(user),
            'two_factor_enabled': bool(user.totp_enabled), 'email_verified': user.email_verified_at is not None}
    if color:
        data['color'] = color
    return data


def tenant_out(tenant: Tenant | None) -> dict | None:
    if tenant is None:
        return None
    return {
        'id': tenant.id, 'name': tenant.name, 'plan': tenant.plan or 'trial',
        'plan_label': tenant.plan_label, 'member_limit': tenant.member_limit,
        'expires_at': iso(tenant.trial_expires_at), 'days_left': tenant.trial_days_left,
        'active': tenant.trial_active, 'extra_members': tenant.extra_members or 0,
        'open_finance': bool(tenant.open_finance_enabled),
        'open_finance_max_items': tenant.open_finance_max_items,
    }


def _clamp_day(year: int, month: int, day: int) -> _date:
    return _date(year, month, max(1, min(day or 1, calendar.monthrange(year, month)[1])))


def expense_due_date(e: Expense) -> _date:
    """Vencimento real: para crédito é o dia de vencimento do cartão no mês da fatura."""
    if e.payment_method == CREDIT and e.card_id and e.credit_card and e.credit_card.due_day:
        return _clamp_day(e.year, e.month, e.credit_card.due_day)
    return _clamp_day(e.year, e.month, e.day)


def expense_status(e: Expense, today: _date | None = None) -> str:
    if e.paid:
        return 'pago'
    today = today or _date.today()
    return 'vencida' if expense_due_date(e) < today else 'pendente'


def expense_out(e: Expense, today: _date | None = None) -> dict:
    series = None
    if e.installment_group_id and e.installment_group:
        series = {'type': 'parcelado', 'group_id': e.installment_group_id,
                  'number': e.installment_number, 'total': e.installment_group.num_installments,
                  'label': f'{e.installment_number}/{e.installment_group.num_installments}'}
    elif e.recurring_group_id and e.recurring_group:
        series = {'type': 'recorrente', 'group_id': e.recurring_group_id,
                  'number': e.recurring_number, 'total': e.recurring_group.num_recurrences,
                  'label': f'{e.recurring_number}/{e.recurring_group.num_recurrences}'}
    return {
        'id': e.id,
        'user_id': e.user_id,
        'user_name': e.user.name if e.user else None,
        'description': e.description,
        'amount': money(e.amount),
        'category': e.category,
        'payment_method': e.payment_method,
        'bank': e.bank,
        'day': e.day,
        'month': e.month,
        'year': e.year,
        'purchase_month': e.display_month,
        'purchase_year': e.display_year,
        'purchase_date': iso(_clamp_day(e.display_year, e.display_month, e.day)),
        'due_date': iso(expense_due_date(e)),
        'status': expense_status(e, today),
        'paid': bool(e.paid),
        'card_id': e.card_id,
        'card_label': e.credit_card.label if e.card_id and e.credit_card else None,
        'series': series,
        'created_at': iso(e.created_at),
    }


def card_out(c: CreditCard) -> dict:
    return {
        'id': c.id, 'user_id': c.user_id, 'owner_name': c.owner.name if c.owner else None,
        'label': c.label, 'nickname': c.nickname, 'last_digits': c.last_digits, 'bank': c.bank,
        'due_day': c.due_day, 'best_buy_day': c.best_buy_day,
        'credit_limit': money(c.credit_limit) if c.credit_limit else None,
        'effective_limit': c.effective_limit,
        'card_type': c.card_type, 'type_label': c.type_label,
        'supports_credit': bool(c.supports_credit), 'supports_debit': bool(c.supports_debit),
        'is_virtual': bool(c.is_virtual), 'is_additional': bool(c.is_additional),
        'monthly_amount': money(c.monthly_amount) if c.monthly_amount else None,
        'renewal_day': c.renewal_day, 'account_id': c.account_id,
        'account_label': c.account.display_label if c.account_id and c.account else None,
    }


def salary_out(s: Salary) -> dict:
    return {
        'id': s.id, 'user_id': s.user_id, 'user_name': s.user.name if s.user else None,
        'year': s.year, 'month': s.month, 'amount': money(s.amount), 'company': s.company,
        'income_type': s.income_type, 'payment_day': s.payment_day,
        'payment_day_type': s.payment_day_type, 'received': bool(s.received),
        'group_id': s.salary_group_id, 'expected_date': iso(s.expected_payment_date()),
    }


def goal_out(d: dict) -> dict:
    """`d` é o dicionário de goal_service.calculate()."""
    g: Goal = d['goal']
    return {
        'id': g.id, 'user_id': g.user_id, 'title': g.title, 'type': g.type,
        'type_label': g.type_label, 'emoji': g.emoji, 'category': g.category,
        'target_amount': money(g.target_amount), 'start_date': iso(g.start_date),
        'due_date': iso(g.due_date), 'completed_at': iso(g.completed_at),
        'current': money(d['current']), 'remaining': money(d['remaining']),
        'pct': d['pct'], 'pct_used': d['pct_used'], 'over_amount': money(d['over_amount']),
        'status': d['status'], 'status_label': d['status_label'], 'status_color': d['status_color'],
        'status_icon': d['status_icon'], 'coaching': d['coaching'], 'forecast': iso(d['forecast']),
        'days_remaining': d['days_remaining'],
        'ideal_pace': round(d['ideal_pace'], 2) if d['ideal_pace'] else None,
        'current_pace': round(d['current_pace'], 2),
    }


def investment_out(i: Investment, yield_info: dict | None = None) -> dict:
    data = {
        'id': i.id, 'user_id': i.user_id, 'user_name': i.user.name if i.user else None,
        'description': i.description, 'amount': money(i.amount), 'investment_type': i.investment_type,
        'annual_rate': float(i.annual_rate or 0), 'crypto_coin': i.crypto_coin,
        'crypto_buy_price': float(i.crypto_buy_price) if i.crypto_buy_price else None,
        'currency': i.currency or 'BRL', 'fx_buy_rate': float(i.fx_buy_rate) if i.fx_buy_rate else None,
        'year': i.year, 'month': i.month, 'created_at': iso(i.created_at),
    }
    if yield_info:
        data['daily_yield'] = {k: (round(v, 6) if isinstance(v, float) else v) for k, v in yield_info.items()}
    return data


def now_iso() -> str:
    return datetime.now().isoformat(timespec='seconds')
