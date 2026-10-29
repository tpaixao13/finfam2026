"""Painel do administrador: famílias, planos, membros extras, usuários online e 2FA."""
import hmac
import logging
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.core.types import Email, utcnow
from app.core import ratelimit
from app.core.db import db, get_or_404
from app.core.deps import get_admin
from app.core.security import create_access_token, decode_access_token, session_fingerprint
from app.models import PluggyConfig, Tenant, User
from app.services import account_mail, mailer
from app.services import family_backup as fb
from app.services import open_finance as of
from app.services import two_factor as tfa
from app.services.serializers import iso, tenant_out

log = logging.getLogger('finfam.admin')

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
            'two_factor_failures': u.totp_failures or 0, 'email_verified': u.email_verified_at is not None}


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
        configs = PluggyConfig.query.filter(PluggyConfig.user_id.in_([u.id for u in members])).all()
        rows.append({**tenant_out(t), 'code': t.code, 'created_at': iso(t.created_at),
                     'members': [_user_row(u, now) for u in members],
                     'pluggy': {'configured': len(configs), 'errors': sum(1 for c in configs if c.status != 'ok')}})

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


@router.post('/users/{user_id}/send-reset', summary='Manda à pessoa o e-mail com o link para criar uma nova senha')
def admin_send_reset(user_id: int, request: Request, admin: User = Depends(get_admin)):
    user = get_or_404(User.query.filter_by(id=user_id, is_admin=False), 'Usuário não encontrado.')
    ratelimit.check(request, 'admin-reset', str(user.id), limit=3, window=3600)
    if not mailer.enabled():
        raise HTTPException(status_code=422, detail='O e-mail não está configurado (SMTP e PUBLIC_URL no .env). '
                                                    'Use "senha" para definir uma nova.')
    if not user.email:
        raise HTTPException(status_code=422, detail='Esta pessoa não tem e-mail cadastrado.')
    account_mail.send_reset(user)
    log.info('Admin %s mandou link de nova senha para o usuário %s.', admin.id, user.id)
    return {'message': f'Link de nova senha enviado para {user.email} (vale 1 hora).'}


@router.post('/users/{user_id}/verify-email', summary='Confirma o e-mail do usuário à mão (ex.: e-mail não chegou)')
def admin_verify_email(user_id: int, admin: User = Depends(get_admin)):
    user = get_or_404(User.query.filter_by(id=user_id, is_admin=False), 'Usuário não encontrado.')
    user.email_verified_at = user.email_verified_at or utcnow()
    db.session.commit()
    return {'message': f'E-mail de {user.name} confirmado.'}


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


class OpenFinanceIn(BaseModel):
    enabled: bool
    max_items: int | None = Field(None, ge=1, le=50, description='Bancos por pessoa; vazio = sem limite')


@router.put('/tenants/{tenant_id}/open-finance', summary='Libera/desliga o Open Finance (Pluggy) da família')
def set_open_finance(tenant_id: int, body: OpenFinanceIn, _: User = Depends(get_admin)):
    tenant = get_or_404(Tenant.query.filter_by(id=tenant_id), 'Família não encontrada.')
    tenant.open_finance_enabled, tenant.open_finance_max_items = body.enabled, body.max_items
    db.session.commit()
    state = 'liberado' if body.enabled else 'desligado (nada é apagado; só para de importar)'
    return {'message': f'Open Finance {state} para "{tenant.name}".', 'tenant': tenant_out(tenant)}


# ── Backup, zerar e restaurar a família ──────────────────────────────────────

def _gz(path) -> Response:
    return Response(path.read_bytes(), media_type='application/gzip',
                    headers={'Content-Disposition': f'attachment; filename={path.name}'})


@router.get('/tenants/{tenant_id}/backups', summary='Backups guardados no servidor desta família')
def list_backups(tenant_id: int, _: User = Depends(get_admin)):
    tenant = get_or_404(Tenant.query.filter_by(id=tenant_id), 'Família não encontrada.')
    return {'backups': fb.list_saved(tenant)}


@router.post('/tenants/{tenant_id}/backups', summary='Faz agora o backup completo da família (desde o início)')
def make_backup(tenant_id: int, _: User = Depends(get_admin)):
    tenant = get_or_404(Tenant.query.filter_by(id=tenant_id), 'Família não encontrada.')
    path = fb.save(tenant, 'manual')
    return {'message': f'Backup de "{tenant.name}" feito.', 'name': path.name, 'backups': fb.list_saved(tenant)}


