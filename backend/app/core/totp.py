"""TOTP (RFC 6238) — o código de 6 dígitos do Google Authenticator, Microsoft
Authenticator, Authy, 1Password etc. Só biblioteca padrão."""
import base64
import hashlib
import hmac
import secrets
import struct
import time
import urllib.parse

DIGITS, PERIOD, WINDOW = 6, 30, 1  # aceita o código anterior/seguinte (relógio do celular adiantado/atrasado)


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')


def _code_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret + '=' * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack('>Q', step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** DIGITS).zfill(DIGITS)


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // PERIOD)


def code_for(secret: str, now: float | None = None) -> str:
    return _code_at(secret, current_step(now))


def match_step(secret: str, code: str, now: float | None = None) -> int | None:
    """Passo de tempo em que `code` é válido (±1 janela), ou None. Comparação em tempo constante."""
    code = ''.join(ch for ch in str(code or '') if ch.isdigit())
    if len(code) != DIGITS:
        return None
    step = current_step(now)
    found = None
    for s in range(step - WINDOW, step + WINDOW + 1):
        if hmac.compare_digest(_code_at(secret, s), code):
            found = s
    return found


def provisioning_uri(secret: str, account: str, issuer: str = 'FinFam') -> str:
    label = urllib.parse.quote(f'{issuer}:{account}')
    query = urllib.parse.urlencode({'secret': secret, 'issuer': issuer, 'digits': DIGITS, 'period': PERIOD})
    return f'otpauth://totp/{label}?{query}'


def qr_svg_data_uri(text: str) -> str | None:
    """QR code em SVG (data URI) para o app ler. None se a biblioteca segno não estiver instalada."""
    try:
        import segno
    except ImportError:
        return None
    return segno.make(text, error='m').svg_data_uri(scale=5, border=2, dark='#13293d')
