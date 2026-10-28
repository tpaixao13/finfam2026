"""Login, cadastro de família, perfil, avatar e tokens de API."""
import hmac
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
from app.core.security import (create_access_token, decode_access_token, new_api_token, session_fingerprint,
                               verify_password)
from app.models import ApiToken, Tenant, User
from app.services import account_mail, mailer
from app.services import two_factor as tfa
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
    if account_mail.needs_verification(user):
        raise HTTPException(status_code=403, detail={
            'code': 'email_not_verified',
            'message': 'Confirme seu e-mail para entrar: abra o link que enviamos para ' + user.email + '.'})
    if user.totp_locked:
        raise HTTPException(status_code=423, detail={'code': '2fa_locked', 'message': str(tfa.TwoFactorError(tfa._LOCKED))})
    if user.totp_enabled:
        # senha certa: falta o código do app. O desafio vale 5 min e não é uma sessão.
        challenge, _ = create_access_token(user.id, kind='2fa', fingerprint=session_fingerprint(user.password_hash),
                                           minutes=5)
        return {'two_factor_required': True, 'challenge': challenge}
    tenant = db.session.get(Tenant, user.tenant_id) if user.tenant_id else None
    return _session_payload(user, tenant)


class TwoFactorLoginIn(BaseModel):
    challenge: str = Field(min_length=10, max_length=2000)
    code: str = Field(min_length=6, max_length=10)


@router.post('/auth/login/2fa', summary='Segundo passo do login: código do app autenticador')
def login_2fa(body: TwoFactorLoginIn, request: Request):
    payload = decode_access_token(body.challenge)
    user = db.session.get(User, int(payload.get('sub', 0))) if payload and payload.get('typ') == '2fa' else None
    if user is None or not hmac.compare_digest(str(payload.get('fp', '')), session_fingerprint(user.password_hash)):
        raise HTTPException(status_code=401, detail='O login expirou. Digite e-mail e senha de novo.')
    ratelimit.check(request, 'login2fa', str(user.id), limit=10, window=900)
    try:
        tfa.verify_login(user, body.code)
    except tfa.TwoFactorError as exc:
        db.session.commit()  # guarda a contagem de erros / o bloqueio
        if exc.locked:
            raise HTTPException(status_code=423, detail={'code': '2fa_locked', 'message': str(exc)})
        raise HTTPException(status_code=401, detail=str(exc))
    db.session.commit()
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
    db.session.flush()
    must_confirm = account_mail.start(user, request)
    db.session.commit()
    if must_confirm:  # entra só depois de clicar no link do e-mail de boas-vindas
        return {'verify_required': True, 'email': email,
                'message': f'Conta criada! Enviamos um e-mail para {email}: abra o link para confirmar e entrar.'}
    return _session_payload(user, tenant)


class TokenOnlyIn(BaseModel):
    token: str = Field(min_length=10, max_length=2000)


class EmailOnlyIn(BaseModel):
    email: Email


class ResetIn(BaseModel):
    token: str = Field(min_length=10, max_length=2000)
    password: str = Field(min_length=8)
    confirm_password: str

    @model_validator(mode='after')
    def _passwords_match(self):
        if self.password != self.confirm_password:
            raise ValueError('As senhas não coincidem.')
        return self


@router.post('/auth/verify', summary='Confirma o e-mail pelo link das boas-vindas (e já entra)')
def verify_email(body: TokenOnlyIn, request: Request):
    ratelimit.check(request, 'verify', limit=60, window=900)
    user = account_mail.verify(body.token)
    if user is None:
        raise HTTPException(status_code=400, detail={
            'code': 'link_invalid', 'message': 'Este link de confirmação expirou ou não vale mais. Peça outro.'})
    db.session.commit()
    if user.totp_enabled:  # com 2FA, entra pelo login normal (o link não pula o código do app)
        return {'message': 'E-mail confirmado! Entre com seu e-mail e senha.'}
    tenant = db.session.get(Tenant, user.tenant_id) if user.tenant_id else None
    return {'message': 'E-mail confirmado! Bem-vindo ao FinFam.', **_session_payload(user, tenant)}


