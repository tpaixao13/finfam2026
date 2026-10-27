"""IA da família (chave própria): transforma uma frase em lançamento ou comando.

Cada família escolhe o provedor e cola a chave dela; o FinFam só repassa a
pergunta. A resposta da IA é sempre CONFERIDA aqui (categoria, cartão, pessoa,
valores) e nunca grava nada: quem grava é o usuário, ao confirmar.
"""
import ipaddress
import json
import logging
import re
import socket
import time
import unicodedata
import urllib.error
import urllib.parse
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
    'gemini': {'label': 'Gemini (Google)', 'default_model': 'gemini-flash-lite-latest',
               'key_url': 'https://aistudio.google.com/apikey'},
    'openai': {'label': 'ChatGPT (OpenAI) ou compatível', 'default_model': 'gpt-5.4-mini',
               'key_url': 'https://platform.openai.com/api-keys'},
}
OPENAI_URL = 'https://api.openai.com/v1'
TIMEOUT = 60         # provedores oficiais (o Gemini às vezes demora); Cloudflare corta em 100 s
CUSTOM_TIMEOUT = 20  # endereço informado pela família: curto, para não prender a API


class AIError(Exception):
    """Falha ao falar com o provedor (mensagem já em português, sem segredos)."""


def check_public_url(url: str) -> None:
    """Endereço compatível informado pela família: só https para um host PÚBLICO.
    Impede usar o servidor para sondar a rede interna (127.0.0.1, 192.168.x.x, nuvem...)."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
        raise AIError('Endereço de IA não permitido: use https://host-publico/...')
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except OSError:
        raise AIError('Não encontrei o endereço de IA informado.') from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split('%')[0])
        if not ip.is_global or ip.is_multicast:
            raise AIError('Endereço de IA não permitido (rede interna).')


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Redirecionamento poderia levar a chave para outro host: vira erro."""

    def redirect_request(self, *args, **kwargs):
        return None


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
        url = f'https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent'
        body = {'systemInstruction': {'parts': [{'text': system}]},
                'contents': [{'role': 'user', 'parts': [{'text': user}]}],
                'generationConfig': {'responseMimeType': 'application/json',
                                     # pensar pouco: tarefa simples, e o padrão leva >30 s no Gemini 3
                                     'thinkingConfig': _gemini_thinking(self.model)}}
        try:
            r = _post(url, {'x-goog-api-key': self.api_key}, body)
        except AIError as exc:
            if 'thinking' not in str(exc).lower():
                raise
            del body['generationConfig']['thinkingConfig']  # modelo que não aceita o ajuste
            r = _post(url, {'x-goog-api-key': self.api_key}, body)
        parts = (r.get('candidates') or [{}])[0].get('content', {}).get('parts', [])
        return ''.join(p.get('text', '') for p in parts if not p.get('thought'))

    def _openai(self, system: str, user: str) -> str:
        body = {'model': self.model, 'messages': [{'role': 'system', 'content': system},
                                                  {'role': 'user', 'content': user}]}
        if not self.base_url:  # nem todo "compatível" aceita response_format
            body['response_format'] = {'type': 'json_object'}
        if self.base_url:
            check_public_url(self.base_url)  # de novo a cada chamada: o DNS pode ter mudado
        r = _post(f'{(self.base_url or OPENAI_URL).rstrip("/")}/chat/completions',
                  {'Authorization': f'Bearer {self.api_key}'}, body, custom=bool(self.base_url))
        return ((r.get('choices') or [{}])[0].get('message') or {}).get('content') or ''


def _gemini_thinking(model: str) -> dict:
    if '2.5' in model:  # Gemini 2.5 usa orçamento de tokens; Flash aceita 0 (desliga)
        return {'thinkingBudget': 0 if 'flash' in model else 128}
    return {'thinkingLevel': 'low'}


class _Overloaded(AIError):
    """Provedor com erro 5xx: vale uma nova tentativa."""


def _post(url: str, headers: dict, body: dict, custom: bool = False) -> dict:
    """Uma nova tentativa se o provedor oficial estiver sobrecarregado (5xx)."""
    try:
        return _post_once(url, headers, body, custom)
    except _Overloaded as exc:
        if custom:
            raise AIError(str(exc)) from None
        time.sleep(2)
        try:
            return _post_once(url, headers, body, custom)
        except _Overloaded as exc2:
            raise AIError(str(exc2)) from None


