"""Dependências de autenticação: quem está chamando e de qual família (tenant)."""
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.types import utcnow
from app.core.db import db
import hmac

from app.core.security import API_TOKEN_PREFIX, decode_access_token, hash_api_token, session_fingerprint
from app.models import ApiToken, Tenant, User

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


def _resolve(creds: HTTPAuthorizationCredentials | None) -> tuple[User, str]:
    """Retorna (usuário, tipo) a partir do header Authorization."""
    if creds is None or not creds.credentials:
        raise _unauthorized()
    raw = creds.credentials
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
    return user, payload.get('typ', 'user')


def _touch(user: User) -> None:
    now = datetime.now()
    if not user.last_seen or now - user.last_seen > _LAST_SEEN_EVERY:
        user.last_seen = now
        db.session.commit()


def _build_ctx(creds, allow_expired: bool) -> Ctx:
    user, kind = _resolve(creds)
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


def get_ctx(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Ctx:
    return _build_ctx(creds, allow_expired=False)


def get_ctx_allow_expired(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Ctx:
    return _build_ctx(creds, allow_expired=True)


def _interactive(ctx: Ctx) -> Ctx:
    if ctx.via_api_token:
        raise HTTPException(status_code=403, detail='Ação não permitida com token de API.')
    return ctx


def get_ctx_session(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Ctx:
    """Ações de conta e credenciais: só com o login do site, nunca com token de API."""
    return _interactive(_build_ctx(creds, allow_expired=False))


def get_ctx_session_allow_expired(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Ctx:
    return _interactive(_build_ctx(creds, allow_expired=True))


def is_owner(ctx: Ctx) -> bool:
    """Dono da família = primeiro membro cadastrado (menor id)."""
    return bool(ctx.members) and ctx.user.id == min(u.id for u in ctx.members)


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
