"""Tipos de validação compartilhados."""
import re
from typing import Annotated

from pydantic import AfterValidator

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _email(value: str) -> str:
    value = value.strip().lower()
    if len(value) > 254 or not _EMAIL_RE.match(value):
        raise ValueError('E-mail inválido.')
    return value


Email = Annotated[str, AfterValidator(_email)]


def utcnow():
    """UTC "naive" (sem fuso), como gravado no banco do FinFam original."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(tzinfo=None)
