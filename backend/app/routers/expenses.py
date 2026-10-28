"""Despesas: listagem com filtros, lançamento, edição, pagamento, contas a pagar e export."""
from datetime import date as _date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func

from app.core.db import db, get_or_404
from app.core.deps import Ctx, Period, get_ctx, get_period
from app.models import Expense, InstallmentGroup, RecurringGroup
from app.services import expense_service as svc
from app.services.categorizer import categorize
from app.services.export_service import expense_rows, to_pdf, to_xlsx
from app.services.finance import CREDIT, PAYMENT_METHODS
from app.services.serializers import expense_out, money

router = APIRouter(prefix='/expenses', tags=['expenses'])

PaymentMethod = Literal['PIX', 'Cartão de Débito', 'Cartão de Crédito', 'Dinheiro', 'VR', 'VA']


class _DateFields(BaseModel):
    date: _date | None = Field(None, description='Data da compra (alternativa a day/month/year)')
    day: int | None = Field(None, ge=1, le=31)
    month: int | None = Field(None, ge=1, le=12)
    year: int | None = Field(None, ge=2000, le=2100)

    def _fill_date(self, default: tuple[int, int, int] | None):
        if self.date:
            self.day, self.month, self.year = self.date.day, self.date.month, self.date.year
        elif default:
            d, m, y = default
            self.day, self.month, self.year = self.day or d, self.month or m, self.year or y


class ExpenseCreate(_DateFields):
    description: str = Field(min_length=1, max_length=200)
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2, description='Valor TOTAL em R$')
    category: str = Field(min_length=1, max_length=50)
    payment_method: PaymentMethod
    user_id: int | None = Field(None, description='Membro da família; padrão: quem está logado')
    bank: str | None = Field(None, max_length=60)
    card_id: int | None = None
    paid: bool = False
    installments: int | None = Field(None, ge=2, le=48, description='Nº de parcelas (só crédito)')
    recurring_months: int | None = Field(None, ge=2, le=120, description='Repetir por N meses')

    @model_validator(mode='after')
    def _defaults(self):
        self._fill_date(svc.today_parts())
        if self.installments and self.payment_method != CREDIT:
            raise ValueError('Parcelamento só é permitido no Cartão de Crédito.')
        if self.installments and self.recurring_months:
            raise ValueError('Escolha parcelado OU recorrente, não os dois.')
        return self


class ExpenseUpdate(_DateFields):
    """Atualização parcial: só os campos enviados mudam."""
    description: str | None = Field(None, min_length=1, max_length=200)
    amount: Decimal | None = Field(None, gt=0, max_digits=12, decimal_places=2)
    category: str | None = Field(None, min_length=1, max_length=50)
    payment_method: PaymentMethod | None = None
    user_id: int | None = None
    bank: str | None = Field(None, max_length=60)
    card_id: int | None = None
    paid: bool | None = None
    apply_to_group: bool = Field(False, description='Propagar para todas as parcelas/recorrências')


class IdsIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)
    paid: bool = True


def _filtered_query(ctx: Ctx, *, user_id, month, year, category, payment_method, bank, paid,
                    date_from, date_to, search, purchase_from=None, purchase_to=None):
    q = Expense.query.filter(Expense.user_id.in_(ctx.uids))
    date_int = Expense.year * 10000 + Expense.month * 100 + Expense.day
    # data REAL da compra (no crédito, year/month são da fatura)
    bought_int = (func.coalesce(Expense.purchase_year, Expense.year) * 10000
                  + func.coalesce(Expense.purchase_month, Expense.month) * 100 + Expense.day)
    if purchase_from or purchase_to:
        if purchase_from:
            q = q.filter(bought_int >= purchase_from.year * 10000 + purchase_from.month * 100 + purchase_from.day)
        if purchase_to:
            q = q.filter(bought_int <= purchase_to.year * 10000 + purchase_to.month * 100 + purchase_to.day)
    elif date_from or date_to:
        if date_from:
            q = q.filter(date_int >= date_from.year * 10000 + date_from.month * 100 + date_from.day)
        if date_to:
            q = q.filter(date_int <= date_to.year * 10000 + date_to.month * 100 + date_to.day)
    else:
        q = q.filter(Expense.year == year)
        if month:
            q = q.filter(Expense.month == month)
    if user_id:
        q = q.filter(Expense.user_id == ctx.member(user_id).id)
    if category:
        q = q.filter(Expense.category == category)
    if payment_method:
        q = q.filter(Expense.payment_method == payment_method)
    if bank:
        q = q.filter(Expense.bank == bank)
    if paid == 'pago':
        q = q.filter(Expense.paid == True)  # noqa: E712
    elif paid == 'pendente':
        q = q.filter(Expense.paid.isnot(True))
    if search:
        q = q.filter(func.lower(Expense.description).like(f'%{search.lower()}%'))
    return q


