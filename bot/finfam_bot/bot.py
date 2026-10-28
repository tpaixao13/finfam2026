"""Conversa do bot: comandos, texto livre com a IA da família e roteamento dos fluxos guiados.

Nada é gravado sem o usuário tocar em ✅. Toda ação passa pela API do FinFam,
que só enxerga os dados da família de quem vinculou a conversa.

Módulos: `fmt` (texto/botões), `parsers` (valores e frases), `views` (respostas),
`flow` + `expense_flow` + `invest_flow` (passo a passo).
"""
import base64
import logging
import re
import secrets
import time
from datetime import date

from finfam_bot.consts import TTL
from finfam_bot.expense_flow import ExpenseFlow
from finfam_bot.flow import Flow
from finfam_bot.fmt import btn, dm, h
from finfam_bot.http import ApiError, FinFam, Telegram
from finfam_bot.invest_flow import InvestFlow
from finfam_bot.parsers import parse_amount, parse_date, pick_person, quick_parse  # noqa: F401  (usados pelos testes)
from finfam_bot.review import Review
from finfam_bot.views import (HELP, MENU, agenda_text, balances_text, bank_text, bills_view, draft_text, goals_text,
                              invoices_text, months_back, portfolio_text, purchases_text, spending_months_text,
                              spending_text, summary_text, week_text)

# perguntas sobre os bancos conectados — respondidas sem IA
BANK_QUESTION = re.compile(
    r'(chegou|veio|vieram|tem|novidade|novo|import|revis|confer|bate|batendo|sincroniz|atualiz)\w*\b.{0,40}'
    r'\b(banco|bancos|importad\w*|open ?finance|pluggy|extrato do banco)\b'
    r'|\b(banco|bancos)\b.{0,30}\b(chegou|importou|revisar|bate|atualizou|sincronizou)\b'
    r'|^\s*(banco|bancos|importados)\s*\??\s*$'
    r'|\bfatura\b.{0,40}\bbate\b.{0,30}\bbanco\b|\bpara revisar\b'
    r'|\b(falta|faltam|pendente\w*)\b.{0,20}\brevis', re.I)
# investimentos e saldo das contas — também sem IA
INVEST_QUESTION = re.compile(r'\b(investiment\w*|investid[oa]s?|minha carteira|aplica[çc](ão|ões|oes)|meus? cdbs?|rendimento\w*)\b', re.I)
BALANCE_QUESTION = re.compile(
    r'\bsaldo\b.{0,25}\b(conta|contas|banco|bancos|c6|nubank|inter|caixa|santander|ita[uú]|bradesco)\b'
    r'|\bquanto (eu )?tenho\b.{0,20}\b(na conta|nas contas|no banco|nos bancos)\b', re.I)
_AMOUNT = re.compile(r'(?<![a-z])\d+(?:[.,]\d{1,2})?\b', re.I)  # valor solto ("25"), não o 6 de "C6"

log = logging.getLogger('finfam.bot')

MEDIA_MAX = 8 * 1024 * 1024  # foto de cupom / áudio (a API aceita até 8 MB)
AUDIO_TYPES = {'audio/ogg', 'audio/mpeg', 'audio/mp4', 'audio/wav'}
DOC_TYPES = {'image/jpeg', 'image/png', 'image/webp', 'application/pdf'}

AI_DRAFT_FIELDS = ('description', 'amount', 'payment_method', 'card_id', 'card_label', 'installments', 'category',
                   'date', 'user_id', 'user_name')


def _chat_id(update: dict) -> int | None:
    msg = update.get('message') or update.get('callback_query', {}).get('message') or {}
    return msg.get('chat', {}).get('id')


