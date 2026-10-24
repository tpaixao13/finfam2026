"""Conversa do bot: comandos, lançamento guiado (/lancar) e texto livre com a IA da família.

Nada é gravado sem o usuário tocar em ✅. Toda ação passa pela API do FinFam,
que só enxerga os dados da família de quem vinculou a conversa.
"""
import html
import logging
import re
import secrets
import time
from datetime import date, timedelta

from finfam_bot.http import ApiError, FinFam, Telegram

log = logging.getLogger('finfam.bot')

TTL = 30 * 60  # rascunhos e lançamentos guiados expiram em 30 min
CREDIT, DEBIT = 'Cartão de Crédito', 'Cartão de Débito'
METHODS = ['PIX', DEBIT, CREDIT, 'Dinheiro', 'VR', 'VA']
METHOD_LABEL = {'PIX': 'PIX', DEBIT: 'Débito', CREDIT: 'Crédito', 'Dinheiro': 'Dinheiro', 'VR': 'VR', 'VA': 'VA'}
INSTALLMENTS = [1, 2, 3, 4, 5, 6, 10, 12]

HELP = ('<b>O que eu faço</b>\n'
        '/contas — contas a pagar (com botão para pagar)\n'
        '/resumo — resumo do mês\n'
        '/lancar — lançar um gasto passo a passo\n'
        '/cancelar — cancela o lançamento em andamento\n'
        '/desconectar — desliga este Telegram da sua conta\n\n'
        'Com a IA configurada no site, é só escrever, por exemplo:\n'
        '<i>gastei 87,50 no mercado no cartão Inter em 3x</i>')


# ── formatação ───────────────────────────────────────────────────────────────

def h(text) -> str:
    return html.escape(str(text if text is not None else ''), quote=False)


def brl(value) -> str:
    return f'R$ {float(value or 0):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def dm(iso: str | None) -> str:
    return f'{iso[8:10]}/{iso[5:7]}' if iso and len(iso) >= 10 else '—'


def parse_amount(text: str) -> float | None:
    s = re.sub(r'[^\d,.]', '', text or '')
    if ',' in s:
        s = s.replace('.', '').replace(',', '.')
    elif s.count('.') > 1:
        s = s.replace('.', '')
    try:
        value = round(float(s), 2)
    except ValueError:
        return None
    return value if 0 < value < 10_000_000 else None


def parse_date(text: str, today: date) -> date | None:
    t = (text or '').strip().lower()
    if t in ('hoje', 'h'):
        return today
    if t in ('ontem', 'o'):
        return today - timedelta(days=1)
    m = re.fullmatch(r'(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?', t)
    if not m:
        return None
    day, month, year = int(m[1]), int(m[2]), int(m[3] or today.year)
    year += 2000 if year < 100 else 0
    try:
        return date(year, month, day)
    except ValueError:
        return None


_STOP = {'gastei', 'paguei', 'comprei', 'gasto', 'compra', 'paguei', 'lancar', 'lança', 'lanca', 'no', 'na', 'nos',
         'nas', 'de', 'do', 'da', 'em', 'com', 'um', 'uma', 'o', 'a', 'e', 'pelo', 'pela', 'reais', 'real', 'r$',
         'hoje', 'ontem', 'cartao', 'cartão', 'via', 'pago', 'paga'}
_METHOD_WORDS = [(('credito', 'crédito'), CREDIT), (('debito', 'débito'), DEBIT), (('pix',), 'PIX'),
                 (('dinheiro', 'especie', 'espécie'), 'Dinheiro'), (('vr',), 'VR'), (('va',), 'VA')]


