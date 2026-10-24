"""/investir — registrar um investimento passo a passo (real, outra moeda ou cripto)."""
from datetime import date

from finfam_bot.flow import Flow, store_number
from finfam_bot.fmt import btn, dec, h, rows
from finfam_bot.parsers import parse_amount
from finfam_bot.views import invest_text

CRYPTO = 'Criptomoedas'


class InvestFlow(Flow):
    kind, prefix, cancel_arg = 'inv', 'i', 'x'
    cancelled = 'Investimento cancelado.'
    expired = '⌛ Expirou. Use /investir.'
    pick_step = {'t': 'type', 'c': 'coin', 'm': 'currency', 'r': 'rate'}

    # ── dados de apoio (cache compartilhado pelo Bot) ──
    def meta(self, chat_id: int) -> dict:
        return self.bot.cached(None, 'meta', lambda: self.api.get(chat_id, '/meta'), ttl=3600)

    def fx_rates(self, chat_id: int) -> dict:
        return self.bot.cached(None, 'fx', lambda: self.api.get(chat_id, '/market/fx')['rates'], ttl=900)

    def symbol(self, chat_id: int, data: dict) -> str:
        code = data.get('currency', 'BRL')
        return next((c['symbol'] for c in self.meta(chat_id)['currencies'] if c['code'] == code), 'R$')

    def pending(self, chat_id: int, f: dict) -> str | None:
        data = f['data']
        crypto = data.get('investment_type') == CRYPTO
        if 'investment_type' not in data:
            return 'type'
        if crypto and 'crypto_coin' not in data:
            return 'coin'
        if not crypto and 'currency' not in data:
            return 'currency'
        if 'amount' not in data:
            return 'amount'
        if self._needs_fx(chat_id, f):
            return 'fx'
        if not crypto and 'annual_rate' not in data:
            return 'rate'
        return None

    def _needs_fx(self, chat_id: int, f: dict) -> bool:
        """Moeda estrangeira com a cotação de hoje fora do ar: pergunta (senão a API usa a de hoje)."""
        currency = f['data'].get('currency', 'BRL')
        if currency == 'BRL' or 'fx_buy_rate' in f['data']:
            return False
        return not self.fx_rates(chat_id).get(currency)

    # ── perguntas ──
    def ask_type(self, chat_id: int, f: dict) -> None:
        types = self.meta(chat_id)['investment_types']
        self.ask(chat_id, '📈 Qual o tipo de investimento?',
                 rows([btn(t, f'i:t:{i}') for i, t in enumerate(types)], 2) + [self.cancel])

    def ask_coin(self, chat_id: int, f: dict) -> None:
        coins = self.meta(chat_id)['crypto_coins']
        self.ask(chat_id, 'Qual criptomoeda?', rows([btn(c['label'], f'i:c:{i}') for i, c in enumerate(coins)], 2)
                 + [self.cancel])

    def ask_currency(self, chat_id: int, f: dict) -> None:
        currencies = self.meta(chat_id)['currencies']
        self.ask(chat_id, 'Em qual moeda?',
                 rows([btn(f"{c['symbol']} {c['name']}", f'i:m:{i}') for i, c in enumerate(currencies)], 2)
                 + [self.cancel])

    def ask_amount(self, chat_id: int, f: dict) -> None:
        symbol = self.symbol(chat_id, f['data'])
        self.ask(chat_id, f'💰 Quanto foi investido (em {h(symbol)})? Ex.: 1.000,00', [self.cancel])

    def ask_fx(self, chat_id: int, f: dict) -> None:
        symbol = self.symbol(chat_id, f['data'])
        self.ask(chat_id, f'💱 Não consegui a cotação de hoje. Quanto valia 1 {h(symbol)} em reais na compra? '
                          'Ex.: 5,40', [self.cancel])

    def ask_rate(self, chat_id: int, f: dict) -> None:
        data, buttons = f['data'], []
        if data['currency'] == 'BRL':
            sug = self.bot.cached(None, 'selic', lambda: self.api.get(chat_id, '/market/selic'), ttl=3600)
            rate = sug['suggestions'].get(data['investment_type']) or 0
            if rate:
                data['_suggested'] = rate
                buttons.append(btn(f'⭐ {dec(rate)}% (sugerida)', 'i:r:s'))
        buttons.append(btn('Sem rendimento', 'i:r:0'))
        self.ask(chat_id, 'Rende quanto ao ano (%)? Toque ou digite, ex.: 12,5', [buttons, self.cancel])

    # ── texto digitado ──
    def read_amount(self, f: dict, text: str) -> str | None:
        return store_number(f, 'amount', parse_amount(text), 'Não entendi o valor. Digite só o número, ex.: <b>1.000,00</b>')

    def read_fx(self, f: dict, text: str) -> str | None:
        return store_number(f, 'fx_buy_rate', parse_amount(text), 'Digite a cotação em reais, ex.: <b>5,40</b>')

    def read_rate(self, f: dict, text: str) -> str | None:
        value = parse_amount(text.replace('%', ''))
        return store_number(f, 'annual_rate', value if value is None or value <= 999 else None,
                            'Digite a taxa ao ano, ex.: <b>12,5</b> (ou toque em "Sem rendimento").')

    # ── botões ──
    def pick_t(self, chat_id: int, f: dict, value: str) -> str:
        f['data']['investment_type'] = self.meta(chat_id)['investment_types'][int(value)]
        return f['data']['investment_type']

    def pick_c(self, chat_id: int, f: dict, value: str) -> str:
        coin = self.meta(chat_id)['crypto_coins'][int(value)]
        f['data'].update(crypto_coin=coin['id'], coin_label=coin['label'], currency='BRL')
        return coin['label']

    def pick_m(self, chat_id: int, f: dict, value: str) -> str:
        cur = self.meta(chat_id)['currencies'][int(value)]
        f['data']['currency'] = cur['code']
        return cur['name']

    def pick_r(self, chat_id: int, f: dict, value: str) -> str:
        rate = f['data'].get('_suggested', 0) if value == 's' else 0
        f['data']['annual_rate'] = rate
        return f'{dec(rate)}% a.a.' if rate else 'sem rendimento'

    # ── fim ──
    def finish(self, chat_id: int) -> None:
        f = self.flows.pop(chat_id)
        d, today = f['data'], date.today()
        currency = d.get('currency', 'BRL')
        fx_rate = (d.get('fx_buy_rate') or self.fx_rates(chat_id).get(currency)) if currency != 'BRL' else None
        text = invest_text(d, self.symbol(chat_id, d), fx_rate, today)
        body = {'investment_type': d['investment_type'], 'amount': f'{d["amount"]:.2f}',
                'annual_rate': f'{d.get("annual_rate") or 0:.2f}', 'crypto_coin': d.get('crypto_coin'),
                'currency': currency, 'month': today.month, 'year': today.year,
                'fx_buy_rate': f'{d["fx_buy_rate"]:.6f}' if d.get('fx_buy_rate') else None}
        body = {k: v for k, v in body.items() if v is not None}
        self.bot.confirm(chat_id, {'body': body, 'text': text}, f.get('edit'), path='/investments', text=text)
