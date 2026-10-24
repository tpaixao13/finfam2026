"""Configurações: lembrete diário do Telegram, listas de referência e planos."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.db import db
from app.core.deps import Ctx, get_ctx, get_ctx_allow_expired, get_ctx_session
from app.core.security import decrypt_field, encrypt_field
from app.services import finance as fin
from app.services.serializers import tenant_out
from app.routers.telegram import link_status
from app.services.telegram import build_daily_reminder, reminder_targets, send_message

router = APIRouter(tags=['settings'])

PLANS = [
    {'id': 'mensal', 'label': 'Mensal', 'members': 1, 'period_days': 30},
    {'id': 'anual', 'label': 'Anual', 'members': 2, 'period_days': 365},
    {'id': 'vitalicio', 'label': 'Vitalício', 'members': 4, 'period_days': None},
]
SUPPORT_EMAIL = 'suporte@finfam.net.br'


class TelegramIn(BaseModel):
    enabled: bool
    token: str | None = Field(None, max_length=200, description='Deixe vazio para manter o atual')
    chat_id: str | None = Field(None, max_length=100, description='Deixe vazio para manter o atual')
    hour: int = Field(8, ge=0, le=23)
    minute: int = Field(0, ge=0, le=59)


def _mask(value: str | None, visible: int = 4) -> str:
    if not value:
        return ''
    return '•' * max(0, len(value) - visible) + value[-visible:]


def _telegram_out(tenant, ctx: Ctx) -> dict:
    return {**link_status(ctx),
            'legacy_configured': bool(tenant.telegram_token and tenant.telegram_chat_id),
            'enabled': bool(tenant.telegram_enabled), 'hour': tenant.telegram_hour or 0,
            'minute': tenant.telegram_minute or 0,
            'token_masked': _mask(decrypt_field(tenant.telegram_token)),
            'chat_id_masked': _mask(decrypt_field(tenant.telegram_chat_id)),
            'configured': bool(reminder_targets(tenant, ctx.uids))}


def _tenant(ctx: Ctx):
    if ctx.tenant is None:
        raise HTTPException(status_code=400, detail='Usuário sem família associada.')
    return ctx.tenant


@router.get('/settings/telegram', summary='Configuração do lembrete diário no Telegram')
def get_telegram(ctx: Ctx = Depends(get_ctx)):
    return _telegram_out(_tenant(ctx), ctx)


@router.put('/settings/telegram', summary='Salva a configuração do Telegram')
def save_telegram(body: TelegramIn, ctx: Ctx = Depends(get_ctx_session)):
    tenant = _tenant(ctx)
    tenant.telegram_enabled = body.enabled
    if body.token and body.token.strip():
        tenant.telegram_token = encrypt_field(body.token.strip())
    if body.chat_id and body.chat_id.strip():
        tenant.telegram_chat_id = encrypt_field(body.chat_id.strip())
    tenant.telegram_hour, tenant.telegram_minute = body.hour, body.minute
    db.session.commit()
    return {'message': 'Configurações do Telegram salvas!', 'telegram': _telegram_out(tenant, ctx)}


@router.post('/settings/telegram/test', summary='Envia o lembrete de teste agora')
def test_telegram(ctx: Ctx = Depends(get_ctx)):
    tenant = _tenant(ctx)
    targets = reminder_targets(tenant, ctx.uids)
    if not targets:
        raise HTTPException(status_code=422, detail='Conecte o Telegram antes de testar.')
    msg = build_daily_reminder(ctx.members) or '✅ <b>FinFam</b>\n\nNenhuma despesa pendente ou em atraso hoje! 🎉'
    errors = [err for token, chat in targets for ok, err in [send_message(token, chat, msg)] if not ok]
    if len(errors) == len(targets):
        raise HTTPException(status_code=502, detail=f'Falha ao enviar: {errors[0]}')
    return {'message': 'Mensagem de teste enviada com sucesso!'}


@router.get('/meta', summary='Listas de referência (categorias, bancos, formas de pagamento etc.)')
def meta():
    return {
        'categories': fin.CATEGORIES, 'payment_methods': fin.PAYMENT_METHODS, 'banks': fin.BANKS,
        'bank_payment_methods': sorted(fin.BANK_PAYMENTS), 'months': fin.MONTH_NAMES_FULL,
        'months_short': fin.MONTH_NAMES_SHORT, 'investment_types': fin.INVESTMENT_TYPES,
        'crypto_coins': [{'id': c, 'label': label} for c, label in fin.CRYPTO_COINS],
        'currencies': [{'code': c, 'name': n, 'symbol': sym} for c, n, sym in fin.CURRENCIES],
        'variable_income_sources': fin.VARIABLE_INCOME_SOURCES,
        'goal_types': [{'id': 'saving', 'label': 'Economizar', 'emoji': '💰'},
                       {'id': 'spending_limit', 'label': 'Limite de gastos', 'emoji': '📉'},
                       {'id': 'debt_reduction', 'label': 'Quitar dívida', 'emoji': '💳'}],
        'category_colors': fin.CATEGORY_COLORS, 'payment_colors': fin.PAYMENT_COLORS,
    }


@router.get('/plans', summary='Planos disponíveis e plano atual')
def plans(ctx: Ctx = Depends(get_ctx_allow_expired)):
    return {'current': tenant_out(ctx.tenant), 'plans': PLANS, 'support_email': SUPPORT_EMAIL}
