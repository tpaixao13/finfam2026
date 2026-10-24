"""Painel do administrador: famílias, planos, membros extras, usuários online e 2FA."""
import hmac
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.types import Email, utcnow
from app.core import ratelimit
from app.core.db import db, get_or_404
from app.core.deps import get_admin
from app.core.security import create_access_token, decode_access_token, session_fingerprint
from app.models import Tenant, User
from app.services import two_factor as tfa
from app.services.serializers import iso, tenant_out

router = APIRouter(prefix='/admin', tags=['admin'])

ONLINE_THRESHOLD = timedelta(minutes=5)


class AdminLoginIn(BaseModel):
    email: Email
    password: str


class PlanIn(BaseModel):
    plan: Literal['trial', 'mensal', 'anual', 'vitalicio']


class PasswordIn(BaseModel):
    new_password: str = Field(min_length=8)


def _user_row(u: User, now: datetime) -> dict:
    return {'id': u.id, 'name': u.name, 'email': u.email, 'last_seen': iso(u.last_seen),
            'online': bool(u.last_seen and now - u.last_seen <= ONLINE_THRESHOLD),
            'two_factor_enabled': bool(u.totp_enabled), 'two_factor_locked': bool(u.totp_locked),
            'two_factor_failures': u.totp_failures or 0}


@router.post('/login', summary='Login do administrador')
def login(body: AdminLoginIn, request: Request):
    ratelimit.check(request, 'admin-login', body.email, limit=5)
    user = User.query.filter_by(email=body.email.strip().lower(), is_admin=True).first()
    if not user or not user.check_password(body.password):
        raise HTTPException(status_code=401, detail='Credenciais inválidas.')
    ratelimit.reset(request, 'admin-login', body.email)
    if user.totp_locked:
        raise HTTPException(status_code=423, detail={'code': '2fa_locked', 'message': ADMIN_LOCKED})
    if user.totp_enabled:  # senha certa: falta o código do app (o desafio não é uma sessão)
        challenge, _ = create_access_token(user.id, kind='2fa-admin',
                                           fingerprint=session_fingerprint(user.password_hash), minutes=5)
        return {'two_factor_required': True, 'challenge': challenge}
    return _admin_session(user)


ADMIN_LOCKED = ('Conta de administrador bloqueada por códigos errados na verificação em duas etapas. '
                'No servidor, rode: cd backend && .venv/bin/python -m scripts.reset_2fa <e-mail>')


def _admin_session(user: User) -> dict:
    token, expires = create_access_token(user.id, kind='admin', fingerprint=session_fingerprint(user.password_hash))
    return {'access_token': token, 'token_type': 'bearer', 'expires_at': expires.isoformat(),
            'admin': {'id': user.id, 'name': user.name, 'email': user.email,
                      'two_factor_enabled': bool(user.totp_enabled)}}


class AdminTwoFactorLoginIn(BaseModel):
    challenge: str = Field(min_length=10, max_length=2000)
    code: str = Field(min_length=6, max_length=10)


@router.post('/login/2fa', summary='Segundo passo do login do administrador')
def login_2fa(body: AdminTwoFactorLoginIn, request: Request):
    payload = decode_access_token(body.challenge)
    user = db.session.get(User, int(payload.get('sub', 0))) if payload and payload.get('typ') == '2fa-admin' else None
    if user is None or not user.is_admin or not hmac.compare_digest(
            str(payload.get('fp', '')), session_fingerprint(user.password_hash)):
        raise HTTPException(status_code=401, detail='O login expirou. Digite e-mail e senha de novo.')
    ratelimit.check(request, 'admin-login2fa', str(user.id), limit=10, window=900)
    try:
        tfa.verify_login(user, body.code)
    except tfa.TwoFactorError as exc:
        db.session.commit()
        if exc.locked:
            raise HTTPException(status_code=423, detail={'code': '2fa_locked', 'message': ADMIN_LOCKED})
        raise HTTPException(status_code=401, detail=str(exc))
    db.session.commit()
    return _admin_session(user)


# ── 2FA do próprio administrador ─────────────────────────────────────────────

class CodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=10)


class DisableIn(CodeIn):
    password: str = Field(min_length=1)


def _tfa(fn, *args):
    try:
        result = fn(*args)
    except tfa.TwoFactorError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.session.commit()
    return result


