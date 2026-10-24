"""Verificação em duas etapas (2FA/TOTP): ativar, conferir no login, bloquear e liberar.

Regras:
- o segredo fica criptografado (encrypt_field) e só vale depois de confirmado com um código;
- no login, 3 códigos errados seguidos bloqueiam a conta; só o admin libera;
- um código já usado não vale de novo (proteção contra repetição).
"""
from app.core import totp
from app.core.security import decrypt_field, encrypt_field
from app.models import User

MAX_FAILURES = 3


class TwoFactorError(Exception):
    def __init__(self, message: str, locked: bool = False, remaining: int | None = None):
        super().__init__(message)
        self.locked, self.remaining = locked, remaining


def status(user: User) -> dict:
    return {'enabled': bool(user.totp_enabled), 'locked': bool(user.totp_locked),
            'failures': user.totp_failures or 0}


def start_setup(user: User) -> dict:
    """Novo segredo (ainda não ativo). Devolve o que o app autenticador precisa."""
    if user.totp_enabled:
        raise TwoFactorError('A verificação em duas etapas já está ativa.')
    secret = totp.new_secret()
    user.totp_secret = encrypt_field(secret)
    uri = totp.provisioning_uri(secret, user.email or user.name)
    return {'secret': secret, 'otpauth_uri': uri, 'qr_svg': totp.qr_svg_data_uri(uri)}


def _check(user: User, code: str) -> bool:
    secret = decrypt_field(user.totp_secret) if user.totp_secret else None
    step = totp.match_step(secret, code) if secret else None
    if step is None or (user.totp_last_step is not None and step <= user.totp_last_step):
        return False
    user.totp_last_step = step
    return True


def confirm_setup(user: User, code: str) -> None:
    if user.totp_enabled:
        raise TwoFactorError('A verificação em duas etapas já está ativa.')
    if not user.totp_secret:
        raise TwoFactorError('Comece a ativação de novo.')
    if not _check(user, code):  # erro na ativação não bloqueia: a pessoa está configurando
        raise TwoFactorError('Código incorreto. Confira o código no app e o horário do celular.')
    user.totp_enabled, user.totp_failures, user.totp_locked = True, 0, False


def verify_login(user: User, code: str) -> None:
    """Segundo passo do login. Levanta TwoFactorError (com `locked`) se falhar."""
    if user.totp_locked:
        raise TwoFactorError(_LOCKED, locked=True)
    if _check(user, code):
        user.totp_failures = 0
        return
    user.totp_failures = (user.totp_failures or 0) + 1
    if user.totp_failures >= MAX_FAILURES:
        user.totp_locked = True
        raise TwoFactorError(_LOCKED, locked=True)
    left = MAX_FAILURES - user.totp_failures
    raise TwoFactorError(f'Código incorreto. Mais {left} tentativa(s) antes de bloquear a conta.', remaining=left)


def disable(user: User, code: str) -> None:
    """Desativar pelo próprio usuário exige um código válido (além da senha, conferida na rota)."""
    if not user.totp_enabled:
        raise TwoFactorError('A verificação em duas etapas não está ativa.')
    if not _check(user, code):
        raise TwoFactorError('Código incorreto.')
    reset(user)


def unlock(user: User) -> None:
    """Admin: zera os erros e desbloqueia (o 2FA continua ativo)."""
    user.totp_failures, user.totp_locked = 0, False


def reset(user: User) -> None:
    """Admin (ou o próprio usuário ao desativar): remove o 2FA e desbloqueia."""
    user.totp_secret, user.totp_enabled, user.totp_last_step = None, False, None
    unlock(user)


_LOCKED = ('Conta bloqueada por excesso de códigos errados na verificação em duas etapas. '
           'Peça ao administrador para desbloquear.')