class Bot:
    def __init__(self, tg: Telegram, api: FinFam, site_url: str = ''):
        self.tg, self.api, self.site_url = tg, api, site_url
        self.flows: dict[int, dict] = {}     # chat → lançamento guiado em andamento
        self.drafts: dict[str, dict] = {}    # id → rascunho aguardando ✅
        self.cache: dict[tuple, tuple[float, object]] = {}  # (chat, recurso) → (quando, dados)
        self.reviews: dict[tuple, dict] = {}  # (chat, lançamento do banco) → categoria escolhida
        self.expense, self.invest = ExpenseFlow(self), InvestFlow(self)
        self.review = Review(self)
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
        for store in (self.flows, self.drafts, self.reviews):
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
        media = _media_of(msg)
        if media:
            return self.media(chat_id, *media, (msg.get('caption') or '').strip()[:500])
        if not text:
            return
        command, _, arg = text.partition(' ')
        command = command.split('@')[0].lower()
        handlers = {'/start': lambda: self.start(chat_id, arg.strip(), msg.get('from', {})),
                    '/ajuda': lambda: self.help(chat_id), '/help': lambda: self.help(chat_id),
                    '/contas': lambda: self.bills(chat_id), '/resumo': lambda: self.summary(chat_id),
                    '/lancar': lambda: self.linked(chat_id) and self.expense.start(chat_id),
                    '/cancelar': lambda: self.cancel(chat_id),
                    '/investir': lambda: self.linked(chat_id) and self.invest.start(chat_id),
                    '/carteira': lambda: self.portfolio(chat_id),
                    '/banco': lambda: self.bank(chat_id),
                    '/openfinance': lambda: self.bank(chat_id), '/open_finance': lambda: self.bank(chat_id),
                    '/agenda': lambda: self.agenda(chat_id), '/metas': lambda: self.goals(chat_id),
                    '/semana': lambda: self.week(chat_id), '/menu': lambda: self.menu(chat_id),
                    '/revisar': lambda: self.review.show(chat_id),
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
    def help(self, chat_id: int) -> None:
        """Ajuda + situação da IA da família (quem não está conectado vê só a ajuda)."""
        try:
            status = self.ai_status(chat_id)
        except ApiError:
            return self.tg.send(chat_id, HELP)  # não conectado: sem menu (os botões só dariam erro)
        line = (f'🤖 IA: <b>ligada</b> ({h(status["label"])})' if status['on'] else
                '🤖 IA: <b>desligada</b> — ligue em Telegram e IA no site.' if status['reason'] == 'off' else
                '🤖 IA: <b>não configurada</b> — o dono da família cadastra a chave em Telegram e IA no site.')
        self.tg.send(chat_id, f'{HELP}\n\n{line}', MENU)

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
        self.tg.send(chat_id, f'👋 Olá, {h(me["user"]["name"])}! O que você quer ver?', MENU)

    def _link_answer(self, cq: dict, code: str, chat_id: int, message_id: int) -> None:
        self.tg.answer(cq['id'])
        if not code:
            return self.tg.edit(chat_id, message_id, 'Ok, não conectei. Se mudar de ideia, gere um novo link no site.')
        r = self.api.post(None, '/telegram/bot/link', {'code': code, 'chat_id': str(chat_id),
                                                        'username': (cq.get('from') or {}).get('username')})
        self.cache.pop((chat_id, 'me'), None)
        family = f' (família {h(r["family"])})' if r.get('family') else ''
        self.tg.edit(chat_id, message_id, f'✅ {h(r["message"])}{family}')
        self.tg.send(chat_id, HELP, MENU)

    def unlink(self, chat_id: int) -> None:
        self.api.delete(chat_id, '/telegram/link')
        self.flows.pop(chat_id, None)
        self.tg.send(chat_id, '👋 Telegram desconectado do FinFam. Para voltar, gere um novo link no site.')

    def cancel(self, chat_id: int) -> None:
        self.tg.send(chat_id, 'Lançamento cancelado.' if self.flows.pop(chat_id, None) else 'Nada em andamento.')

    def bank(self, chat_id: int) -> None:
        """Bancos conectados (Open Finance): o que chegou, o que falta revisar e se a fatura bate."""
        try:
            summary = self.api.get(chat_id, '/pluggy/summary', status='pending', limit=20)
            check = self.api.get(chat_id, '/pluggy/invoice-check')
        except ApiError as exc:
            if exc.status != 403 or exc.code == 'telegram_not_linked':
                raise
            self.tg.send(chat_id, '🏦 O Open Finance (bancos conectados) não está liberado para a sua família.')
            return
        buttons = [[btn('📝 Revisar aqui no Telegram', 'rv:go:0')]] if summary['counts']['pending'] else None
        self.tg.send(chat_id, bank_text(summary, check, self.site_url, self.optional(chat_id, '/pluggy/balances'),
                                        self.optional(chat_id, '/pluggy/investments')), buttons)

    def optional(self, chat_id: int, path: str) -> dict | None:
        """Complemento que pode faltar (Open Finance desligado, API antiga): sem ele, a resposta segue."""
        try:
            return self.api.get(chat_id, path)
        except ApiError as exc:
            if exc.code == 'telegram_not_linked':
                raise
            return None

    def bills(self, chat_id: int, message_id: int | None = None, days: int = 7) -> None:
        text, markup = bills_view(self.api.get(chat_id, '/expenses/bills', days_ahead=days), days)
        if message_id:
            self.tg.edit(chat_id, message_id, text, markup)
        else:
            self.tg.send(chat_id, text, markup)

    def summary(self, chat_id: int) -> None:
        self.tg.send(chat_id, summary_text(self.api.get(chat_id, '/dashboard')))

    def menu(self, chat_id: int) -> None:
        self.linked(chat_id)
        self.tg.send(chat_id, 'O que você quer ver?', MENU)

    def agenda(self, chat_id: int) -> None:
        self.tg.send(chat_id, agenda_text(self.api.get(chat_id, '/expenses/agenda', days=7)))

    def goals(self, chat_id: int) -> None:
        self.tg.send(chat_id, goals_text(self.api.get(chat_id, '/goals')))

    def week(self, chat_id: int) -> None:
        self.tg.send(chat_id, week_text(self.api.get(chat_id, '/dashboard/week')))

    def balances(self, chat_id: int) -> None:
        self.tg.send(chat_id, balances_text(self.optional(chat_id, '/pluggy/balances')))

    def portfolio(self, chat_id: int) -> None:
        self.tg.send(chat_id, portfolio_text(self.api.get(chat_id, '/investments'),
                                             self.optional(chat_id, '/pluggy/investments')))

    # texto livre com IA ──────────────────────────────────────────────────────
    def ai_status(self, chat_id: int) -> dict:
        """IA da família: {'on': bool, 'reason': 'missing'|'off'|None, 'label': 'Gemini · modelo'}."""
        def fetch():
            cfg = self.api.get(chat_id, '/settings/ai')
            label = next((p['label'] for p in cfg.get('providers', []) if p['id'] == cfg.get('provider')), '')
            model = cfg.get('model') or next((p['default_model'] for p in cfg.get('providers', [])
                                              if p['id'] == cfg.get('provider')), '')
            reason = None if cfg['configured'] and cfg['enabled'] else ('off' if cfg['configured'] else 'missing')
            return {'on': reason is None, 'reason': reason, 'label': f'{label.split(" (")[0]} · {model}'}
        return self.cached(chat_id, 'ai', fetch, ttl=120)

    def ai_off_text(self, reason: str | None) -> str:
        why = ('🤖 A IA está <b>desligada</b> para a sua família (opção "Usar IA no bot").' if reason == 'off'
               else '🤖 A IA <b>não está configurada</b> para a sua família.')
        return (f'{why} Por isso não consigo entender frases livres.\n\n'
                '• <b>Para ativar:</b> no site, abra <b>Telegram e IA</b> e cadastre a chave '
                '(só o dono da família pode).\n'
                '• <b>Sem IA:</b> use /lancar, ou escreva no formato simples: <b>almoço 25 no pix</b>.')

    def free_text(self, chat_id: int, text: str) -> None:
        if not _AMOUNT.search(text):  # perguntas que não precisam de IA
            if BALANCE_QUESTION.search(text):
                self.balances(chat_id)
                return
            if INVEST_QUESTION.search(text):
                self.portfolio(chat_id)
                return
            if BANK_QUESTION.search(text):  # "chegou algo do banco?"
                self.bank(chat_id)
                return
        status = self.ai_status(chat_id)
        if not status['on']:  # verifica antes: sem IA, só o formato simples
            if not self.quick_flow(chat_id, text):
                self.tg.send(chat_id, self.ai_off_text(status['reason']))
            return
        self.tg.typing(chat_id)
        try:
            r = self.api.post(chat_id, '/ai/interpret', {'text': text[:500]})
        except ApiError as exc:
            if exc.code != 'ai_not_configured' and exc.status not in (502, 0):
                raise
            # sem IA (ou IA fora do ar): tenta entender sozinho frases simples
            if self.quick_flow(chat_id, text):
                return
            if exc.code == 'ai_not_configured':  # desligaram a IA há pouco (o status fica 2 min em cache)
                self.cache.pop((chat_id, 'ai'), None)
                self.tg.send(chat_id, self.ai_off_text('missing'))
                return
            raise
        self.answer_ai(chat_id, r, text)

    def media(self, chat_id: int, file_id: str, mime: str, size: int, caption: str) -> None:
        """Foto do cupom/comprovante ou áudio: a IA da família lê e propõe o lançamento (confirma com ✅)."""
        status = self.ai_status(chat_id)
        if not status['on']:
            return self.tg.send(chat_id, self.ai_off_text(status['reason']).replace(
                'não consigo entender frases livres', 'não consigo ler fotos nem áudios'))
        if size > MEDIA_MAX:
            return self.tg.send(chat_id, '📎 Arquivo grande demais (máximo 8 MB). Mande uma foto só do cupom.')
        self.tg.typing(chat_id)
        data = self.tg.download(file_id, MEDIA_MAX)
        if data is None:
            return self.tg.send(chat_id, '📎 Não consegui baixar o arquivo do Telegram. Tente mandar de novo.')
        r = self.api.post(chat_id, '/ai/interpret-media',
                          {'mime': mime, 'data': base64.b64encode(data).decode(), 'caption': caption})
        if r['intent'] == 'expense' and not r.get('draft'):
            return self.tg.send(chat_id, '🤔 Não achei um gasto aí. Mande a foto do cupom/comprovante mais de perto.')
        self.answer_ai(chat_id, r, caption)

    def answer_ai(self, chat_id: int, r: dict, text: str) -> None:
        """Responde ao que a IA entendeu (de texto, foto ou áudio)."""
        intent, q = r['intent'], r.get('query') or {}
        if intent == 'bills':
            self.bills(chat_id, days=q.get('days') or 7)
        elif intent == 'spending':
            self.spending(chat_id, q)
        elif intent == 'purchases':
            self.purchases(chat_id, q)
        elif intent == 'invoices':
            filters = {k: q[k] for k in ('card_id', 'bank') if q.get(k)}
            r = self.api.get(chat_id, '/cards/invoices', month=q['month'], year=q['year'], **filters)
            self.tg.send(chat_id, invoices_text(r, q.get('bank')))
        elif intent == 'summary':
            self.summary(chat_id)
        elif intent == 'investments':
            if _AMOUNT.search(text):  # "investi 500 no CDB": abre o passo a passo de registrar
                self.invest.start(chat_id)
            else:
                self.portfolio(chat_id)
        elif intent == 'balance':
            self.balances(chat_id)
        elif intent == 'bank':
            self.bank(chat_id)
        elif intent == 'expense' and r.get('draft'):
            self.ai_expense(chat_id, r['draft'])
        else:
            self.tg.send(chat_id, h(r.get('reply') or 'Não entendi.') + '\n\nVeja o que eu faço em /ajuda.')

    def _search(self, chat_id: int, **filters) -> dict:
        clean = {k: v for k, v in filters.items() if v not in (None, '')}
        return self.api.get(chat_id, '/expenses', per_page=15, **clean)

    def spending(self, chat_id: int, q: dict) -> None:
        if (q.get('months') or 1) > 1:  # "nos últimos meses": um total por mês
            rows = [(m, y, self._search(chat_id, month=m, year=y, category=q.get('category'), search=q.get('text'),
                                        payment_method=q.get('payment_method')))
                    for m, y in months_back(q['month'], q['year'], q['months'])]
            return self.tg.send(chat_id, spending_months_text(q, rows))
        r = self._search(chat_id, month=q['month'], year=q['year'], category=q.get('category'), search=q.get('text'),
                         payment_method=q.get('payment_method'))
        self.tg.send(chat_id, spending_text(q, r))

    def purchases(self, chat_id: int, q: dict) -> None:
        r = self._search(chat_id, purchase_from=q['date_from'], purchase_to=q['date_to'],
                         category=q.get('category'), search=q.get('text'))
        self.tg.send(chat_id, purchases_text(q, r))

    def ai_expense(self, chat_id: int, d: dict) -> None:
        """O que a IA entendeu entra no passo a passo: pergunta só o que falta
        (valor, banco, se já está pago) e depois mostra a confirmação."""
        data = {k: d[k] for k in AI_DRAFT_FIELDS if d.get(k)}
        self.expense.begin(chat_id, data, '', from_ai=True)
        self.expense.next(chat_id)

    def quick_flow(self, chat_id: int, text: str) -> bool:
        """Frase com valor ("almoço 25 no pix") → lançamento guiado só com o que falta.
        "… pra Greyce" lança para a pessoa da família com esse nome."""
        members = self.cached(chat_id, 'me', lambda: self.api.get(chat_id, '/auth/me'), ttl=300).get('members', [])
        person, text = pick_person(text, members)
        data = quick_parse(text, date.today())
        if person:
            data.update(user_id=person['id'], user_name=person['name'])
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
        if kind == 'm':
            return self._menu_answer(cq, arg, chat_id)
        if kind == 'rv':
            return self.review.button(cq, arg, chat_id, message_id)
        if kind in ('ok', 'no'):
            return self._draft_answer(cq, kind, arg, chat_id, message_id)
        flow = next((f for f in self.guided.values() if f.prefix == kind), None)
        if flow:
            return flow.button(cq, arg, chat_id, message_id)
        self.tg.answer(cq['id'])

    def _menu_answer(self, cq: dict, item: str, chat_id: int) -> None:
        self.tg.answer(cq['id'])
        actions = {'contas': self.bills, 'agenda': self.agenda, 'resumo': self.summary, 'semana': self.week,
                   'metas': self.goals, 'carteira': self.portfolio, 'banco': self.bank,
                   'revisar': self.review.show,
                   'lancar': lambda c: self.linked(c) and self.expense.start(c)}
        if item in actions:
            actions[item](chat_id)

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


def _media_of(msg: dict) -> tuple[str, str, int] | None:
    """(file_id, tipo, tamanho) da foto/áudio/arquivo da mensagem, se for algo que a IA lê."""
    if msg.get('photo'):  # várias resoluções: a maior que não passe de 8 MB
        sizes = [p for p in msg['photo'] if (p.get('file_size') or 0) <= MEDIA_MAX] or msg['photo'][:1]
        best = sizes[-1]
        return best['file_id'], 'image/jpeg', best.get('file_size') or 0
    for key in ('voice', 'audio'):
        if msg.get(key):
            f = msg[key]
            mime = f.get('mime_type') or 'audio/ogg'
            mime = 'audio/mpeg' if mime == 'audio/mp3' else mime
            if mime in AUDIO_TYPES:
                return f['file_id'], mime, f.get('file_size') or 0
    doc = msg.get('document')
    if doc and doc.get('mime_type') in DOC_TYPES:
        return doc['file_id'], doc['mime_type'], doc.get('file_size') or 0
    return None
