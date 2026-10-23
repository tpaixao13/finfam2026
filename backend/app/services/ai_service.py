"""IA da família (chave própria): transforma uma frase em lançamento ou comando.

Cada família escolhe o provedor e cola a chave dela; o FinFam só repassa a
pergunta. A resposta da IA é sempre CONFERIDA aqui (categoria, cartão, pessoa,
valores) e nunca grava nada: quem grava é o usuário, ao confirmar.
"""
import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from app.core.security import decrypt_field
from app.services.market import _ssl_context

log = logging.getLogger('finfam.ai')

PROVIDERS = {
    'anthropic': {'label': 'Claude (Anthropic)', 'default_model': 'claude-haiku-5-5',
                  'key_url': 'https://console.anthropic.com/settings/keys'},
    'gemini': {'label': 'Gemini (Google)', 'default_model': 'gemini-flash-latest',
               'key_url': 'https://aistudio.google.com/apikey'},
    'openai': {'label': 'ChatGPT (OpenAI) ou compatível', 'default_model': 'gpt-5.4-mini',
               'key_url': 'https://platform.openai.com/api-keys'},
}
OPENAI_URL = 'https://api.openai.com/v1'
TIMEOUT = 30


class AIError(Exception):
    """Falha ao falar com o provedor (mensagem já em português, sem segredos)."""


@dataclass
class AIClient:
    provider: str
    api_key: str
    model: str
    base_url: str | None = None

    @classmethod
    def from_config(cls, cfg) -> 'AIClient':
        return cls(provider=cfg.provider, api_key=decrypt_field(cfg.api_key) or '',
                   model=cfg.model or PROVIDERS[cfg.provider]['default_model'], base_url=cfg.base_url or None)

    def complete_json(self, system: str, user: str) -> dict:
        text = getattr(self, f'_{self.provider}')(system, user)
        return parse_json(text)

    # ── um método por provedor ──
    def _anthropic(self, system: str, user: str) -> str:
        r = _post('https://api.anthropic.com/v1/messages',
                  {'x-api-key': self.api_key, 'anthropic-version': '2023-06-01'},
                  {'model': self.model, 'max_tokens': 600, 'system': system,
                   'messages': [{'role': 'user', 'content': user}]})
        return ''.join(b.get('text', '') for b in r.get('content', []) if b.get('type') == 'text')

    def _gemini(self, system: str, user: str) -> str:
        r = _post(f'https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent',
                  {'x-goog-api-key': self.api_key},
                  {'systemInstruction': {'parts': [{'text': system}]},
                   'contents': [{'role': 'user', 'parts': [{'text': user}]}],
                   'generationConfig': {'responseMimeType': 'application/json'}})
        parts = (r.get('candidates') or [{}])[0].get('content', {}).get('parts', [])
        return ''.join(p.get('text', '') for p in parts)

    def _openai(self, system: str, user: str) -> str:
        body = {'model': self.model, 'messages': [{'role': 'system', 'content': system},
                                                  {'role': 'user', 'content': user}]}
        if not self.base_url:  # nem todo "compatível" aceita response_format
            body['response_format'] = {'type': 'json_object'}
        r = _post(f'{(self.base_url or OPENAI_URL).rstrip("/")}/chat/completions',
                  {'Authorization': f'Bearer {self.api_key}'}, body)
        return ((r.get('choices') or [{}])[0].get('message') or {}).get('content') or ''


def _post(url: str, headers: dict, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method='POST',
                                 headers={'Content-Type': 'application/json', **headers})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ssl_context()) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise AIError(_http_message(exc.code, exc.read().decode('utf-8', 'replace'))) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AIError(f'Não consegui falar com o provedor de IA ({getattr(exc, "reason", exc)}).') from None
    except ValueError:
        raise AIError('O provedor de IA devolveu uma resposta inválida.') from None


def _http_message(code: int, body: str) -> str:
    if code in (401, 403):
        return 'A chave de IA foi recusada pelo provedor. Confira a chave em Telegram e IA.'
    if code == 404:
        return 'Modelo de IA não encontrado. Confira o nome do modelo em Telegram e IA.'
    if code == 429:
        return 'Limite ou crédito da chave de IA esgotado no provedor. Tente mais tarde.'
    try:  # mensagem do provedor, curta (nunca repete a chave)
        err = json.loads(body).get('error')
        detail = err.get('message') if isinstance(err, dict) else str(err or '')
    except ValueError:
        detail = ''
    return f'O provedor de IA respondeu {code}' + (f': {detail[:160]}' if detail else '.')


def parse_json(text: str) -> dict:
    text = (text or '').strip()
    match = re.search(r'\{.*\}', text, re.S)
    try:
        data = json.loads(match.group(0) if match else text)
    except (ValueError, AttributeError):
        raise AIError('A IA não respondeu no formato esperado. Tente reescrever a mensagem.') from None
    if not isinstance(data, dict):
        raise AIError('A IA não respondeu no formato esperado. Tente reescrever a mensagem.')
    return data


# ── Interpretação de mensagens ───────────────────────────────────────────────

INTENTS = ('expense', 'bills', 'summary', 'chat')

