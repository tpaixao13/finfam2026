"""Bot do Telegram 2026.4: revisar o banco pelo bot, foto/áudio na IA, agenda, semana, metas e avisos."""
import base64
from datetime import date, datetime, timedelta

import pytest

from app.core.db import session
from app.models import Goal, Tenant, User
from app.services import ai_service, bot_notices as bn
from app.services import telegram as tg_service
from conftest import API
from test_pluggy_import import _connect, _post_item, day, txs  # noqa: F401  (fixtures)
from test_pluggy_items import bank  # noqa: F401
from test_telegram_ai import _bot, _link, fake_ai  # noqa: F401

TODAY = date.today()


def _expense(family, description, amount, when: date, method='PIX', **extra):
    r = family.post('/expenses', json={'description': description, 'amount': str(amount), 'category': extra.pop('category', 'Moradia'),
                                       'payment_method': method, 'date': when.isoformat(), **extra})
    assert r.status_code == 201, r.text
    return r.json()


# ── revisar o banco pelo bot ─────────────────────────────────────────────────

def test_bot_reviews_bank_transactions_but_api_token_cannot(client, family, other_family, bank, txs):  # noqa: F811
    acc, _cc, _ = _connect(client, family, bank, txs)
    txs[acc] += [{'id': 't-1', 'type': 'DEBIT', 'amount': -50.0, 'date': day(2), 'description': 'Uber *Viagem'},
                 {'id': 't-2', 'type': 'DEBIT', 'amount': -80.0, 'date': day(1), 'description': 'Farmacia Sao Joao'}]
    _post_item(family)
    _link(client, family, '7001')
    bot = _bot('7001')

    first = client.get(f'{API}/pluggy/review/next', headers=bot).json()
    assert first['total'] == 2 and first['item']['description'] == 'Uber *Viagem'  # mais antigo primeiro
    assert first['item']['category'] and first['categories']
    second = client.get(f'{API}/pluggy/review/next?offset=1', headers=bot).json()['item']
    assert second['description'] == 'Farmacia Sao Joao'

    r = client.post(f'{API}/pluggy/transactions/approve', headers=bot,
                    json={'items': [{'id': first['item']['id'], 'category': 'Transporte'}]})
    assert r.status_code == 200 and r.json()['imported'] == 1
    assert client.post(f'{API}/pluggy/transactions/ignore', headers=bot, json={'ids': [second['id']]}).status_code == 200
    assert client.get(f'{API}/pluggy/review/next', headers=bot).json() == {'total': 0, 'offset': 0, 'item': None,
                                                                           'categories': []}

    # token de API (MCP) só consulta; a outra família não enxerga nada
    raw = family.post('/auth/tokens', json={'name': 'mcp'}).json()['token']
    api = {'Authorization': f'Bearer {raw}'}
    assert client.get(f'{API}/pluggy/review/next', headers=api).status_code == 403
    assert client.post(f'{API}/pluggy/transactions/ignore', headers=api, json={'ids': [second['id']]}).status_code == 403
    _link(client, other_family, '7002')
    assert client.post(f'{API}/pluggy/transactions/ignore', headers=_bot('7002'),
                       json={'ids': [second['id']]}).status_code in (403, 404)
    # o bot continua sem acesso às credenciais
    assert client.get(f'{API}/pluggy/config', headers=bot).status_code == 403
    session.remove()


# ── foto e áudio ─────────────────────────────────────────────────────────────

def _enable_ai(family):
    r = family.put('/settings/ai', json={'provider': 'gemini', 'api_key': 'AIzaSy-chave-secreta-123'})
    assert r.status_code == 200, r.text


