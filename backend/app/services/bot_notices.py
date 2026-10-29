"""Avisos automáticos do bot central do Telegram.

Cada função faz uma coisa:
  week_report / week_text   resumo da semana (gasto × média, 3 maiores, categoria que mais subiu)
  agenda                    contas dos próximos dias, dia a dia (faturas somadas)
  card_notices              fatura que fecha amanhã / vence em 2 dias (família)
  balance_notices           conta que vence hoje/amanhã maior que o saldo do banco (só do dono do banco)
  goal_notices              limite de gastos que passou de 80% / 100% (uma vez por nível no mês)
  run_weekly / run_goal_alerts   os disparos (chamados a cada minuto pelo laço da API)
"""
import logging
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import and_, func, or_, text

from app.core.config import settings
from app.core.db import db
from app.models import BankAccount, BankConnection, CreditCard, Expense, Goal, Tenant, User
from app.services import telegram as tg
from app.services.finance import CREDIT, brl

log = logging.getLogger('finfam.notices')

WEEKLY_WEEKDAY, WEEKLY_HOUR = 6, 19      # domingo, 19h (hora do servidor)
QUIET_START, QUIET_END = 8, 22           # avisos de meta só entre 8h e 22h
WEEKS_FOR_AVERAGE = 8
_WEEKDAYS = ['segunda', 'terça', 'quarta', 'quinta', 'sexta', 'sábado', 'domingo']


def _h(value) -> str:
    return tg._h(value)


def _dm(d: date) -> str:
    return f'{d.day:02d}/{d.month:02d}'


def _norm(value) -> str:
    return unicodedata.normalize('NFKD', str(value or '')).encode('ascii', 'ignore').decode().lower().strip()


def _clamp(year: int, month: int, day: int) -> date:
    for d in range(min(day, 31), 27, -1):
        try:
            return date(year, month, d)
        except ValueError:
            continue
    return date(year, month, min(day, 28))


# ── resumo da semana ─────────────────────────────────────────────────────────

def _bought_int():
    """Data REAL da compra como AAAAMMDD (no crédito, year/month são da fatura)."""
    return (func.coalesce(Expense.purchase_year, Expense.year) * 10000
            + func.coalesce(Expense.purchase_month, Expense.month) * 100 + Expense.day)


def _as_int(d: date) -> int:
    return d.year * 10000 + d.month * 100 + d.day


def _spent(uids: list[int], start: date, end: date) -> list[Expense]:
    """Gastos com data de compra entre start e end (parcelas 2, 3… não contam: já contaram na compra)."""
    bought = _bought_int()
    return (Expense.query.filter(Expense.user_id.in_(uids), bought >= _as_int(start), bought <= _as_int(end),
                                 or_(Expense.installment_number.is_(None), Expense.installment_number <= 1))
            .all())


def week_report(uids: list[int], today: date) -> dict:
    """Semana de segunda a domingo que contém `today`, comparada com a média das 8 anteriores."""
    start = today - timedelta(days=today.weekday())
    end = start + timedelta(days=6)
    week = _spent(uids, start, end)
    before = _spent(uids, start - timedelta(weeks=WEEKS_FOR_AVERAGE), start - timedelta(days=1))
    total = round(sum(float(e.amount) for e in week), 2)
    average = round(sum(float(e.amount) for e in before) / WEEKS_FOR_AVERAGE, 2)
    by_cat, past_cat = defaultdict(float), defaultdict(float)
    for e in week:
        by_cat[e.category] += float(e.amount)
    for e in before:
        past_cat[e.category] += float(e.amount) / WEEKS_FOR_AVERAGE
    rises = sorted(((cat, v - past_cat.get(cat, 0.0), v) for cat, v in by_cat.items()), key=lambda r: -r[1])
    rise = next(({'category': c, 'amount': round(v, 2), 'average': round(v - d, 2), 'diff': round(d, 2)}
                 for c, d, v in rises if d >= 1), None)
    top = sorted(week, key=lambda e: -float(e.amount))[:3]
    return {'start': start.isoformat(), 'end': end.isoformat(), 'total': total, 'average': average,
            'count': len(week), 'rise': rise,
            'top': [{'description': e.description, 'amount': round(float(e.amount), 2), 'category': e.category,
                     'user_name': e.user.name if e.user else None} for e in top]}


