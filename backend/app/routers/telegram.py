"""Bot central do Telegram: vincular a conversa de cada pessoa à conta dela.

Fluxo: o site pede um código (POST /telegram/link-code) → a pessoa abre
t.me/<bot>?start=<código> → o serviço do bot chama POST /telegram/bot/link.
A partir daí o bot age pela pessoa com `Authorization: Bearer ffbot_<BOT_API_KEY>`
e `X-Telegram-Chat: <chat_id>` — com o mesmo isolamento por família do site.
"""
import html
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.db import db
from app.core.deps import Ctx, get_bot, get_ctx, get_ctx_session, is_owner
from app.core.security import hash_api_token
from app.core.types import utcnow
from app.models import TelegramLink, TelegramLinkCode, Tenant, User

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
        'group': {'connected': bool(ctx.tenant and ctx.tenant.telegram_group_chat),
                  'title': ctx.tenant.telegram_group_title if ctx.tenant and ctx.tenant.telegram_group_chat else None,
                  'can_manage': is_owner(ctx)},
    }


@router.get('/status', summary='Situação do vínculo com o bot central')
def status(ctx: Ctx = Depends(get_ctx)):
    return link_status(ctx)


def _new_code(ctx: Ctx, kind: str) -> tuple[str, object]:
    if not bot_configured():
        raise HTTPException(status_code=503, detail='O bot do Telegram ainda não foi configurado no servidor.')
    now = utcnow()
    TelegramLinkCode.query.filter(((TelegramLinkCode.user_id == ctx.user.id) & (TelegramLinkCode.kind == kind))
                                  | (TelegramLinkCode.expires_at < now)).delete(synchronize_session=False)
    code = secrets.token_urlsafe(18)  # só [A-Za-z0-9_-], aceito no ?start= do Telegram
    expires = now + timedelta(minutes=CODE_MINUTES)
    db.session.add(TelegramLinkCode(user_id=ctx.user.id, code_hash=hash_api_token(code), expires_at=expires, kind=kind))
    db.session.commit()
    return code, expires


@router.post('/link-code', summary='Gera o link para conectar o Telegram (vale 15 minutos)')
def link_code(ctx: Ctx = Depends(get_ctx_session)):
    code, expires = _new_code(ctx, 'chat')
    return {'code': code, 'url': f'https://t.me/{settings.telegram_bot_username}?start={code}',
            'bot_username': settings.telegram_bot_username, 'expires_at': expires.isoformat() + 'Z'}


def _owner_only(ctx: Ctx) -> None:
    if not is_owner(ctx):
        raise HTTPException(status_code=403, detail='Só quem criou a família pode conectar ou desconectar o grupo.')


def _tell_members(tenant: Tenant, text: str) -> None:
    """Aviso no privado de cada pessoa da família (ex.: quando o grupo muda) — melhor esforço."""
    from app.services.telegram import linked_chats, send_message
    uids = [u.id for u in User.query.filter_by(tenant_id=tenant.id).all()]
    for chat in linked_chats(uids):
        send_message(settings.telegram_bot_token, chat, text)


@router.post('/group-code', summary='Link para pôr o bot no grupo da família (vale 15 minutos)')
def group_code(ctx: Ctx = Depends(get_ctx_session)):
    """Abre o Telegram para escolher o grupo; o bot entra e conecta o grupo à família."""
    if ctx.tenant is None:
        raise HTTPException(status_code=400, detail='Usuário sem família associada.')
    _owner_only(ctx)
    code, expires = _new_code(ctx, 'group')
    return {'code': code, 'url': f'https://t.me/{settings.telegram_bot_username}?startgroup={code}',
            'bot_username': settings.telegram_bot_username, 'expires_at': expires.isoformat() + 'Z'}