def test_receipt_photo_becomes_draft(client, family, fake_ai):  # noqa: F811
    _link(client, family, '7004')
    photo = base64.b64encode(b'\xff\xd8\xff' + b'0' * 200).decode()
    no_ai = client.post(f'{API}/ai/interpret-media', headers=_bot('7004'), json={'mime': 'image/jpeg', 'data': photo})
    assert no_ai.status_code == 409  # sem IA: recusa antes de ler o arquivo
    huge = client.post(f'{API}/ai/interpret-media', headers={**_bot('7004'), 'Content-Length': str(13 * 2 ** 20)},
                       content=b'{}')
    assert huge.status_code == 413
    _enable_ai(family)
    fake_ai['answer'] = {'intent': 'expense', 'reply': 'ok', 'expense': {
        'description': 'Drogasil', 'amount': 42.9, 'payment_method': 'Cartão de Débito', 'date': TODAY.isoformat()}}
    r = client.post(f'{API}/ai/interpret-media', headers=_bot('7004'),
                    json={'mime': 'image/jpeg', 'data': photo, 'caption': 'remédio'})
    assert r.status_code == 200, r.text
    draft = r.json()['draft']
    assert draft['description'] == 'Drogasil' and draft['amount'] == 42.9
    assert 'FOTO' in fake_ai['system'] and fake_ai['user'] == 'remédio'
    # nada foi gravado
    assert family.get('/expenses').json()['total'] == 0
    bad = client.post(f'{API}/ai/interpret-media', headers=_bot('7004'),
                      json={'mime': 'image/jpeg', 'data': '%%%não é base64%%%'})
    assert bad.status_code == 422
    exe = client.post(f'{API}/ai/interpret-media', headers=_bot('7004'),
                      json={'mime': 'application/x-msdownload', 'data': photo})
    assert exe.status_code == 422
    session.remove()


def test_audio_needs_a_provider_that_listens():
    client = ai_service.AIClient(provider='openai', api_key='k', model='m')
    with pytest.raises(ai_service.AIError, match='áudio'):
        client.complete_json('s', 'u', {'mime': 'audio/ogg', 'data': 'AAAA'})
    claude = ai_service.AIClient(provider='anthropic', api_key='k', model='m')
    with pytest.raises(ai_service.AIError, match='áudio'):
        claude.complete_json('s', 'u', {'mime': 'audio/ogg', 'data': 'AAAA'})


def test_gemini_gets_the_file_inline(monkeypatch):
    sent = {}
    monkeypatch.setattr(ai_service, '_post', lambda url, headers, body, custom=False: (
        sent.update(body=body), {'candidates': [{'content': {'parts': [{'text': '{"intent": "chat", "reply": "oi"}'}]}}]})[1])
    client = ai_service.AIClient(provider='gemini', api_key='k', model='gemini-flash-lite-latest')
    out = ai_service.interpret(client, '', 'sistema', {'mime': 'audio/ogg', 'data': 'AAAA'})
    assert out['intent'] == 'chat'
    parts = sent['body']['contents'][0]['parts']
    assert parts[0] == {'inline_data': {'mime_type': 'audio/ogg', 'data': 'AAAA'}}
    assert 'ÁUDIO' in sent['body']['systemInstruction']['parts'][0]['text']


# ── agenda, semana e metas ───────────────────────────────────────────────────

def test_agenda_groups_by_day_and_skips_paid(client, family):
    _expense(family, 'Conta de luz', 287.40, TODAY + timedelta(days=1))
    _expense(family, 'Internet', 120, TODAY + timedelta(days=3), paid=True)
    _expense(family, 'Escola', 900, TODAY + timedelta(days=10))
    r = family.get('/expenses/agenda').json()
    assert len(r['days']) == 7 and r['total'] == 287.40
    tomorrow = r['days'][1]
    assert tomorrow['items'][0]['label'] == 'Conta de luz' and tomorrow['total'] == 287.40
    assert r['days'][3]['items'] == []  # paga não aparece
    session.remove()