@router.get('/tenants/{tenant_id}/backups/{name}', summary='Baixa um backup (.json.gz)')
def download_backup(tenant_id: int, name: str, _: User = Depends(get_admin)):
    tenant = get_or_404(Tenant.query.filter_by(id=tenant_id), 'Família não encontrada.')
    try:
        fb.load_saved(tenant, name)
    except fb.BackupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _gz(fb.BACKUP_DIR / name)


class WipeIn(BaseModel):
    confirm: str = Field(min_length=1, max_length=200, description='Nome da família, digitado para confirmar')
    months: Literal[1, 3, 6, 12] | None = Field(None, description='Quanto os bancos conectados reimportam (vazio = nada)')


def _confirm(tenant: Tenant, typed: str) -> None:
    if typed.strip().casefold() != (tenant.name or '').strip().casefold():
        raise HTTPException(status_code=422, detail='Confirmação: digite o nome da família exatamente.')


@router.post('/tenants/{tenant_id}/wipe-expenses', summary='Zera as despesas da família (faz backup antes)')
def wipe_expenses(tenant_id: int, body: WipeIn, _: User = Depends(get_admin)):
    tenant = get_or_404(Tenant.query.filter_by(id=tenant_id), 'Família não encontrada.')
    _confirm(tenant, body.confirm)
    backup = fb.save(tenant, 'antes de zerar')
    gone = fb.wipe_expenses(tenant)
    start = of.months_back(datetime.utcnow().date(), body.months) if body.months else None
    banks = fb.reimport_from(tenant, start)
    db.session.commit()
    brought = sum(of.sync_stale(uid) for uid in fb.member_ids(tenant)) if start and banks else 0
    msg = f'{gone} despesa(s) de "{tenant.name}" apagada(s). Backup guardado: {backup.name}.'
    if start and banks:
        msg += f' {brought} lançamento(s) trazido(s) dos bancos desde {start:%d/%m/%Y}.'
    return {'message': msg, 'deleted': gone, 'imported': brought, 'backup': backup.name,
            'backups': fb.list_saved(tenant)}


class RestoreIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    confirm: str = Field(min_length=1, max_length=200)


@router.post('/tenants/{tenant_id}/restore', summary='Devolve as despesas do backup (troca as atuais; faz backup antes)')
def restore(tenant_id: int, body: RestoreIn, _: User = Depends(get_admin)):
    tenant = get_or_404(Tenant.query.filter_by(id=tenant_id), 'Família não encontrada.')
    _confirm(tenant, body.confirm)
    try:
        data = fb.load_saved(tenant, body.name)
    except fb.BackupError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    before = fb.save(tenant, 'antes de restaurar')
    n = fb.restore_expenses(tenant, data)
    fb.resync_later(tenant)  # a próxima leitura reclassifica o que veio do banco
    db.session.commit()
    return {'message': f'{n} despesa(s) de "{tenant.name}" restaurada(s) do backup. '
                       f'O estado de antes ficou guardado em {before.name}.',
            'restored': n, 'backups': fb.list_saved(tenant)}


@router.post('/tenants/{tenant_id}/extra-members', summary='Concede +1 membro extra (Anual/Vitalício)')
def add_extra(tenant_id: int, _: User = Depends(get_admin)):
    tenant = get_or_404(Tenant.query.filter_by(id=tenant_id), 'Família não encontrada.')
    if tenant.plan not in ('anual', 'vitalicio'):
        raise HTTPException(status_code=422, detail='Slot extra só pode ser concedido para planos Anual ou Vitalício.')
    tenant.extra_members = (tenant.extra_members or 0) + 1
    db.session.commit()
    return {'message': f'Membro extra liberado para "{tenant.name}" (agora cabem {tenant.member_limit}).',
            'tenant': tenant_out(tenant)}


@router.delete('/tenants/{tenant_id}/extra-members', summary='Remove 1 membro extra')
def remove_extra(tenant_id: int, _: User = Depends(get_admin)):
    tenant = get_or_404(Tenant.query.filter_by(id=tenant_id), 'Família não encontrada.')
    if (tenant.extra_members or 0) > 0:
        tenant.extra_members -= 1
        db.session.commit()
    return {'message': f'Membro extra retirado de "{tenant.name}" (agora cabem {tenant.member_limit}).',
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
    from app.routers.pluggy import release_key
    release_key(user.id)  # quem usa a chave Pluggy dela continua usando
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
