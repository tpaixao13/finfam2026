"""Links por e-mail da conta: confirmar e-mail (48 h) e criar nova senha (1 h, uso único).

Os links são JWT assinados com a SECRET_KEY, sem tabela nova:
- confirmar: amarrado ao e-mail atual (trocou o e-mail → o link antigo não vale);
- nova senha: amarrado à senha atual (usou o link, a senha muda → o link não vale de novo).
"""
import hashlib
import hmac

from fastapi import Request

from app.core.config import settings
from app.core.security import create_access_token, decode_access_token, session_fingerprint
from app.core.types import utcnow
from app.core.db import db
from app.models import Tenant, User
from app.services import mailer

VERIFY_MINUTES = 48 * 60
RESET_MINUTES = 60


def _email_fp(email: str | None) -> str:
    return hashlib.sha256(f'verify|{(email or "").lower()}|{settings.secret_key}'.encode()).hexdigest()[:24]


def base_url(request: Request | None) -> str:
    """Endereço público do site (PUBLIC_URL); sem ele, o endereço por onde o pedido chegou."""
    if settings.public_url:
        return settings.public_url
    return str(request.base_url).rstrip('/') if request is not None else ''


def needs_verification(user: User) -> bool:
    """Bloqueia o login só com e-mail ligado (sem e-mail não há como confirmar)."""
    return mailer.enabled() and not user.is_admin and user.email_verified_at is None


def start(user: User, request: Request | None, invited_by: str | None = None) -> bool:
    """Conta nova (ou e-mail trocado): sem e-mail configurado já fica confirmada; com e-mail,
    manda as boas-vindas com o link. Devolve True se o link foi enviado (precisa confirmar)."""
    if not mailer.enabled():
        user.email_verified_at = user.email_verified_at or utcnow()
        return False
    user.email_verified_at = None
    send_verification(user, request, invited_by)
    return True


def send_verification(user: User, request: Request | None, invited_by: str | None = None) -> None:
    token, _ = create_access_token(user.id, kind='verify', fingerprint=_email_fp(user.email), minutes=VERIFY_MINUTES)
    tenant = db.session.get(Tenant, user.tenant_id) if user.tenant_id else None
    link = f'{base_url(request)}/confirmar-email?token={token}'
    mailer.send_welcome(user.email, user.name, tenant.name if tenant else None, link, invited_by)


def verify(token: str) -> User | None:
    """Usuário do link de confirmação válido (e marca confirmado), ou None."""
    payload = decode_access_token(token or '')
    if not payload or payload.get('typ') != 'verify':
        return None
    user = db.session.get(User, int(payload.get('sub', 0) or 0))
    if user is None or not hmac.compare_digest(str(payload.get('fp', '')), _email_fp(user.email)):
        return None
    user.email_verified_at = user.email_verified_at or utcnow()
    return user


def send_reset(user: User, request: Request | None) -> None:
    token, _ = create_access_token(user.id, kind='reset', fingerprint=session_fingerprint(user.password_hash),
                                   minutes=RESET_MINUTES)
    mailer.send_reset(user.email, user.name, f'{base_url(request)}/redefinir-senha?token={token}')


def reset_user(token: str) -> User | None:
    """Usuário do link de nova senha, se ainda vale (não expirou e a senha não mudou depois)."""
    payload = decode_access_token(token or '')
    if not payload or payload.get('typ') != 'reset':
        return None
    user = db.session.get(User, int(payload.get('sub', 0) or 0))
    if user is None or user.is_admin or \
            not hmac.compare_digest(str(payload.get('fp', '')), session_fingerprint(user.password_hash)):
        return None
    return user
