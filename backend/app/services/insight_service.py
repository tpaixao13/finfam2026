"""
insight_service.py — Engine de inteligência financeira.

Analisa os dados do tenant e gera uma lista de insights ordenados por
prioridade. Cada insight tem:

  type     : 'danger' | 'warning' | 'success' | 'info'
  icon     : classe Bootstrap Icons
  title    : título curto
  message  : mensagem completa
  value    : valor em destaque (opcional)
  priority : menor = mais urgente (usado para ordenação interna)
"""

from datetime import datetime, date as _date
from urllib.parse import urlencode
from sqlalchemy import func, or_, and_
from app.core.db import db
from app.models import Expense, RecurringGroup, Goal
from app.services.finance import sum_expenses_month, sum_salaries_month, sum_salaries_month_projected, month_offset, brl as _brl


def _exp_url(month: int, year: int, **kwargs) -> str:
    """Monta URL para a página de despesas com filtros opcionais."""
    params = {'month': month, 'year': year}
    params.update({k: v for k, v in kwargs.items() if v is not None})
    return '/expenses?' + urlencode(params)


# ── Limites e thresholds ────────────────────────────────────────────
_TH = {
    'exp_danger':   30,   # % aumento nos gastos totais → danger
    'exp_warning':  15,   # % aumento nos gastos totais → warning
    'exp_success': -15,   # % queda nos gastos totais → success
    'bal_danger':  -40,   # % queda no saldo → danger
    'bal_warning': -20,   # % queda no saldo → warning
    'bal_success':  20,   # % subida no saldo → success
    'ratio_danger':  95,  # % da renda comprometida → danger
    'ratio_warning': 80,  # % da renda comprometida → warning
    'ratio_success': 50,  # % da renda comprometida → success (abaixo desse é bom)
    'cat_spike':     25,  # % aumento numa categoria → warning/danger
    'cat_dominant':  40,  # % do total para "categoria dominante"
    'card_danger':   80,  # % do limite → danger
    'card_warning':  60,  # % do limite → warning
    'trend_3m_warn': 20,  # % acima da média 3 meses → warning
    'trend_3m_good':-15,  # % abaixo da média 3 meses → success
    'min_data':      50,  # valor mínimo para comparações terem sentido (R$)
    'max_insights':   6,  # máximo de insights retornados
}


def _pct(cur: float, prev: float):
    if prev == 0:
        return None
    return (cur - prev) / prev * 100


def _plur(n: int, sing: str, plur: str) -> str:
    return sing if n == 1 else plur