def week_text(r: dict) -> str:
    start, end = date.fromisoformat(r['start']), date.fromisoformat(r['end'])
    lines = [f'📈 <b>Sua semana</b> ({_dm(start)} a {_dm(end)})', '']
    if not r['count']:
        lines.append('Nenhum gasto lançado nesta semana.')
        return '\n'.join(lines)
    lines.append(f'Gastos: <b>{brl(r["total"])}</b> em {r["count"]} lançamento(s)')
    if r['average']:
        diff = r['total'] - r['average']
        pct = abs(diff) / r['average'] * 100
        trend = (f'🔺 {pct:.0f}% acima' if diff > 0.005 * r['average'] else
                 f'🔻 {pct:.0f}% abaixo' if diff < -0.005 * r['average'] else 'igual')
        lines.append(f'Média das últimas {WEEKS_FOR_AVERAGE} semanas: {brl(r["average"])} ({trend})')
    lines += ['', '<b>Maiores gastos</b>']
    lines += [f'• {_h(t["description"])} — {brl(t["amount"])} ({_h(t["category"])})' for t in r['top']]
    if r['rise']:
        x = r['rise']
        lines += ['', f'📊 <b>{_h(x["category"])}</b> foi a que mais subiu: {brl(x["amount"])} '
                      f'(média {brl(x["average"])}/semana, +{brl(x["diff"])})']
    return '\n'.join(lines)


# ── agenda ───────────────────────────────────────────────────────────────────

def agenda(uids: list[int], today: date, days: int = 7) -> dict:
    """Contas a pagar de hoje até hoje+days-1, por dia (como o calendário do site)."""
    from app.services.expense_service import calendar
    end = today + timedelta(days=days - 1)
    merged: dict[str, list] = {}
    month = (today.year, today.month)
    while month <= (end.year, end.month):
        cal = calendar(uids, month[0], month[1], today)
        for day, items in cal['days'].items():
            if today.isoformat() <= day <= end.isoformat():
                merged[day] = [i for i in items if i['status'] != 'pago']
        month = (month[0] + (month[1] == 12), month[1] % 12 + 1)
    out = []
    for i in range(days):
        d = today + timedelta(days=i)
        items = merged.get(d.isoformat(), [])
        out.append({'date': d.isoformat(), 'weekday': _WEEKDAYS[d.weekday()], 'items': items,
                    'total': round(sum(x['amount'] for x in items), 2)})
    return {'start': today.isoformat(), 'days': out, 'total': round(sum(d['total'] for d in out), 2)}


# ── cartões: fecha amanhã / vence em 2 dias ──────────────────────────────────

def _bill_groups(uids: list[int]) -> list[list[CreditCard]]:
    """Cartões de crédito agrupados por fatura (mesma conta de crédito = uma fatura)."""
    groups: dict[tuple, list] = {}
    for c in CreditCard.query.filter(CreditCard.user_id.in_(uids), CreditCard.supports_credit.is_(True)).all():
        groups.setdefault(('acc', c.account_id) if c.account_id else ('card', c.id), []).append(c)
    return list(groups.values())


def _bill_totals(cards: list[CreditCard], year: int, month: int) -> tuple[float, float]:
    """(total, falta pagar) da fatura do mês."""
    rows = (db.session.query(Expense.paid, func.sum(Expense.amount))
            .filter(Expense.card_id.in_([c.id for c in cards]), Expense.year == year, Expense.month == month,
                    Expense.payment_method == CREDIT).group_by(Expense.paid).all())
    total = sum(float(v or 0) for _, v in rows)
    pending = sum(float(v or 0) for paid, v in rows if not paid)
    return round(total, 2), round(pending, 2)


def _card_name(cards: list[CreditCard]) -> str:
    c = cards[0]
    return c.bank or c.nickname or f'final {c.last_digits}'


