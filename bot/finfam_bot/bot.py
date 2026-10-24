"""Conversa do bot: comandos, texto livre com a IA da família e roteamento dos fluxos guiados.

Nada é gravado sem o usuário tocar em ✅. Toda ação passa pela API do FinFam,
que só enxerga os dados da família de quem vinculou a conversa.

Módulos: `fmt` (texto/botões), `parsers` (valores e frases), `views` (respostas),
`flow` + `expense_flow` + `invest_flow` (passo a passo).
"""
import logging
import secrets
import time
from datetime import date

from finfam_bot.consts import TTL
from finfam_bot.expense_flow import ExpenseFlow
from finfam_bot.flow import Flow
from finfam_bot.fmt import btn, dm, h
from finfam_bot.http import ApiError, FinFam, Telegram
from finfam_bot.invest_flow import InvestFlow
from finfam_bot.parsers import parse_amount, parse_date, quick_parse  # noqa: F401  (usados pelos testes)
from finfam_bot.views import HELP, bills_view, draft_text, portfolio_text, summary_text

log = logging.getLogger('finfam.bot')

AI_DRAFT_FIELDS = ('description', 'payment_method', 'card_id', 'installments', 'category', 'date', 'user_id')


def _chat_id(update: dict) -> int | None:
    msg = update.get('message') or update.get('callback_query', {}).get('message') or {}
    return msg.get('chat', {}).get('id')


