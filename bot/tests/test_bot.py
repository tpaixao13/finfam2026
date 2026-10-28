"""Bot de ponta a ponta: API real (uvicorn, banco temporário) + Telegram falso."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'bot'))

from finfam_bot.bot import Bot, parse_amount, parse_date, pick_person, quick_parse  # noqa: E402
from finfam_bot.http import FinFam  # noqa: E402

KEY = 'chave-de-teste-do-bot-0123456789abcdefXYZ'


class FakeTelegram:
    def __init__(self):
        self.sent, self.edits, self.answers, self.log = [], [], [], []
        self._mid = 100

    def send(self, chat_id, text, buttons=None):
        self._mid += 1
        self.sent.append({'chat': chat_id, 'text': text, 'buttons': buttons or [], 'id': self._mid})
        self.log.append(self.sent[-1])

    def edit(self, chat_id, message_id, text, buttons=None):
        self.edits.append({'chat': chat_id, 'id': message_id, 'text': text, 'buttons': buttons or []})
        self.log.append(self.edits[-1])

    def answer(self, callback_id, text=None):
        self.answers.append(text)

    def typing(self, chat_id):
        pass

    def download(self, file_id, max_bytes):
        self.downloaded = file_id
        return b'\xff\xd8\xff-foto-do-cupom'

    @property
    def last(self):
        """Última mensagem mostrada (nova ou editada)."""
        return self.log[-1]

    def button(self, prefix, msg=None):
        for row in (msg or self.last)['buttons']:
            for b in row:
                if b['text'].startswith(prefix) or b['callback_data'].startswith(prefix):
                    return b['callback_data']
        raise AssertionError(f'botão {prefix!r} não encontrado em {(msg or self.last)["buttons"]}')


def _free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _call(base, method, path, body=None, token=None):
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Content-Type': 'application/json',
                                          **({'Authorization': f'Bearer {token}'} if token else {})})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


@pytest.fixture(scope='module')
def api_url():
    tmp = tempfile.mkdtemp(prefix='finfam-bot-')
    port = _free_port()
    env = {**os.environ, 'DATABASE_URL': f'sqlite:///{tmp}/t.db', 'SECRET_KEY': 's' * 40, 'SCHEDULER_ENABLED': '0',
           'RATE_LIMIT_ENABLED': '0', 'BOT_API_KEY': KEY, 'TELEGRAM_BOT_TOKEN': '1:x',
           'TELEGRAM_BOT_USERNAME': 'TesteBot', 'FRONTEND_DIST': '', 'UPLOAD_DIR': f'{tmp}/up',
           'ADMIN_INITIAL_PASSWORD': 'admin-123456', 'NO_PROXY': '*', 'no_proxy': '*'}
    proc = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--port', str(port)],
                            cwd=ROOT / 'backend', env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f'http://127.0.0.1:{port}/api/v1'
    for _ in range(100):
        try:
            _call(base, 'GET', '/health')
            break
        except OSError:
            time.sleep(0.1)
    os.environ['NO_PROXY'] = os.environ['no_proxy'] = '*'
    yield base
    proc.terminate()
    proc.wait(5)


@pytest.fixture(scope='module')
def family(api_url):
    r = _call(api_url, 'POST', '/auth/register', {'name': 'Tiago', 'email': 'tiago@bot.test',
                                                  'password': 'senha-segura', 'confirm_password': 'senha-segura'})
    token = r['access_token']
    _call(api_url, 'POST', '/cards', {'supports_credit': True, 'last_digits': '4321', 'bank': 'Inter',
                                      'due_day': 10, 'best_buy_day': 3}, token)
    _call(api_url, 'POST', '/expenses', {'description': 'Conta de luz', 'amount': '150', 'category': 'Moradia',
                                         'payment_method': 'PIX', 'date': date.today().isoformat()}, token)
    return token


def _msg(chat, text, chat_type='private'):
    return {'update_id': 1, 'message': {'chat': {'id': chat, 'type': chat_type}, 'from': {'id': chat, 'username': 'u'},
                                        'text': text}}


def _click(chat, data, message_id=None):
    return {'update_id': 2, 'callback_query': {'id': 'cb', 'data': data, 'from': {'id': chat},
                                               'message': {'chat': {'id': chat, 'type': 'private'},
                                                           'message_id': message_id or 101}}}


@pytest.fixture()
def bot(api_url):
    tg = FakeTelegram()
    return Bot(tg, FinFam(api_url, KEY), 'https://finfam2026.exemplo'), tg


def test_parsers():
    assert parse_amount('R$ 1.234,56') == 1234.56 and parse_amount('87,5') == 87.5 and parse_amount('87.50') == 87.5
    assert parse_amount('1.000') == 1000 and parse_amount('2.500.000') == 2500000 and parse_amount('10.5') == 10.5
    assert parse_amount('abc') is None and parse_amount('0') is None
    today = date(2026, 10, 23)
    assert parse_date('05/10', today) == date(2026, 10, 5) and parse_date('ontem', today) == date(2026, 10, 22)
    assert parse_date('31/02', today) is None
    # 23/10/2026 é sexta-feira
    assert [parse_date(x, today) for x in ('anteontem', 'dia 5', '30', 'dia 31', 'sexta', 'Sábado', 'segunda-feira')] == [
        date(2026, 10, 21), date(2026, 10, 5), date(2026, 9, 30), date(2026, 8, 31), date(2026, 10, 23),
        date(2026, 10, 17), date(2026, 10, 19)]
    assert parse_date('5', date(2026, 1, 3)) == date(2025, 12, 5)
    assert quick_parse('Gastei 25 no almoço no pix', today) == {'amount': 25.0, 'payment_method': 'PIX', 'description': 'Almoço'}
    assert quick_parse('mercado R$ 300,00 no crédito em 3x', today) == {
        'amount': 300.0, 'payment_method': 'Cartão de Crédito', 'installments': 3, 'description': 'Mercado'}
    assert quick_parse('uber 18,90 ontem', today)['date'] == '2026-10-22'


def test_unlinked_chat_gets_instructions(bot):
    b, tg = bot
    b.handle(_msg(10, '/contas'))
    assert 'não está conectado' in tg.last['text'] and 'Conectar Telegram' in tg.last['text']


def test_group_chats_are_refused(bot, family):
    b, tg = bot
    b.handle(_msg(-500, '/contas', 'group'))
    assert 'conversa privada' in tg.last['text']


def test_full_flow(bot, api_url, family):
    b, tg = bot
    code = _call(api_url, 'POST', '/telegram/link-code', token=family)['code']
    b.handle(_msg(20, f'/start {code}'))
    assert 'Conectar este Telegram à conta de <b>Tiago</b>' in tg.last['text']
    b.handle(_msg(20, '/contas'))  # ainda não conectou
    assert 'não está conectado' in tg.last['text']
    b.handle(_click(20, tg.button('✅ Conectar', tg.sent[-2])))
    assert 'conectado' in tg.edits[-1]['text'] and 'Tiago' in tg.edits[-1]['text']

    # contas a pagar + botão pagar
    b.handle(_msg(20, '/contas'))
    assert 'Conta de luz' in tg.last['text']
    b.handle(_click(20, tg.button('pay:')))
    assert 'Tudo em dia' in tg.edits[-1]['text']

    # lançamento guiado no crédito, 3x
    b.handle(_msg(20, '/lancar'))
    b.handle(_msg(20, 'Mercado'))
    b.handle(_msg(20, '300,00'))
    b.handle(_click(20, tg.button('Crédito')))
    b.handle(_click(20, tg.button('f:card:', tg.last)))
    b.handle(_click(20, tg.button('3x')))
    b.handle(_click(20, tg.button('Alimentação')))
    b.handle(_click(20, tg.button('Hoje')))
    assert 'Essa despesa já está paga?' in tg.last['text'] and 'fatura' in tg.last['text']
    b.handle(_click(20, tg.button('⏳ Não')))
    assert 'Confere' in tg.last['text'] and '3x de R$ 100,00' in tg.last['text'] and 'Inter' in tg.last['text']
    assert 'Banco:' not in tg.last['text'] and 'pendente' in tg.last['text']  # banco vem do cartão
    b.handle(_click(20, tg.button('ok:')))
    assert '✅' in tg.edits[-1]['text']
    y = date.today().year
    found = sum(_call(api_url, 'GET', f'/expenses?search=Mercado&month=0&year={yr}', token=family)['total']
                for yr in (y, y + 1))
    assert found == 3  # 3 parcelas (podem virar o ano)

    # frase inteira depois do /lancar, sem IA: entende valor, forma e descrição
    b.handle(_msg(20, '/lancar'))
    b.handle(_msg(20, 'Gastei 25 no almoço no pix'))
    assert tg.last['text'].startswith('🔁 Essa conta se repete todo mês?')
    b.handle(_click(20, tg.button('Não, só esta')))
    assert '🏦 Qual banco?' in tg.last['text']
    b.handle(_click(20, tg.button('Nubank')))
    b.handle(_click(20, tg.button('Alimentação')))
    b.handle(_click(20, tg.button('Hoje')))
    b.handle(_click(20, tg.button('✅ Sim')))
    assert '<b>Almoço</b> — R$ 25,00' in tg.last['text'] and 'PIX' in tg.last['text']
    assert 'Banco: Nubank' in tg.last['text'] and 'Situação: ✅ pago' in tg.last['text']
    assert tg.last in tg.edits  # a confirmação reaproveita a mensagem dos botões
    b.handle(_click(20, tg.button('ok:')))
    assert '✅' in tg.edits[-1]['text']
    almoco = _call(api_url, 'GET', '/expenses?search=Almo&month=0', token=family)['items'][0]
    assert (almoco['bank'], almoco['paid']) == ('Nubank', True)

    # conta recorrente no débito: digita 6 meses → 6 lançamentos, só o 1º pago
    b.handle(_msg(20, '/lancar'))
    b.handle(_msg(20, 'Academia'))
    b.handle(_msg(20, '99,90'))
    b.handle(_click(20, tg.button('Débito')))
    if 'Qual cartão?' in tg.last['text']:
        b.handle(_click(20, 'f:card:0'))
    b.handle(_msg(20, '1'))  # inválido: pede de novo
    assert 'de <b>2</b> a <b>120</b>' in tg.last['text']
    b.handle(_msg(20, '6'))
    b.handle(_click(20, 'f:bank:-'))
    b.handle(_click(20, 'f:cat:0'))
    b.handle(_click(20, tg.button('Hoje')))
    b.handle(_click(20, tg.button('✅ Sim')))
    assert 'Repete: todo mês, por 6 meses' in tg.last['text']
    b.handle(_click(20, tg.button('ok:')))
    y = date.today().year
    gym = [e for yr in (y, y + 1) for e in _call(api_url, 'GET', f'/expenses?search=Academia&month=0&year={yr}&per_page=50',
                                                   token=family)['items']]
    assert len(gym) == 6 and sum(e['paid'] for e in gym) == 1

    # texto livre sem IA: frase com valor pergunta só o que falta; sem valor orienta
    b.handle(_msg(20, 'uber 18,90'))
    assert tg.last['text'] == 'Como foi pago?'
    b.handle(_msg(20, '/cancelar'))
    b.handle(_msg(20, 'bom dia'))
    assert 'não está configurada' in tg.last['text'] and '/lancar' in tg.last['text']
    b.handle(_msg(20, '/ajuda'))
    assert 'IA: <b>não configurada</b>' in tg.last['text']

    # passo a passo escolhendo PIX nos botões (não pergunta cartão)
    b.handle(_msg(20, '/lancar'))
    b.handle(_msg(20, 'Padaria'))
    b.handle(_msg(20, '12'))
    b.handle(_click(20, tg.button('PIX')))
    assert tg.last['text'].startswith('✔️ PIX\n\n🔁 Essa conta se repete todo mês?')
    b.handle(_msg(20, '/resumo'))
    assert 'Resumo de' in tg.last['text']
    # investimento em dólar pelo passo a passo + carteira
    sent_before = len(tg.sent)
    b.handle(_msg(20, '/investir'))
    b.handle(_click(20, tg.button('Outros')))
    b.handle(_click(20, tg.button('US$')))
    assert len(tg.sent) == sent_before + 1  # botões editam a mesma mensagem
    b.handle(_msg(20, '1.000'))
    if 'cotação' in tg.last['text']:  # sem internet aqui: o bot pergunta a cotação
        b.handle(_msg(20, '5,40'))
    b.handle(_msg(20, '4,5'))
    assert 'Confere o investimento' in tg.last['text'] and 'US$ 1.000,00' in tg.last['text']
    b.handle(_click(20, tg.button('ok:')))
    assert 'Registrado' in tg.last['text'] or 'registrado' in tg.last['text'], tg.last['text']
    inv = _call(api_url, 'GET', '/investments', token=family)['fixed']
    assert [(i['currency'], i['amount'], i['annual_rate']) for i in inv] == [('USD', 1000, 4.5)]
    b.handle(_msg(20, '/carteira'))
    assert 'Carteira' in tg.last['text'] and 'USD 1.000,00' in tg.last['text'] and 'R$ 5.400,00' in tg.last['text']

    b.handle(_msg(20, '/desconectar'))
    b.handle(_msg(20, '/contas'))
    assert 'não está conectado' in tg.last['text']


def test_expired_or_foreign_draft_is_rejected(bot):
    b, tg = bot
    b.drafts['abc'] = {'chat': 999, 'body': {}, 'view': {}, 'ts': time.time()}
    b.handle(_click(20, 'ok:abc'))
    assert 'expirou' in tg.answers[-1]


class StubApi:
    """Simula a API para o caminho da IA (o provedor de verdade fica fora do teste)."""
    def __init__(self, answer, ai=(True, True)):
        self.answer, self.posted, self.ai, self.interpreted = answer, [], ai, 0

    def get(self, chat_id, path, **query):
        self.gets = getattr(self, 'gets', []) + [(path, query)]
        if path == '/expenses':
            items = [{'description': 'Mercado <Extra>', 'amount': 120.5, 'purchase_date': '2026-10-20', 'user_name': 'Tiago'}]
            return {'items': items, 'total': 3, 'total_amount': 300.0}
        if path == '/cards/invoices' and query.get('bank'):
            return {'month': 11, 'year': 2026, 'total': 17.25, 'paid_total': 0, 'pending_total': 17.25, 'filtered': True,
                    'invoices': [{'label': 'Caixa Econômica Federal', 'due_date': '2026-11-15', 'status': 'aberta',
                                  'total': 17.25, 'pending_total': 17.25, 'count': 1}]}
        if path == '/cards/invoices':
            return {'month': 11, 'year': 2026, 'total': 350.0, 'paid_total': 100.0, 'pending_total': 250.0,
                    'invoices': [{'label': 'Inter <conta>', 'due_date': '2026-11-15', 'status': 'aberta', 'total': 300.0},
                                 {'label': 'Crédito sem cartão', 'due_date': None, 'status': 'paga', 'total': 50.0}]}
        if path == '/expenses/bills':
            return {'total': 0, 'totals': {}, 'overdue': [], 'today': [], 'upcoming': []}
        if path == '/expenses/options':
            return {'categories': ['Alimentação', 'Outros'], 'banks': ['Inter']}
        if path == '/meta':
            return {'banks': ['Nubank', 'Inter', 'Outros']}
        if path == '/expenses/suggest-category':
            return {'category': 'Alimentação'}
        if path == '/pluggy/summary':
            return {'counts': {'pending': 2, 'imported': 5, 'duplicate': 0, 'ignored': 1},
                    'banks': [{'name': 'C6 Bank', 'status': 'UPDATED', 'auto_import': False,
                               'last_updated_at': '2026-10-27T12:47:00Z'}],
                    'items': [{'date': '2026-10-24', 'description': 'COOP <DIADEMA>', 'amount': 38.78},
                              {'date': '2026-10-23', 'description': 'UBER', 'amount': 11.22}]}
        if path == '/pluggy/invoice-check':
            return {'month': 11, 'year': 2026, 'cards': [
                {'label': 'C6 Bank •••• 0982', 'linked': True, 'bank_total': 7264.41, 'finfam_total': 5865.6, 'diff': -1398.81},
                {'label': 'Santander •••• 0676', 'linked': True, 'bank_total': 1597.76, 'finfam_total': 1597.76, 'diff': 0}]}
        if path == '/pluggy/balances':
            return {'enabled': True, 'total': 1832.44, 'items': [{'bank': 'C6 Bank', 'last_digits': '5665', 'balance': 1832.44}]}
        if path == '/pluggy/investments':
            return {'enabled': True, 'total': 3839.92, 'items': [
                {'name': 'CDB - BANCO C6 S.A.', 'bank': 'C6 Bank', 'balance': 3839.92, 'currency': 'BRL'}]}
        if path == '/investments':
            return {'fixed': [], 'crypto': [], 'fixed_total': 0, 'crypto_total': 0}
        if path == '/auth/me':
            return {'user': {'id': 1, 'name': 'Tiago'}, 'members': [{'id': 1, 'name': 'Tiago'},
                                                                   {'id': 2, 'name': 'Greyce Paixão'}]}
        assert path == '/settings/ai', path
        return {'configured': self.ai[0], 'enabled': self.ai[1], 'provider': 'gemini', 'model': None,
                'providers': [{'id': 'gemini', 'label': 'Gemini (Google)', 'default_model': 'gemini-flash-lite-latest'}]}

    def post(self, chat_id, path, body=None):
        if path in ('/ai/interpret', '/ai/interpret-media'):
            self.interpreted += 1
            self.media = body if path == '/ai/interpret-media' else None
            return self.answer
        self.posted.append((path, body))
        return {'message': 'Despesa lançada!', 'expenses': [{}]}


def test_ai_draft_confirm_and_missing_amount():
    tg = FakeTelegram()
    d = {'description': 'Uber', 'amount': 23.9, 'payment_method': 'PIX', 'category': 'Transporte',
         'date': '2026-10-23', 'card_label': None, 'installments': None, 'user_name': None, 'missing': [],
         'body': {'description': 'Uber', 'amount': '23.90'}}
    b = Bot(tg, StubApi({'intent': 'expense', 'draft': d, 'reply': ''}))
    b.handle(_msg(30, 'uber 23,90'))
    b.handle(_click(30, 'f:rec:0'))
    assert 'Qual banco?' in tg.last['text']
    assert tg.last['buttons'][0][0]['text'] == 'Inter'  # bancos já usados pela família primeiro
    b.handle(_click(30, 'f:bank:-'))
    b.handle(_click(30, 'f:paid:0'))
    assert 'Uber' in tg.last['text'] and 'R$ 23,90' in tg.last['text'] and 'pendente' in tg.last['text']
    b.handle(_click(30, tg.button('ok:')))
    assert b.api.posted == [('/expenses', {'description': 'Uber', 'amount': '23.90', 'category': 'Transporte',
                                           'payment_method': 'PIX', 'date': '2026-10-23', 'paid': False})]

    b2 = Bot(tg, StubApi({'intent': 'expense', 'reply': '', 'draft': {**d, 'amount': None, 'missing': ['amount']}}))
    b2.handle(_msg(31, 'comprei uber'))
    assert 'Quanto custou' in tg.last['text'] and b2.flows[31]['step'] == 'amount'


def test_link_can_be_declined(bot, api_url, family):
    b, tg = bot
    code = _call(api_url, 'POST', '/telegram/link-code', token=family)['code']
    b.handle(_msg(40, f'/start {code}'))
    b.handle(_click(40, 'lk:'))
    assert 'não conectei' in tg.edits[-1]['text']
    b.handle(_msg(40, '/contas'))
    assert 'não está conectado' in tg.last['text']


def test_unlinked_chat_cannot_open_guided_flows(bot):
    b, tg = bot
    for cmd in ('/lancar', '/investir'):
        b.handle(_msg(12, cmd))
        assert 'não está conectado' in tg.last['text'] and 12 not in b.flows


def test_dispatcher_keeps_order_per_chat_and_runs_chats_in_parallel():
    import threading
    from finfam_bot.__main__ import Dispatcher
    seen, slow_started, fast_done = [], threading.Event(), threading.Event()

    def handle(update):
        chat, n = update['message']['chat']['id'], update['n']
        if chat == 1 and n == 0:
            slow_started.set()
            assert fast_done.wait(5)  # outra conversa termina enquanto esta espera
        seen.append((chat, n))
        if chat == 2:
            fast_done.set()
    d = Dispatcher(handle, workers=4)
    for n in range(3):
        d.submit({'n': n, 'message': {'chat': {'id': 1}}})
    d.submit({'n': 0, 'message': {'chat': {'id': 2}}})
    d.pool.shutdown(wait=True)
    assert [x for x in seen if x[0] == 1] == [(1, 0), (1, 1), (1, 2)] and seen[0] == (2, 0)


def test_ai_checked_before_calling_it():
    tg = FakeTelegram()
    b = Bot(tg, StubApi({'intent': 'chat', 'reply': 'oi'}, ai=(True, False)))  # cadastrada, mas desligada
    b.handle(_msg(50, 'quanto gastei com mercado?'))
    assert 'desligada' in tg.last['text'] and b.api.interpreted == 0
    b.handle(_msg(50, 'almoço 25 no pix'))  # formato simples segue funcionando sem IA
    assert 'se repete' in tg.last['text'] and b.api.interpreted == 0
    b2 = Bot(tg, StubApi({'intent': 'chat', 'reply': 'Posso lançar gastos.'}))
    b2.handle(_msg(51, '/ajuda'))
    assert 'IA: <b>ligada</b> (Gemini · gemini-flash-lite-latest)' in tg.last['text']
    b2.handle(_msg(51, 'oi'))
    assert b2.api.interpreted == 1 and 'Posso lançar gastos.' in tg.last['text']


def test_other_date_button_asks_to_type():
    tg = FakeTelegram()
    b = Bot(tg, StubApi({}, ai=(False, False)))
    b.handle(_msg(60, 'almoço 25 no pix'))
    b.handle(_click(60, 'f:rec:0'))
    b.handle(_click(60, 'f:bank:-'))
    b.handle(_click(60, 'f:cat:0'))
    assert tg.button('Anteontem') and tg.button('📅 Outra data')
    b.handle(_click(60, 'f:date:x'))
    assert 'Digite a data' in tg.last['text'] and b.flows[60]['step'] == 'date'
    b.handle(_msg(60, 'dia 5'))
    b.handle(_click(60, 'f:paid:1'))
    assert 'Confere o lançamento' in tg.last['text'] and 'Data: 05/' in tg.last['text']
    b.handle(_msg(61, 'uber 10 no pix'))
    b.handle(_click(61, 'f:rec:0'))
    b.handle(_click(61, 'f:bank:0'))
    b.handle(_click(61, 'f:cat:0'))
    b.handle(_msg(61, 'amanhã de manhã'))
    assert 'Não entendi a data' in tg.last['text']


def test_ai_queries_spending_purchases_and_bills_days():
    tg = FakeTelegram()
    q = {'days': 7, 'category': 'Alimentação', 'text': None, 'month': 10, 'year': 2026, 'date_from': None, 'date_to': None}
    b = Bot(tg, StubApi({'intent': 'spending', 'query': q, 'reply': ''}))
    b.handle(_msg(70, 'quanto gastei de alimento esse mes'))
    assert ('/expenses', {'per_page': 15, 'month': 10, 'year': 2026, 'category': 'Alimentação'}) in b.api.gets
    assert '<b>Alimentação</b> em outubro/2026: <b>R$ 300,00</b> em 3' in tg.last['text']
    assert 'Mercado &lt;Extra&gt;' in tg.last['text'] and 'e mais 2' in tg.last['text']

    q6 = {**q, 'category': None, 'text': 'DEBITO PRESTACAO HAB', 'months': 3}
    b6 = Bot(tg, StubApi({'intent': 'spending', 'query': q6, 'reply': ''}))
    b6.handle(_msg(73, 'gastos com DEBITO PRESTACAO HAB nos últimos meses'))
    asked = [(qq['month'], qq['year'], qq['search']) for path, qq in b6.api.gets if path == '/expenses']
    assert asked == [(8, 2026, 'DEBITO PRESTACAO HAB'), (9, 2026, 'DEBITO PRESTACAO HAB'), (10, 2026, 'DEBITO PRESTACAO HAB')]
    assert 'nos últimos 3 meses: <b>R$ 900,00</b> em 9' in tg.last['text'] and '• out/2026: R$ 300,00' in tg.last['text']
    assert '<b>Total</b>: R$ 900,00' in tg.last['text'] and '<b>Média por mês</b>: R$ 300,00 (3 meses)' in tg.last['text']

    q2 = {**q, 'category': None, 'date_from': '2026-10-20', 'date_to': '2026-10-20'}
    b2 = Bot(tg, StubApi({'intent': 'purchases', 'query': q2, 'reply': ''}))
    b2.handle(_msg(71, 'o que comprei dia 20/10'))
    assert ('/expenses', {'per_page': 15, 'purchase_from': '2026-10-20', 'purchase_to': '2026-10-20'}) in b2.api.gets
    assert tg.last['text'].startswith('🧾 Compras de 20/10:')

    b3 = Bot(tg, StubApi({'intent': 'bills', 'query': {**q, 'days': 5}, 'reply': ''}))
    b3.handle(_msg(72, 'contas dos próximos 5 dias'))
    assert ('/expenses/bills', {'days_ahead': 5}) in b3.api.gets


def test_ai_invoice_question():
    tg = FakeTelegram()
    q = {'days': 7, 'category': None, 'text': None, 'payment_method': None, 'month': 11, 'year': 2026,
         'date_from': None, 'date_to': None}
    b = Bot(tg, StubApi({'intent': 'invoices', 'query': q, 'reply': ''}))
    b.handle(_msg(80, 'qual o valor do cartão de credito do mes de novembro?'))
    assert ('/cards/invoices', {'month': 11, 'year': 2026}) in b.api.gets
    text = tg.last['text']
    assert '<b>Fatura de novembro/2026</b>: <b>R$ 350,00</b> (falta pagar R$ 250,00)' in text
    assert 'Inter &lt;conta&gt;: R$ 300,00 · vence 15/11 · ⏳ aberta' in text and '✅ paga' in text


def test_ai_invoice_for_one_bank():
    tg = FakeTelegram()
    q = {'days': 7, 'category': None, 'text': None, 'payment_method': None, 'card_id': None,
         'bank': 'Caixa Econômica Federal', 'month': 11, 'year': 2026, 'date_from': None, 'date_to': None}
    b = Bot(tg, StubApi({'intent': 'invoices', 'query': q, 'reply': ''}))
    b.handle(_msg(81, 'qual o valor do mes de novembro no cartão da caixa'))
    assert ('/cards/invoices', {'month': 11, 'year': 2026, 'bank': 'Caixa Econômica Federal'}) in b.api.gets
    assert tg.last['text'].startswith('💳 <b>Fatura Caixa Econômica Federal — novembro/2026</b>: <b>R$ 17,25</b>')


def test_bank_question_without_ai():
    """"chegou algo do banco?" responde pelos bancos conectados mesmo sem IA; gasto com valor não."""
    tg = FakeTelegram()
    b = Bot(tg, StubApi({}, ai=(False, False)), 'https://finfam.test')
    b.handle(_msg(90, 'chegou algo do banco'))
    text = tg.last['text']
    assert ('/pluggy/summary', {'status': 'pending', 'limit': 20}) in b.api.gets
    assert '2 para revisar' in text and 'COOP &lt;DIADEMA&gt;' in text and 'https://finfam.test/bank/review' in text
    assert 'C6 Bank •••• 0982: banco R$ 7.264,41 · FinFam R$ 5.865,60 — diferença' in text
    assert 'Santander •••• 0676: banco R$ 1.597,76 · FinFam R$ 1.597,76 ✅' in text
    b.handle(_msg(90, '/banco'))
    assert '2 para revisar' in tg.last['text']
    b.handle(_msg(90, '/openfinance'))
    assert 'Saldo nas contas</b>: R$ 1.832,44' in tg.last['text'] and 'C6 Bank •••• 5665' in tg.last['text']
    assert 'Investimentos nos bancos</b>: R$ 3.839,92' in tg.last['text']
    b.handle(_msg(90, '/carteira'))
    assert 'Total investido</b>: R$ 3.839,92' in tg.last['text'] and 'CDB - BANCO C6 S.A. (C6 Bank)' in tg.last['text']
    b.handle(_msg(91, 'almoço 25 no banco'))  # gasto com valor segue para o lançamento
    assert 'Bancos conectados' not in tg.last['text']


def test_investments_and_balance_questions_without_ai():
    tg = FakeTelegram()
    b = Bot(tg, StubApi({}, ai=(False, False)), 'https://finfam.test')
    b.handle(_msg(92, 'quais os meus investimentos?'))
    assert 'CDB - BANCO C6 S.A. (C6 Bank)' in tg.last['text']
    b.handle(_msg(92, 'qual o saldo da conta do C6?'))
    assert 'Saldo nas contas</b>: R$ 1.832,44' in tg.last['text']
    b.handle(_msg(92, 'quanto tenho na conta'))
    assert 'Saldo nas contas' in tg.last['text']


def test_ai_investments_intent():
    tg = FakeTelegram()
    b = Bot(tg, StubApi({'intent': 'investments', 'reply': ''}), 'https://finfam.test')
    b.handle(_msg(93, 'como estão minhas economias?'))
    assert 'Total investido' in tg.last['text']


def test_menu_agenda_goals_and_week_on_real_api(bot, api_url, family):
    b, tg = bot
    code = _call(api_url, 'POST', '/telegram/link-code', token=family)['code']
    b.handle(_msg(30, f'/start {code}'))
    b.handle(_click(30, tg.button('✅ Conectar', tg.sent[-1])))
    assert tg.button('m:agenda')  # menu logo depois de conectar
    b.handle(_msg(30, '/start'))
    assert 'O que você quer ver?' in tg.last['text'] and tg.button('📝 Revisar')
    _call(api_url, 'POST', '/expenses', {'description': 'Escola <Kids>', 'amount': '900', 'category': 'Educação',
                                         'payment_method': 'PIX', 'date': date.today().isoformat()}, family)
    b.handle(_click(30, 'm:agenda'))
    assert 'Agenda da semana' in tg.last['text'] and 'Escola &lt;Kids&gt;' in tg.last['text']
    _call(api_url, 'POST', '/goals', {'title': 'Mercado', 'type': 'spending_limit', 'target_amount': '1000',
                                      'category': 'Educação'}, family)
    b.handle(_msg(30, '/metas'))
    assert 'Mercado' in tg.last['text'] and '% usado' in tg.last['text']
    b.handle(_click(30, 'm:semana'))
    assert 'Sua semana' in tg.last['text'] and 'Escola' in tg.last['text']
    b.handle(_msg(30, '/revisar'))  # Open Finance não liberado para a família
    assert 'não está liberado' in tg.last['text']
    b.handle(_msg(30, '/desconectar'))


def test_receipt_photo_goes_to_ai_and_waits_for_confirmation():
    tg = FakeTelegram()
    api = StubApi({'intent': 'expense', 'reply': '', 'draft': {
        'description': 'Drogasil', 'amount': 42.9, 'payment_method': 'Cartão de Débito', 'category': 'Saúde',
        'date': '2026-10-27', 'card_id': None}})
    b = Bot(tg, api)
    photo = {'update_id': 9, 'message': {'chat': {'id': 60, 'type': 'private'}, 'from': {'id': 60}, 'caption': 'remédio',
                                         'photo': [{'file_id': 'small', 'file_size': 900},
                                                   {'file_id': 'big', 'file_size': 90000}]}}
    b.handle(photo)
    assert tg.downloaded == 'big' and api.media['mime'] == 'image/jpeg' and api.media['caption'] == 'remédio'
    assert api.posted == []  # nada gravado antes do ✅
    voice = {'update_id': 10, 'message': {'chat': {'id': 61, 'type': 'private'}, 'from': {'id': 61},
                                          'voice': {'file_id': 'v1', 'mime_type': 'audio/ogg', 'file_size': 5000}}}
    b.handle(voice)
    assert api.media['mime'] == 'audio/ogg'
    huge = {'update_id': 11, 'message': {'chat': {'id': 62, 'type': 'private'}, 'from': {'id': 62},
                                         'document': {'file_id': 'd', 'mime_type': 'application/pdf', 'file_size': 9 * 2 ** 20}}}
    b.handle(huge)
    assert 'grande demais' in tg.last['text']
    off = Bot(tg, StubApi({}, ai=(False, False)))
    off.handle(photo)
    assert 'não consigo ler fotos' in tg.last['text']


def test_expense_for_another_member_without_ai():
    tg = FakeTelegram()
    b = Bot(tg, StubApi({}, ai=(False, False)))
    b.handle(_msg(63, 'farmácia 30 pra Greyce'))
    data = b.flows[63]['data']
    assert (data['user_id'], data['user_name'], data['description'], data['amount']) == (2, 'Greyce Paixão', 'Farmácia', 30.0)


class ReviewApi(StubApi):
    def __init__(self):
        super().__init__({})
        self.queue = [{'id': 7, 'date': '2026-10-26', 'description': 'IFOOD *REST', 'amount': 45.9, 'refund': False,
                       'installment': None, 'bank_pending': False, 'bill': '11/2026', 'bank_name': 'C6 Bank',
                       'account_type': 'CREDIT', 'account_last_digits': '0982', 'card_digits': None,
                       'category': 'Alimentação'},
                      {'id': 8, 'date': '2026-10-27', 'description': 'UBER', 'amount': 12.0, 'refund': False,
                       'installment': None, 'bank_pending': True, 'bill': None, 'bank_name': 'C6 Bank',
                       'account_type': 'BANK', 'account_last_digits': '5665', 'card_digits': None,
                       'category': 'Transporte'}]

    def get(self, chat_id, path, **query):
        if path == '/pluggy/review/next':
            off = query.get('offset', 0)
            item = self.queue[off] if off < len(self.queue) else None
            return {'total': len(self.queue), 'offset': off, 'item': item,
                    'categories': ['Alimentação', 'Lazer', 'Transporte'] if item else []}
        return super().get(chat_id, path, **query)

    def post(self, chat_id, path, body=None):
        self.posted.append((path, body))
        if path == '/pluggy/transactions/approve':
            self.queue = [t for t in self.queue if t['id'] != body['items'][0]['id']]
            return {'message': '1 despesa(s) lançada(s) no FinFam.'}
        if path == '/pluggy/transactions/ignore':
            self.queue = [t for t in self.queue if t['id'] not in body['ids']]
            return {'message': 'ok'}
        return super().post(chat_id, path, body)


def test_review_bank_transactions_with_buttons():
    tg, api = FakeTelegram(), ReviewApi()
    b = Bot(tg, api)
    b.handle(_click(70, 'rv:go:0'))  # botão do aviso "chegou do banco"
    assert '1 de 2' in tg.last['text'] and 'IFOOD' in tg.last['text'] and 'Alimentação' in tg.last['text']
    b.handle(_click(70, tg.button('⏭')))  # deixa para depois
    assert 'UBER' in tg.last['text'] and '2 de 2' in tg.last['text']
    b.handle(_click(70, tg.button('🏷️')))
    assert 'Qual categoria?' in tg.last['text']
    b.handle(_click(70, tg.button('Lazer')))
    assert api.posted[-1] == ('/pluggy/transactions/approve', {'items': [{'id': 8, 'category': 'Lazer'}]})
    assert 'lançada' in tg.last['text'] and 'IFOOD' in tg.last['text']  # volta ao começo da fila
    b.handle(_click(70, tg.button('🚫')))
    assert api.posted[-1] == ('/pluggy/transactions/ignore', {'ids': [7]})
    assert 'Nada para revisar' in tg.last['text']
    b.reviews.clear()  # bot reiniciou: o botão antigo só atualiza a lista
    b.handle(_click(70, 'rv:ok:7:0'))
    assert len(api.posted) == 2 and 'Toque de novo' in tg.answers[-1]


def test_pick_person():
    members = [{'id': 1, 'name': 'Tiago Paixão'}, {'id': 2, 'name': 'Greyce Paixão'}]
    assert pick_person('gastei 30 de farmácia pra Greyce', members) == (members[1], 'gastei 30 de farmácia')
    assert pick_person('uber 20 para a greyce ontem', members) == (members[1], 'uber 20 ontem')
    assert pick_person('conta de luz 100', members) == (None, 'conta de luz 100')
    assert pick_person('mercado 20 pra Greyce', members[:1]) == (None, 'mercado 20 pra Greyce')
