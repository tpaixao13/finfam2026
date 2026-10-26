"""Cliente da API da Pluggy (Open Finance) — só biblioteca padrão.

Cada PESSOA usa a própria conta Pluggy (Client ID + Client Secret). A API Key que a
Pluggy devolve em POST /auth vale 2 horas: fica em memória, por Client ID, e é renovada
sozinha. Nada aqui grava no banco: quem guarda credenciais é o router (criptografadas).
"""
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request

from app.services.market import _ssl_context

log = logging.getLogger('finfam.pluggy')

BASE_URL = 'https://api.pluggy.ai'
TIMEOUT = 20
_KEY_TTL = 110 * 60  # a API Key vale 2 h; renova antes

_lock = threading.Lock()
_keys: dict[str, tuple[float, str]] = {}  # client_id → (quando, api_key)


class PluggyError(Exception):
    """Falha ao falar com a Pluggy (mensagem em português, sem segredos)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _http(method: str, path: str, body: dict | None = None, api_key: str | None = None) -> dict | list | None:
    req = urllib.request.Request(BASE_URL + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Content-Type': 'application/json', 'Accept': 'application/json',
                                          **({'X-API-KEY': api_key} if api_key else {})})
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=_ssl_context()))
    try:
        with opener.open(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read(2048).decode('utf-8', 'replace')
        log.warning('Pluggy %s %s → %s: %s', method, path.split('?')[0], exc.code, re.sub(r'\s+', ' ', detail)[:200])
        if exc.code in (401, 403):
            raise PluggyError('A Pluggy recusou o Client ID / Client Secret. Confira no painel da Pluggy.',
                              exc.code) from None
        if exc.code == 429:
            raise PluggyError('Limite de chamadas da Pluggy atingido. Tente em alguns minutos.', 429) from None
        try:
            message = json.loads(detail).get('message') or ''
        except ValueError:
            message = ''
        raise PluggyError(f'A Pluggy respondeu {exc.code}' + (f': {message[:160]}' if message else '.'),
                          exc.code) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        log.warning('Pluggy %s %s sem conexão: %s', method, path.split('?')[0], getattr(exc, 'reason', exc))
        raise PluggyError('Não consegui falar com a Pluggy agora. Tente de novo em instantes.') from None
    except ValueError:
        raise PluggyError('A Pluggy devolveu uma resposta inválida.') from None


class PluggyClient:
    """Chamadas em nome de UMA conta Pluggy (a de uma pessoa)."""

    def __init__(self, client_id: str, client_secret: str):
        self.client_id, self.client_secret = client_id, client_secret

    def api_key(self, fresh: bool = False) -> str:
        with _lock:
            hit = _keys.get(self.client_id)
            if hit and not fresh and time.time() - hit[0] < _KEY_TTL:
                return hit[1]
        r = _http('POST', '/auth', {'clientId': self.client_id, 'clientSecret': self.client_secret})
        key = (r or {}).get('apiKey')
        if not key:
            raise PluggyError('A Pluggy não devolveu a chave de acesso.')
        with _lock:
            _keys[self.client_id] = (time.time(), key)
        return key

    def call(self, method: str, path: str, body: dict | None = None):
        try:
            return _http(method, path, body, self.api_key())
        except PluggyError as exc:
            if exc.status != 401:
                raise
            return _http(method, path, body, self.api_key(fresh=True))  # chave expirou antes da hora

    # ── webhooks (um por pessoa, apontando para o FinFam) ──
    def create_webhook(self, url: str, secret: str) -> str:
        r = self.call('POST', '/webhooks', {'event': 'all', 'url': url, 'headers': {'X-FinFam-Hook': secret}})
        hook_id = (r or {}).get('id')
        if not hook_id:
            raise PluggyError('A Pluggy não devolveu o id do webhook.')
        return str(hook_id)

    def delete_webhook(self, hook_id: str) -> None:
        if not re.fullmatch(r'[A-Za-z0-9-]{1,64}', hook_id or ''):
            return
        try:
            self.call('DELETE', f'/webhooks/{safe_id(hook_id)}')
        except PluggyError as exc:
            if exc.status != 404:  # já não existe: tudo bem
                raise

    # ── Connect e bancos conectados ("items") ──
    def connect_token(self, client_user_id: str, webhook_url: str | None = None, item_id: str | None = None) -> str:
        """Token de 30 min para o widget Pluggy Connect (seguro para o navegador)."""
        options = {'clientUserId': client_user_id, 'avoidDuplicates': True}
        if webhook_url:
            options['webhookUrl'] = webhook_url
        body = {'options': options, **({'itemId': safe_id(item_id)} if item_id else {})}
        token = (self.call('POST', '/connect_token', body) or {}).get('accessToken')
        if not token:
            raise PluggyError('A Pluggy não devolveu o token de conexão.')
        return token

    def item(self, item_id: str) -> dict:
        return self.call('GET', f'/items/{safe_id(item_id)}') or {}

    def update_item(self, item_id: str) -> dict:
        """Pede à Pluggy para buscar novidades no banco agora (roda em segundo plano lá)."""
        return self.call('PATCH', f'/items/{safe_id(item_id)}', {}) or {}

    def delete_item(self, item_id: str) -> None:
        try:
            self.call('DELETE', f'/items/{safe_id(item_id)}')
        except PluggyError as exc:
            if exc.status != 404:
                raise

    def accounts(self, item_id: str) -> list[dict]:
        r = self.call('GET', f'/accounts?itemId={safe_id(item_id)}') or {}
        return list(r.get('results') or []) if isinstance(r, dict) else list(r)


def safe_id(value: str | None) -> str:
    """Ids da Pluggy (UUID) — recusa qualquer coisa que mude o caminho da URL."""
    if not re.fullmatch(r'[A-Za-z0-9-]{1,64}', value or ''):
        raise PluggyError('Identificador da Pluggy inválido.', 400)
    return value


def forget(client_id: str) -> None:
    """Esquece a API Key em memória (ao trocar ou remover a chave)."""
    with _lock:
        _keys.pop(client_id, None)
