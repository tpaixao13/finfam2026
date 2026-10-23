"""
alert_service.py — Motor de alertas proativos.

Diferença em relação a insights:
  - Insights: análise histórica, mostrada no dashboard
  - Alertas:  condições críticas AGORA, visíveis em todas as páginas

Cada alerta tem:
  type        : 'danger' | 'warning' | 'info'
  icon        : classe Bootstrap Icons
  title       : título curto
  message     : descrição completa
  action_label: texto do botão de ação (None se não houver)
  action_url  : URL do botão de ação
  priority    : número menor = mais urgente
  key         : hash estável da condição (usado para snooze futuro)
"""

import hashlib
from datetime import datetime, date as _date, timedelta
from sqlalchemy import func, or_, and_
from urllib.parse import urlencode
from app.core.db import db
from app.models import Expense, Salary, CreditCard, Goal
from app.services.finance import sum_expenses_month, sum_salaries_month, month_offset, brl as _brl

# ── Thresholds ──────────────────────────────────────────────────────
_TH = {
    'card_critical':  90,   # % limite → danger
    'card_warning':   75,   # % limite → warning
    'overdue_danger':  1,   # qualquer atraso → danger
    'due_soon_days':   3,   # dias para vencer → warning
    'balance_neg':     0,   # saldo < 0 → danger
    'spend_ratio_crit': 95, # % renda gasta → danger
    'spend_ratio_warn': 85, # % renda gasta → warning
    'pending_ratio':   50,  # pendentes > 50% da renda → warning
    'no_income_day':   10,  # após dia X sem renda → info
    'no_expense_day':   5,  # após dia X sem despesa → info
    'max_alerts':       8,
}


def _url(path: str, **params) -> str:
    """Rota do frontend (o link é aberto pela SPA)."""
    params = {k: v for k, v in params.items() if v is not None}
    return path + ('?' + urlencode(params) if params else '')


