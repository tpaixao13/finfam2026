"""Clientes HTTP (só biblioteca padrão): Bot API do Telegram e API do FinFam."""
import http.client
import json
import logging
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger('finfam.bot')


class ApiError(Exception):
    def __init__(self, status: int, message: str, code: str | None = None):
        super().__init__(message)
        self.status, self.message, self.code = status, message, code


def _request(url: str, method: str = 'GET', body: dict | None = None, headers: dict | None = None,
             timeout: float = 30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        'Accept': 'application/json', **({'Content-Type': 'application/json'} if data else {}), **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None


def fit(text: str, limit: int = 4000) -> str:
    """O Telegram recusa mensagens acima de 4096 caracteres: corta numa quebra de linha
    (fora de qualquer tag HTML, já que cada linha é fechada em si)."""
    if len(text) <= limit:
        return text
    return text[:limit - 100].rsplit('\n', 1)[0] + '\n\n… lista cortada. Veja tudo no site.'


class _KeepAlive:
    """Conexão HTTPS reaproveitada: evita um aperto de mão TLS (0,2–0,5 s no Raspberry) a cada chamada."""

    def __init__(self, host: str):
        self.host, self.conn, self.lock = host, None, threading.Lock()
        self.ctx = ssl.create_default_context()

    def post(self, path: str, body: dict, timeout: float):
        data = json.dumps(body).encode()
        with self.lock:
            for attempt in (1, 2):  # a conexão pode ter sido fechada pelo servidor: reconecta uma vez
                if self.conn is None:
                    self.conn = http.client.HTTPSConnection(self.host, timeout=timeout, context=self.ctx)
                self.conn.timeout = timeout
                if self.conn.sock is not None:
                    self.conn.sock.settimeout(timeout)
                try:
                    self.conn.request('POST', path, data, {'Content-Type': 'application/json'})
                    resp = self.conn.getresponse()
                    return resp.status, resp.read()
                except (http.client.HTTPException, OSError):
                    self.conn.close()
                    self.conn = None
                    if attempt == 2:
                        raise


class Telegram:
    def __init__(self, token: str):
        self.path = f'/bot{token}/'
        self.send_conn = _KeepAlive('api.telegram.org')   # mensagens, botões
        self.poll_conn = _KeepAlive('api.telegram.org')   # getUpdates (fica parado esperando)

    def call(self, method: str, http_timeout: float = 30, **params):
        conn = self.poll_conn if method == 'getUpdates' else self.send_conn
        try:
            status, raw = conn.post(self.path + method, {k: v for k, v in params.items() if v is not None},
                                    http_timeout)
        except (http.client.HTTPException, OSError) as exc:
            log.warning('Telegram %s sem conexão: %s', method, exc)
            return None
        if status != 200:
            detail = raw.decode('utf-8', 'replace')
            if 'message is not modified' not in detail:
                log.warning('Telegram %s falhou: %s %s', method, status, detail[:200])
            return None
        try:
            return json.loads(raw).get('result')
        except ValueError:
            return None

    def updates(self, offset: int | None, timeout: int = 50) -> list[dict] | None:
        """None = falha de rede/Telegram (quem chama espera antes de tentar de novo)."""
        return self.call('getUpdates', http_timeout=timeout + 10, offset=offset, timeout=timeout,
                         allowed_updates=['message', 'callback_query'])

    def send(self, chat_id, text: str, buttons: list[list[dict]] | None = None):
        return self.call('sendMessage', chat_id=chat_id, text=fit(text), parse_mode='HTML',
                         disable_web_page_preview=True,
                         reply_markup={'inline_keyboard': buttons} if buttons else None)

    def edit(self, chat_id, message_id: int, text: str, buttons: list[list[dict]] | None = None):
        return self.call('editMessageText', chat_id=chat_id, message_id=message_id, text=fit(text), parse_mode='HTML',
                         disable_web_page_preview=True,
                         reply_markup={'inline_keyboard': buttons} if buttons else None)

    def answer(self, callback_id: str, text: str | None = None):
        return self.call('answerCallbackQuery', callback_query_id=callback_id, text=text)

    def typing(self, chat_id):
        return self.call('sendChatAction', chat_id=chat_id, action='typing')


class FinFam:
    """API do FinFam agindo pela pessoa dona da conversa (`X-Telegram-Chat`)."""

    def __init__(self, base_url: str, bot_key: str):
        self.base_url, self.key = base_url, bot_key

    def request(self, chat_id, method: str, path: str, body: dict | None = None, **query):
        url = self.base_url + path
        clean = {k: v for k, v in query.items() if v is not None}
        if clean:
            url += '?' + urllib.parse.urlencode(clean)
        headers = {'Authorization': f'Bearer ffbot_{self.key}'}
        if chat_id is not None:
            headers['X-Telegram-Chat'] = str(chat_id)
        try:
            return _request(url, method, body, headers, timeout=75)
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode('utf-8', 'replace')).get('detail')
            except ValueError:
                detail = None
            code = detail.get('code') if isinstance(detail, dict) else None
            message = detail.get('message') if isinstance(detail, dict) else detail
            raise ApiError(exc.code, str(message or f'Erro {exc.code} na API do FinFam.'), code) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ApiError(0, 'O FinFam está fora do ar agora. Tente de novo em instantes.') from None

    def get(self, chat_id, path: str, **query):
        return self.request(chat_id, 'GET', path, **query)

    def post(self, chat_id, path: str, body: dict | None = None):
        return self.request(chat_id, 'POST', path, body or {})

    def delete(self, chat_id, path: str):
        return self.request(chat_id, 'DELETE', path)