def test_week_report_compares_with_average(client, family):
    monday = TODAY - timedelta(days=TODAY.weekday())
    _expense(family, 'Mercado grande', 600, monday, category='Alimentação')
    _expense(family, 'Uber', 40, monday, category='Transporte')
    _expense(family, 'Mercado antigo', 160, monday - timedelta(days=14), category='Alimentação')
    r = family.get('/dashboard/week').json()
    assert r['total'] == 640 and r['count'] == 2
    assert r['average'] == 20.0  # 160 em 8 semanas
    assert r['top'][0]['description'] == 'Mercado grande'
    assert r['rise']['category'] == 'Alimentação' and r['rise']['diff'] == 580.0
    assert 'Mercado grande' in bn.week_text(r)
    session.remove()


def test_goal_alerts_once_per_level(client, family):
    family.post('/goals', json={'title': 'Mercado', 'type': 'spending_limit', 'target_amount': '100',
                                'category': 'Alimentação'})
    uids = [family.user['id']]
    _expense(family, 'Feira', 85, TODAY, category='Alimentação')
    first = bn.goal_notices(uids, TODAY)
    session.commit()  # o disparo grava o nível avisado
    assert len(first) == 1 and '85%' in first[0]
    assert bn.goal_notices(uids, TODAY) == []  # não repete
    _expense(family, 'Açougue', 30, TODAY, category='Alimentação')
    again = bn.goal_notices(uids, TODAY)
    session.commit()
    assert len(again) == 1 and 'estourou' in again[0]
    assert Goal.query.one().alert_sent.endswith(':100')
    session.remove()


# ── cartões e saldo ──────────────────────────────────────────────────────────

def test_card_closes_tomorrow_and_due_in_two_days(client, family):
    tomorrow, in_two = TODAY + timedelta(days=1), TODAY + timedelta(days=2)
    card = family.post('/cards', json={'supports_credit': True, 'last_digits': '1111', 'bank': 'Nubank',
                                       'due_day': in_two.day, 'best_buy_day': tomorrow.day}).json()['card']
    _expense(family, 'Tênis', 250, TODAY, method='Cartão de Crédito', card_id=card['id'], category='Vestuário')
    notes = bn.card_notices([family.user['id']], TODAY)
    assert any('fecha amanhã' in n and 'Nubank' in n for n in notes), notes
    session.remove()


def test_bill_bigger_than_bank_balance_goes_only_to_owner(client, family, bank, txs, monkeypatch):  # noqa: F811
    _connect(client, family, bank, txs)
    _post_item(family)  # saldo da conta Nubank: 1.520,35
    _expense(family, 'Aluguel', 2000, TODAY + timedelta(days=1), bank='Nubank')
    _expense(family, 'Água', 50, TODAY + timedelta(days=1), bank='Itaú')  # outro banco: não entra
    user = session.get(User, family.user['id'])
    notes = bn.balance_notices(user, TODAY)
    assert len(notes) == 1 and 'Aluguel' in notes[0] and '1.520,35' in notes[0] and 'Água' not in notes[0]
    _link(client, family, '7005')
    sent = []
    monkeypatch.setattr(tg_service, 'send_message', lambda token, chat, text, buttons=None: (sent.append(chat), (True, ''))[1])
    assert bn.run_personal_notices(user.tenant, TODAY) == 1 and sent == ['7005']
    session.remove()


def test_weekly_summary_sunday_evening_once(client, family, monkeypatch):
    _link(client, family, '7006')
    sent = []
    monkeypatch.setattr(tg_service, 'send_message', lambda token, chat, text, buttons=None: (sent.append(text), (True, ''))[1])
    sunday = datetime(2026, 11, 1, 19, 0)
    assert bn.run_weekly(sunday) == 1 and 'Sua semana' in sent[0]
    assert bn.run_weekly(sunday) == 0  # uma vez por domingo
    assert bn.run_weekly(datetime(2026, 11, 2, 19, 0)) == 0  # segunda não
    tenant = session.get(Tenant, family.tenant['id'])
    tenant.telegram_alerts = False
    tenant.telegram_weekly_sent = None
    session.commit()
    assert bn.run_weekly(sunday) == 0  # avisos desligados
    r = family.put('/settings/telegram', json={'enabled': False, 'hour': 8, 'minute': 0, 'alerts': True})
    assert r.json()['telegram']['alerts'] is True
    session.remove()


