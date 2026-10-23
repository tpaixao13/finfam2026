"""Clientes HTTP (só biblioteca padrão): Bot API do Telegram e API do FinFam."""
import json
import logging
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


class Telegram:
    def __init__(self, token: str):
        self.base = f'https://api.telegram.org/bot{token}/'

    def call(self, method: str, http_timeout: float = 30, **params):
        try:
            r = _request(self.base + method, 'POST', {k: v for k, v in params.items() if v is not None},
                         timeout=http_timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode('utf-8', 'replace')
            if 'message is not modified' not in detail:
                log.warning('Telegram %s falhou: %s %s', method, exc.code, detail[:200])
            return None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            log.warning('Telegram %s sem conexão: %s', method, exc)
            return None
        return r.get('result') if r else None

    def updates(self, offset: int | None, timeout: int = 50) -> list[dict] | None:
        """None = falha de rede/Telegram (quem chama espera antes de tentar de novo)."""
        return self.call('getUpdates', http_timeout=timeout + 10, offset=offset, timeout=timeout,
                         allowed_updates=['message', 'callback_query'])

    def send(self, chat_id, text: str, buttons: list[list[dict]] | None = None):
        return self.call('sendMessage', chat_id=chat_id, text=text, parse_mode='HTML',
                         disable_web_page_preview=True,
                         reply_markup={'inline_keyboard': buttons} if buttons else None)

    def edit(self, chat_id, message_id: int, text: str, buttons: list[list[dict]] | None = None):
        return self.call('editMessageText', chat_id=chat_id, message_id=message_id, text=text, parse_mode='HTML',
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
            return _request(url, method, body, headers, timeout=45)
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
