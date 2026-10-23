"""Cliente HTTP mínimo da API do FinFam (só biblioteca padrão)."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request


class FinFamError(Exception):
    """Erro devolvido pela API (a mensagem já vem em português)."""


class FinFamAPI:
    def __init__(self, base_url: str | None = None, token: str | None = None, timeout: float = 20):
        self.base_url = (base_url or os.environ.get('FINFAM_API_URL', 'http://127.0.0.1:8000/api/v1')).rstrip('/')
        self.token = token or os.environ.get('FINFAM_API_TOKEN', '')
        self.timeout = timeout

    def request(self, method: str, path: str, body: dict | None = None, query: dict | None = None):
        if not self.token:
            raise FinFamError('FINFAM_API_TOKEN não configurado. Gere um token em "Perfil e acesso" no FinFam.')
        url = self.base_url + path
        if query:
            clean = {k: v for k, v in query.items() if v is not None and v != ''}
            if clean:
                url += '?' + urllib.parse.urlencode(clean)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            'Authorization': f'Bearer {self.token}', 'Accept': 'application/json',
            **({'Content-Type': 'application/json'} if data else {}),
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode('utf-8', 'replace')
            try:
                parsed = json.loads(detail).get('detail', detail)
                if isinstance(parsed, dict):
                    parsed = parsed.get('message', str(parsed))
            except ValueError:
                parsed = detail
            raise FinFamError(f'{parsed}') from None
        except urllib.error.URLError as exc:
            raise FinFamError(f'Não consegui falar com a API do FinFam em {self.base_url}: {exc.reason}') from None

    def get(self, path: str, **query):
        return self.request('GET', path, query=query)

    def post(self, path: str, body: dict | None = None):
        return self.request('POST', path, body=body)

    def patch(self, path: str, body: dict):
        return self.request('PATCH', path, body=body)

    def delete(self, path: str):
        return self.request('DELETE', path)