@router.get('/2fa', summary='Situação do 2FA do administrador')
def my_2fa(admin: User = Depends(get_admin)):
    return tfa.status(admin)


@router.post('/2fa/setup', summary='Começa a ativar o 2FA do administrador (QR code / chave)')
def my_2fa_setup(admin: User = Depends(get_admin)):
    return _tfa(tfa.start_setup, admin)


@router.post('/2fa/enable', summary='Confirma a ativação com um código do app')
def my_2fa_enable(body: CodeIn, admin: User = Depends(get_admin)):
    _tfa(tfa.confirm_setup, admin, body.code)
    return {'message': 'Verificação em duas etapas ativada no login do administrador.'}


@router.post('/2fa/disable', summary='Desativa (exige senha e um código do app)')
def my_2fa_disable(body: DisableIn, admin: User = Depends(get_admin)):
    if not admin.check_password(body.password):
        raise HTTPException(status_code=422, detail='Senha incorreta.')
    _tfa(tfa.disable, admin, body.code)
    return {'message': 'Verificação em duas etapas desativada no login do administrador.'}


@router.get('/dashboard', summary='Visão geral das famílias e usuários')
def dashboard(admin: User = Depends(get_admin)):
    now = datetime.now()
    utc = utcnow()
    tenants = (Tenant.query.filter(Tenant.users.any(User.is_admin == False))  # noqa: E712
               .order_by(Tenant.created_at.desc()).all())
    rows = []
    for t in tenants:
        members = t.users.filter(User.is_admin == False).order_by(User.name).all()  # noqa: E712
        rows.append({**tenant_out(t), 'code': t.code, 'created_at': iso(t.created_at),
                     'members': [_user_row(u, now) for u in members]})

    def active(t):
        return t.trial_expires_at and t.trial_expires_at > utc

    counts = {
        'tenants': len(tenants),
        'users': User.query.filter(User.is_admin == False).count(),  # noqa: E712
        'trial_active': sum(1 for t in tenants if t.plan == 'trial' and active(t)),
        'expired': sum(1 for t in tenants if t.trial_expires_at and t.trial_expires_at <= utc),
        'mensal': sum(1 for t in tenants if t.plan == 'mensal' and active(t)),
        'anual': sum(1 for t in tenants if t.plan == 'anual' and active(t)),
        'vitalicio': sum(1 for t in tenants if t.plan == 'vitalicio'),
        'expiring_soon': sum(1 for t in tenants if t.plan != 'vitalicio' and active(t)
                             and (t.trial_expires_at - utc).days <= 7),
    }
    online = User.query.filter(User.is_admin == False,  # noqa: E712
                               User.last_seen >= now - ONLINE_THRESHOLD).all()
    return {'admin': {'id': admin.id, 'name': admin.name}, 'counts': counts, 'tenants': rows,
            'online': [_user_row(u, now) for u in online]}


@router.post('/users/{user_id}/reset-password', summary='Redefine a senha de um usuário')
def reset_password(user_id: int, body: PasswordIn, admin: User = Depends(get_admin)):
    user = get_or_404(User.query.filter_by(id=user_id), 'Usuário não encontrado.')
    if user.is_admin and user.id != admin.id:
        raise HTTPException(status_code=403, detail='Não é possível redefinir a senha de outro administrador.')
    revoked = user.change_password(body.new_password)
    db.session.commit()
    return {'message': f'Senha de {user.name} redefinida com sucesso.' + (' Telegram e tokens de API foram desconectados: conecte de novo.' if revoked else '')}


def _family_user(user_id: int) -> User:
    """Os botões do painel valem só para usuários das famílias. 2FA de administrador
    só se tira com senha + código, ou pelo comando no servidor (scripts.reset_2fa)."""
    user = get_or_404(User.query.filter_by(id=user_id), 'Usuário não encontrado.')
    if user.is_admin:
        raise HTTPException(status_code=403, detail='2FA de administrador: use o comando no servidor (scripts.reset_2fa).')
    return user


@router.post('/users/{user_id}/2fa/unlock', summary='Zera os erros do 2FA e desbloqueia o usuário')
def unlock_2fa(user_id: int, _: User = Depends(get_admin)):
    user = _family_user(user_id)
    tfa.unlock(user)
    db.session.commit()
    return {'message': f'Erros do 2FA de {user.name} zerados e conta desbloqueada.'}