_GENERIC_RESEND = 'Se o e-mail estiver cadastrado e ainda não confirmado, enviamos um novo link. Veja também o spam.'


@router.post('/auth/verify/resend', summary='Manda de novo o link de confirmação do e-mail')
def resend_verification(body: EmailOnlyIn, request: Request):
    email = body.email.strip().lower()
    ratelimit.check(request, 'verify-resend', email, limit=3, window=3600)
    user = User.query.filter_by(email=email).first()
    if user is not None and account_mail.needs_verification(user):
        account_mail.send_verification(user, request)
    return {'message': _GENERIC_RESEND}


_GENERIC_FORGOT = ('Se o e-mail estiver cadastrado, enviamos um link para criar uma nova senha. '
                   'Ele vale por 1 hora. Veja também a caixa de spam.')


@router.post('/auth/forgot-password', summary='Esqueci minha senha: manda o link para criar uma nova')
def forgot_password(body: EmailOnlyIn, request: Request):
    if not mailer.enabled():
        raise HTTPException(status_code=503, detail='A recuperação por e-mail não está configurada. '
                                                    'Peça ao administrador para redefinir sua senha.')
    email = body.email.strip().lower()
    ratelimit.check(request, 'forgot', limit=30, window=3600)
    ratelimit.check(request, 'forgot', email, limit=3, window=3600)
    user = User.query.filter_by(email=email).first()
    if user is not None and not user.is_admin and user.password_hash:
        account_mail.send_reset(user, request)
    return {'message': _GENERIC_FORGOT}  # mesma resposta exista ou não a conta


@router.post('/auth/reset-password', summary='Cria a nova senha com o link do e-mail')
def reset_password(body: ResetIn, request: Request):
    ratelimit.check(request, 'reset', limit=30, window=900)
    user = account_mail.reset_user(body.token)
    if user is None:
        raise HTTPException(status_code=400, detail={
            'code': 'link_invalid', 'message': 'Este link expirou ou já foi usado. Peça outro em "Esqueci minha senha".'})
    user.change_password(body.password)  # desliga sessões, tokens de API e Telegram antigos
    user.email_verified_at = user.email_verified_at or utcnow()  # recebeu o e-mail: é dele
    db.session.commit()
    return {'message': 'Senha alterada! Entre com a nova senha.'}


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


# ── Verificação em duas etapas (2FA) ─────────────────────────────────────────

class TwoFactorCodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=10)


class TwoFactorDisableIn(TwoFactorCodeIn):
    password: str = Field(min_length=1)


def _tfa_call(fn, *args):
    try:
        result = fn(*args)
    except tfa.TwoFactorError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.session.commit()
    return result


@router.get('/auth/2fa', summary='Situação da verificação em duas etapas')
def tfa_status(ctx: Ctx = Depends(get_ctx_session_allow_expired)):
    return tfa.status(ctx.user)


@router.post('/auth/2fa/setup', summary='Começa a ativar: devolve o QR code / chave para o app autenticador')
def tfa_setup(ctx: Ctx = Depends(get_ctx_session_allow_expired)):
    return _tfa_call(tfa.start_setup, ctx.user)


@router.post('/auth/2fa/enable', summary='Confirma a ativação com um código do app')
def tfa_enable(body: TwoFactorCodeIn, ctx: Ctx = Depends(get_ctx_session_allow_expired)):
    _tfa_call(tfa.confirm_setup, ctx.user, body.code)
    return {'message': 'Verificação em duas etapas ativada! A partir de agora o login pede o código do app.',
            'user': user_out(ctx.user)}


@router.post('/auth/2fa/disable', summary='Desativa (exige senha e um código do app)')
def tfa_disable(body: TwoFactorDisableIn, ctx: Ctx = Depends(get_ctx_session_allow_expired)):
    if not ctx.user.check_password(body.password):
        raise HTTPException(status_code=422, detail='Senha incorreta.')
    _tfa_call(tfa.disable, ctx.user, body.code)
    return {'message': 'Verificação em duas etapas desativada.', 'user': user_out(ctx.user)}


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
