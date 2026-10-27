"""Regras de negócio de despesas: lançamento (à vista, parcelado, recorrente),
edição, pagamento e "contas a pagar". Sem dependência de HTTP/formulários."""
from dataclasses import dataclass
from datetime import date as _date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func

from app.core.db import db
from app.models import CreditCard, Expense, InstallmentGroup, RecurringGroup, User
from app.services.categorizer import count_rule_use, is_duplicate
from app.services.finance import (BANK_PAYMENTS, CATEGORIES, CREDIT, DEBIT, MONTH_NAMES_SHORT,
                                  brl, month_offset)
from app.services.serializers import expense_due_date


class ExpenseError(ValueError):
    """Erro de regra de negócio (vira HTTP 422)."""


@dataclass
class ExpenseData:
    """Dados já validados de uma despesa (data = data real da compra)."""
    user_id: int
    description: str
    amount: Decimal
    category: str
    payment_method: str
    day: int
    month: int
    year: int
    bank: str | None = None
    card_id: int | None = None
    paid: bool = False
    installments: int | None = None
    recurring_months: int | None = None


# ── Cartão / fatura ──────────────────────────────────────────────────────────

def find_card(uids: list, card_id: int | None) -> CreditCard | None:
    if not card_id:
        return None
    return CreditCard.query.filter(CreditCard.id == card_id, CreditCard.user_id.in_(uids)).first()


def resolve_card(uids: list, payment: str, card_id: int | None,
                 day: int, month: int, year: int) -> tuple[CreditCard | None, int, int]:
    """
    Retorna (cartão, mês_fatura, ano_fatura).
    - Crédito: compra após o melhor dia de compra cai na fatura do mês seguinte.
    - Débito/VR/VA: usa o cartão informado; VR/VA se associa sozinho ao cartão do tipo.
    """
    if payment == CREDIT:
        card = find_card(uids, card_id)
        if card is None:
            if card_id:
                raise ExpenseError('Cartão não encontrado.')
            return None, month, year
        bm, by = month_offset(month, year, 1 if day > card.best_buy_day else 0)
        return card, bm, by

    if payment in (DEBIT, 'VR', 'VA'):
        card = find_card(uids, card_id)
        if card_id and card is None:
            raise ExpenseError('Cartão não encontrado.')
        if card is None and payment in ('VR', 'VA'):
            card = (CreditCard.query
                    .filter(CreditCard.user_id.in_(uids), CreditCard.card_type == payment.lower())
                    .first())
        return card, month, year

    return None, month, year


def _bank_for(payment: str, bank: str | None, card: CreditCard | None) -> str | None:
    if card is not None and card.bank:
        return card.bank
    return (bank or None) if payment in BANK_PAYMENTS else None


# ── Lançamento ───────────────────────────────────────────────────────────────

def _write_series(data: ExpenseData, bank, card_id, start_month, start_year,
                  group_field, group_id, number_field, amounts) -> list[Expense]:
    created = []
    for i, amount in enumerate(amounts):
        m, y = month_offset(start_month, start_year, i)
        e = Expense(
            user_id=data.user_id, description=data.description, amount=amount,
            category=data.category, payment_method=data.payment_method, bank=bank,
            year=y, month=m, day=data.day,
            # só a 1ª ocorrência tem a data real da compra; as demais são futuras
            purchase_month=data.month if i == 0 else m,
            purchase_year=data.year if i == 0 else y,
            card_id=card_id, paid=bool(data.paid) if i == 0 else False,
            **{group_field: group_id, number_field: i + 1},
        )
        db.session.add(e)
        created.append(e)
    return created


def split_installments(total: Decimal, n: int) -> list[Decimal]:
    """Divide o total em N parcelas; a última absorve a diferença de arredondamento."""
    parcela = (total / n).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return [parcela] * (n - 1) + [total - parcela * (n - 1)]