def quick_parse(text: str, today: date) -> dict:
    """Entende frases simples sem IA: "gastei 25 no almoço no pix", "uber 18,90 ontem".
    Devolve só o que encontrou (description, amount, payment_method, installments, date)."""
    out: dict = {}
    words = (text or '').split()
    rest = []
    for w in words:
        low = w.lower().strip('.,;!?')
        if 'amount' not in out and re.fullmatch(r'(r\$)?\d[\d.]*(,\d{1,2})?', low.replace('r$', 'r$')):
            value = parse_amount(low)
            if value:
                out['amount'] = value
                continue
        if m := re.fullmatch(r'(\d{1,2})x', low):
            out['installments'] = int(m[1])
            continue
        method = next((mth for keys, mth in _METHOD_WORDS if low in keys), None)
        if method and 'payment_method' not in out:
            out['payment_method'] = method
            continue
        if low in ('hoje', 'ontem'):
            out['date'] = parse_date(low, today).isoformat()
        if low in _STOP or low == 'r$':
            continue
        rest.append(w.strip('.,;!?'))
    if out.get('installments') and out.get('payment_method', CREDIT) != CREDIT:
        out.pop('installments')
    elif out.get('installments'):
        out['payment_method'] = CREDIT
    if rest:
        desc = ' '.join(rest)[:200]
        out['description'] = desc[0].upper() + desc[1:]
    return out


def rows(buttons: list[dict], per_row: int = 2) -> list[list[dict]]:
    return [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]


def btn(text: str, data: str) -> dict:
    return {'text': text, 'callback_data': data[:64]}


def draft_text(d: dict, title: str = 'Confere o lançamento?') -> str:
    lines = [f'🧾 <b>{h(title)}</b>', '',
             f'<b>{h(d["description"])}</b> — {brl(d["amount"])}',
             f'Forma: {h(METHOD_LABEL.get(d["payment_method"], d["payment_method"]))}'
             + (f' · {h(d["card_label"])}' if d.get('card_label') else '')
             + (f' · {d["installments"]}x de {brl(d["amount"] / d["installments"])}' if d.get('installments') else ''),
             f'Categoria: {h(d["category"])}',
             f'Data: {dm(d["date"])}']
    if d.get('user_name'):
        lines.append(f'Pessoa: {h(d["user_name"])}')
    return '\n'.join(lines)


# ── bot ──────────────────────────────────────────────────────────────────────

