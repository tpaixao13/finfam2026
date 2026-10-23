"""
goal_service.py — Engine de metas financeiras e coaching automático.

Para cada meta, calcula:
  current_value  : progresso atual em R$
  pct            : % de conclusão (0-100)
  remaining      : R$ restantes para atingir a meta
  status         : 'completed' | 'on_track' | 'ahead' | 'at_risk' | 'behind' | 'over_limit'
  status_label   : texto amigável
  status_icon    : Bootstrap Icons
  coaching_msg   : mensagem motivacional personalizada
  forecast_date  : previsão de conclusão (se houver ritmo)
"""

from datetime import datetime, date as _date, timedelta
from sqlalchemy import func, and_, or_
from app.core.db import db
from app.models import Goal, Expense
from app.services.finance import sum_expenses_month, sum_salaries_month, brl as _brl


# ── Status configs ──────────────────────────────────────────────────
_STATUS = {
    'completed':  ('bi-check-circle-fill', 'Concluída 🎉',  'success'),
    'ahead':      ('bi-rocket-takeoff',    'Adiantado 🚀',   'success'),
    'on_track':   ('bi-check2-circle',     'No ritmo ✅',    'success'),
    'at_risk':    ('bi-exclamation-circle','Em risco ⚠️',    'warning'),
    'behind':     ('bi-clock-history',     'Atrasada ⏳',    'danger'),
    'over_limit': ('bi-x-circle-fill',     'Limite excedido 🚨', 'danger'),
    'not_started':('bi-play-circle',       'Não iniciada',   'secondary'),
}


def _days_diff(d1: _date, d2: _date) -> int:
    return (d1 - d2).days


def _forecast(current: float, target: float, days_elapsed: int) -> _date | None:
    """Prevê a data de conclusão com base no ritmo atual."""
    if days_elapsed <= 0 or current <= 0:
        return None
    pace_per_day = current / days_elapsed
    if pace_per_day <= 0:
        return None
    days_needed = (target - current) / pace_per_day
    if days_needed <= 0:
        return _date.today()
    return _date.today() + timedelta(days=int(days_needed))


def calculate(goal: Goal, uids: list, month: int, year: int) -> dict:
    """
    Calcula o progresso completo de uma meta.
    Retorna dict com todos os dados necessários para renderização.
    """
    today    = _date.today()
    start    = goal.start_date
    target   = float(goal.target_amount)
    due      = goal.due_date

    # ── Calcular current_value por tipo ─────────────────────────────
    if goal.type == 'saving':
        cur_income  = sum_salaries_month(uids, year, month)
        cur_expense = sum_expenses_month(uids, year, month)
        current     = max(0.0, cur_income - cur_expense)

    elif goal.type == 'spending_limit':
        cat = goal.category or ''
        current = sum_expenses_month(uids, year, month,
                                     Expense.category == cat) if cat else \
                  sum_expenses_month(uids, year, month)

    elif goal.type == 'debt_reduction':
        # Soma despesas pagas desde start_date (na categoria da meta, se definida)
        q = (db.session.query(func.sum(Expense.amount))
             .filter(
                 Expense.user_id.in_(uids),
                 Expense.paid == True,
                 or_(Expense.year > start.year,
                     and_(Expense.year == start.year,
                          Expense.month >= start.month)),
             ))
        if goal.category:
            q = q.filter(Expense.category == goal.category)
        current = float(q.scalar() or 0)

    else:
        current = 0.0

    # ── Progresso ────────────────────────────────────────────────────
    if goal.type == 'spending_limit':
        # Para limite de gastos: progresso é o % RESTANTE do orçamento
        remaining  = max(target - current, 0)
        pct_used   = min(current / target * 100, 150) if target > 0 else 0
        pct        = max(0, 100 - pct_used)          # % do orçamento disponível
        over_amount = max(current - target, 0)
    else:
        remaining  = max(target - current, 0)
        pct        = min(current / target * 100, 100) if target > 0 else 0
        pct_used   = pct
        over_amount = 0.0

    # ── Dias ─────────────────────────────────────────────────────────
    days_elapsed  = max(_days_diff(today, start), 1)
    days_remaining = _days_diff(due, today) if due else None
    days_total    = max(_days_diff(due, start), 1) if due else None

    # ── Status ───────────────────────────────────────────────────────
    if goal.completed_at or (goal.type != 'spending_limit' and pct >= 100):
        status = 'completed'

    elif goal.type == 'spending_limit':
        if over_amount > 0:
            status = 'over_limit'
        elif pct_used >= 85:
            status = 'at_risk'
        else:
            status = 'on_track'

    elif due:
        expected_pct = (days_elapsed / days_total) * 100
        if pct >= expected_pct + 10:
            status = 'ahead'
        elif pct >= expected_pct - 10:
            status = 'on_track'
        elif pct >= expected_pct - 25:
            status = 'at_risk'
        else:
            status = 'behind'

    elif current == 0:
        status = 'not_started'
    else:
        status = 'on_track'

    icon, label, color = _STATUS.get(status, _STATUS['not_started'])

    # ── Previsão de conclusão ────────────────────────────────────────
    forecast = None
    if goal.type != 'spending_limit' and status not in ('completed',):
        forecast = _forecast(current, target, days_elapsed)

    # ── Mensagem de coaching ─────────────────────────────────────────
    coaching = _coaching_message(
        goal.type, status, current, target, remaining,
        days_remaining, pct, over_amount, forecast
    )

    # ── Ritmo ideal vs atual ─────────────────────────────────────────
    ideal_pace   = (target / days_total) if days_total else None
    current_pace = current / days_elapsed if days_elapsed > 0 else 0

    return {
        'goal':          goal,
        'current':       current,
        'target':        target,
        'remaining':     remaining,
        'pct':           round(pct, 1),
        'pct_used':      round(pct_used, 1),
        'over_amount':   over_amount,
        'status':        status,
        'status_label':  label,
        'status_icon':   icon,
        'status_color':  color,
        'coaching':      coaching,
        'forecast':      forecast,
        'days_remaining': days_remaining,
        'ideal_pace':    ideal_pace,
        'current_pace':  current_pace,
    }