def create_expense(uids: list, data: ExpenseData) -> dict:
    """Lança uma despesa. Retorna {kind, message, expenses, duplicate}."""
    if data.installments and data.payment_method != CREDIT:
        raise ExpenseError('Parcelamento só é permitido no Cartão de Crédito.')
    if data.installments and data.recurring_months:
        raise ExpenseError('Uma despesa não pode ser parcelada e recorrente ao mesmo tempo.')

    card, bill_month, bill_year = resolve_card(uids, data.payment_method, data.card_id,
                                               data.day, data.month, data.year)
    card_id = card.id if card else None
    owner = db.session.get(User, data.user_id)
    count_rule_use(owner.tenant_id if owner else None, data.description, data.category)  # "Usos" da regra
    bank = _bank_for(data.payment_method, data.bank, card)
    amount = Decimal(str(data.amount))

    if data.installments:
        n = data.installments
        amounts = split_installments(amount, n)
        group = InstallmentGroup(user_id=data.user_id, description=data.description,
                                 total_amount=amount, num_installments=n, bank=bank or '')
        db.session.add(group)
        db.session.flush()
        created = _write_series(data, bank, card_id, bill_month, bill_year,
                                'installment_group_id', group.id, 'installment_number', amounts)
        db.session.commit()
        end_m, end_y = month_offset(bill_month, bill_year, n - 1)
        return {'kind': 'parcelado', 'duplicate': False, 'expenses': created,
                'message': (f'{n} parcelas criadas de {brl(amounts[0])} cada '
                            f'({MONTH_NAMES_SHORT[bill_month - 1]}/{bill_year} → '
                            f'{MONTH_NAMES_SHORT[end_m - 1]}/{end_y}).')}

    if data.recurring_months:
        n = data.recurring_months
        group = RecurringGroup(user_id=data.user_id, description=data.description,
                               amount=amount, num_recurrences=n)
        db.session.add(group)
        db.session.flush()
        created = _write_series(data, bank, card_id, bill_month, bill_year,
                                'recurring_group_id', group.id, 'recurring_number', [amount] * n)
        db.session.commit()
        end_m, end_y = month_offset(bill_month, bill_year, n - 1)
        return {'kind': 'recorrente', 'duplicate': False, 'expenses': created,
                'message': (f'Despesa recorrente criada por {n} meses '
                            f'({MONTH_NAMES_SHORT[bill_month - 1]}/{bill_year} → '
                            f'{MONTH_NAMES_SHORT[end_m - 1]}/{end_y}).')}

    duplicate = is_duplicate(data.user_id, bill_year, bill_month, data.day,
                             float(amount), data.description)
    expense = Expense(
        user_id=data.user_id, description=data.description, amount=amount,
        category=data.category, payment_method=data.payment_method, bank=bank,
        year=bill_year, month=bill_month, day=data.day,
        purchase_month=data.month, purchase_year=data.year,
        card_id=card_id, paid=bool(data.paid),
    )
    db.session.add(expense)
    db.session.commit()
    message = ('Despesa salva, mas parece duplicada — verifique se já existe igual.'
               if duplicate else 'Despesa adicionada com sucesso!')
    return {'kind': 'avista', 'duplicate': duplicate, 'expenses': [expense], 'message': message}


# ── Edição ───────────────────────────────────────────────────────────────────

def expense_to_data(e: Expense) -> ExpenseData:
    return ExpenseData(
        user_id=e.user_id, description=e.description, amount=Decimal(str(e.amount)),
        category=e.category, payment_method=e.payment_method, day=e.day,
        month=e.display_month, year=e.display_year, bank=e.bank, card_id=e.card_id,
        paid=bool(e.paid),
    )


