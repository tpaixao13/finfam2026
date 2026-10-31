"""Limite simples de tentativas (em memória, por processo) para login, cadastro e afins.

- `check(request, scope, key)`: por (escopo, IP, chave) — ex.: login de um e-mail a partir de um IP.
- `check_key(scope, key)`: só pela chave, de qualquer IP — ex.: tentativas contra UMA conta.
IPv6 conta por /64 (trocar de endereço dentro da mesma rede não zera o limite). A memória é limitada:
passando de MAX_BUCKETS, saem os baldes usados há mais tempo (O(1), sem varrer tudo a cada pedido).
"""
import ipaddress
import os
import threading
import time
from collections import OrderedDict, deque

from fastapi import HTTPException, Request

MAX_BUCKETS = 20000
_lock = threading.Lock()
_hits: 'OrderedDict[str, deque]' = OrderedDict()
_LOCAL = {'127.0.0.1', '::1', 'localhost'}


def _network(ip: str) -> str:
    """IPv6 → a rede /64 (um aparelho troca de endereço dentro dela à vontade); IPv4 fica como está."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip[:64]
    if addr.version == 6 and not addr.ipv4_mapped:
        return str(ipaddress.ip_network(f'{addr}/64', strict=False))
    return str(getattr(addr, 'ipv4_mapped', None) or addr)


def _client_ip(request: Request) -> str:
    """IP de quem pediu. Atrás do cloudflared (mesma máquina) todo pedido chega de 127.0.0.1: aí vale o
    CF-Connecting-IP, senão o limite seria um só para o site inteiro. Pedido de fora não escolhe o IP."""
    host = request.client.host if request.client else 'desconhecido'
    if host in _LOCAL:
        return _network((request.headers.get('cf-connecting-ip') or host).strip()[:64])
    return _network(host)


def _disabled() -> bool:
    return os.environ.get('RATE_LIMIT_ENABLED', '1').split('#')[0].strip() == '0'


def _hit(bucket: str, limit: int, window: int) -> None:
    now = time.monotonic()
    with _lock:
        hits = _hits.get(bucket)
        if hits is None:
            hits = _hits[bucket] = deque()
        else:
            _hits.move_to_end(bucket)  # usado agora: vai para o fim da fila de despejo
        while hits and now - hits[0] > window:
            hits.popleft()
        if len(hits) >= limit:
            wait = int(window - (now - hits[0])) // 60 + 1
            raise HTTPException(status_code=429, detail=f'Muitas tentativas. Tente de novo em {wait} min.')
        hits.append(now)
        while len(_hits) > MAX_BUCKETS:
            _hits.popitem(last=False)  # o balde usado há mais tempo


def check(request: Request, scope: str, key: str = '', limit: int = 10, window: int = 900) -> None:
    """Permite `limit` tentativas por `window` segundos para (scope, IP, key)."""
    if not _disabled():
        _hit(f'{scope}|{_client_ip(request)}|{key.lower()}', limit, window)


def check_key(scope: str, key: str, limit: int, window: int) -> None:
    """Permite `limit` tentativas por `window` segundos para a chave, venha de que IP vier."""
    if not _disabled() and key:
        _hit(f'{scope}||{key.lower()}', limit, window)


def reset(request: Request, scope: str, key: str = '') -> None:
    with _lock:
        _hits.pop(f'{scope}|{_client_ip(request)}|{key.lower()}', None)
