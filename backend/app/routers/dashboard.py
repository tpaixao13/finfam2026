"""Dashboard, alertas e dados dos gráficos."""
import calendar
from datetime import date as _date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func

from app.core.db import db
from app.core.deps import Ctx, Period, get_ctx, get_period
from app.models import CreditCard, Expense, Goal, Salary
from app.services.alert_service import generate_alerts
from app.services.finance import (CATEGORY_COLORS, CREDIT, MONTH_NAMES_FULL, PAYMENT_COLORS, month_label,
                                  month_offset, pct_delta, sum_expenses_month, sum_salaries_month,
                                  user_color_map)
from app.services.goal_service import calculate_all
from app.services.insight_service import generate_insights
from app.services.serializers import expense_out, goal_out, user_out

router = APIRouter(tags=['dashboard'])


def _cards(ctx: Ctx):
    return CreditCard.query.filter(CreditCard.user_id.in_(ctx.uids)).all()


@router.get('/dashboard', summary='Resumo do mês: KPIs, por pessoa, insights, metas, pendentes')
def dashboard(period: Period = Depends(get_period), ctx: Ctx = Depends(get_ctx)):
    month, year, uids = period.month, period.year, ctx.uids
    prev_m, prev_y = month_offset(month, year, -1)
    next_m, next_y = month_offset(month, year, 1)

    exp_map = dict(db.session.query(Expense.user_id, func.sum(Expense.amount))
                   .filter(Expense.user_id.in_(uids), Expense.year == year, Expense.month == month)
                   .group_by(Expense.user_id).all())
    sal_map = dict(db.session.query(Salary.user_id, func.sum(Salary.amount))
                   .filter(Salary.user_id.in_(uids), Salary.year == year, Salary.month == month,
                           Salary.received == True)  # noqa: E712
                   .group_by(Salary.user_id).all())
    colors = user_color_map(ctx.members)
    people = []
    for u in ctx.members:
        spent, income = float(exp_map.get(u.id) or 0), float(sal_map.get(u.id) or 0)
        people.append({'user': user_out(u, colors[u.id]), 'income': round(income, 2),
                       'expenses': round(spent, 2), 'balance': round(income - spent, 2)})

    total_income = sum(p['income'] for p in people)
    total_expenses = sum(p['expenses'] for p in people)
    credit = sum_expenses_month(uids, year, month, Expense.payment_method == CREDIT)
    prev_exp = sum_expenses_month(uids, prev_y, prev_m)
    prev_inc = sum_salaries_month(uids, prev_y, prev_m)
    prev_credit = sum_expenses_month(uids, prev_y, prev_m, Expense.payment_method == CREDIT)

    today = _date.today()
    pending = (Expense.query.filter(Expense.user_id.in_(uids), Expense.year == year, Expense.month == month,
                                    Expense.paid.isnot(True))
               .order_by(Expense.day, Expense.created_at).all())
    recent = (Expense.query.filter(Expense.user_id.in_(uids), Expense.year == year, Expense.month == month)
              .order_by(Expense.day.desc(), Expense.created_at.desc()).limit(10).all())
    goals = (Goal.query.filter(Goal.user_id.in_(uids), Goal.active == True)  # noqa: E712
             .order_by(Goal.created_at).all())

    return {
        'month': month, 'year': year, 'month_name': MONTH_NAMES_FULL[month - 1],
        'prev': {'month': prev_m, 'year': prev_y}, 'next': {'month': next_m, 'year': next_y},
        'kpis': {
            'income': round(total_income, 2), 'expenses': round(total_expenses, 2),
            'credit': round(credit, 2), 'balance': round(total_income - total_expenses, 2),
            'deltas': {'income': pct_delta(total_income, prev_inc),
                       'expenses': pct_delta(total_expenses, prev_exp),
                       'credit': pct_delta(credit, prev_credit),
                       'balance': pct_delta(total_income - total_expenses, prev_inc - prev_exp)},
        },
        'people': people,
        'insights': generate_insights(uids, month, year, cards=_cards(ctx)),
        'goals': [goal_out(d) for d in calculate_all(goals, uids, month, year)[:3]],
        'pending': [expense_out(e, today) for e in pending],
        'pending_total': round(sum(float(e.amount) for e in pending), 2),
        'recent': [expense_out(e, today) for e in recent],
    }


