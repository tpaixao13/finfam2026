"""Bot central do Telegram: vincular a conversa de cada pessoa à conta dela.

Fluxo: o site pede um código (POST /telegram/link-code) → a pessoa abre
t.me/<bot>?start=<código> → o serviço do bot chama POST /telegram/bot/link.
A partir daí o bot age pela pessoa com `Authorization: Bearer ffbot_<BOT_API_KEY>`
e `X-Telegram-Chat: <chat_id>` — com o mesmo isolamento por família do site.
"""
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.db import db
from app.core.deps import Ctx, get_bot, get_ctx, get_ctx_session
from app.core.security import hash_api_token
from app.core.types import utcnow
from app.models import TelegramLink, TelegramLinkCode, User

router = APIRouter(prefix='/telegram', tags=['telegram'])

CODE_MINUTES = 15


def bot_configured() -> bool:
    return bool(settings.telegram_bot_token and settings.telegram_bot_username)


def link_status(ctx: Ctx) -> dict:
    links = TelegramLink.query.filter(TelegramLink.user_id.in_(ctx.uids)).all()
    mine = [lk for lk in links if lk.user_id == ctx.user.id]
    linked_ids = {lk.user_id for lk in links}
    return {
        'bot_configured': bot_configured(),
        'bot_username': settings.telegram_bot_username or None,
        'linked': bool(mine),
        'telegram_username': next((lk.username for lk in mine if lk.username), None),
        'members': [{'id': u.id, 'name': u.name, 'linked': u.id in linked_ids} for u in ctx.members],
    }


@router.get('/status', summary='Situação do vínculo com o bot central')
def status(ctx: Ctx = Depends(get_ctx)):
    return link_status(ctx)


@router.post('/link-code', summary='Gera o link para conectar o Telegram (vale 15 minutos)')
def link_code(ctx: Ctx = Depends(get_ctx_session)):
    if not bot_configured():
        raise HTTPException(status_code=503, detail='O bot do Telegram ainda não foi configurado no servidor.')
    now = utcnow()
    TelegramLinkCode.query.filter((TelegramLinkCode.user_id == ctx.user.id)
                                  | (TelegramLinkCode.expires_at < now)).delete(synchronize_session=False)
    code = secrets.token_urlsafe(18)  # só [A-Za-z0-9_-], aceito no ?start= do Telegram
    expires = now + timedelta(minutes=CODE_MINUTES)
    db.session.add(TelegramLinkCode(user_id=ctx.user.id, code_hash=hash_api_token(code), expires_at=expires))
    db.session.commit()
    return {'code': code, 'url': f'https://t.me/{settings.telegram_bot_username}?start={code}',
            'bot_username': settings.telegram_bot_username, 'expires_at': expires.isoformat() + 'Z'}


@router.delete('/link', summary='Desconecta o Telegram (pelo site: todas as conversas; pelo bot: a atual)')
def unlink(ctx: Ctx = Depends(get_ctx), chat_id: str | None = Header(None, alias='X-Telegram-Chat',
                                                                    include_in_schema=False)):
    q = TelegramLink.query.filter_by(user_id=ctx.user.id)
    if ctx.via_api_token and chat_id:
        q = q.filter_by(chat_id=str(chat_id))
    removed = q.delete(synchronize_session=False)
    db.session.commit()
    return {'message': 'Telegram desconectado.' if removed else 'Nenhum Telegram conectado.', 'removed': removed}


# ── Rotas do serviço do bot ──────────────────────────────────────────────────

class BotLinkIn(BaseModel):
    code: str = Field(min_length=8, max_length=64)
    chat_id: str = Field(min_length=1, max_length=32, pattern=r'^\d+$', description='Só conversa privada (id positivo)')
    username: str | None = Field(None, max_length=64)


@router.post('/bot/link', summary='(bot) Vincula a conversa pelo código do link', include_in_schema=False)
def bot_link(body: BotLinkIn, _: None = Depends(get_bot)):
    row = TelegramLinkCode.query.filter_by(code_hash=hash_api_token(body.code.strip())).first()
    if row is None or row.expires_at < utcnow():
        if row is not None:
            db.session.delete(row)
            db.session.commit()
        raise HTTPException(status_code=422, detail='Link inválido ou expirado. Gere um novo no site do FinFam.')
    user = db.session.get(User, row.user_id)
    db.session.delete(row)  # uso único
    if user is None or user.is_admin:
        db.session.commit()
        raise HTTPException(status_code=422, detail='Conta não encontrada.')
    link = TelegramLink.query.filter_by(chat_id=body.chat_id).first()
    if link is None:
        link = TelegramLink(chat_id=body.chat_id)
        db.session.add(link)
    link.user_id, link.username, link.created_at = user.id, body.username, utcnow()
    db.session.commit()
    family = user.tenant.name if user.tenant else None
    return {'message': f'Telegram conectado à conta de {user.name}!', 'user_name': user.name, 'family': family}