def _filters(month: int | None = Query(None, ge=0, le=12, description='0 = todos os meses do ano'),
             year: int | None = Query(None, ge=2000, le=2100),
             user_id: int | None = None, category: str | None = None,
             payment_method: str | None = None, bank: str | None = None,
             paid: Literal['pago', 'pendente'] | None = None,
             date_from: _date | None = None, date_to: _date | None = None,
             purchase_from: _date | None = Query(None, description='Data da compra a partir de (real, não a da fatura)'),
             purchase_to: _date | None = Query(None, description='Data da compra até'),
             search: str | None = Query(None, max_length=100)) -> dict:
    now = datetime.now()
    return dict(user_id=user_id, month=now.month if month is None else month,
                year=year or now.year, category=category, payment_method=payment_method,
                bank=bank, paid=paid, date_from=date_from, date_to=date_to, search=search,
                purchase_from=purchase_from, purchase_to=purchase_to)


@router.get('', summary='Lista despesas com filtros e paginação')
def list_expenses(f: dict = Depends(_filters), page: int = Query(1, ge=1),
                  per_page: int = Query(20, ge=1, le=200), ctx: Ctx = Depends(get_ctx)):
    q = _filtered_query(ctx, **f)
    total_amount = float(q.with_entities(func.sum(Expense.amount)).scalar() or 0)
    total = q.count()
    rows = (q.order_by(Expense.year.desc(), Expense.month.desc(), Expense.day.desc(),
                       Expense.created_at.desc())
            .offset((page - 1) * per_page).limit(per_page).all())
    today = _date.today()
    return {'items': [expense_out(e, today) for e in rows], 'page': page, 'per_page': per_page,
            'total': total, 'pages': max(1, -(-total // per_page)), 'total_amount': round(total_amount, 2),
            'filters': {k: (v.isoformat() if isinstance(v, _date) else v) for k, v in f.items()}}


@router.get('/options', summary='Valores para os filtros (categorias, bancos, formas de pagamento)')
def options(ctx: Ctx = Depends(get_ctx)):
    return {'categories': svc.tenant_categories(ctx.uids), 'banks': svc.tenant_banks(ctx.uids),
            'payment_methods': PAYMENT_METHODS}


@router.get('/calendar', summary='Contas do mês pelo dia de vencimento (faturas de cartão somadas)')
def calendar(period: Period = Depends(get_period), user_id: int | None = None, ctx: Ctx = Depends(get_ctx)):
    if user_id:
        ctx.member(user_id)
    return svc.calendar(ctx.uids, period.year, period.month, _date.today(), user_id)


@router.get('/bills', summary='Contas a pagar: em atraso, hoje e próximos dias')
def bills(days_ahead: int = Query(7, ge=0, le=90), include_overdue: bool = True,
          user_id: int | None = None, ctx: Ctx = Depends(get_ctx)):
    if user_id:
        ctx.member(user_id)
    today = _date.today()
    groups = svc.bills(ctx.uids, today, days_ahead, include_overdue, user_id)
    out, totals = {}, {}
    for key, pairs in groups.items():
        out[key] = [expense_out(e, today) for _, e in pairs]
        totals[key] = round(sum(float(e.amount) for _, e in pairs), 2)
    return {'date': today.isoformat(), 'days_ahead': days_ahead, **out, 'totals': totals,
            'total': round(sum(totals.values()), 2)}


@router.get('/suggest-category', summary='Sugere a categoria pela descrição (regras + histórico)')
def suggest_category(description: str = Query(min_length=3, max_length=200), ctx: Ctx = Depends(get_ctx)):
    category, source = categorize(description, ctx.uids, ctx.tenant_id) if ctx.tenant_id else (None, None)
    if not category:
        category = svc.suggest_category_from_history(description, ctx.uids)
        source = 'history' if category else None
    return {'category': category, 'source': source}


@router.get('/export', summary='Exporta as despesas filtradas (xlsx ou pdf)')
def export(fmt: Literal['xlsx', 'pdf'] = 'xlsx', f: dict = Depends(_filters), ctx: Ctx = Depends(get_ctx)):
    q = _filtered_query(ctx, **f).order_by(Expense.year, Expense.month, Expense.day, Expense.created_at)
    rows = expense_rows(q.all())
    suffix = f"{f['year']}_{f['month']:02d}"
    if fmt == 'pdf':
        period = 'Todos os meses' if not f['month'] else f"{f['month']:02d}/{f['year']}"
        content, mime = to_pdf(rows, period), 'application/pdf'
    else:
        content = to_xlsx(rows)
        mime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    return Response(content, media_type=mime,
                    headers={'Content-Disposition': f'attachment; filename=despesas_{suffix}.{fmt}'})


@router.post('', status_code=201, summary='Lança despesa (à vista, parcelada ou recorrente)')
def create(body: ExpenseCreate, ctx: Ctx = Depends(get_ctx)):
    data = svc.ExpenseData(
        user_id=ctx.member(body.user_id).id, description=body.description.strip(), amount=body.amount,
        category=body.category.strip(), payment_method=body.payment_method, day=body.day,
        month=body.month, year=body.year, bank=body.bank, card_id=body.card_id, paid=body.paid,
        installments=body.installments, recurring_months=body.recurring_months)
    try:
        result = svc.create_expense(ctx.uids, data)
    except svc.ExpenseError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    today = _date.today()
    return {'message': result['message'], 'kind': result['kind'], 'duplicate': result['duplicate'],
            'expenses': [expense_out(e, today) for e in result['expenses']]}


def _get(ctx: Ctx, expense_id: int) -> Expense:
    return get_or_404(Expense.query.filter(Expense.id == expense_id, Expense.user_id.in_(ctx.uids)),
                      'Despesa não encontrada.')


@router.get('/{expense_id}', summary='Detalhe de uma despesa')
def detail(expense_id: int, ctx: Ctx = Depends(get_ctx)):
    return expense_out(_get(ctx, expense_id))


@router.patch('/{expense_id}', summary='Edita uma despesa (parcial)')
def update(expense_id: int, body: ExpenseUpdate, ctx: Ctx = Depends(get_ctx)):
    expense = _get(ctx, expense_id)
    data = svc.expense_to_data(expense)
    sent = body.model_fields_set
    for field in ('description', 'amount', 'category', 'payment_method', 'bank', 'card_id', 'paid'):
        if field in sent and getattr(body, field) is not None:
            setattr(data, field, getattr(body, field))
    if 'card_id' in sent and body.card_id is None:
        data.card_id = None
    if 'user_id' in sent and body.user_id is not None:
        data.user_id = ctx.member(body.user_id).id
    if sent & {'date', 'day', 'month', 'year'}:
        body._fill_date((data.day, data.month, data.year))
        data.day, data.month, data.year = body.day, body.month, body.year
    try:
        message = svc.update_expense(ctx.uids, expense, data, body.apply_to_group)
    except svc.ExpenseError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {'message': message, 'expense': expense_out(expense)}


@router.delete('/{expense_id}', summary='Exclui uma despesa')
def delete(expense_id: int, ctx: Ctx = Depends(get_ctx)):
    db.session.delete(_get(ctx, expense_id))
    db.session.commit()
    return {'message': 'Despesa excluída.'}


@router.post('/{expense_id}/toggle-paid', summary='Alterna pago/pendente')
def toggle_paid(expense_id: int, ctx: Ctx = Depends(get_ctx)):
    expense = _get(ctx, expense_id)
    expense.paid = not bool(expense.paid)
    db.session.commit()
    return {'message': 'Marcada como paga.' if expense.paid else 'Marcada como pendente.',
            'expense': expense_out(expense)}


@router.post('/pay', summary='Marca várias despesas como pagas (ou pendentes com paid=false)')
def pay_many(body: IdsIn, ctx: Ctx = Depends(get_ctx)):
    changed = svc.mark_paid(ctx.uids, body.ids, body.paid)
    word = 'paga(s)' if body.paid else 'pendente(s)'
    return {'message': f'{changed} despesa(s) marcada(s) como {word}.', 'changed': changed}


@router.delete('/installment-groups/{group_id}', summary='Exclui todas as parcelas de uma compra')
def delete_installments(group_id: int, ctx: Ctx = Depends(get_ctx)):
    group = get_or_404(InstallmentGroup.query.filter(InstallmentGroup.id == group_id,
                                                     InstallmentGroup.user_id.in_(ctx.uids)))
    count = group.installments.count()
    db.session.delete(group)
    db.session.commit()
    return {'message': f'Todas as {count} parcelas foram excluídas.'}


@router.delete('/recurring-groups/{group_id}', summary='Exclui todas as ocorrências de uma recorrência')
def delete_recurring(group_id: int, ctx: Ctx = Depends(get_ctx)):
    group = get_or_404(RecurringGroup.query.filter(RecurringGroup.id == group_id,
                                                   RecurringGroup.user_id.in_(ctx.uids)))
    count = group.recurrences.count()
    db.session.delete(group)
    db.session.commit()
    return {'message': f'Todas as {count} recorrências foram excluídas.'}


@router.get('/summary/by-category', summary='Total por categoria no mês (útil para o bot)')
def by_category(month: int | None = Query(None, ge=1, le=12), year: int | None = Query(None, ge=2000, le=2100),
                ctx: Ctx = Depends(get_ctx)):
    now = datetime.now()
    month, year = month or now.month, year or now.year
    rows = (db.session.query(Expense.category, func.sum(Expense.amount), func.count(Expense.id))
            .filter(Expense.user_id.in_(ctx.uids), Expense.year == year, Expense.month == month)
            .group_by(Expense.category).order_by(func.sum(Expense.amount).desc()).all())
    return {'month': month, 'year': year,
            'items': [{'category': c, 'total': money(t), 'count': n} for c, t, n in rows],
            'total': money(sum(float(t) for _, t, _ in rows))}