def generate_insights(uids: list, month: int, year: int,
                      cards: list | None = None) -> list[dict]:
    """Gera insights financeiros para o tenant no mês/ano indicados."""
    insights: list[dict] = []
    prev_m, prev_y = month_offset(month, year, -1)

    # ── Totais base ──────────────────────────────────────────────────
    cur_exp  = sum_expenses_month(uids, year, month)
    # Usa renda projetada (recebida + pendente) para evitar falso positivo quando
    # o salário do mês corrente ainda não foi marcado como recebido
    cur_sal  = sum_salaries_month_projected(uids, year, month)
    cur_bal  = cur_sal - cur_exp

    prev_exp = sum_expenses_month(uids, prev_y, prev_m)
    prev_sal = sum_salaries_month_projected(uids, prev_y, prev_m)
    prev_bal = prev_sal - prev_exp

    # ── 1. Tendência de gastos vs mês anterior ───────────────────────
    exp_delta = _pct(cur_exp, prev_exp)
    if exp_delta is not None and prev_exp >= _TH['min_data']:
        if exp_delta >= _TH['exp_danger']:
            insights.append({
                'type': 'danger', 'priority': 10,
                'icon': 'bi-graph-up-arrow',
                'title': 'Gastos dispararam',
                'message': (f'Seus gastos totais aumentaram {exp_delta:.0f}% '
                            f'em relação ao mês passado ({_brl(prev_exp)} → {_brl(cur_exp)}).'),
                'value': f'+{exp_delta:.0f}%',
                'link': _exp_url(month, year),
            })
        elif exp_delta >= _TH['exp_warning']:
            insights.append({
                'type': 'warning', 'priority': 20,
                'icon': 'bi-graph-up',
                'title': 'Gastos em alta',
                'message': (f'Seus gastos aumentaram {exp_delta:.0f}% '
                            f'em relação ao mês passado ({_brl(prev_exp)} → {_brl(cur_exp)}).'),
                'value': f'+{exp_delta:.0f}%',
                'link': _exp_url(month, year),
            })
        elif exp_delta <= _TH['exp_success']:
            insights.append({
                'type': 'success', 'priority': 35,
                'icon': 'bi-graph-down-arrow',
                'title': 'Gastos reduziram',
                'message': (f'Ótimo! Seus gastos caíram {abs(exp_delta):.0f}% '
                            f'em relação ao mês passado.'),
                'value': f'{exp_delta:.0f}%',
                'link': _exp_url(month, year),
            })

    # ── 2. Tendência de saldo ────────────────────────────────────────
    bal_delta = _pct(cur_bal, prev_bal)
    if bal_delta is not None and abs(prev_bal) >= _TH['min_data']:
        if bal_delta <= _TH['bal_danger']:
            insights.append({
                'type': 'danger', 'priority': 12,
                'icon': 'bi-wallet2',
                'title': 'Saldo crítico',
                'message': (f'Seu saldo livre caiu {abs(bal_delta):.0f}% '
                            f'em relação ao mês passado.'),
                'value': f'{bal_delta:.0f}%',
                'link': _exp_url(month, year),
            })
        elif bal_delta <= _TH['bal_warning']:
            insights.append({
                'type': 'warning', 'priority': 22,
                'icon': 'bi-arrow-down-circle',
                'title': 'Saldo em queda',
                'message': (f'Seu saldo livre caiu {abs(bal_delta):.0f}% '
                            f'em relação ao mês passado.'),
                'value': f'{bal_delta:.0f}%',
                'link': _exp_url(month, year),
            })
        elif bal_delta >= _TH['bal_success']:
            insights.append({
                'type': 'success', 'priority': 38,
                'icon': 'bi-arrow-up-circle',
                'title': 'Saldo melhorou',
                'message': f'Seu saldo livre subiu {bal_delta:.0f}% em relação ao mês passado.',
                'value': f'+{bal_delta:.0f}%',
                'link': _exp_url(month, year),
            })

    # ── 3. Comprometimento da renda ──────────────────────────────────
    if cur_sal > 0 and cur_exp > 0:
        ratio = cur_exp / cur_sal * 100
        if ratio >= _TH['ratio_danger']:
            insights.append({
                'type': 'danger', 'priority': 5,
                'icon': 'bi-exclamation-octagon',
                'title': 'Orçamento no limite',
                'message': (f'Seus gastos comprometem {ratio:.0f}% da sua renda '
                            f'este mês. Você tem apenas {_brl(cur_bal)} de sobra.'),
                'value': f'{ratio:.0f}%',
                'link': _exp_url(month, year),
            })
        elif ratio >= _TH['ratio_warning']:
            insights.append({
                'type': 'warning', 'priority': 18,
                'icon': 'bi-pie-chart',
                'title': 'Alta taxa de comprometimento',
                'message': (f'Seus gastos comprometem {ratio:.0f}% da sua renda este mês.'),
                'value': f'{ratio:.0f}%',
                'link': _exp_url(month, year),
            })
        elif ratio <= _TH['ratio_success']:
            insights.append({
                'type': 'success', 'priority': 42,
                'icon': 'bi-piggy-bank',
                'title': 'Bom controle financeiro',
                'message': (f'Seus gastos representam apenas {ratio:.0f}% da sua renda. '
                            f'Você está economizando {_brl(cur_bal)}!'),
                'value': f'{ratio:.0f}%',
                'link': _exp_url(month, year),
            })

    # ── 4. Spike por categoria ──────────────────────────────────────
    cur_cats = dict(
        db.session.query(Expense.category, func.sum(Expense.amount))
        .filter(Expense.user_id.in_(uids), Expense.year == year, Expense.month == month)
        .group_by(Expense.category).all()
    )
    prev_cats = dict(
        db.session.query(Expense.category, func.sum(Expense.amount))
        .filter(Expense.user_id.in_(uids), Expense.year == prev_y, Expense.month == prev_m)
        .group_by(Expense.category).all()
    )

    worst_spike: tuple | None = None
    for cat, cur_v in cur_cats.items():
        prev_v = float(prev_cats.get(cat, 0))
        if prev_v < _TH['min_data']:
            continue
        d = _pct(float(cur_v), prev_v)
        if d and d >= _TH['cat_spike']:
            if worst_spike is None or d > worst_spike[1]:
                worst_spike = (cat, d)

    if worst_spike:
        cat, d = worst_spike
        insights.append({
            'type': 'danger' if d >= 50 else 'warning', 'priority': 24,
            'icon': 'bi-tag',
            'title': f'{cat} em alta',
            'message': f'Seus gastos com {cat} aumentaram {d:.0f}% em relação ao mês passado.',
            'value': f'+{d:.0f}%',
            'link': _exp_url(month, year, category=cat),
        })

    # Categoria dominante (> 40% do total)
    if cur_exp > 0:
        top = max(cur_cats.items(), key=lambda x: x[1], default=None)
        if top:
            pct_dom = float(top[1]) / cur_exp * 100
            if pct_dom >= _TH['cat_dominant']:
                insights.append({
                    'type': 'info', 'priority': 62,
                    'icon': 'bi-pie-chart-fill',
                    'title': f'{top[0]} domina os gastos',
                    'message': (f'{top[0]} representa {pct_dom:.0f}% '
                                f'de todos os seus gastos este mês ({_brl(float(top[1]))}).'),
                    'value': f'{pct_dom:.0f}%',
                    'link': _exp_url(month, year, category=top[0]),
                })

    # ── 5. Limite dos cartões ────────────────────────────────────────
    if cards:
        from app.services.card_service import credit_limit_usage
        for card, used, limit in credit_limit_usage(cards, month, year):
            pct   = used / limit * 100 if limit > 0 else 0
            card_link = f'/cards/{card.id}/invoice?month={month}&year={year}'
            if pct >= _TH['card_danger']:
                insights.append({
                    'type': 'danger', 'priority': 8,
                    'icon': 'bi-credit-card-2-front',
                    'title': f'{card.label} no limite',
                    'message': (f'Seu cartão atingiu {pct:.0f}% do limite '
                                f'({_brl(used)} de {_brl(limit)}).'),
                    'value': f'{pct:.0f}%',
                    'link': card_link,
                })
            elif pct >= _TH['card_warning']:
                insights.append({
                    'type': 'warning', 'priority': 26,
                    'icon': 'bi-credit-card',
                    'title': f'{card.label} acima de 60%',
                    'message': (f'Seu cartão está em {pct:.0f}% do limite '
                                f'({_brl(used)} de {_brl(limit)}).'),
                    'value': f'{pct:.0f}%',
                    'link': card_link,
                })

    # ── 6. Despesas vencidas ─────────────────────────────────────────
    today = datetime.now().date()
    overdue_total, overdue_n = 0.0, 0

    pending_exps = (Expense.query
                    .filter(Expense.user_id.in_(uids),
                            Expense.year == year, Expense.month == month,
                            Expense.paid.isnot(True))
                    .all())

    pending_total = sum(float(e.amount) for e in pending_exps)
    for e in pending_exps:
        try:
            if e.payment_method == 'Cartão de Crédito':
                # Crédito: vencido só quando o mês da fatura ficou no passado
                is_overdue = (e.year < today.year or
                              (e.year == today.year and e.month < today.month))
            else:
                is_overdue = _date(e.year, e.month, e.day) < today
            if is_overdue:
                overdue_n     += 1
                overdue_total += float(e.amount)
        except (ValueError, TypeError):
            pass

    if overdue_n > 0:
        insights.append({
            'type': 'danger', 'priority': 3,
            'icon': 'bi-clock-history',
            'title': f'{overdue_n} {_plur(overdue_n, "despesa vencida", "despesas vencidas")}',
            'message': (f'Você tem {overdue_n} {_plur(overdue_n, "despesa não paga", "despesas não pagas")} '
                        f'vencida{"s" if overdue_n > 1 else ""}, totalizando {_brl(overdue_total)}.'),
            'value': _brl(overdue_total),
            'link': _exp_url(month, year, paid='pendente'),
        })

    # Pendentes não vencidas
    pending_n = len(pending_exps) - overdue_n
    if pending_n > 0:
        insights.append({
            'type': 'info', 'priority': 55,
            'icon': 'bi-hourglass-split',
            'title': f'{len(pending_exps)} {_plur(len(pending_exps), "despesa pendente", "despesas pendentes")}',
            'message': (f'Você tem {len(pending_exps)} '
                        f'{_plur(len(pending_exps), "despesa a pagar", "despesas a pagar")} '
                        f'no total de {_brl(pending_total)}.'),
            'value': _brl(pending_total),
            'link': _exp_url(month, year, paid='pendente'),
        })

    # ── 7. Despesas recorrentes ──────────────────────────────────────
    rec_count = (db.session.query(func.count(RecurringGroup.id))
                 .filter(RecurringGroup.user_id.in_(uids))
                 .scalar() or 0)
    if rec_count > 0:
        insights.append({
            'type': 'info', 'priority': 72,
            'icon': 'bi-arrow-repeat',
            'title': f'{rec_count} {_plur(rec_count, "despesa recorrente", "despesas recorrentes")}',
            'message': (f'Você possui {rec_count} '
                        f'{_plur(rec_count, "despesa recorrente ativa", "despesas recorrentes ativas")} no sistema.'),
            'value': str(rec_count),
            'link': _exp_url(month, year),
        })

    # ── 8. Tendência de 3 meses ──────────────────────────────────────
    months_3 = [month_offset(month, year, -i) for i in range(1, 4)]
    vals_3   = [sum_expenses_month(uids, y, m) for m, y in months_3]
    avg_3m   = sum(vals_3) / 3

    if avg_3m >= _TH['min_data'] and cur_exp > 0:
        d3 = _pct(cur_exp, avg_3m)
        if d3 and d3 >= _TH['trend_3m_warn']:
            insights.append({
                'type': 'warning', 'priority': 30,
                'icon': 'bi-bar-chart-line',
                'title': 'Acima da média trimestral',
                'message': (f'Seus gastos estão {d3:.0f}% acima '
                            f'da sua média dos últimos 3 meses ({_brl(avg_3m)}/mês).'),
                'value': f'+{d3:.0f}%',
                'link': _exp_url(month, year),
            })
        elif d3 and d3 <= _TH['trend_3m_good']:
            insights.append({
                'type': 'success', 'priority': 40,
                'icon': 'bi-bar-chart-line',
                'title': 'Abaixo da média trimestral',
                'message': (f'Seus gastos estão {abs(d3):.0f}% abaixo '
                            f'da sua média dos últimos 3 meses ({_brl(avg_3m)}/mês). Excelente!'),
                'value': f'{d3:.0f}%',
                'link': _exp_url(month, year),
            })

    # ── 9. Parcelas futuras ──────────────────────────────────────────
    future = float(
        db.session.query(func.sum(Expense.amount))
        .filter(
            Expense.user_id.in_(uids),
            Expense.installment_group_id.isnot(None),
            or_(Expense.year > year,
                and_(Expense.year == year, Expense.month > month)),
        ).scalar() or 0
    )
    if future >= _TH['min_data']:
        insights.append({
            'type': 'info', 'priority': 68,
            'icon': 'bi-calendar-range',
            'title': 'Parcelas futuras comprometidas',
            'message': f'Você tem {_brl(future)} em parcelas já comprometidas para faturas futuras.',
            'value': _brl(future),
            'link': f'/expenses?month=0&year={year}',
        })

    # ── 10. Insights de metas ────────────────────────────────────────
    active_goals = (Goal.query
                    .filter(Goal.user_id.in_(uids), Goal.active == True)
                    .all())
    if active_goals:
        from app.services.goal_service import calculate_all as _calc_goals, goals_for_insights
        goals_data = _calc_goals(active_goals, uids, month, year)
        insights.extend(goals_for_insights(goals_data))

    # ── Ordenar e limitar ────────────────────────────────────────────
    _order = {'danger': 0, 'warning': 1, 'success': 3, 'info': 4}
    insights.sort(key=lambda x: (_order.get(x['type'], 5), x.get('priority', 99)))
    result = insights[:_TH['max_insights']]

    # ── All-clear: nenhum danger/warning → adicionar insight positivo
    has_negative = any(i['type'] in ('danger', 'warning') for i in result)
    if result and not has_negative:
        result.insert(0, {
            'type': 'success', 'priority': 0,
            'icon': 'bi-shield-check',
            'title': 'Finanças saudáveis!',
            'message': 'Nenhum alerta crítico este mês. Continue com o bom trabalho!',
            'value': None,
        })

    return result
