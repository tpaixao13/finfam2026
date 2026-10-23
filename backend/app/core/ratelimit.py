"""Limite simples de tentativas (em memória, por processo) para login e cadastro."""
import os
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

_lock = threading.Lock()
_hits: dict[str, deque] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else 'desconhecido'


def check(request: Request, scope: str, key: str = '', limit: int = 10, window: int = 900) -> None:
    """Permite `limit` tentativas por `window` segundos para (scope, IP, key)."""
    if os.environ.get('RATE_LIMIT_ENABLED', '1') == '0':
        return
    bucket = f'{scope}|{_client_ip(request)}|{key.lower()}'
    now = time.monotonic()
    with _lock:
        hits = _hits[bucket]
        while hits and now - hits[0] > window:
            hits.popleft()
        if len(hits) >= limit:
            wait = int(window - (now - hits[0])) // 60 + 1
            raise HTTPException(status_code=429, detail=f'Muitas tentativas. Tente de novo em {wait} min.')
        hits.append(now)
        if len(_hits) > 10000:  # evita crescer sem limite
            for k in [k for k, v in _hits.items() if not v or now - v[-1] > window]:
                del _hits[k]


def reset(request: Request, scope: str, key: str = '') -> None:
    with _lock:
        _hits.pop(f'{scope}|{_client_ip(request)}|{key.lower()}', None)