@router.delete('/users/{user_id}/2fa', summary='Remove o 2FA do usuário (ex.: perdeu o celular)')
def remove_2fa(user_id: int, _: User = Depends(get_admin)):
    user = _family_user(user_id)
    tfa.reset(user)
    db.session.commit()
    return {'message': f'Verificação em duas etapas de {user.name} removida. Ele entra só com a senha.'}


@router.post('/tenants/{tenant_id}/plan', summary='Define o plano da família')
def set_plan(tenant_id: int, body: PlanIn, _: User = Depends(get_admin)):
    tenant = get_or_404(Tenant.query.filter_by(id=tenant_id), 'Família não encontrada.')
    tenant.plan = body.plan
    if body.plan == 'vitalicio':
        tenant.trial_expires_at = None
    elif body.plan == 'mensal':
        tenant.trial_expires_at = utcnow() + timedelta(days=30)
    elif body.plan == 'anual':
        tenant.trial_expires_at = utcnow() + timedelta(days=365)
    db.session.commit()
    return {'message': f'Plano de "{tenant.name}" alterado para {tenant.plan_label}.', 'tenant': tenant_out(tenant)}


@router.post('/tenants/{tenant_id}/extra-members', summary='Concede +1 membro extra (Anual/Vitalício)')
def add_extra(tenant_id: int, _: User = Depends(get_admin)):
    tenant = get_or_404(Tenant.query.filter_by(id=tenant_id), 'Família não encontrada.')
    if tenant.plan not in ('anual', 'vitalicio'):
        raise HTTPException(status_code=422, detail='Slot extra só pode ser concedido para planos Anual ou Vitalício.')
    tenant.extra_members = (tenant.extra_members or 0) + 1
    db.session.commit()
    return {'message': f'+1 slot extra para "{tenant.name}" (limite: {tenant.member_limit}).',
            'tenant': tenant_out(tenant)}


@router.delete('/tenants/{tenant_id}/extra-members', summary='Remove 1 membro extra')
def remove_extra(tenant_id: int, _: User = Depends(get_admin)):
    tenant = get_or_404(Tenant.query.filter_by(id=tenant_id), 'Família não encontrada.')
    if (tenant.extra_members or 0) > 0:
        tenant.extra_members -= 1
        db.session.commit()
    return {'message': f'Slot extra removido de "{tenant.name}" (limite: {tenant.member_limit}).',
            'tenant': tenant_out(tenant)}


@router.post('/tenants/{tenant_id}/revoke', summary='Revoga o acesso (expira agora)')
def revoke(tenant_id: int, _: User = Depends(get_admin)):
    tenant = get_or_404(Tenant.query.filter_by(id=tenant_id), 'Família não encontrada.')
    tenant.plan = 'trial'
    tenant.trial_expires_at = utcnow() - timedelta(days=1)
    db.session.commit()
    return {'message': f'Acesso de "{tenant.name}" revogado.', 'tenant': tenant_out(tenant)}


@router.delete('/users/{user_id}', summary='Exclui um usuário (e a família, se ficar vazia)')
def delete_user(user_id: int, _: User = Depends(get_admin)):
    user = get_or_404(User.query.filter_by(id=user_id), 'Usuário não encontrado.')
    if user.is_admin:
        raise HTTPException(status_code=403, detail='Não é possível excluir um administrador.')
    tenant = db.session.get(Tenant, user.tenant_id) if user.tenant_id else None
    name = user.name
    db.session.delete(user)
    db.session.flush()
    if tenant and tenant.users.filter(User.is_admin == False).count() == 0:  # noqa: E712
        db.session.delete(tenant)
    db.session.commit()
    return {'message': f'Usuário "{name}" excluído.'}


@router.delete('/tenants/{tenant_id}', summary='Exclui a família e todos os membros')
def delete_tenant(tenant_id: int, _: User = Depends(get_admin)):
    tenant = get_or_404(Tenant.query.filter_by(id=tenant_id), 'Família não encontrada.')
    name = tenant.name
    db.session.delete(tenant)
    db.session.commit()
    return {'message': f'Família "{name}" e todos os seus membros excluídos.'}
