"""Dependências de autenticação: quem está chamando e de qual família (tenant)."""
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import Depends, Header, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import hmac

from app.core.config import settings
from app.core.db import db
from app.core.security import API_TOKEN_PREFIX, decode_access_token, hash_api_token, session_fingerprint
from app.core.types import utcnow
from app.models import ApiToken, TelegramLink, Tenant, User

bearer = HTTPBearer(auto_error=False, description='JWT do login ou token de API (ffk_...)')

_LAST_SEEN_EVERY = timedelta(minutes=1)


@dataclass
class Ctx:
    """Contexto do request: usuário logado, sua família e os membros dela."""
    user: User
    tenant: Tenant | None
    members: list[User]
    via_api_token: bool = False

    @property
    def uids(self) -> list[int]:
        return [u.id for u in self.members]

    @property
    def tenant_id(self) -> int | None:
        return self.tenant.id if self.tenant else None

    def member(self, user_id: int | None) -> User:
        """Valida que `user_id` pertence à família; None → o próprio usuário."""
        if user_id is None:
            return self.user
        for u in self.members:
            if u.id == user_id:
                return u
        raise HTTPException(status_code=422, detail='Pessoa inválida para esta família.')


def _unauthorized(detail='Não autenticado.'):
    return HTTPException(status_code=401, detail=detail, headers={'WWW-Authenticate': 'Bearer'})


BOT_KEY_PREFIX = 'ffbot_'
_TelegramChat = Header(None, alias='X-Telegram-Chat', include_in_schema=False)


_LOCAL_HOSTS = {'127.0.0.1', '::1', 'localhost', 'testclient'}
_FORWARD_HEADERS = ('x-forwarded-for', 'cf-connecting-ip', 'forwarded', 'x-real-ip')


def _from_this_server(request: Request | None) -> bool:
    """O bot fala com a API por 127.0.0.1. Pedido vindo da internet (cloudflared/proxy
    acrescenta cabeçalhos de encaminhamento) nunca vale como bot, mesmo com a chave."""
    if request is None:
        return False
    host = request.client.host if request.client else ''
    return host in _LOCAL_HOSTS and not any(h in request.headers for h in _FORWARD_HEADERS)


def bot_key_valid(raw: str | None, request: Request | None = None) -> bool:
    """`Bearer ffbot_<BOT_API_KEY>`: o serviço do bot central (mesmo servidor)."""
    key = settings.bot_api_key
    if not raw or not raw.startswith(BOT_KEY_PREFIX) or len(key) < 32 or len(set(key)) < 10:
        return False
    if not _from_this_server(request):
        return False
    return hmac.compare_digest(raw[len(BOT_KEY_PREFIX):].encode(), key.encode())


def _resolve(creds: HTTPAuthorizationCredentials | None, chat_id: str | None = None,
             request: Request | None = None) -> tuple[User, str]:
    """Retorna (usuário, tipo) a partir do header Authorization."""
    if creds is None or not creds.credentials:
        raise _unauthorized()
    raw = creds.credentials
    if raw.startswith(BOT_KEY_PREFIX):
        # bot central agindo por uma pessoa: só com a conversa que ELA vinculou
        if not bot_key_valid(raw, request):
            raise _unauthorized()
        link = TelegramLink.query.filter_by(chat_id=str(chat_id or '')).first() if chat_id else None
        if link is None or link.user is None:
            raise HTTPException(status_code=401, detail={
                'code': 'telegram_not_linked', 'message': 'Este Telegram não está vinculado a uma conta do FinFam.'})
        return link.user, 'api'
    if raw.startswith(API_TOKEN_PREFIX):
        token = ApiToken.query.filter_by(token_hash=hash_api_token(raw), revoked=False).first()
        if token is None or token.user is None:
            raise _unauthorized('Token de API inválido ou revogado.')
        now = utcnow()
        if not token.last_used_at or now - token.last_used_at > _LAST_SEEN_EVERY:
            token.last_used_at = now
            db.session.commit()
        return token.user, 'api'
    payload = decode_access_token(raw)
    if not payload:
        raise _unauthorized('Sessão expirada. Faça login novamente.')
    user = db.session.get(User, int(payload.get('sub', 0)))
    if user is None or not hmac.compare_digest(str(payload.get('fp', '')),
                                               session_fingerprint(user.password_hash)):
        raise _unauthorized('Sessão expirada. Faça login novamente.')
    kind = payload.get('typ', 'user')
    if kind not in ('user', 'admin'):  # ex.: o desafio do 2FA ('2fa') não é uma sessão
        raise _unauthorized('Sessão expirada. Faça login novamente.')
    return user, kind