def card_notices(uids: list[int], today: date) -> list[str]:
    tomorrow, in_two = today + timedelta(days=1), today + timedelta(days=2)
    out = []
    for cards in _bill_groups(uids):
        card = cards[0]
        name = _h(_card_name(cards))
        if card.best_buy_day and _clamp(tomorrow.year, tomorrow.month, card.best_buy_day) == tomorrow:
            total, _ = _bill_totals(cards, tomorrow.year, tomorrow.month)
            if total:
                out.append(f'💳 A fatura do <b>{name}</b> fecha amanhã ({_dm(tomorrow)}): {brl(total)} até agora.')
        if card.due_day and _clamp(in_two.year, in_two.month, card.due_day) == in_two:
            _, pending = _bill_totals(cards, in_two.year, in_two.month)
            if pending:
                out.append(f'⏰ A fatura do <b>{name}</b> vence em 2 dias ({_dm(in_two)}): {brl(pending)} a pagar.')
    return out


# ── conta × saldo do banco (dado do dono do banco: vai só para ele) ──────────

def _due_soon(user_id: int, today: date) -> list[tuple[Expense, date]]:
    tomorrow = today + timedelta(days=1)
    days = [(d.year, d.month, d.day) for d in (today, tomorrow)]
    rows = (Expense.query.filter(Expense.user_id == user_id, Expense.paid.isnot(True), Expense.payment_method != CREDIT,
                                 or_(*[and_(Expense.year == y, Expense.month == m, Expense.day == d) for y, m, d in days]))
            .order_by(Expense.amount.desc()).all())
    return [(e, date(e.year, e.month, e.day)) for e in rows]


def _bank_accounts(user_id: int) -> list[BankAccount]:
    return (BankAccount.query.join(BankConnection)
            .filter(BankConnection.user_id == user_id, BankAccount.type == 'BANK', BankAccount.balance.isnot(None),
                    func.coalesce(BankAccount.currency, 'BRL') == 'BRL').all())


def _account_for(e: Expense, accounts: list[BankAccount]) -> BankAccount | None:
    """A conta do banco da despesa (campo Banco); sem banco e com uma conta só, essa."""
    wanted = _norm(e.bank)
    if wanted:
        for a in accounts:
            if wanted in _norm(a.connection.label) or _norm(a.connection.label) in wanted or wanted in _norm(a.name):
                return a
        return None
    return accounts[0] if len(accounts) == 1 else None


def balance_notices(user: User, today: date) -> list[str]:
    tenant = user.tenant
    if tenant is None or not tenant.open_finance_enabled:
        return []
    accounts = _bank_accounts(user.id)
    if not accounts:
        return []
    by_account: dict[int, list] = defaultdict(list)
    for e, due in _due_soon(user.id, today):
        acc = _account_for(e, accounts)
        if acc is not None:
            by_account[acc.id].append((e, due))
    out = []
    for acc in accounts:
        bills = by_account.get(acc.id)
        if not bills:
            continue
        total, balance = sum(float(e.amount) for e, _ in bills), float(acc.balance)
        if total <= balance:
            continue
        when = lambda d: 'hoje' if d == today else 'amanhã'  # noqa: E731
        what = ', '.join(f'{_h(e.description)} {brl(float(e.amount))} ({when(d)})' for e, d in bills[:4])
        out.append(f'⚠️ Saldo do <b>{_h(acc.connection.label)}</b> é {brl(balance)} e vence {what}'
                   + (f' — total {brl(total)}.' if len(bills) > 1 else '.'))
    return out


# ── metas de limite de gastos ────────────────────────────────────────────────

