"""Senhas, JWT, tokens de API e criptografia de campos sensíveis."""
import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from werkzeug.security import check_password_hash, generate_password_hash

from app.core.config import settings

ALGORITHM = 'HS256'
API_TOKEN_PREFIX = 'ffk_'


# ── Senhas (formato werkzeug, compatível com o banco do FinFam original) ──────

def hash_password(password: str) -> str:
    return generate_password_hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    if not password_hash:
        return False
    return check_password_hash(password_hash, password)


# ── JWT de sessão (frontend) ─────────────────────────────────────────────────

def create_access_token(user_id: int, kind: str = 'user') -> tuple[str, datetime]:
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_minutes)
    payload = {'sub': str(user_id), 'typ': kind, 'exp': expires}
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM), expires


def decode_access_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None


# ── Tokens de API (MCP / bot) ────────────────────────────────────────────────

def new_api_token() -> tuple[str, str, str]:
    """Retorna (token_em_texto, sha256_hex, prefixo_para_exibição)."""
    raw = API_TOKEN_PREFIX + secrets.token_urlsafe(32)
    return raw, hash_api_token(raw), raw[:12]


def hash_api_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# ── Criptografia de campos (mesmo esquema do FinFam original) ────────────────

def _fernet():
    from cryptography.fernet import Fernet
    raw_key = hashlib.sha256(settings.secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(raw_key))


def encrypt_field(value: str | None) -> str | None:
    if not value:
        return value
    return _fernet().encrypt(value.encode()).decode()


def decrypt_field(value: str | None) -> str | None:
    """Decifra; se o valor não estiver cifrado (legado), devolve como está."""
    if not value:
        return value
    try:
        return _fernet().decrypt(value.encode()).decode()
    except Exception:
        return value