def calculate_all(goals: list[Goal], uids: list, month: int, year: int) -> list[dict]:
    return [calculate(g, uids, month, year) for g in goals]


def _coaching_message(gtype, status, current, target, remaining,
                      days_remaining, pct, over_amount, forecast) -> str:
    """Gera mensagem motivacional personalizada."""

    if status == 'completed':
        return f'🎉 Parabéns! Você atingiu sua meta!'

    if gtype == 'spending_limit':
        if status == 'over_limit':
            return (f'⚠️ Você ultrapassou o limite em {_brl(over_amount)}. '
                    f'Evite novos gastos nessa categoria até o fim do mês.')
        elif status == 'at_risk':
            return (f'Atenção! Você usou {pct:.0f}% do orçamento. '
                    f'Restam apenas {_brl(remaining)} — use com cuidado.')
        else:
            return f'Ótimo! Você ainda tem {_brl(remaining)} disponíveis nessa categoria.'

    if status == 'ahead':
        return f'🚀 Você está adiantado! Continue assim e vai superar a meta.'

    if status == 'behind':
        if days_remaining and days_remaining > 0:
            daily_needed = remaining / days_remaining
            return (f'Você precisa de {_brl(daily_needed)}/dia nos próximos '
                    f'{days_remaining} dias para atingir a meta.')
        return f'Faltam {_brl(remaining)} para atingir a meta. Aumente o ritmo!'

    if status == 'at_risk':
        if days_remaining:
            return (f'Em risco! Faltam {_brl(remaining)} e apenas '
                    f'{days_remaining} dias. Redobre o esforço.')
        return f'Em risco. Faltam {_brl(remaining)} — mantenha o foco.'

    # on_track / not_started
    if forecast:
        from datetime import date
        today = date.today()
        days_to = (forecast - today).days
        if days_to <= 0:
            return f'No ritmo! Você deve atingir a meta em breve.'
        return (f'No ritmo! Previsão de conclusão em '
                f'{forecast.strftime("%d/%m")} ({days_to} dias).')

    if remaining > 0:
        return f'Bom progresso! Faltam {_brl(remaining)} para atingir a meta.'

    return 'Continue assim!'


def goals_for_alerts(goals_data: list[dict]) -> list[dict]:
    """
    Converte progresso de metas em alertas para o alert_service.
    """
    alerts = []
    for d in goals_data:
        g = d['goal']

        if d['status'] == 'completed':
            alerts.append({
                'type': 'success', 'priority': 45,
                'key': f'goal_done_{g.id}',
                'icon': 'bi-trophy-fill',
                'title': f'Meta concluída: {g.title}',
                'message': f'🎉 Você atingiu sua meta "{g.title}"! Defina um novo objetivo.',
                'action_label': 'Ver metas',
                'action_url': '/goals',
            })

        elif d['status'] in ('behind', 'at_risk'):
            alerts.append({
                'type': 'warning' if d['status'] == 'at_risk' else 'danger',
                'priority': 18,
                'key': f'goal_late_{g.id}',
                'icon': 'bi-flag',
                'title': f'Meta atrasada: {g.title}',
                'message': d['coaching'],
                'action_label': 'Ver metas',
                'action_url': '/goals',
            })

        elif d['status'] == 'over_limit':
            alerts.append({
                'type': 'danger', 'priority': 6,
                'key': f'goal_over_{g.id}',
                'icon': 'bi-x-circle',
                'title': f'Limite excedido: {g.title}',
                'message': d['coaching'],
                'action_label': 'Ver metas',
                'action_url': '/goals',
            })

        elif d['pct'] >= 90 and d['status'] != 'completed':
            alerts.append({
                'type': 'info', 'priority': 35,
                'key': f'goal_near_{g.id}',
                'icon': 'bi-flag-fill',
                'title': f'Quase lá: {g.title}',
                'message': f'Você está a {100 - d["pct"]:.0f}% de atingir sua meta! {d["coaching"]}',
                'action_label': 'Ver metas',
                'action_url': '/goals',
            })

    return alerts


def goals_for_insights(goals_data: list[dict]) -> list[dict]:
    """Converte progresso de metas em insights para o insight_service."""
    insights = []
    for d in goals_data:
        g = d['goal']

        if d['status'] == 'completed':
            insights.append({
                'type': 'success', 'priority': 15,
                'icon': 'bi-trophy',
                'title': f'Meta concluída!',
                'message': f'Você atingiu a meta "{g.title}". Excelente disciplina!',
                'value': '100%',
                'link': '/goals',
            })
        elif d['status'] in ('behind', 'at_risk', 'over_limit'):
            insights.append({
                'type': 'warning' if d['status'] == 'at_risk' else 'danger',
                'priority': 16,
                'icon': 'bi-flag',
                'title': g.title,
                'message': d['coaching'],
                'value': f'{d["pct"]:.0f}%',
                'link': '/goals',
            })
        elif d['status'] == 'ahead':
            insights.append({
                'type': 'success', 'priority': 38,
                'icon': 'bi-rocket',
                'title': f'{g.title} — adiantado!',
                'message': d['coaching'],
                'value': f'{d["pct"]:.0f}%',
                'link': '/goals',
            })

    return insights