def goal_notices(uids: list[int], today: date) -> list[str]:
    """Limite de gastos que passou de 80% ou 100% no mês. Grava o nível avisado na meta (não repete)."""
    from app.services.goal_service import calculate
    period = f'{today.year}-{today.month:02d}'
    out = []
    goals = Goal.query.filter(Goal.user_id.in_(uids), Goal.active.is_(True), Goal.type == 'spending_limit').all()
    for g in goals:
        d = calculate(g, uids, today.month, today.year)
        used = d['pct_used']
        level = 100 if used >= 100 else 80 if used >= 80 else 0
        sent_period, _, sent_level = (g.alert_sent or '').partition(':')
        already = int(sent_level) if sent_period == period and sent_level.isdigit() else 0
        if level <= already:
            continue
        g.alert_sent = f'{period}:{level}'
        what = f' de {_h(g.category)}' if g.category else ''
        if level == 100:
            out.append(f'🚨 Meta <b>{_h(g.title)}</b>: o limite{what} estourou — {brl(d["current"])} '
                       f'de {brl(float(g.target_amount))} ({used:.0f}%).')
        else:
            out.append(f'⚠️ Meta <b>{_h(g.title)}</b>: {used:.0f}% do limite{what} usado — '
                       f'{brl(d["current"])} de {brl(float(g.target_amount))}, restam {brl(d["remaining"])}.')
    return out


# ── disparos ─────────────────────────────────────────────────────────────────

def _send(chats: list[str], message: str, tenant: Tenant | None = None) -> int:
    """Envia a cada conversa. Se o grupo da família não aceita mais (bot removido), desliga o grupo
    e manda no privado de cada pessoa."""
    sent = 0
    for chat in chats:
        ok, err = tg.send_message(settings.telegram_bot_token, chat, message)
        sent += ok
        if not ok:
            log.warning('Aviso do bot não foi entregue: %s', err)
            if tenant is not None and tg.drop_dead_group(tenant, chat, err):
                sent += _send(tg.linked_chats(tg.family_user_ids(tenant)), message)
    return sent


def _family(tenant: Tenant) -> tuple[list[int], list[str]]:
    """Membros e conversas que recebem os avisos da família (o grupo, se a família conectou um)."""
    uids = tg.family_user_ids(tenant)
    return uids, tg.family_chats(tenant, uids)


def _claim_week(tenant: Tenant, today: date) -> bool:
    """UPDATE atômico: com vários workers, só um envia o resumo da semana."""
    r = db.session.execute(text(
        'UPDATE tenants SET telegram_weekly_sent = :today WHERE id = :id '
        'AND (telegram_weekly_sent IS NULL OR telegram_weekly_sent != :today)'),
        {'today': today.isoformat(), 'id': tenant.id})
    db.session.commit()
    return r.rowcount == 1


def run_weekly(now: datetime) -> int:
    """Domingo às 19h: resumo da semana para as conversas vinculadas (avisos ligados)."""
    if now.weekday() != WEEKLY_WEEKDAY or now.hour != WEEKLY_HOUR or not settings.telegram_bot_token:
        return 0
    sent = 0
    for tenant in Tenant.query.filter(Tenant.telegram_alerts.isnot(False)).all():
        if not tenant.trial_active:
            continue
        uids, chats = _family(tenant)
        if not chats or not _claim_week(tenant, now.date()):
            continue
        sent += _send(chats, week_text(week_report(uids, now.date())), tenant)
    return sent


def run_goal_alerts(now: datetime) -> int:
    """A cada 15 minutos, em horário comercial: avisos de limite de gastos."""
    if now.minute % 15 or not QUIET_START <= now.hour < QUIET_END or not settings.telegram_bot_token:
        return 0
    sent = 0
    for tenant in Tenant.query.filter(Tenant.telegram_alerts.isnot(False)).all():
        if not tenant.trial_active:
            continue
        uids, chats = _family(tenant)
        if not chats:
            continue
        notes = goal_notices(uids, now.date())
        db.session.commit()  # grava o nível avisado antes de enviar (não repete se o envio cair no meio)
        if notes:
            sent += _send(chats, '🎯 <b>Metas</b>\n\n' + '\n'.join(notes), tenant)
    return sent


def run_personal_notices(tenant: Tenant, today: date) -> int:
    """Junto do lembrete diário: conta × saldo, para cada pessoa no Telegram dela (dado só dela)."""
    sent = 0
    for user in User.query.filter_by(tenant_id=tenant.id).all():
        notes = balance_notices(user, today)
        chats = tg.linked_chats([user.id]) if notes else []
        if chats:
            sent += _send(chats, '🏦 <b>Saldo × contas</b>\n\n' + '\n'.join(notes))
    return sent
