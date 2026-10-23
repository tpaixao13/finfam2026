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

from finfam_bot.bot import Bot, parse_amount, parse_date  # noqa: E402
from finfam_bot.http import FinFam  # noqa: E402

KEY = 'k' * 40


class FakeTelegram:
    def __init__(self):
        self.sent, self.edits, self.answers = [], [], []
        self._mid = 100

    def send(self, chat_id, text, buttons=None):
        self._mid += 1
        self.sent.append({'chat': chat_id, 'text': text, 'buttons': buttons or [], 'id': self._mid})

    def edit(self, chat_id, message_id, text, buttons=None):
        self.edits.append({'chat': chat_id, 'id': message_id, 'text': text, 'buttons': buttons or []})

    def answer(self, callback_id, text=None):
        self.answers.append(text)

    def typing(self, chat_id):
        pass

    @property
    def last(self):
        return self.sent[-1]

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


def _click(chat, data, message_id=101):
    return {'update_id': 2, 'callback_query': {'id': 'cb', 'data': data, 'from': {'id': chat},
                                               'message': {'chat': {'id': chat, 'type': 'private'}, 'message_id': message_id}}}


@pytest.fixture()
def bot(api_url):
    tg = FakeTelegram()
    return Bot(tg, FinFam(api_url, KEY), 'https://finfam2026.exemplo'), tg


def test_parsers():
    assert parse_amount('R$ 1.234,56') == 1234.56 and parse_amount('87,5') == 87.5 and parse_amount('87.50') == 87.5
    assert parse_amount('abc') is None and parse_amount('0') is None
    today = date(2026, 10, 23)
    assert parse_date('05/10', today) == date(2026, 10, 5) and parse_date('ontem', today) == date(2026, 10, 22)
    assert parse_date('31/02', today) is None


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
    assert 'conectado' in tg.last['text'] and 'Tiago' in tg.last['text']

    # contas a pagar + botão pagar
    b.handle(_msg(20, '/contas'))
    assert 'Conta de luz' in tg.last['text']
    b.handle(_click(20, tg.button('pay:')))
    assert '1 despesa(s) marcada(s) como paga(s)' in tg.answers[-1] and 'Tudo em dia' in tg.edits[-1]['text']

    # lançamento guiado no crédito, 3x
    b.handle(_msg(20, '/lancar'))
    b.handle(_msg(20, 'Mercado'))
    b.handle(_msg(20, '300,00'))
    b.handle(_click(20, tg.button('Crédito')))
    b.handle(_click(20, tg.button('f:card:', tg.last)))
    b.handle(_click(20, tg.button('3x')))
    b.handle(_click(20, tg.button('Alimentação')))
    b.handle(_click(20, tg.button('Hoje')))
    assert 'Confere' in tg.last['text'] and '3x de R$ 100,00' in tg.last['text'] and 'Inter' in tg.last['text']
    b.handle(_click(20, tg.button('ok:')))
    assert '✅' in tg.edits[-1]['text']
    y = date.today().year
    found = sum(_call(api_url, 'GET', f'/expenses?search=Mercado&month=0&year={yr}', token=family)['total']
                for yr in (y, y + 1))
    assert found == 3  # 3 parcelas (podem virar o ano)

    # texto livre sem IA → orienta /lancar ; resumo ; desconectar
    b.handle(_msg(20, 'gastei 10 no pão'))
    assert '/lancar' in tg.last['text']
    b.handle(_msg(20, '/resumo'))
    assert 'Resumo de' in tg.last['text']
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
    def __init__(self, answer):
        self.answer, self.posted = answer, []

    def post(self, chat_id, path, body=None):
        if path == '/ai/interpret':
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
    assert 'Uber' in tg.last['text'] and 'R$ 23,90' in tg.last['text']
    b.handle(_click(30, tg.button('ok:')))
    assert b.api.posted == [('/expenses', {'description': 'Uber', 'amount': '23.90'})]

    b2 = Bot(tg, StubApi({'intent': 'expense', 'reply': '', 'draft': {**d, 'amount': None, 'missing': ['amount']}}))
    b2.handle(_msg(31, 'comprei uber'))
    assert 'Quanto custou' in tg.last['text'] and b2.flows[31]['step'] == 'amount'