def _touch(user: User) -> None:
    now = datetime.now()
    if not user.last_seen or now - user.last_seen > _LAST_SEEN_EVERY:
        user.last_seen = now
        db.session.commit()


def _build_ctx(creds, allow_expired: bool, chat_id: str | None = None, request: Request | None = None) -> Ctx:
    user, kind = _resolve(creds, chat_id, request)
    if user.is_admin or kind == 'admin':
        raise HTTPException(status_code=403, detail='Conta de administrador: use o painel /admin.')
    tenant = db.session.get(Tenant, user.tenant_id) if user.tenant_id else None
    if not allow_expired and tenant is not None and not tenant.trial_active:
        raise HTTPException(status_code=402, detail={
            'code': 'plan_expired',
            'message': 'Seu período de acesso expirou. Faça upgrade para continuar.',
        })
    _touch(user)
    if tenant is not None:
        members = User.query.filter_by(tenant_id=tenant.id).order_by(User.name).all()
    else:
        members = [user]
    return Ctx(user=user, tenant=tenant, members=members, via_api_token=(kind == 'api'))


def get_ctx(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(bearer),
            chat_id: str | None = _TelegramChat) -> Ctx:
    return _build_ctx(creds, allow_expired=False, chat_id=chat_id, request=request)


def get_ctx_allow_expired(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(bearer),
                          chat_id: str | None = _TelegramChat) -> Ctx:
    return _build_ctx(creds, allow_expired=True, chat_id=chat_id, request=request)


def _interactive(ctx: Ctx) -> Ctx:
    if ctx.via_api_token:
        raise HTTPException(status_code=403, detail='Ação não permitida com token de API.')
    return ctx


def get_ctx_session(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(bearer),
                    chat_id: str | None = _TelegramChat) -> Ctx:
    """Ações de conta e credenciais: só com o login do site, nunca com token de API ou bot."""
    return _interactive(_build_ctx(creds, allow_expired=False, chat_id=chat_id, request=request))


def get_ctx_session_allow_expired(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(bearer),
                                  chat_id: str | None = _TelegramChat) -> Ctx:
    return _interactive(_build_ctx(creds, allow_expired=True, chat_id=chat_id, request=request))


def is_owner(ctx: Ctx) -> bool:
    """Dono da família = primeiro membro cadastrado (menor id)."""
    return bool(ctx.members) and ctx.user.id == min(u.id for u in ctx.members)


def get_bot(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
    """Rotas exclusivas do serviço do bot (vincular conversa)."""
    if creds is None or not bot_key_valid(creds.credentials, request):
        raise HTTPException(status_code=403, detail='Acesso restrito ao bot do FinFam.')


def get_admin(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> User:
    user, kind = _resolve(creds)
    if kind != 'admin' or not user.is_admin:
        raise HTTPException(status_code=403, detail='Acesso restrito ao administrador.')
    return user


@dataclass
class Period:
    month: int
    year: int


def get_period(month: int | None = Query(None, ge=1, le=12, description='Mês (1-12); padrão: atual'),
               year: int | None = Query(None, ge=2000, le=2100, description='Ano; padrão: atual')) -> Period:
    now = datetime.now()
    return Period(month=month or now.month, year=year or now.year)