@router.delete('/group', summary='Desconecta o grupo: os avisos da família voltam para cada pessoa')
def unlink_group(ctx: Ctx = Depends(get_ctx_session)):
    if ctx.tenant is None or not ctx.tenant.telegram_group_chat:
        return {'message': 'Nenhum grupo conectado.', 'telegram': link_status(ctx)}
    _owner_only(ctx)
    ctx.tenant.telegram_group_chat = ctx.tenant.telegram_group_title = None
    db.session.commit()
    _tell_members(ctx.tenant, '🔕 O grupo do Telegram foi desconectado: os avisos da família voltam a chegar aqui.')
    return {'message': 'Grupo desconectado. Os avisos da família voltam para a conversa de cada pessoa.',
            'telegram': link_status(ctx)}


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


def _valid_code(code: str, kind: str = 'chat') -> TelegramLinkCode:
    row = TelegramLinkCode.query.filter_by(code_hash=hash_api_token(code.strip())).first()
    if row is not None and (row.kind or 'chat') != kind:  # link de pessoa não conecta grupo, e vice-versa
        raise HTTPException(status_code=422, detail='Link inválido ou expirado. Gere um novo no site do FinFam.')
    if row is None or row.expires_at < utcnow():
        if row is not None:
            db.session.delete(row)
            db.session.commit()
        raise HTTPException(status_code=422, detail='Link inválido ou expirado. Gere um novo no site do FinFam.')
    return row


class BotPeekIn(BaseModel):
    code: str = Field(min_length=8, max_length=64)


@router.post('/bot/peek', summary='(bot) De quem é o link, para confirmar antes de conectar', include_in_schema=False)
def bot_peek(body: BotPeekIn, _: None = Depends(get_bot)):
    user = db.session.get(User, _valid_code(body.code).user_id)
    if user is None or user.is_admin:
        raise HTTPException(status_code=422, detail='Conta não encontrada.')
    return {'user_name': user.name, 'family': user.tenant.name if user.tenant else None}


@router.post('/bot/link', summary='(bot) Vincula a conversa pelo código do link', include_in_schema=False)
def bot_link(body: BotLinkIn, _: None = Depends(get_bot)):
    row = _valid_code(body.code)
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


class BotGroupIn(BaseModel):
    code: str = Field(min_length=8, max_length=64)
    chat_id: str = Field(min_length=2, max_length=32, pattern=r'^-\d+$', description='Grupo (id negativo)')
    title: str | None = Field(None, max_length=128)


@router.post('/bot/link-group', summary='(bot) Conecta o grupo à família pelo código do link', include_in_schema=False)
def bot_link_group(body: BotGroupIn, _: None = Depends(get_bot)):
    row = _valid_code(body.code, 'group')
    user = db.session.get(User, row.user_id)
    db.session.delete(row)  # uso único
    if user is None or user.is_admin or user.tenant is None:
        db.session.commit()
        raise HTTPException(status_code=422, detail='Conta não encontrada.')
    owner = User.query.filter_by(tenant_id=user.tenant_id, is_admin=False).order_by(User.id).first()
    if owner is None or owner.id != user.id:  # o dono pode ter mudado depois do link gerado
        db.session.commit()
        raise HTTPException(status_code=403, detail='Só quem criou a família pode conectar o grupo.')
    other = Tenant.query.filter(Tenant.telegram_group_chat == body.chat_id, Tenant.id != user.tenant_id).first()
    if other is not None:  # um grupo, uma família
        db.session.commit()
        raise HTTPException(status_code=409, detail='Este grupo já está conectado a outra família.')
    user.tenant.telegram_group_chat = body.chat_id
    user.tenant.telegram_group_title = (body.title or '').strip()[:128] or None
    db.session.commit()
    title = html.escape(user.tenant.telegram_group_title or 'sem nome', quote=False)
    _tell_members(user.tenant, f'👥 Os avisos da família agora vão para o grupo <b>{title}</b> '
                               f'(conectado por {html.escape(user.name, quote=False)}), e não mais aqui no privado. '
                               'Não reconhece esse grupo? Desconecte em Telegram e IA no site.')
    return {'message': f'Grupo conectado à família {user.tenant.name}!', 'family': user.tenant.name}