class Bot:
    def __init__(self, tg: Telegram, api: FinFam, site_url: str = ''):
        self.tg, self.api, self.site_url = tg, api, site_url
        self.flows: dict[int, dict] = {}     # chat → lançamento guiado em andamento
        self.drafts: dict[str, dict] = {}    # id → rascunho aguardando ✅
        self.cache: dict[tuple, tuple[float, object]] = {}  # (chat, recurso) → (quando, dados)
        self.expense, self.invest = ExpenseFlow(self), InvestFlow(self)
        self.guided: dict[str, Flow] = {f.kind: f for f in (self.expense, self.invest)}

    # entrada ─────────────────────────────────────────────────────────────────
    def handle(self, update: dict) -> None:
        self._expire()
        try:
            if 'callback_query' in update:
                self._callback(update['callback_query'])
            elif 'message' in update:
                self._message(update['message'])
        except ApiError as exc:
            if chat_id := _chat_id(update):
                self.tg.send(chat_id, self._api_error_text(exc))
        except Exception:  # um update ruim nunca derruba o bot
            log.exception('Falha ao processar update %s', update.get('update_id'))
            if chat_id := _chat_id(update):
                self.flows.pop(chat_id, None)
                self.tg.send(chat_id, '⚠️ Algo deu errado aqui. Nada foi lançado — tente de novo com /lancar.')

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
            for key, value in list(store.items()):  # list(): outras conversas mexem ao mesmo tempo
                if value['ts'] < limit:
                    store.pop(key, None)

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
                    '/lancar': lambda: self.linked(chat_id) and self.expense.start(chat_id),
                    '/cancelar': lambda: self.cancel(chat_id),
                    '/investir': lambda: self.linked(chat_id) and self.invest.start(chat_id),
                    '/carteira': lambda: self.portfolio(chat_id),
                    '/desconectar': lambda: self.unlink(chat_id)}
        if command in handlers:
            handlers[command]()
        elif chat_id in self.flows:
            self.flow_text(chat_id, text)
        elif text.startswith('/'):
            self.tg.send(chat_id, 'Comando desconhecido.\n\n' + HELP)
        else:
            self.free_text(chat_id, text)

    def linked(self, chat_id: int) -> bool:
        """Só abre passo a passo para quem está conectado (o erro vira o aviso de conectar)."""
        self.cached(chat_id, 'me', lambda: self.api.get(chat_id, '/auth/me'), ttl=300)
        return True

    def flow_text(self, chat_id: int, text: str) -> None:
        f = self.flows[chat_id]
        f['ts'] = time.time()
        self.guided[f.get('kind', 'expense')].text(chat_id, text)

    # comandos ────────────────────────────────────────────────────────────────
    def start(self, chat_id: int, code: str, sender: dict) -> None:
        if code:  # link do site: confirma de quem é a conta antes de conectar
            who = self.api.post(None, '/telegram/bot/peek', {'code': code[:64]})
            family = f' (família {h(who["family"])})' if who.get('family') else ''
            self.tg.send(chat_id, f'🔗 Conectar este Telegram à conta de <b>{h(who["user_name"])}</b>{family}?\n\n'
                                  'Quem conectar verá as contas e poderá lançar gastos dessa família.',
                         [[btn('✅ Conectar', f'lk:{code[:60]}'), btn('❌ Não', 'lk:')]])
            return
        try:
            me = self.api.get(chat_id, '/auth/me')
        except ApiError as exc:
            if exc.code != 'telegram_not_linked':
                raise
            self.tg.send(chat_id, '👋 Olá! Eu sou o bot do <b>FinFam</b>.\n\n' + self._how_to_link())
            return
        self.tg.send(chat_id, f'👋 Olá, {h(me["user"]["name"])}!\n\n{HELP}')

    def _link_answer(self, cq: dict, code: str, chat_id: int, message_id: int) -> None:
        self.tg.answer(cq['id'])
        if not code:
            return self.tg.edit(chat_id, message_id, 'Ok, não conectei. Se mudar de ideia, gere um novo link no site.')
        r = self.api.post(None, '/telegram/bot/link', {'code': code, 'chat_id': str(chat_id),
                                                        'username': (cq.get('from') or {}).get('username')})
        self.cache.pop((chat_id, 'me'), None)
        family = f' (família {h(r["family"])})' if r.get('family') else ''
        self.tg.edit(chat_id, message_id, f'✅ {h(r["message"])}{family}')
        self.tg.send(chat_id, HELP)

    def unlink(self, chat_id: int) -> None:
        self.api.delete(chat_id, '/telegram/link')
        self.flows.pop(chat_id, None)
        self.tg.send(chat_id, '👋 Telegram desconectado do FinFam. Para voltar, gere um novo link no site.')

    def cancel(self, chat_id: int) -> None:
        self.tg.send(chat_id, 'Lançamento cancelado.' if self.flows.pop(chat_id, None) else 'Nada em andamento.')

    def bills(self, chat_id: int, message_id: int | None = None, days: int = 7) -> None:
        text, markup = bills_view(self.api.get(chat_id, '/expenses/bills', days_ahead=days), days)
        if message_id:
            self.tg.edit(chat_id, message_id, text, markup)
        else:
            self.tg.send(chat_id, text, markup)

    def summary(self, chat_id: int) -> None:
        self.tg.send(chat_id, summary_text(self.api.get(chat_id, '/dashboard')))

    def portfolio(self, chat_id: int) -> None:
        self.tg.send(chat_id, portfolio_text(self.api.get(chat_id, '/investments')))

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
            self.ai_expense(chat_id, r['draft'])
        else:
            self.tg.send(chat_id, h(r.get('reply') or 'Não entendi.') + '\n\nVeja o que eu faço em /ajuda.')

    def ai_expense(self, chat_id: int, d: dict) -> None:
        if not d['missing']:
            return self.confirm(chat_id, d)
        # faltou valor/descrição: segue no passo a passo com o que a IA entendeu
        data = {k: d[k] for k in AI_DRAFT_FIELDS if d.get(k)}
        self.expense.begin(chat_id, data, 'amount' if d['description'] else 'description', from_ai=True)
        self.expense.ask_step(chat_id)

    def quick_flow(self, chat_id: int, text: str) -> bool:
        """Frase com valor ("almoço 25 no pix") → lançamento guiado só com o que falta."""
        data = quick_parse(text, date.today())
        if not data.get('amount') or not data.get('description'):
            return False
        self.expense.begin(chat_id, data, 'method')
        self.expense.next(chat_id)
        return True

    # utilidades dos fluxos ───────────────────────────────────────────────────
    def cached(self, chat_id, key: str, fetch, ttl: float = 300):
        """Guarda por alguns minutos o que muda pouco (cartões, categorias, listas)."""
        hit = self.cache.get((chat_id, key))
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        value = fetch()
        if len(self.cache) > 500:
            self.cache.clear()
        self.cache[(chat_id, key)] = (time.time(), value)
        return value

    def confirm(self, chat_id: int, d: dict, message_id: int | None = None, path: str = '/expenses',
                text: str | None = None) -> None:
        draft_id = secrets.token_hex(4)
        self.drafts[draft_id] = {'chat': chat_id, 'body': d['body'], 'view': d, 'path': path, 'ts': time.time()}
        buttons = [[btn('✅ Lançar' if path == '/expenses' else '✅ Registrar', f'ok:{draft_id}'),
                    btn('❌ Cancelar', f'no:{draft_id}')]]
        if message_id:
            self.tg.edit(chat_id, message_id, text or draft_text(d), buttons)
        else:
            self.tg.send(chat_id, text or draft_text(d), buttons)

    # botões ──────────────────────────────────────────────────────────────────
    def _callback(self, cq: dict) -> None:
        msg = cq.get('message') or {}
        chat_id, message_id, data = msg.get('chat', {}).get('id'), msg.get('message_id'), cq.get('data') or ''
        if msg.get('chat', {}).get('type') != 'private':
            return self.tg.answer(cq['id'])
        kind, _, arg = data.partition(':')
        if kind == 'lk':
            return self._link_answer(cq, arg, chat_id, message_id)
        if kind == 'pay':
            self.tg.answer(cq['id'], 'Marcando como paga…')  # tira o "carregando" do botão na hora
            self.api.post(chat_id, '/expenses/pay', {'ids': [int(arg)], 'paid': True})
            return self.bills(chat_id, message_id)
        if kind == 'bills':
            self.tg.answer(cq['id'])
            return self.bills(chat_id, message_id)
        if kind in ('ok', 'no'):
            return self._draft_answer(cq, kind, arg, chat_id, message_id)
        flow = next((f for f in self.guided.values() if f.prefix == kind), None)
        if flow:
            return flow.button(cq, arg, chat_id, message_id)
        self.tg.answer(cq['id'])

    def _draft_answer(self, cq, kind, draft_id, chat_id, message_id) -> None:
        draft = self.drafts.pop(draft_id, None)
        if draft is None or draft['chat'] != chat_id:
            self.tg.answer(cq['id'], 'Esse lançamento expirou.')
            return self.tg.edit(chat_id, message_id, '⌛ Lançamento expirado. Envie de novo.')
        if kind == 'no':
            self.tg.answer(cq['id'], 'Cancelado')
            return self.tg.edit(chat_id, message_id, '❌ Lançamento cancelado.')
        self.tg.answer(cq['id'], 'Registrando…')
        r = self.api.post(chat_id, draft.get('path', '/expenses'), draft['body'])
        if draft.get('path') == '/investments':
            return self.tg.edit(chat_id, message_id, f'✅ <b>{h(r.get("message", "Registrado!"))}</b>\n\n'
                                + draft['view']['text'] + '\n\nVeja a carteira em /carteira.')
        self.tg.edit(chat_id, message_id,
                     draft_text(draft['view'], '✅ ' + r.get('message', 'Lançado!')) + self._invoice_note(r))

    @staticmethod
    def _invoice_note(r: dict) -> str:
        """Aviso quando a compra no crédito entrou na fatura de outro mês."""
        first = (r.get('expenses') or [{}])[0]
        if first.get('month') and (first.get('month'), first.get('year')) != (first.get('purchase_month'),
                                                                               first.get('purchase_year')):
            return f'\nEntrou na fatura de {first["month"]:02d}/{first["year"]} (vence {dm(first.get("due_date"))}).'
        return ''