@router.get('/alerts', summary='Alertas proativos (atrasos, limites, saldo, metas)')
def alerts(period: Period = Depends(get_period), ctx: Ctx = Depends(get_ctx)):
    items = generate_alerts(ctx.uids, period.month, period.year, ctx.tenant_id, cards=_cards(ctx))
    return {'month': period.month, 'year': period.year, 'alerts': items,
            'danger': sum(1 for a in items if a['type'] == 'danger'),
            'warning': sum(1 for a in items if a['type'] == 'warning')}


# ── Gráficos ─────────────────────────────────────────────────────────────────

def _last_months(n: int, month: int, year: int) -> list[tuple[int, int]]:
    return [month_offset(month, year, -i) for i in range(n - 1, -1, -1)]


@router.get('/charts/categories', tags=['charts'], summary='Gastos por categoria no mês')
def chart_categories(period: Period = Depends(get_period), ctx: Ctx = Depends(get_ctx)):
    rows = (db.session.query(Expense.category, func.sum(Expense.amount))
            .filter(Expense.user_id.in_(ctx.uids), Expense.year == period.year, Expense.month == period.month)
            .group_by(Expense.category).order_by(func.sum(Expense.amount).desc()).all())
    return {'labels': [r[0] for r in rows], 'data': [round(float(r[1]), 2) for r in rows],
            'colors': [CATEGORY_COLORS.get(r[0], '#C9CBCF') for r in rows],
            'total_income': sum_salaries_month(ctx.uids, period.year, period.month)}


@router.get('/charts/monthly', tags=['charts'], summary='Gastos x renda nos últimos N meses')
def chart_monthly(months: int = Query(6, ge=1, le=24), period: Period = Depends(get_period),
                  ctx: Ctx = Depends(get_ctx)):
    ms = _last_months(months, period.month, period.year)
    return {'labels': [month_label(m, y) for m, y in ms],
            'expenses': [sum_expenses_month(ctx.uids, y, m) for m, y in ms],
            'income': [sum_salaries_month(ctx.uids, y, m) for m, y in ms]}


@router.get('/charts/people', tags=['charts'], summary='Gastos x renda por pessoa no mês')
def chart_people(period: Period = Depends(get_period), ctx: Ctx = Depends(get_ctx)):
    colors = user_color_map(ctx.members)
    return {'labels': [u.name for u in ctx.members],
            'expenses': [sum_expenses_month([u.id], period.year, period.month) for u in ctx.members],
            'income': [sum_salaries_month([u.id], period.year, period.month) for u in ctx.members],
            'colors': [colors[u.id] for u in ctx.members]}


@router.get('/charts/daily', tags=['charts'], summary='Gasto acumulado por dia no mês')
def chart_daily(period: Period = Depends(get_period), ctx: Ctx = Depends(get_ctx)):
    days = calendar.monthrange(period.year, period.month)[1]
    rows = dict(db.session.query(Expense.day, func.sum(Expense.amount))
                .filter(Expense.user_id.in_(ctx.uids), Expense.year == period.year, Expense.month == period.month)
                .group_by(Expense.day).all())
    cumulative, data = 0.0, []
    for d in range(1, days + 1):
        cumulative += float(rows.get(d) or 0)
        data.append(round(cumulative, 2))
    return {'labels': list(range(1, days + 1)), 'data': data}


@router.get('/charts/payment-methods', tags=['charts'], summary='Gastos por forma de pagamento (N meses)')
def chart_payment_methods(months: int = Query(6, ge=1, le=24), period: Period = Depends(get_period),
                          ctx: Ctx = Depends(get_ctx)):
    ms = _last_months(months, period.month, period.year)
    series = [{'label': method, 'color': color,
               'data': [sum_expenses_month(ctx.uids, y, m, Expense.payment_method == method) for m, y in ms]}
              for method, color in PAYMENT_COLORS.items()]
    return {'labels': [month_label(m, y) for m, y in ms], 'series': series,
            'totals': {s['label']: round(sum(s['data']), 2) for s in series}}


@router.get('/charts/paid-vs-pending', tags=['charts'], summary='Pago x pendente no mês')
def chart_paid(period: Period = Depends(get_period), ctx: Ctx = Depends(get_ctx)):
    paid = sum_expenses_month(ctx.uids, period.year, period.month, Expense.paid == True)  # noqa: E712
    pending = sum_expenses_month(ctx.uids, period.year, period.month, Expense.paid.isnot(True))
    return {'labels': ['Pago', 'Pendente'], 'data': [paid, pending], 'colors': ['#198754', '#ffc107']}

