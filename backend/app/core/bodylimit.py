"""Corpo de pedido grande demais é recusado (413) sem ser lido até o fim.

Confere o Content-Length e também conta os bytes de verdade enquanto chegam: um pedido
`Transfer-Encoding: chunked` (sem Content-Length) não passa do limite lendo pedaço por pedaço.
"""
import json


class _TooLarge(BaseException):
    """BaseException de propósito: o FastAPI troca qualquer Exception na leitura do corpo por um 400."""


class BodyLimitMiddleware:
    def __init__(self, app, max_bytes: int):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        length = dict(scope.get('headers') or []).get(b'content-length', b'')
        if length.isdigit() and int(length) > self.max_bytes:
            return await _reject(send)
        seen, started = 0, False

        async def counted_receive():
            nonlocal seen
            message = await receive()
            if message['type'] == 'http.request':
                seen += len(message.get('body', b''))
                if seen > self.max_bytes:
                    raise _TooLarge()
            return message

        async def tracked_send(message):
            nonlocal started
            if message['type'] == 'http.response.start':
                started = True
            await send(message)

        try:
            await self.app(scope, counted_receive, tracked_send)
        except BaseException as exc:  # pode chegar embrulhada num grupo de exceções (anyio)
            if not _is_too_large(exc):
                raise
            if not started:
                await _reject(send)


def _is_too_large(exc: BaseException) -> bool:
    if isinstance(exc, _TooLarge):
        return True
    return any(_is_too_large(e) for e in getattr(exc, 'exceptions', ()))


async def _reject(send) -> None:
    body = json.dumps({'detail': 'Arquivo grande demais.'}).encode()
    await send({'type': 'http.response.start', 'status': 413,
                'headers': [(b'content-type', b'application/json'), (b'content-length', str(len(body)).encode())]})
    await send({'type': 'http.response.body', 'body': body})
