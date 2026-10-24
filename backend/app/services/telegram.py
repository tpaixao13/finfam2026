"""Lembrete diário via Telegram (contas de hoje, em atraso e alertas críticos).

Destino do lembrete de uma família:
1. bot central (TELEGRAM_BOT_TOKEN) → todas as conversas vinculadas pelos membros;
2. sem ninguém vinculado → bot próprio configurado no FinFam antigo (token + chat id).
"""
import html
import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, date as date_type

from sqlalchemy import and_, or_, text

from app.core.config import settings
from app.core.db import db
from app.core.security import decrypt_field
from app.models import CreditCard, Expense, TelegramLink, Tenant, User
from app.services.finance import CREDIT, brl
from app.services.market import _ssl_context

log = logging.getLogger('finfam.telegram')


def _h(text) -> str:
    return html.escape(str(text or ''), quote=False)


def send_message(token: str, chat_id: str, text_html: str) -> tuple[bool, str]:
    """Envia mensagem pelo Bot API. Retorna (sucesso, erro)."""
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    if len(text_html) > 4000:  # limite do Telegram é 4096: corta numa quebra de linha
        text_html = text_html[:3900].rsplit('\n', 1)[0] + '\n\n… lista cortada. Veja tudo no site.'
    payload = json.dumps({'chat_id': chat_id, 'text': text_html, 'parse_mode': 'HTML'}).encode()
    req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=10, context=_ssl_context()) as resp:
            return resp.status == 200, ''
    except urllib.error.HTTPError as exc:
        body = exc.read().decode('utf-8', errors='replace')
        try:
            detail = json.loads(body).get('description', body)
        except ValueError:
            detail = body
        return False, f'Telegram API {exc.code}: {detail}'
    except Exception as exc:
        return False, str(exc)


def linked_chats(user_ids: list[int]) -> list[str]:
    if not settings.telegram_bot_token or not user_ids:
        return []
    return [link.chat_id for link in TelegramLink.query.filter(TelegramLink.user_id.in_(user_ids)).all()]


def reminder_targets(tenant: Tenant, user_ids: list[int]) -> list[tuple[str, str]]:
    """[(token do bot, chat_id)] para onde vai o lembrete desta família."""
    chats = linked_chats(user_ids)
    if chats:
        return [(settings.telegram_bot_token, c) for c in chats]
    if tenant.telegram_token and tenant.telegram_chat_id:
        return [(decrypt_field(tenant.telegram_token), decrypt_field(tenant.telegram_chat_id))]
    return []


def build_daily_reminder(users: list, today: date_type | None = None) -> str | None:
    """Texto HTML com vencimentos de hoje e despesas em atraso. None se não há nada."""
    today = today or datetime.now().date()
    uids = [u.id for u in users]
    names = {u.id: u.name for u in users}
    is_credit = Expense.payment_method == CREDIT

    due_today = (Expense.query
                 .filter(Expense.user_id.in_(uids), Expense.year == today.year,
                         Expense.month == today.month, Expense.day == today.day,
                         Expense.paid.isnot(True), ~is_credit)
                 .order_by(Expense.user_id, Expense.description).all())

    before_this_month = or_(Expense.year < today.year,
                            and_(Expense.year == today.year, Expense.month < today.month))
    overdue = (Expense.query
               .filter(Expense.user_id.in_(uids), Expense.paid.isnot(True),
                       or_(and_(~is_credit, or_(before_this_month,
                                                and_(Expense.year == today.year,
                                                     Expense.month == today.month,
                                                     Expense.day < today.day))),
                           and_(is_credit, before_this_month)))
               .order_by(Expense.year, Expense.month, Expense.day).all())

    if not due_today and not overdue:
        return None

    lines = [f'📅 <b>FinFam — {today.strftime("%d/%m/%Y")}</b>\n']
    if due_today:
        lines.append('🔔 <b>Vencimentos de hoje:</b>')
        for e in due_today:
            lines += [f'• {_h(names.get(e.user_id, "?"))} — {_h(e.description)} ({_h(e.category)}) — {brl(e.amount)}', '']
    if overdue:
        lines.append('⚠️ <b>Em atraso:</b>')
        for e in overdue:
            try:
                days = (today - date_type(e.year, e.month, e.day)).days
            except ValueError:
                days = 0
            lines += [f'• {_h(names.get(e.user_id, "?"))} — {_h(e.description)} ({_h(e.category)})'
                      f' — {brl(e.amount)} — {days}d de atraso', '']
    return '\n'.join(lines)


def _tenant_message(tenant: Tenant, now: datetime) -> str | None:
    users = User.query.filter_by(tenant_id=tenant.id).all()
    if not users:
        return None
    msg = build_daily_reminder(users)
    try:
        from app.services.alert_service import build_alert_telegram_text, generate_alerts
        uids = [u.id for u in users]
        cards = CreditCard.query.filter(CreditCard.user_id.in_(uids)).all()
        alert_msg = build_alert_telegram_text(generate_alerts(uids, now.month, now.year, tenant.id, cards=cards))
        if alert_msg:
            msg = f'{msg}\n\n{alert_msg}' if msg else alert_msg
    except Exception as exc:
        log.warning('Alertas do tenant %s falharam: %s', tenant.id, exc)
    return msg


def run_daily_reminders(now: datetime | None = None) -> int:
    """Executado a cada minuto: envia o lembrete das famílias cujo horário chegou.
    Retorna quantas mensagens foram enviadas."""
    now = now or datetime.now()
    today = now.date()
    sent = 0
    try:
        for tenant in Tenant.query.filter_by(telegram_enabled=True).all():
            if (now.hour, now.minute) != (tenant.telegram_hour or 0, tenant.telegram_minute or 0):
                continue
            if tenant.telegram_last_sent == today:
                continue
            uids = [u.id for u in User.query.filter_by(tenant_id=tenant.id).all()]
            targets = reminder_targets(tenant, uids)
            if not targets:
                continue
            # UPDATE atômico: com vários workers, só um envia.
            claimed = db.session.execute(text(
                'UPDATE tenants SET telegram_last_sent = :today WHERE id = :id '
                'AND (telegram_last_sent IS NULL OR telegram_last_sent != :today)'),
                {'today': today.isoformat(), 'id': tenant.id})
            db.session.commit()
            if claimed.rowcount == 0:
                continue
            msg = _tenant_message(tenant, now)
            if not msg:
                continue
            delivered = 0
            for token, chat_id in targets:
                ok, err = send_message(token, chat_id, msg)
                if ok:
                    delivered += 1
                else:
                    log.warning('Telegram tenant %s: %s', tenant.id, err)
            sent += delivered
            if not delivered:  # tenta de novo no próximo minuto/dia
                db.session.execute(text('UPDATE tenants SET telegram_last_sent = NULL WHERE id = :id'),
                                   {'id': tenant.id})
                db.session.commit()
    finally:
        db.session.remove()
    return sent