def _post_once(url: str, headers: dict, body: dict, custom: bool = False) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method='POST',
                                 headers={'Content-Type': 'application/json', **headers})
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=_ssl_context()))
    where = urllib.parse.urlsplit(url).hostname
    try:
        with opener.open(req, timeout=CUSTOM_TIMEOUT if custom else TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body_text = exc.read(4096).decode('utf-8', 'replace')
        log.warning('IA %s respondeu %s: %s', where, exc.code, _log_safe(body_text))
        if 300 <= exc.code < 400:
            raise AIError('O endereço de IA tentou redirecionar a chamada (bloqueado).') from None
        error = _Overloaded if exc.code in (500, 502, 503, 504) else AIError
        raise error(_http_message(exc.code, body_text)) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, 'reason', exc)
        log.warning('IA %s sem conexão: %s', where, _log_safe(str(reason)))
        if custom:  # não devolve o erro bruto: evitaria mapear a rede pelo erro
            raise AIError('Não consegui conectar ao endereço de IA configurado.') from None
        if 'CERTIFICATE' in str(reason).upper():
            raise AIError('Certificado SSL não reconhecido pelo servidor. No Raspberry, defina '
                          'INSECURE_EXTERNAL_SSL=1 no .env ou atualize os certificados (ca-certificates).') from None
        if 'timed out' in str(reason).lower():
            raise AIError('O provedor de IA demorou demais para responder. Tente de novo ou escolha outro modelo.') from None
        raise AIError('Não consegui falar com o provedor de IA agora.') from None
    except ValueError:
        raise AIError('O provedor de IA devolveu uma resposta inválida.') from None


def _log_safe(text: str) -> str:
    """Uma linha só, curta: o corpo vem de fora e não pode forjar linhas no log."""
    return re.sub(r'[\r\n\t]+', ' ', text)[:200]


def _http_message(code: int, body: str) -> str:
    invalid_key = any(t in body for t in ('API_KEY_INVALID', 'API key not valid', 'invalid_api_key', 'invalid x-api-key'))
    if code in (401, 403) or invalid_key:
        return 'A chave de IA foi recusada pelo provedor. Confira a chave em Telegram e IA.'
    if code == 404:
        return 'Modelo de IA não encontrado. Confira o nome do modelo em Telegram e IA.'
    if code in (500, 502, 503, 504):
        return ('O modelo de IA está sobrecarregado no provedor agora. Tente de novo em instantes '
                'ou escolha outro modelo em Telegram e IA.')
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

INTENTS = ('expense', 'bills', 'spending', 'purchases', 'invoices', 'summary', 'investments', 'balance', 'bank', 'chat')
QUERY_INTENTS = ('bills', 'spending', 'purchases', 'invoices')

_SYSTEM = """Você é o assistente do FinFam, app de controle financeiro familiar no Brasil.
Interprete a mensagem do usuário e responda SOMENTE com um objeto JSON, sem texto fora dele:
{"intent": "expense" | "bills" | "spending" | "purchases" | "invoices" | "summary" | "investments" | "balance" | "bank" | "chat",
 "expense": {"description": str, "amount": number, "payment_method": str, "card_id": int|null,
             "installments": int|null, "category": str|null, "date": "AAAA-MM-DD"|null, "person_id": int|null} | null,
 "query": {"days": int|null, "category": str|null, "text": str|null, "payment_method": str|null,
           "card_id": int|null, "bank": str|null,
           "month": int|null, "year": int|null,
           "date_from": "AAAA-MM-DD"|null, "date_to": "AAAA-MM-DD"|null} | null,
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
- "bills": quer ver contas a pagar/vencimentos. query.days = quantos dias à frente ("próximos 5 dias" → 5; padrão 7).
- "spending": pergunta QUANTO gastou com algo ("quanto gastei de alimentação/mercado este mês?").
  query.category = uma de {categories} se der para mapear ("alimento", "comida" → Alimentação); senão null e
  query.text = a palavra buscada ("uber", "ifood"). query.month/year = o mês perguntado (padrão: o mês atual).
  Se a pessoa falar da FORMA de pagamento ("no débito", "no PIX"), use query.payment_method (uma de {methods})
  e deixe text null.
- "invoices": pergunta o valor da FATURA do cartão de crédito ("qual o valor do cartão de crédito de novembro?",
  "quanto vem a fatura?"). query.month/year = mês da fatura (padrão: o mês atual). Se citar um cartão ou banco
  ("da Caixa", "do cartão roxo", "final 1234"): query.card_id = id da lista de cartões, se der para identificar;
  senão query.bank = o banco citado.
- "purchases": pergunta O QUE comprou/lançou num dia ou período ("o que comprei dia 20/10?", "compras da semana
  passada"). query.date_from e query.date_to (o mesmo dia se for um dia só); category/text se a pessoa filtrar.
- "summary": quer o resumo geral do mês (renda, gastos, saldo), sem categoria específica.
- "investments": pergunta sobre investimentos/aplicações/carteira ("quais meus investimentos?", "quanto tenho
  investido?", "quanto rende meu CDB?"). Não é lançar: "investi 500 no CDB" também é "investments".
- "balance": pergunta o SALDO da conta corrente/banco ("quanto tenho na conta?", "saldo do C6?").
- "bank": pergunta sobre os bancos conectados/Open Finance ("chegou algo do banco?", "a fatura bate com o banco?").
- "chat": qualquer outra coisa; em "reply" explique em 1 frase o que você consegue fazer (lançar gastos,
  contas a pagar, faturas, gastos por categoria, resumo, investimentos, saldo e bancos conectados).
- "reply": frase curta em português para o usuário."""