def _key(prefix: str, *parts) -> str:
    raw = prefix + '|' + '|'.join(str(p) for p in parts)
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def generate_alerts(uids: list, month: int, year: int,
                    tenant_id: int,
                    cards: list | None = None) -> list[dict]:
    """
    Gera alertas proativos para o tenant.
    Retorna lista ordenada por prioridade (mais urgente primeiro).
    """
    alerts: list[dict] = []
    today  = datetime.now().date()
    now    = datetime.now()

    cur_exp = sum_expenses_month(uids, year, month)
    cur_sal = sum_salaries_month(uids, year, month)
    cur_bal = cur_sal - cur_exp

    # ── Helper: data real de vencimento da despesa ───────────────────
    def _due_date(e) -> _date:
        """
        Para cartão de crédito: vencimento é o due_day do cartão no mês da fatura.
        Para demais: é o dia registrado na despesa.
        """
        if (e.payment_method == 'Cartão de Crédito'
                and e.card_id and e.credit_card and e.credit_card.due_day):
            d = min(e.credit_card.due_day, 28)
            try:
                return _date(e.year, e.month, d)
            except ValueError:
                return _date(e.year, e.month, 28)
        try:
            return _date(e.year, e.month, e.day)
        except (ValueError, TypeError):
            return _date(e.year, e.month, 1)

    # ── 1. Despesas vencidas ─────────────────────────────────────────
    all_unpaid = (Expense.query
                  .filter(Expense.user_id.in_(uids), Expense.paid.isnot(True))
                  .order_by(Expense.year, Expense.month, Expense.day)
                  .all())

    overdue_rows = [e for e in all_unpaid if _due_date(e) < today]

    if overdue_rows:
        total_ov = sum(float(e.amount) for e in overdue_rows)
        n = len(overdue_rows)
        oldest = overdue_rows[0]
        days_late = (today - _due_date(oldest)).days
        alerts.append({
            'type': 'danger', 'priority': 1,
            'key': _key('overdue', n, year, month),
            'icon': 'bi-clock-history',
            'title': f'{n} despesa{"s" if n > 1 else ""} em atraso',
            'message': (f'{n} despesa{"s" if n > 1 else ""} não paga{"s" if n > 1 else ""} '
                        f'totalizando {_brl(total_ov)}. '
                        f'A mais antiga venceu há {days_late} dia{"s" if days_late > 1 else ""}.'),
            'action_label': 'Ver atrasadas',
            'action_url': _url('/expenses',
                                  month=oldest.month, year=oldest.year,
                                  paid='pendente'),
        })

    # ── 2. Vencimentos nos próximos 3 dias ───────────────────────────
    soon_limit = today + timedelta(days=_TH['due_soon_days'])
    pending_month = [e for e in all_unpaid
                     if e.year == today.year and e.month == today.month]
    due_soon = [e for e in pending_month
                if today <= _due_date(e) <= soon_limit]

    if due_soon:
        n = len(due_soon)
        total_soon = sum(float(e.amount) for e in due_soon)
        alerts.append({
            'type': 'warning', 'priority': 5,
            'key': _key('due_soon', n, today.isoformat()),
            'icon': 'bi-calendar-event',
            'title': f'{n} despesa{"s" if n > 1 else ""} vence{"m" if n > 1 else ""} em breve',
            'message': (f'{n} despesa{"s" if n > 1 else ""} com vencimento '
                        f'nos próximos {_TH["due_soon_days"]} dias, '
                        f'total de {_brl(total_soon)}.'),
            'action_label': 'Ver agenda',
            'action_url': _url('/expenses', month=month, year=year),
        })

    # ── 3. Limite dos cartões ────────────────────────────────────────
    if cards:
        from app.services.card_service import credit_limit_usage
        for card, used, limit in credit_limit_usage(cards, month, year):
            pct   = used / limit * 100 if limit > 0 else 0

            if pct >= _TH['card_critical']:
                alerts.append({
                    'type': 'danger', 'priority': 2,
                    'key': _key('card_crit', card.id, month, year),
                    'icon': 'bi-credit-card-2-front',
                    'title': f'{card.label} — limite crítico',
                    'message': (f'Você usou {pct:.0f}% do limite do cartão '
                                f'({_brl(used)} de {_brl(limit)}).'),
                    'action_label': 'Ver fatura',
                    'action_url': _url(f'/cards/{card.id}/invoice',
                                         month=month, year=year),
                })
            elif pct >= _TH['card_warning']:
                alerts.append({
                    'type': 'warning', 'priority': 8,
                    'key': _key('card_warn', card.id, month, year),
                    'icon': 'bi-credit-card',
                    'title': f'{card.label} — {pct:.0f}% do limite',
                    'message': (f'Seu cartão já usou {pct:.0f}% do limite '
                                f'({_brl(used)} de {_brl(limit)}).'),
                    'action_label': 'Ver fatura',
                    'action_url': _url(f'/cards/{card.id}/invoice',
                                         month=month, year=year),
                })

    # ── 4. Saldo negativo ────────────────────────────────────────────
    if cur_sal > 0 and cur_bal < _TH['balance_neg']:
        alerts.append({
            'type': 'danger', 'priority': 3,
            'key': _key('neg_bal', month, year, round(cur_bal)),
            'icon': 'bi-exclamation-octagon',
            'title': 'Saldo negativo',
            'message': (f'Seus gastos ({_brl(cur_exp)}) superaram sua renda '
                        f'({_brl(cur_sal)}) em {_brl(abs(cur_bal))}.'),
            'action_label': 'Ver dashboard',
            'action_url': _url('/', month=month, year=year),
        })

    # ── 5. Taxa de comprometimento crítica ──────────────────────────
    elif cur_sal > 0 and cur_exp > 0:
        ratio = cur_exp / cur_sal * 100
        if ratio >= _TH['spend_ratio_crit']:
            alerts.append({
                'type': 'danger', 'priority': 4,
                'key': _key('spend_crit', month, year, round(ratio)),
                'icon': 'bi-pie-chart-fill',
                'title': 'Orçamento quase esgotado',
                'message': (f'Você comprometeu {ratio:.0f}% da sua renda este mês. '
                            f'Resta apenas {_brl(cur_sal - cur_exp)}.'),
                'action_label': 'Ver despesas',
                'action_url': _url('/expenses', month=month, year=year),
            })
        elif ratio >= _TH['spend_ratio_warn']:
            alerts.append({
                'type': 'warning', 'priority': 10,
                'key': _key('spend_warn', month, year, round(ratio)),
                'icon': 'bi-pie-chart',
                'title': f'{ratio:.0f}% da renda comprometida',
                'message': (f'Seus gastos já representam {ratio:.0f}% da sua renda. '
                            f'Saldo livre: {_brl(cur_sal - cur_exp)}.'),
                'action_label': 'Ver despesas',
                'action_url': _url('/expenses', month=month, year=year),
            })

    # ── 6. Pendentes > 50% da renda ─────────────────────────────────
    if cur_sal > 0:
        total_pend = sum(float(e.amount) for e in pending_month if e not in due_soon)
        if total_pend > cur_sal * (_TH['pending_ratio'] / 100):
            alerts.append({
                'type': 'warning', 'priority': 12,
                'key': _key('pend_ratio', month, year, round(total_pend)),
                'icon': 'bi-hourglass-split',
                'title': 'Alto volume de pendências',
                'message': (f'Você tem {_brl(total_pend)} em despesas pendentes — '
                            f'mais de {_TH["pending_ratio"]}% da sua renda.'),
                'action_label': 'Ver pendentes',
                'action_url': _url('/', month=month, year=year),
            })

    # ── 7. Renda não registrada ──────────────────────────────────────
    if today.month == month and today.year == year:
        if today.day >= _TH['no_income_day'] and cur_sal == 0:
            alerts.append({
                'type': 'info', 'priority': 20,
                'key': _key('no_income', month, year),
                'icon': 'bi-cash-coin',
                'title': 'Nenhuma renda registrada',
                'message': (f'Já é dia {today.day} e nenhuma renda foi registrada '
                            f'para este mês.'),
                'action_label': 'Registrar renda',
                'action_url': _url('/incomes'),
            })

        # ── 8. Nenhuma despesa registrada ───────────────────────────
        if today.day >= _TH['no_expense_day'] and cur_exp == 0:
            alerts.append({
                'type': 'info', 'priority': 25,
                'key': _key('no_expense', month, year),
                'icon': 'bi-receipt',
                'title': 'Nenhuma despesa registrada',
                'message': (f'Já é dia {today.day} e nenhuma despesa foi '
                            f'registrada para este mês.'),
                'action_label': 'Adicionar despesa',
                'action_url': _url('/expenses', new=1),
            })

    # ── 9. Parcelas de alto valor nos próximos meses ─────────────────
    next_m, next_y = month_offset(month, year, 1)
    future_high = float(
        db.session.query(func.sum(Expense.amount))
        .filter(
            Expense.user_id.in_(uids),
            Expense.installment_group_id.isnot(None),
            or_(Expense.year > year,
                and_(Expense.year == year, Expense.month > month)),
        ).scalar() or 0
    )
    if cur_sal > 0 and future_high > cur_sal * 0.3:
        alerts.append({
            'type': 'info', 'priority': 30,
            'key': _key('future_inst', month, year, round(future_high)),
            'icon': 'bi-calendar-range',
            'title': 'Parcelas comprometidas',
            'message': (f'{_brl(future_high)} em parcelas já estão comprometidas '
                        f'para os próximos meses.'),
            'action_label': 'Ver parcelas',
            'action_url': _url('/expenses', month=next_m, year=next_y),
        })

    # ── 10. Alertas de metas ─────────────────────────────────────────
    active_goals = (Goal.query
                    .filter(Goal.user_id.in_(uids), Goal.active == True)
                    .all())
    if active_goals:
        from app.services.goal_service import calculate_all as _calc_goals, goals_for_alerts
        goals_data = _calc_goals(active_goals, uids, month, year)
        alerts.extend(goals_for_alerts(goals_data))

    # ── Ordenar e limitar ────────────────────────────────────────────
    _order = {'danger': 0, 'warning': 1, 'info': 2, 'success': 3}
    alerts.sort(key=lambda x: (_order.get(x['type'], 4), x.get('priority', 99)))
    return alerts[:_TH['max_alerts']]