# ── grupo da família ─────────────────────────────────────────────────────────

def _BOT():
    from test_telegram_ai import BOT
    return BOT


def test_family_group_gets_family_notices_instead_of_each_person(client, family, other_family, monkeypatch):
    _link(client, family, '7101')
    person_code = family.post('/telegram/link-code').json()['code']
    # link de pessoa não conecta grupo (e vice-versa)
    assert client.post(f'{API}/telegram/bot/link-group', headers=_BOT(),
                       json={'code': person_code, 'chat_id': '-1001', 'title': 'x'}).status_code == 422
    r = family.post('/telegram/group-code')
    assert r.status_code == 200 and '?startgroup=' in r.json()['url']
    group_code = r.json()['code']
    assert client.post(f'{API}/telegram/bot/link', headers=_BOT(),
                       json={'code': group_code, 'chat_id': '555'}).status_code == 422
    group_code = family.post('/telegram/group-code').json()['code']
    assert client.post(f'{API}/telegram/bot/link-group', json={'code': group_code, 'chat_id': '-1001'}).status_code == 403
    ok = client.post(f'{API}/telegram/bot/link-group', headers=_BOT(),
                     json={'code': group_code, 'chat_id': '-1001', 'title': 'Família Paixão'})
    assert ok.status_code == 200, ok.text
    assert family.get('/telegram/status').json()['group'] == {'connected': True, 'title': 'Família Paixão', 'can_manage': True}
    # outra família não toma o mesmo grupo
    other = other_family.post('/telegram/group-code').json()['code']
    assert client.post(f'{API}/telegram/bot/link-group', headers=_BOT(),
                       json={'code': other, 'chat_id': '-1001'}).status_code == 409

    tenant = session.get(Tenant, family.tenant['id'])
    uids = [family.user['id']]
    assert tg_service.reminder_targets(tenant, uids) == [(tg_service.settings.telegram_bot_token, '-1001')]
    sent = []
    monkeypatch.setattr(tg_service, 'send_message', lambda token, chat, text, buttons=None: (sent.append(chat), (True, ''))[1])
    assert bn.run_weekly(datetime(2026, 11, 1, 19, 0)) >= 1 and '-1001' in sent and '7101' not in sent
    session.remove()

    # o bot foi tirado do grupo: o grupo é desligado e o aviso vai para o privado
    monkeypatch.setattr(tg_service, 'send_message', lambda token, chat, text, buttons=None: (
        sent.append(chat), (False, 'Telegram API 403: bot was kicked') if chat == '-1001' else (True, ''))[1])
    sent.clear()
    tenant = session.get(Tenant, family.tenant['id'])
    tenant.telegram_weekly_sent = None
    session.commit()
    assert bn.run_weekly(datetime(2026, 11, 1, 19, 0)) == 1 and sent == ['-1001', '7101']
    assert session.get(Tenant, family.tenant['id']).telegram_group_chat is None
    session.remove()
    assert family.delete('/telegram/group').json()['telegram']['group']['connected'] is False
    tenant = session.get(Tenant, family.tenant['id'])
    assert tg_service.family_chats(tenant, uids) == ['7101']
    session.remove()


def test_chunked_body_over_limit_is_refused(client):
    def chunks():
        for _ in range(14):
            yield b'x' * (1024 * 1024)
    r = client.post(f'{API}/auth/login', content=chunks(), headers={'Content-Type': 'application/json'})
    assert r.status_code == 413
    ok = client.post(f'{API}/auth/login', json={'email': 'a@b.com', 'password': 'x'})
    assert ok.status_code == 401