class Bot:
    def __init__(self, tg: Telegram, api: FinFam, site_url: str = ''):
        self.tg, self.api, self.site_url = tg, api, site_url
        self.flows: dict[int, dict] = {}     # chat → lançamento guiado em andamento
        self.drafts: dict[str, dict] = {}    # id → rascunho aguardando ✅

    # entrada ─────────────────────────────────────────────────────────────────
    def handle(self, update: dict) -> None:
        self._expire()
        try:
            if 'callback_query' in update:
                self._callback(update['callback_query'])
            elif 'message' in update:
                self._message(update['message'])
        except ApiError as exc:
            chat = (update.get('message') or update.get('callback_query', {}).get('message') or {}).get('chat', {})
            if chat.get('id'):
                self.tg.send(chat['id'], self._api_error_text(exc))
        except Exception:  # um update ruim nunca derruba o bot
            log.exception('Falha ao processar update %s', update.get('update_id'))
            chat = (update.get('message') or update.get('callback_query', {}).get('message') or {}).get('chat', {})
            if chat.get('id'):
                self.flows.pop(chat['id'], None)
                self.tg.send(chat['id'], '⚠️ Algo deu errado aqui. Nada foi lançado — tente de novo com /lancar.')

    def _api_error_text(self, exc: ApiError) -> str:
        if exc.code == 'telegram_not_linked':
            return '🔗 Este Telegram não está conectado ao FinFam.\n\n' + self._how_to_link()
        if exc.status == 402:
            return '⏳ O acesso da sua família ao FinFam expirou. Renove o plano pelo site.'
        if exc.code == 'ai_not_configured':
            return ('🤖 A IA não está configurada para a sua família.\n'
                    'Use /lancar para lançar passo a passo, ou cadastre uma chave de IA no site em <b>Telegram e IA</b>.')
        return f'⚠️ {h(exc.message)}'

    def _how_to_link(self) -> str:
        where = f' ({h(self.site_url)})' if self.site_url else ''
        return (f'Para conectar: entre no FinFam{where}, abra <b>Telegram e IA</b> e toque em '
                '<b>Conectar Telegram</b>.')

    def _expire(self) -> None:
        limit = time.time() - TTL
        for store in (self.flows, self.drafts):
            for key in [k for k, v in store.items() if v['ts'] < limit]:
                del store[key]

    def _message(self, msg: dict) -> None:
        chat = msg.get('chat', {})
        chat_id, text = chat.get('id'), (msg.get('text') or '').strip()
        if chat.get('type') != 'private':  # finanças só em conversa privada
            if text.startswith('/'):
                self.tg.send(chat_id, 'Por segurança, eu só funciono em conversa privada.')
            return
        if not text:
            return
        command, _, arg = text.partition(' ')
        command = command.split('@')[0].lower()
        handlers = {'/start': lambda: self.start(chat_id, arg.strip(), msg.get('from', {})),
                    '/ajuda': lambda: self.tg.send(chat_id, HELP), '/help': lambda: self.tg.send(chat_id, HELP),
                    '/contas': lambda: self.bills(chat_id), '/resumo': lambda: self.summary(chat_id),
                    '/lancar': lambda: self.flow_start(chat_id), '/cancelar': lambda: self.cancel(chat_id),
                    '/desconectar': lambda: self.unlink(chat_id)}
        if command in handlers:
            handlers[command]()
        elif chat_id in self.flows:
            self.flow_text(chat_id, text)
        elif text.startswith('/'):
            self.tg.send(chat_id, 'Comando desconhecido.\n\n' + HELP)
        else:
            self.free_text(chat_id, text)

    # comandos ────────────────────────────────────────────────────────────────
    def start(self, chat_id: int, code: str, sender: dict) -> None:
        if code:
            r = self.api.post(None, '/telegram/bot/link', {'code': code, 'chat_id': str(chat_id),
                                                            'username': sender.get('username')})
            family = f' (família {h(r["family"])})' if r.get('family') else ''
            self.tg.send(chat_id, f'✅ {h(r["message"])}{family}\n\n{HELP}')
            return
        try:
            me = self.api.get(chat_id, '/auth/me')
        except ApiError as exc:
            if exc.code != 'telegram_not_linked':
                raise
            self.tg.send(chat_id, '👋 Olá! Eu sou o bot do <b>FinFam</b>.\n\n' + self._how_to_link())
            return
        self.tg.send(chat_id, f'👋 Olá, {h(me["user"]["name"])}!\n\n{HELP}')

    def unlink(self, chat_id: int) -> None:
        self.api.delete(chat_id, '/telegram/link')
        self.flows.pop(chat_id, None)
        self.tg.send(chat_id, '👋 Telegram desconectado do FinFam. Para voltar, gere um novo link no site.')

    def cancel(self, chat_id: int) -> None:
        self.tg.send(chat_id, 'Lançamento cancelado.' if self.flows.pop(chat_id, None) else 'Nada em andamento.')

    def bills(self, chat_id: int, message_id: int | None = None, days: int = 7) -> None:
        b = self.api.get(chat_id, '/expenses/bills', days_ahead=days)
        text, buttons = [f'📅 <b>Contas a pagar</b> — total {brl(b["total"])}'], []
        for key, title in (('overdue', '⚠️ Em atraso'), ('today', '🔔 Vencem hoje'), ('upcoming', f'🗓 Próximos {days} dias')):
            items = b[key]
            if not items:
                continue
            text.append(f'\n<b>{title}</b> ({brl(b["totals"][key])})')
            for e in items[:15]:
                who = f' · {h(e["user_name"])}' if e.get('user_name') else ''
                text.append(f'• {dm(e["due_date"])} — {h(e["description"])} — {brl(e["amount"])}{who}')
                if len(buttons) < 10:
                    buttons.append(btn(f'✅ {e["description"][:22]} {brl(e["amount"])}', f'pay:{e["id"]}'))
            if len(items) > 15:
                text.append(f'… e mais {len(items) - 15}.')
        if len(text) == 1:
            text.append('\nNada pendente. Tudo em dia! 🎉')
        else:
            text.append('\nToque para marcar como paga:')
        markup = rows(buttons, 1) + [[btn('🔄 Atualizar', 'bills')]]
        if message_id:
            self.tg.edit(chat_id, message_id, '\n'.join(text), markup)
        else:
            self.tg.send(chat_id, '\n'.join(text), markup)

    def summary(self, chat_id: int) -> None:
        d = self.api.get(chat_id, '/dashboard')
        k = d['kpis']
        lines = [f'📊 <b>Resumo de {h(d["month_name"])}/{d["year"]}</b>', '',
                 f'Renda recebida: {brl(k["income"])}', f'Gastos: {brl(k["expenses"])} (crédito {brl(k["credit"])})',
                 f'Sobra: <b>{brl(k["balance"])}</b>',
                 f'Pendente no mês: {brl(d["pending_total"])} em {len(d["pending"])} conta(s)']
        if len(d.get('people', [])) > 1:
            lines.append('')
            lines += [f'• {h(p["user"]["name"])}: gastos {brl(p["expenses"])}, saldo {brl(p["balance"])}'
                      for p in d['people']]
        if d.get('insights'):
            lines += ['', '<b>Atenção</b>'] + [f'• {h(i["title"])}' for i in d['insights'][:3]]
        self.tg.send(chat_id, '\n'.join(lines))

    # texto livre com IA ──────────────────────────────────────────────────────
    def free_text(self, chat_id: int, text: str) -> None:
        self.tg.typing(chat_id)
        try:
            r = self.api.post(chat_id, '/ai/interpret', {'text': text[:500]})
        except ApiError as exc:
            if exc.code != 'ai_not_configured' and exc.status not in (502, 0):
                raise
            # sem IA (ou IA fora do ar): tenta entender sozinho frases simples
            if self.quick_flow(chat_id, text):
                return
            if exc.code == 'ai_not_configured':
                self.tg.send(chat_id, 'Não entendi. 🙂 Use /lancar para lançar um gasto, ou /ajuda.\n\n'
                             '<i>Dica: escreva o gasto com o valor, ex.: <b>almoço 25 no pix</b>.</i>')
                return
            raise
        intent = r['intent']
        if intent == 'bills':
            self.bills(chat_id)
        elif intent == 'summary':
            self.summary(chat_id)
        elif intent == 'expense' and r.get('draft'):
            d = r['draft']
            if d['missing']:  # faltou valor/descrição: segue no passo a passo
                self.flows[chat_id] = {'step': 'amount' if d['description'] else 'description', 'ts': time.time(),
                                       'data': {k: d[k] for k in ('description', 'payment_method', 'card_id',
                                                                  'installments', 'category', 'date', 'user_id') if d.get(k)},
                                       'from_ai': True}
                self.flow_ask(chat_id)
            else:
                self.confirm(chat_id, d)
        else:
            self.tg.send(chat_id, h(r.get('reply') or 'Não entendi.') + '\n\nVeja o que eu faço em /ajuda.')

    def quick_flow(self, chat_id: int, text: str) -> bool:
        """Frase com valor ("almoço 25 no pix") → lançamento guiado só com o que falta."""
        data = quick_parse(text, date.today())
        if not data.get('amount') or not data.get('description'):
            return False
        self.flows[chat_id] = {'step': 'method', 'data': data, 'ts': time.time()}
        self.flow_next(chat_id)
        return True

    def confirm(self, chat_id: int, d: dict) -> None:
        draft_id = secrets.token_hex(4)
        self.drafts[draft_id] = {'chat': chat_id, 'body': d['body'], 'view': d, 'ts': time.time()}
        self.tg.send(chat_id, draft_text(d), [[btn('✅ Lançar', f'ok:{draft_id}'), btn('❌ Cancelar', f'no:{draft_id}')]])

    # lançamento guiado ───────────────────────────────────────────────────────
    def flow_start(self, chat_id: int) -> None:
        self.flows[chat_id] = {'step': 'description', 'data': {}, 'ts': time.time()}
        self.flow_ask(chat_id)

    def _cards_for(self, chat_id: int, method: str) -> list[dict]:
        cards = self.api.get(chat_id, '/cards')['cards']
        if method == CREDIT:
            return [c for c in cards if c['supports_credit']]
        if method == DEBIT:
            return [c for c in cards if c['supports_debit']]
        if method in ('VR', 'VA'):
            return [c for c in cards if c['card_type'] == method.lower()]
        return []

    def flow_ask(self, chat_id: int) -> None:
        f = self.flows[chat_id]
        step, data = f['step'], f['data']
        cancel = [btn('❌ Cancelar', 'f:cancel')]
        if step == 'description':
            self.tg.send(chat_id, '📝 O que foi? (ex.: <i>Mercado</i>, <i>Conta de luz</i>)', [cancel])
        elif step == 'amount':
            self.tg.send(chat_id, f'💰 Quanto custou <b>{h(data["description"])}</b>? (valor total, ex.: 87,50)', [cancel])
        elif step == 'method':
            self.tg.send(chat_id, 'Como foi pago?',
                         rows([btn(METHOD_LABEL[m], f'f:pm:{i}') for i, m in enumerate(METHODS)], 3) + [cancel])
        elif step == 'card':
            cards = self._cards_for(chat_id, data['payment_method'])
            if not cards:  # PIX/dinheiro ou nenhum cartão do tipo: pula a pergunta
                data['card_id'] = None
                return self.flow_next(chat_id)
            f['cards'] = {c['id']: c['label'] for c in cards}
            self.tg.send(chat_id, 'Qual cartão?', rows([btn(c['label'][:30], f'f:card:{c["id"]}') for c in cards[:12]], 1)
                         + [[btn('Sem cartão', 'f:card:0')], cancel])
        elif step == 'installments':
            self.tg.send(chat_id, 'Em quantas vezes?',
                         rows([btn('À vista' if n == 1 else f'{n}x', f'f:inst:{n}') for n in INSTALLMENTS], 4)
                         + [cancel])
        elif step == 'category':
            cats = self.api.get(chat_id, '/expenses/options')['categories']
            f['cats'] = cats
            suggestion = None
            if len(data['description']) >= 3:
                suggestion = self.api.get(chat_id, '/expenses/suggest-category',
                                          description=data['description']).get('category')
            buttons = ([btn(f'⭐ {suggestion}', f'f:cat:{cats.index(suggestion)}')] if suggestion in cats else [])
            buttons += [btn(c, f'f:cat:{i}') for i, c in enumerate(cats) if c != suggestion][:20]
            self.tg.send(chat_id, 'Categoria?', rows(buttons, 2) + [cancel])
        elif step == 'date':
            self.tg.send(chat_id, '📆 Quando foi? Toque ou digite a data (dd/mm).',
                         [[btn('Hoje', 'f:date:0'), btn('Ontem', 'f:date:1')], cancel])

    def flow_next(self, chat_id: int) -> None:
        """Avança para o próximo passo que ainda falta."""
        f = self.flows[chat_id]
        data = f['data']
        order = ['description', 'amount', 'method', 'card', 'installments', 'category', 'date']
        done = {'description': bool(data.get('description')), 'amount': bool(data.get('amount')),
                'method': bool(data.get('payment_method')), 'card': 'card_id' in data or f.get('from_ai'),
                'installments': 'installments' in data or data.get('payment_method') != CREDIT or f.get('from_ai'),
                'category': bool(data.get('category')), 'date': bool(data.get('date'))}
        pending = [s for s in order if not done[s]]
        if pending:
            f['step'] = pending[0]
            return self.flow_ask(chat_id)
        self.flows.pop(chat_id, None)
        view = {**data, 'card_label': f.get('cards', {}).get(data.get('card_id')),
                'installments': data.get('installments') if (data.get('installments') or 1) > 1 else None}
        body = {k: v for k, v in {'description': data['description'], 'amount': f'{data["amount"]:.2f}',
                                  'category': data['category'], 'payment_method': data['payment_method'],
                                  'date': data['date'], 'card_id': data.get('card_id') or None,
                                  'installments': view['installments'], 'user_id': data.get('user_id')}.items()
                if v is not None}
        self.confirm(chat_id, {**view, 'body': body})

    def flow_text(self, chat_id: int, text: str) -> None:
        f = self.flows[chat_id]
        f['ts'] = time.time()
        step, data = f['step'], f['data']
        if step == 'description':
            if re.search(r'\d', text) and len(text.split()) > 1:  # frase inteira: "almoço 25 no pix"
                self.flows.pop(chat_id, None)
                return self.free_text(chat_id, text)
            data['description'] = text[:200]
        elif step == 'amount':
            value = parse_amount(text)
            if value is None:
                return self.tg.send(chat_id, 'Não entendi o valor. Digite só o número, ex.: <b>87,50</b>')
            data['amount'] = value
        elif step == 'date':
            d = parse_date(text, date.today())
            if d is None:
                return self.tg.send(chat_id, 'Data inválida. Use dd/mm, ex.: <b>05/10</b>')
            data['date'] = d.isoformat()
        else:
            return self.tg.send(chat_id, 'Toque numa das opções acima (ou /cancelar).')
        self.flow_next(chat_id)

    # botões ──────────────────────────────────────────────────────────────────
    def _callback(self, cq: dict) -> None:
        msg = cq.get('message') or {}
        chat_id, message_id, data = msg.get('chat', {}).get('id'), msg.get('message_id'), cq.get('data') or ''
        if msg.get('chat', {}).get('type') != 'private':
            return self.tg.answer(cq['id'])
        kind, _, arg = data.partition(':')
        if kind == 'pay':
            r = self.api.post(chat_id, '/expenses/pay', {'ids': [int(arg)], 'paid': True})
            self.tg.answer(cq['id'], r.get('message', 'Pago!'))
            return self.bills(chat_id, message_id)
        if kind == 'bills':
            self.tg.answer(cq['id'])
            return self.bills(chat_id, message_id)
        if kind in ('ok', 'no'):
            return self._draft_answer(cq, kind, arg, chat_id, message_id)
        if kind == 'f':
            return self._flow_button(cq, arg, chat_id, message_id)
        self.tg.answer(cq['id'])

    def _draft_answer(self, cq, kind, draft_id, chat_id, message_id) -> None:
        draft = self.drafts.pop(draft_id, None)
        if draft is None or draft['chat'] != chat_id:
            self.tg.answer(cq['id'], 'Esse lançamento expirou.')
            return self.tg.edit(chat_id, message_id, '⌛ Lançamento expirado. Envie de novo.')
        if kind == 'no':
            self.tg.answer(cq['id'], 'Cancelado')
            return self.tg.edit(chat_id, message_id, '❌ Lançamento cancelado.')
        r = self.api.post(chat_id, '/expenses', draft['body'])
        self.tg.answer(cq['id'], 'Lançado!')
        first = (r.get('expenses') or [{}])[0]
        extra = ''
        if first.get('month') and (first.get('month'), first.get('year')) != (first.get('purchase_month'), first.get('purchase_year')):
            extra = f'\nEntrou na fatura de {first["month"]:02d}/{first["year"]} (vence {dm(first.get("due_date"))}).'
        self.tg.edit(chat_id, message_id, draft_text(draft['view'], '✅ ' + r.get('message', 'Lançado!')) + extra)

    def _flow_button(self, cq, arg, chat_id, message_id) -> None:
        self.tg.answer(cq['id'])
        f = self.flows.get(chat_id)
        if arg == 'cancel' or f is None:
            self.flows.pop(chat_id, None)
            return self.tg.edit(chat_id, message_id, 'Lançamento cancelado.' if arg == 'cancel'
                                else '⌛ Esse lançamento expirou. Use /lancar.')
        f['ts'] = time.time()
        field, _, value = arg.partition(':')
        data, chosen = f['data'], ''
        if field == 'pm' and f['step'] == 'method':
            data['payment_method'] = METHODS[int(value)]
            chosen = METHOD_LABEL[data['payment_method']]
        elif field == 'card' and f['step'] == 'card':
            data['card_id'] = int(value) or None
            chosen = f.get('cards', {}).get(data['card_id'], 'Sem cartão')
        elif field == 'inst' and f['step'] == 'installments':
            data['installments'] = int(value) if int(value) > 1 else None
            chosen = 'À vista' if int(value) == 1 else f'{value}x'
        elif field == 'cat' and f['step'] == 'category':
            data['category'] = f['cats'][int(value)]
            chosen = data['category']
        elif field == 'date' and f['step'] == 'date':
            data['date'] = (date.today() - timedelta(days=int(value))).isoformat()
            chosen = 'Hoje' if value == '0' else 'Ontem'
        else:
            return  # botão de um passo antigo
        self.tg.edit(chat_id, message_id, f'✔️ {h(chosen)}')
        self.flow_next(chat_id)