_WEEKDAYS = ['segunda-feira', 'terça-feira', 'quarta-feira', 'quinta-feira', 'sexta-feira', 'sábado', 'domingo']
_METHOD_ALIASES = {'credito': 'Cartão de Crédito', 'cartao': 'Cartão de Crédito', 'cartao de credito': 'Cartão de Crédito',
                   'debito': 'Cartão de Débito', 'cartao de debito': 'Cartão de Débito', 'pix': 'PIX',
                   'dinheiro': 'Dinheiro', 'vr': 'VR', 'va': 'VA'}


def _norm(text: str) -> str:
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
    """{'intent', 'expense'/'query' (crus, conferir com to_draft/to_query), 'reply'}."""
    data = client.complete_json(system_prompt, text)
    intent = data.get('intent') if data.get('intent') in INTENTS else 'chat'
    reply = str(data.get('reply') or '').strip()[:500]
    return {'intent': intent, 'expense': data.get('expense'), 'query': data.get('query'), 'reply': reply}


def match_category(value, categories: list[str]) -> str | None:
    """'Alimentação', 'alimentacao' ou 'aliment' → 'Alimentação' (só categorias da família)."""
    wanted = _norm(value)
    if not wanted:
        return None
    for c in categories:
        if _norm(c) == wanted:
            return c
    hits = [c for c in categories if len(wanted) >= 4 and (_norm(c).startswith(wanted[:5]) or wanted.startswith(_norm(c)[:5]))]
    return hits[0] if len(hits) == 1 else None


def match_bank(value, banks: list[str]) -> str | None:
    """'caixa' → 'Caixa Econômica Federal', 'nu' não basta, 'nubank' → 'Nubank' (só bancos da família)."""
    wanted = _norm(value)
    if len(wanted) < 3:
        return None
    hits = [b for b in banks if wanted in _norm(b) or _norm(b) in wanted]
    return hits[0] if len(set(hits)) == 1 else None


def to_query(raw: dict | None, *, intent: str, today: date, categories: list[str],
             methods: list[str] | None = None, cards: list[dict] | None = None) -> dict:
    """Confere os filtros da consulta: números em faixa, categoria real, datas plausíveis."""
    raw = raw if isinstance(raw, dict) else {}
    days = _int(raw.get('days'))
    category = match_category(raw.get('category'), categories)
    text = re.sub(r'[^\w\s.-]', '', str(raw.get('text') or ''))[:60].strip() or None
    method = str(raw.get('payment_method') or '')
    method = method if method in (methods or []) else _METHOD_ALIASES.get(_norm(method))
    if method and _norm(text or '') in (_norm(method), 'cartao de credito', 'credito', 'debito', 'pix', 'cartao'):
        text = None  # "no débito" é a forma de pagamento, não um nome de lançamento
    month, year = _int(raw.get('month')), _int(raw.get('year'))
    month = month if month and 1 <= month <= 12 else today.month
    year = year if year and abs(year - today.year) <= 3 else today.year
    start = _date(raw.get('date_from'), today) if raw.get('date_from') else None
    end = _date(raw.get('date_to'), today) if raw.get('date_to') else start
    if intent == 'purchases' and start is None:
        start = end = today
    if start and end and end < start:
        start, end = end, start
    if start and end and (end - start).days > 400:
        end = start + timedelta(days=400)
    cards = cards or []
    card_id = _int(raw.get('card_id'))
    card_id = card_id if card_id in {c['id'] for c in cards} else None
    bank = None if card_id else match_bank(raw.get('bank'), sorted({c['bank'] for c in cards if c.get('bank')}))
    if bank and text and match_bank(text, [bank]):
        text = None  # "da Caixa" é o banco, não um nome de lançamento
    return {'days': days if days and 0 <= days <= 90 else 7, 'category': category, 'payment_method': method,
            'card_id': card_id, 'bank': bank,
            'text': None if category else text, 'month': month, 'year': year,
            'date_from': start.isoformat() if start else None, 'date_to': end.isoformat() if end else None}