_SYSTEM = """Você é o assistente do FinFam, app de controle financeiro familiar no Brasil.
Interprete a mensagem do usuário e responda SOMENTE com um objeto JSON, sem texto fora dele:
{"intent": "expense" | "bills" | "summary" | "chat",
 "expense": {"description": str, "amount": number, "payment_method": str, "card_id": int|null,
             "installments": int|null, "category": str|null, "date": "AAAA-MM-DD"|null, "person_id": int|null} | null,
 "reply": str}
Regras:
- "expense": a pessoa conta um gasto/compra/conta para lançar. amount é o valor TOTAL em reais (ex.: "87,50" → 87.5).
  Se faltar o valor, use amount 0. description curta, como apareceria no extrato (ex.: "Mercado", "Uber").
- payment_method: um de {methods}. "crédito"/"cartão" → Cartão de Crédito; "débito" → Cartão de Débito. Padrão: PIX.
- card_id: só um id da lista de cartões quando a pessoa citar o cartão (banco, apelido ou final). Senão null.
- installments: nº de parcelas só no crédito ("em 3x" → 3). Senão null.
- category: uma de {categories}, ou null se não tiver certeza.
- date: hoje é {today} ({weekday}); "ontem", "sexta" etc. viram data. Sem data → null.
- person_id: só se a pessoa disser que o gasto é de outro membro. Senão null.
- "bills": quer ver contas a pagar/vencimentos. "summary": quer resumo/saldo/quanto gastou no mês.
- "chat": qualquer outra coisa; em "reply" explique em 1 frase o que você consegue fazer.
- "reply": frase curta em português para o usuário."""

_WEEKDAYS = ['segunda-feira', 'terça-feira', 'quarta-feira', 'quinta-feira', 'sexta-feira', 'sábado', 'domingo']
_METHOD_ALIASES = {'credito': 'Cartão de Crédito', 'cartao': 'Cartão de Crédito', 'cartao de credito': 'Cartão de Crédito',
                   'debito': 'Cartão de Débito', 'cartao de debito': 'Cartão de Débito', 'pix': 'PIX',
                   'dinheiro': 'Dinheiro', 'vr': 'VR', 'va': 'VA'}


def _norm(text: str) -> str:
    import unicodedata
    return unicodedata.normalize('NFKD', str(text or '')).encode('ascii', 'ignore').decode().lower().strip()


def build_prompt(*, today: date, categories: list[str], methods: list[str], cards: list[dict],
                 members: list[dict], me: str) -> str:
    system = (_SYSTEM.replace('{methods}', ', '.join(methods)).replace('{categories}', ', '.join(categories))
              .replace('{today}', today.isoformat()).replace('{weekday}', _WEEKDAYS[today.weekday()]))
    card_lines = '; '.join(f"id {c['id']}: {c['label']} ({c['kind']})" for c in cards) or 'nenhum'
    people = '; '.join(f"id {m['id']}: {m['name']}" for m in members)
    return f'{system}\nCartões da família: {card_lines}\nMembros: {people}\nQuem está escrevendo: {me}'


@dataclass
class Draft:
    """Lançamento proposto pela IA, já conferido. Nada é gravado aqui."""
    description: str
    amount: Decimal | None
    payment_method: str
    category: str | None
    date: date
    card_id: int | None = None
    installments: int | None = None
    user_id: int | None = None

    @property
    def missing(self) -> list[str]:
        return [f for f, ok in (('amount', self.amount), ('description', self.description)) if not ok]

    def as_body(self) -> dict:
        """Corpo de POST /expenses."""
        body = {'description': self.description, 'amount': f'{self.amount:.2f}' if self.amount else None,
                'category': self.category or 'Outros', 'payment_method': self.payment_method,
                'date': self.date.isoformat(), 'card_id': self.card_id, 'installments': self.installments,
                'user_id': self.user_id}
        return {k: v for k, v in body.items() if v is not None}


def _amount(value) -> Decimal | None:
    try:
        amount = Decimal(str(value).replace('R$', '').strip()).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        return None
    return amount if Decimal('0') < amount < Decimal('10000000') else None


def _date(value, today: date) -> date:
    try:
        d = date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return today
    return d if today - timedelta(days=400) <= d <= today + timedelta(days=400) else today


def _int(value) -> int | None:
    try:
        return int(value) if value not in (None, '', False) else None
    except (TypeError, ValueError):
        return None


def to_draft(raw: dict | None, *, today: date, categories: list[str], methods: list[str], cards: list[dict],
             member_ids: list[int], suggest) -> Draft:
    """Confere cada campo devolvido pela IA contra os dados reais da família."""
    raw = raw if isinstance(raw, dict) else {}
    description = str(raw.get('description') or '').strip()[:200]
    method = str(raw.get('payment_method') or 'PIX')
    method = method if method in methods else _METHOD_ALIASES.get(_norm(method), 'PIX')
    by_id = {c['id']: c for c in cards}
    card = by_id.get(_int(raw.get('card_id')))
    if card and method in ('PIX', 'Dinheiro'):  # citou o cartão sem dizer a forma
        method = 'Cartão de Crédito' if card['supports_credit'] else 'Cartão de Débito'
    installments = _int(raw.get('installments'))
    installments = installments if installments and 2 <= installments <= 48 and method == 'Cartão de Crédito' else None
    category = raw.get('category') if raw.get('category') in categories else None
    if not category and len(description) >= 3:
        category = suggest(description)
    person = _int(raw.get('person_id'))
    return Draft(description=description, amount=_amount(raw.get('amount')), payment_method=method,
                 category=category or 'Outros', date=_date(raw.get('date'), today),
                 card_id=card['id'] if card else None, installments=installments,
                 user_id=person if person in member_ids else None)


def interpret(client: AIClient, text: str, system_prompt: str) -> dict:
    """{'intent', 'expense' (cru, conferir com to_draft), 'reply'}."""
    data = client.complete_json(system_prompt, text)
    intent = data.get('intent') if data.get('intent') in INTENTS else 'chat'
    reply = str(data.get('reply') or '').strip()[:500]
    return {'intent': intent, 'expense': data.get('expense'), 'reply': reply}