def update_expense(uids: list, expense: Expense, data: ExpenseData, apply_to_group: bool = False) -> str:
    """Atualiza a despesa (recalculando o mês da fatura) e, se pedido, propaga
    descrição/categoria/pagamento/cartão para as demais parcelas ou recorrências."""
    card, bill_month, bill_year = resolve_card(uids, data.payment_method, data.card_id,
                                               data.day, data.month, data.year)
    expense.user_id = data.user_id
    expense.description = data.description
    expense.amount = Decimal(str(data.amount))
    expense.category = data.category
    expense.payment_method = data.payment_method
    expense.bank = _bank_for(data.payment_method, data.bank, card)
    expense.card_id = card.id if card else None
    expense.day = data.day
    expense.purchase_month, expense.purchase_year = data.month, data.year
    expense.month, expense.year = (bill_month, bill_year) if card else (data.month, data.year)
    expense.paid = bool(data.paid)

    message = 'Despesa atualizada com sucesso!'
    group_filter = None
    if apply_to_group and expense.installment_group_id:
        group_filter = Expense.installment_group_id == expense.installment_group_id
        noun = 'parcelas'
    elif apply_to_group and expense.recurring_group_id:
        group_filter = Expense.recurring_group_id == expense.recurring_group_id
        noun = 'recorrências'

    if group_filter is not None:
        siblings = Expense.query.filter(group_filter, Expense.id != expense.id,
                                        Expense.user_id.in_(uids)).all()
        for s in siblings:
            s.description = expense.description
            s.category = expense.category
            s.payment_method = expense.payment_method
            s.bank = expense.bank
            s.card_id = expense.card_id
            if expense.recurring_group_id:
                s.amount = expense.amount  # recorrência tem o mesmo valor
        message = f'Alterações aplicadas a todas as {len(siblings) + 1} {noun}.'

    db.session.commit()
    return message


# ── Pagamento ────────────────────────────────────────────────────────────────

def mark_paid(uids: list, ids: list[int], paid: bool = True) -> int:
    """Marca várias despesas como pagas (ou pendentes). Retorna quantas mudaram."""
    if not ids:
        return 0
    rows = Expense.query.filter(Expense.id.in_(ids), Expense.user_id.in_(uids)).all()
    changed = 0
    for e in rows:
        if bool(e.paid) != paid:
            e.paid = paid
            changed += 1
    db.session.commit()
    return changed


# ── Contas a pagar ───────────────────────────────────────────────────────────

def bills(uids: list, today: _date | None = None, days_ahead: int = 7,
          include_overdue: bool = True, user_id: int | None = None) -> dict:
    """
    Despesas não pagas agrupadas em: em atraso, vencem hoje e próximos `days_ahead` dias.
    Usa o vencimento real (dia de vencimento do cartão para compras no crédito).
    """
    today = today or _date.today()
    limit = today + timedelta(days=days_ahead)
    end_m, end_y = limit.month, limit.year
    q = Expense.query.filter(Expense.user_id.in_(uids), Expense.paid.isnot(True),
                             (Expense.year * 100 + Expense.month) <= end_y * 100 + end_m)
    if user_id:
        q = q.filter(Expense.user_id == user_id)
    groups = {'overdue': [], 'today': [], 'upcoming': []}
    for e in q.order_by(Expense.year, Expense.month, Expense.day).all():
        due = expense_due_date(e)
        if due < today:
            if include_overdue:
                groups['overdue'].append((due, e))
        elif due == today:
            groups['today'].append((due, e))
        elif due <= limit:
            groups['upcoming'].append((due, e))
    for key in groups:
        groups[key].sort(key=lambda pair: (pair[0], pair[1].id))
    return groups


# ── Apoio ────────────────────────────────────────────────────────────────────

def tenant_categories(uids: list) -> list[str]:
    used = {c[0] for c in db.session.query(Expense.category).filter(Expense.user_id.in_(uids)).distinct()}
    return sorted(set(CATEGORIES) | used)


def tenant_banks(uids: list) -> list[str]:
    rows = (db.session.query(Expense.bank)
            .filter(Expense.user_id.in_(uids), Expense.bank.isnot(None), Expense.bank != '')
            .distinct().order_by(Expense.bank).all())
    return [r[0] for r in rows]


def suggest_category_from_history(description: str, uids: list) -> str | None:
    term = f'%{description.lower()}%'
    row = (db.session.query(Expense.category, func.count('*'))
           .filter(Expense.user_id.in_(uids), func.lower(Expense.description).like(term))
           .group_by(Expense.category).order_by(func.count('*').desc()).first())
    return row[0] if row else None


def today_parts() -> tuple[int, int, int]:
    now = datetime.now()
    return now.day, now.month, now.year
