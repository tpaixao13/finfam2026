"""/lancar — lançar uma despesa passo a passo."""
import re
from datetime import date, timedelta

from finfam_bot.consts import (BANK_METHODS, CREDIT, DEBIT, INSTALLMENTS, METHOD_LABEL, METHODS,
                               RECURRING_METHODS, RECURRING_OPTIONS)
from finfam_bot.flow import Flow, store_number
from finfam_bot.fmt import btn, h, rows
from finfam_bot.parsers import parse_amount, parse_date


class ExpenseFlow(Flow):
    kind, prefix, cancel_arg = 'expense', 'f', 'cancel'
    cancelled = 'Lançamento cancelado.'
    expired = '⌛ Esse lançamento expirou. Use /lancar.'
    pick_step = {'pm': 'method', 'card': 'card', 'inst': 'installments', 'rec': 'recurring', 'bank': 'bank',
                 'cat': 'category', 'date': 'date', 'paid': 'paid'}

    def pending(self, chat_id: int, f: dict) -> str | None:
        data, ai = f['data'], f.get('from_ai')
        checks = [('description', data.get('description')), ('amount', data.get('amount')),
                  ('method', data.get('payment_method')),
                  ('card', 'card_id' in data or ai),
                  ('installments', 'installments' in data or data.get('payment_method') != CREDIT or ai),
                  ('recurring', 'recurring' in data or data.get('payment_method') not in RECURRING_METHODS),
                  ('bank', 'bank' in data or data.get('card_id') or data.get('payment_method') not in BANK_METHODS),
                  ('category', data.get('category')), ('date', data.get('date')),
                  ('paid', 'paid' in data)]
        return next((step for step, done in checks if not done), None)

    # ── perguntas ──
    def ask_description(self, chat_id: int, f: dict) -> None:
        self.ask(chat_id, '📝 O que foi? (ex.: <i>Mercado</i>, <i>Conta de luz</i>)', [self.cancel])

    def ask_amount(self, chat_id: int, f: dict) -> None:
        self.ask(chat_id, f'💰 Quanto custou <b>{h(f["data"]["description"])}</b>? (valor total, ex.: 87,50)',
                 [self.cancel])

    def ask_method(self, chat_id: int, f: dict) -> None:
        self.ask(chat_id, 'Como foi pago?',
                 rows([btn(METHOD_LABEL[m], f'f:pm:{i}') for i, m in enumerate(METHODS)], 3) + [self.cancel])

    def ask_card(self, chat_id: int, f: dict) -> None:
        cards = self._cards_for(chat_id, f['data']['payment_method'])
        if not cards:  # PIX/dinheiro ou nenhum cartão do tipo: pula a pergunta
            f['data']['card_id'] = None
            return self.next(chat_id)
        f['cards'] = {c['id']: c['label'] for c in cards}
        self.ask(chat_id, 'Qual cartão?',
                 rows([btn(c['label'][:30], f'f:card:{c["id"]}') for c in cards[:12]], 1)
                 + [[btn('Sem cartão', 'f:card:0')], self.cancel])

    def ask_installments(self, chat_id: int, f: dict) -> None:
        self.ask(chat_id, 'Em quantas vezes?',
                 rows([btn('À vista' if n == 1 else f'{n}x', f'f:inst:{n}') for n in INSTALLMENTS], 4) + [self.cancel])

    def ask_recurring(self, chat_id: int, f: dict) -> None:
        self.ask(chat_id, '🔁 Essa conta se repete todo mês? (ex.: aluguel, internet, escola)\n'
                          'Toque ou digite por quantos meses (2 a 120).',
                 [[btn('Não, só esta', 'f:rec:0')],
                  [btn(f'{n} meses', f'f:rec:{n}') for n in RECURRING_OPTIONS], self.cancel])

    def _options(self, chat_id: int) -> dict:
        return self.bot.cached(chat_id, 'options', lambda: self.api.get(chat_id, '/expenses/options'))

    def ask_bank(self, chat_id: int, f: dict) -> None:
        """Bancos já usados pela família primeiro, depois a lista geral (como no site)."""
        used = self._options(chat_id).get('banks', [])
        everything = self.bot.cached(None, 'meta', lambda: self.api.get(chat_id, '/meta'), ttl=3600).get('banks', [])
        banks = list(dict.fromkeys(used + everything))[:20]
        f['banks'] = banks
        self.ask(chat_id, '🏦 Qual banco?', rows([btn(b, f'f:bank:{i}') for i, b in enumerate(banks)], 2)
                 + [[btn('Pular', 'f:bank:-')], self.cancel])

    def ask_paid(self, chat_id: int, f: dict) -> None:
        hint = '\n<i>No crédito, normalmente fica pendente até pagar a fatura.</i>' \
            if f['data'].get('payment_method') == CREDIT else ''
        self.ask(chat_id, f'Essa despesa já está paga?{hint}',
                 [[btn('✅ Sim, já paguei', 'f:paid:1'), btn('⏳ Não, pendente', 'f:paid:0')], self.cancel])

    def ask_category(self, chat_id: int, f: dict) -> None:
        description = f['data']['description']
        cats = self._options(chat_id)['categories']
        f['cats'] = cats
        suggestion = None
        if len(description) >= 3:
            suggestion = self.api.get(chat_id, '/expenses/suggest-category', description=description).get('category')
        buttons = [btn(f'⭐ {suggestion}', f'f:cat:{cats.index(suggestion)}')] if suggestion in cats else []
        buttons += [btn(c, f'f:cat:{i}') for i, c in enumerate(cats) if c != suggestion][:20]
        self.ask(chat_id, 'Categoria?', rows(buttons, 2) + [self.cancel])

    DATE_HINT = 'ex.: <b>05/10</b>, <b>5/10/2026</b>, <b>dia 5</b> ou <b>sexta</b>'

    def ask_date(self, chat_id: int, f: dict) -> None:
        self.ask(chat_id, f'📆 Quando foi? Toque abaixo ou <b>digite a data</b> ({self.DATE_HINT}).',
                 [[btn('Hoje', 'f:date:0'), btn('Ontem', 'f:date:1'), btn('Anteontem', 'f:date:2')],
                  [btn('📅 Outra data', 'f:date:x')], self.cancel])

    def _cards_for(self, chat_id: int, method: str) -> list[dict]:
        if method not in (CREDIT, DEBIT, 'VR', 'VA'):  # PIX/dinheiro: nem busca cartões
            return []
        cards = self.bot.cached(chat_id, 'cards', lambda: self.api.get(chat_id, '/cards', light='true')['cards'])
        if method == CREDIT:
            return [c for c in cards if c['supports_credit']]
        if method == DEBIT:
            return [c for c in cards if c['supports_debit']]
        if method in ('VR', 'VA'):
            return [c for c in cards if c['card_type'] == method.lower()]
        return []

    # ── texto digitado ──
    def text(self, chat_id: int, text: str) -> None:
        f = self.flows[chat_id]
        if f['step'] == 'description' and re.search(r'\d', text) and len(text.split()) > 1:
            del self.flows[chat_id]  # frase inteira: "almoço 25 no pix"
            return self.bot.free_text(chat_id, text)
        super().text(chat_id, text)

    def read_description(self, f: dict, text: str) -> None:
        f['data']['description'] = text[:200]

    def read_amount(self, f: dict, text: str) -> str | None:
        return store_number(f, 'amount', parse_amount(text), 'Não entendi o valor. Digite só o número, ex.: <b>87,50</b>')

    def read_recurring(self, f: dict, text: str) -> str | None:
        n = int(text.strip()) if text.strip().isdigit() else 0
        if not 2 <= n <= 120:
            return 'Digite quantos meses a conta se repete, de <b>2</b> a <b>120</b> (ou toque em "Não, só esta").'
        f['data']['recurring'] = n
        return None

    def read_date(self, f: dict, text: str) -> str | None:
        d = parse_date(text, date.today())
        return store_number(f, 'date', d and d.isoformat(), f'Não entendi a data. Digite, {self.DATE_HINT}.')

    # ── botões ──
    def pick_pm(self, chat_id: int, f: dict, value: str) -> str:
        f['data']['payment_method'] = METHODS[int(value)]
        return METHOD_LABEL[f['data']['payment_method']]

    def pick_card(self, chat_id: int, f: dict, value: str) -> str:
        f['data']['card_id'] = int(value) or None
        return f.get('cards', {}).get(f['data']['card_id'], 'Sem cartão')

    def pick_inst(self, chat_id: int, f: dict, value: str) -> str:
        n = int(value)
        f['data']['installments'] = n if n > 1 else None
        return 'À vista' if n == 1 else f'{n}x'

    def pick_rec(self, chat_id: int, f: dict, value: str) -> str:
        n = int(value)
        f['data']['recurring'] = n if n > 1 else None
        return f'Repete {n} meses' if n > 1 else 'Não repete'

    def pick_bank(self, chat_id: int, f: dict, value: str) -> str:
        f['data']['bank'] = None if value == '-' else f['banks'][int(value)]
        return f['data']['bank'] or 'Sem banco'

    def pick_paid(self, chat_id: int, f: dict, value: str) -> str:
        f['data']['paid'] = value == '1'
        return 'Pago' if f['data']['paid'] else 'Pendente'

    def pick_cat(self, chat_id: int, f: dict, value: str) -> str:
        f['data']['category'] = f['cats'][int(value)]
        return f['data']['category']

    def pick_date(self, chat_id: int, f: dict, value: str) -> str | None:
        if value == 'x':  # "Outra data": só pede para digitar (o passo continua sendo a data)
            self.ask(chat_id, f'📅 Digite a data da compra ({self.DATE_HINT}).', [self.cancel])
            return None
        f['data']['date'] = (date.today() - timedelta(days=int(value))).isoformat()
        return ['Hoje', 'Ontem', 'Anteontem'][int(value)]

    # ── fim ──
    def finish(self, chat_id: int) -> None:
        f = self.flows.pop(chat_id)
        data = f['data']
        installments = data.get('installments') if (data.get('installments') or 1) > 1 else None
        view = {**data, 'card_label': data.get('card_label') or f.get('cards', {}).get(data.get('card_id')),
                'installments': installments}
        body = {'description': data['description'], 'amount': f'{data["amount"]:.2f}', 'category': data['category'],
                'payment_method': data['payment_method'], 'date': data['date'],
                'card_id': data.get('card_id') or None, 'installments': installments, 'user_id': data.get('user_id'),
                'bank': None if data.get('card_id') else data.get('bank'), 'paid': bool(data.get('paid')),
                'recurring_months': data.get('recurring') or None}
        body = {k: v for k, v in body.items() if v is not None}
        self.bot.confirm(chat_id, {**view, 'body': body}, f.get('edit'))