def quick_alert_count(uids: list, month: int, year: int) -> int:
    """
    Contagem rápida de alertas críticos.
    Executada em todo request logado — deve ser muito leve.
    """
    today = datetime.now().date()

    # Overdue
    overdue = (db.session.query(func.count(Expense.id))
               .filter(
                   Expense.user_id.in_(uids),
                   Expense.paid.isnot(True),
                   or_(
                       Expense.year < today.year,
                       and_(Expense.year == today.year,
                            Expense.month < today.month),
                       and_(Expense.year == today.year,
                            Expense.month == today.month,
                            Expense.day < today.day),
                   )
               ).scalar() or 0)

    # Saldo negativo
    cur_exp = sum_expenses_month(uids, year, month)
    cur_sal = sum_salaries_month(uids, year, month)
    neg_bal = 1 if cur_sal > 0 and cur_exp > cur_sal else 0

    return overdue + neg_bal


def build_alert_telegram_text(alerts: list[dict]) -> str | None:
    """Monta texto para Telegram com alertas críticos e de atenção."""
    critical = [a for a in alerts if a['type'] in ('danger', 'warning')]
    if not critical:
        return None

    lines = ['⚠️ <b>Alertas FinFam</b>\n']
    icons = {'danger': '🔴', 'warning': '🟡', 'info': 'ℹ️'}
    for a in critical:
        ico = icons.get(a['type'], '•')
        lines.append(f"{ico} <b>{a['title']}</b>")
        lines.append(f"   {a['message']}\n")
    return '\n'.join(lines)
