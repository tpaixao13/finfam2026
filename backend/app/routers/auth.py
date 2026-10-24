"""Login, cadastro de família, perfil, avatar e tokens de API."""
import secrets
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator

from app.core import ratelimit
from app.core.config import settings
from app.core.types import Email, utcnow
from app.core.db import db, get_or_404
from app.core.deps import (Ctx, Period, get_ctx, get_ctx_allow_expired, get_ctx_session,
                           get_ctx_session_allow_expired, get_period)
from app.core.security import create_access_token, new_api_token, session_fingerprint, verify_password
from app.models import ApiToken, Tenant, User
from app.services.alert_service import quick_alert_count
from app.services.serializers import iso, tenant_out, user_out

router = APIRouter(tags=['auth'])

AVATAR_EXT = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
MAX_AVATAR_BYTES = 2 * 1024 * 1024


class LoginIn(BaseModel):
    email: Email
    password: str = Field(min_length=1)


class RegisterIn(BaseModel):
    name: str = Field(min_length=2, max_length=30)
    email: Email
    password: str = Field(min_length=8)
    confirm_password: str

    @model_validator(mode='after')
    def _passwords_match(self):
        if self.password != self.confirm_password:
            raise ValueError('As senhas não coincidem.')
        return self


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)
    confirm_password: str

    @model_validator(mode='after')
    def _passwords_match(self):
        if self.new_password != self.confirm_password:
            raise ValueError('As senhas não coincidem.')
        return self


class TokenIn(BaseModel):
    name: str = Field(min_length=2, max_length=60, description='Ex.: "Bot do Telegram"')


def _session_payload(user: User, tenant: Tenant | None) -> dict:
    token, expires = create_access_token(user.id, fingerprint=session_fingerprint(user.password_hash))
    return {'access_token': token, 'token_type': 'bearer', 'expires_at': expires.isoformat(),
            'user': user_out(user), 'tenant': tenant_out(tenant)}


@router.post('/auth/login', summary='Login (retorna JWT)')
def login(body: LoginIn, request: Request):
    ratelimit.check(request, 'login', body.email)
    user = User.query.filter_by(email=body.email.strip().lower()).first()
    if not (user.check_password(body.password) if user else verify_password(None, body.password)):
        raise HTTPException(status_code=401, detail='E-mail ou senha incorretos.')
    ratelimit.reset(request, 'login', body.email)
    if user.is_admin:
        raise HTTPException(status_code=403, detail='Esta conta é de administrador. Acesse em /admin.')
    tenant = db.session.get(Tenant, user.tenant_id) if user.tenant_id else None
    return _session_payload(user, tenant)


@router.post('/auth/register', status_code=201, summary='Cria uma família (trial) e o primeiro usuário')
def register(body: RegisterIn, request: Request):
    ratelimit.check(request, 'register', limit=5, window=3600)
    email = body.email.strip().lower()
    if User.query.filter_by(email=email).first():
        raise HTTPException(status_code=409, detail='E-mail já cadastrado. Faça login ou use outro e-mail.')
    first_name = body.name.strip().split()[0]
    tenant = Tenant(name=f'Família {first_name}', code=secrets.token_hex(8),
                    trial_expires_at=utcnow() + timedelta(days=settings.trial_days))
    db.session.add(tenant)
    db.session.flush()
    user = User(name=body.name.strip(), email=email, tenant_id=tenant.id)
    user.set_password(body.password)
    db.session.add(user)
    db.session.commit()
    return _session_payload(user, tenant)


@router.get('/auth/me', summary='Usuário logado, família, plano e contador de alertas')
def me(ctx: Ctx = Depends(get_ctx_allow_expired), period: Period = Depends(get_period)):
    active = ctx.tenant is None or ctx.tenant.trial_active
    return {
        'user': user_out(ctx.user),
        'tenant': tenant_out(ctx.tenant),
        'members': [user_out(u) for u in ctx.members],
        'alert_count': quick_alert_count(ctx.uids, period.month, period.year) if active else 0,
        'via_api_token': ctx.via_api_token,
    }


@router.post('/auth/change-password', summary='Troca a senha do usuário logado')
def change_password(body: ChangePasswordIn, ctx: Ctx = Depends(get_ctx_session_allow_expired)):
    if not ctx.user.check_password(body.current_password):
        raise HTTPException(status_code=422, detail='Senha atual incorreta.')
    revoked = ctx.user.change_password(body.new_password)
    db.session.commit()
    # as sessões antigas deixam de valer; devolve uma nova para quem trocou a senha
    return {'message': 'Senha alterada com sucesso!' + (' Telegram e tokens de API foram desconectados: conecte de novo.' if revoked else ''), **_session_payload(ctx.user, ctx.tenant)}


@router.post('/auth/avatar', summary='Envia foto de perfil (jpg, png, gif, webp — até 2 MB)')
async def upload_avatar(file: UploadFile = File(...), ctx: Ctx = Depends(get_ctx_session_allow_expired)):
    ext = Path(file.filename or '').suffix.lower()
    if ext not in AVATAR_EXT:
        raise HTTPException(status_code=422, detail='Formato de imagem não suportado.')
    content = await file.read()
    if len(content) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=413, detail='Imagem maior que 2 MB.')
    folder = settings.upload_dir / 'avatars'
    folder.mkdir(parents=True, exist_ok=True)
    if ctx.user.avatar:
        (folder / Path(ctx.user.avatar).name).unlink(missing_ok=True)
    filename = f'{secrets.token_hex(12)}{ext}'  # nome imprevisível (não dá para enumerar)
    (folder / filename).write_bytes(content)
    ctx.user.avatar = filename
    db.session.commit()
    return {'message': 'Foto atualizada com sucesso!', 'user': user_out(ctx.user)}


@router.get('/avatars/{filename}', include_in_schema=False)
def get_avatar(filename: str):
    path = (settings.upload_dir / 'avatars' / Path(filename).name)
    if not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path)


# ── Tokens de API (para MCP / bot) ───────────────────────────────────────────

def _token_out(t: ApiToken) -> dict:
    return {'id': t.id, 'name': t.name, 'prefix': t.prefix, 'created_at': iso(t.created_at),
            'last_used_at': iso(t.last_used_at), 'revoked': bool(t.revoked)}


@router.get('/auth/tokens', summary='Lista os tokens de API do usuário')
def list_tokens(ctx: Ctx = Depends(get_ctx_session)):
    tokens = ApiToken.query.filter_by(user_id=ctx.user.id).order_by(ApiToken.created_at.desc()).all()
    return [_token_out(t) for t in tokens]


@router.post('/auth/tokens', status_code=201,
             summary='Cria um token de API (o valor só é exibido nesta resposta)')
def create_token(body: TokenIn, ctx: Ctx = Depends(get_ctx_session)):
    if ctx.via_api_token:
        raise HTTPException(status_code=403, detail='Tokens de API não podem criar outros tokens.')
    raw, digest, prefix = new_api_token()
    token = ApiToken(user_id=ctx.user.id, name=body.name.strip(), token_hash=digest, prefix=prefix)
    db.session.add(token)
    db.session.commit()
    return {**_token_out(token), 'token': raw}


@router.delete('/auth/tokens/{token_id}', summary='Revoga um token de API')
def revoke_token(token_id: int, ctx: Ctx = Depends(get_ctx_session)):
    token = get_or_404(ApiToken.query.filter_by(id=token_id, user_id=ctx.user.id))
    token.revoked = True
    db.session.commit()
    return {'message': 'Token revogado.'}
