"""Bot central do Telegram (vínculo por código, autenticação do bot) e IA da família."""
import pytest

from app.core.db import session
from app.models import AiConfig, TelegramLink
from app.services import ai_service, telegram as tg_service
from conftest import API, Family

BOT = {'Authorization': 'Bearer ffbot_chave-do-bot-' + 'b' * 40}


def _bot(chat_id):
    return {**BOT, 'X-Telegram-Chat': str(chat_id)}


def _link(client, family, chat_id='5550001'):
    code = family.post('/telegram/link-code').json()['code']
    r = client.post(f'{API}/telegram/bot/link', headers=BOT, json={'code': code, 'chat_id': chat_id, 'username': 'tiago'})
    assert r.status_code == 200, r.text
    return code


# ── vínculo ──────────────────────────────────────────────────────────────────

def test_link_code_and_bot_acts_as_user(client, family):
    r = family.post('/telegram/link-code')
    assert r.status_code == 200 and r.json()['url'].startswith('https://t.me/FinFamTesteBot?start=')
    _link(client, family)
    me = client.get(f'{API}/auth/me', headers=_bot('5550001'))
    assert me.status_code == 200 and me.json()['user']['id'] == family.user['id']
    assert family.get('/telegram/status').json()['linked'] is True


def test_link_code_is_single_use_and_needs_bot_key(client, family):
    code = family.post('/telegram/link-code').json()['code']
    wrong = {'Authorization': 'Bearer ffbot_errada-' + 'x' * 40}
    assert client.post(f'{API}/telegram/bot/link', headers=wrong, json={'code': code, 'chat_id': '1'}).status_code == 403
    assert client.post(f'{API}/telegram/bot/link', headers=BOT, json={'code': code, 'chat_id': '1'}).status_code == 200
    assert client.post(f'{API}/telegram/bot/link', headers=BOT, json={'code': code, 'chat_id': '2'}).status_code == 422


def test_unlinked_chat_and_wrong_key_rejected(client, family):
    _link(client, family)
    assert client.get(f'{API}/expenses/bills', headers=_bot('999')).status_code == 401
    assert client.get(f'{API}/expenses/bills', headers=BOT).status_code == 401  # sem chat
    bad = {'Authorization': 'Bearer ffbot_' + 'z' * 53, 'X-Telegram-Chat': '5550001'}
    assert client.get(f'{API}/expenses/bills', headers=bad).status_code == 401


def test_bot_only_sees_own_family_and_cannot_touch_account(client, family, other_family):
    _link(client, family, '111')
    _link(client, other_family, '222')
    other_family.post('/expenses', json={'description': 'Segredo', 'amount': '10', 'category': 'Outros',
                                         'payment_method': 'PIX'})
    r = client.get(f'{API}/expenses', headers=_bot('111'), params={'search': 'Segredo', 'month': 0})
    assert r.status_code == 200 and r.json()['total'] == 0
    blocked = [client.post(f'{API}/auth/tokens', headers=_bot('111'), json={'name': 'x'}),
               client.post(f'{API}/telegram/link-code', headers=_bot('111')),
               client.put(f'{API}/settings/ai', headers=_bot('111'), json={'provider': 'gemini', 'api_key': 'k' * 20})]
    assert [x.status_code for x in blocked] == [403, 403, 403]


def test_unlink_from_bot_and_cascade_on_member_delete(client, family):
    _link(client, family, '333')
    assert client.delete(f'{API}/telegram/link', headers=_bot('333')).json()['removed'] == 1
    assert client.get(f'{API}/auth/me', headers=_bot('333')).status_code == 401
    _link(client, family, '333')
    admin = {'Authorization': 'Bearer ' + client.post(f'{API}/admin/login', json={
        'email': 'admin@finfam.test', 'password': 'admin-pass-123'}).json()['access_token']}
    assert client.delete(f"{API}/admin/tenants/{family.tenant['id']}", headers=admin).status_code == 200
    assert TelegramLink.query.count() == 0
    session.remove()


def test_reminder_goes_to_linked_chats(client, family, monkeypatch):
    sent = []
    monkeypatch.setattr(tg_service, 'send_message', lambda token, chat, msg: (sent.append((token, chat)) or (True, '')))
    family.post('/expenses', json={'description': 'Luz', 'amount': '90', 'category': 'Moradia',
                                   'payment_method': 'PIX', 'date': '2026-01-05'})  # em atraso
    assert family.put('/settings/telegram', json={'enabled': True, 'hour': 8, 'minute': 0}).status_code == 200
    from datetime import datetime
    assert tg_service.run_daily_reminders(datetime(2026, 2, 1, 8, 0)) == 0  # ninguém vinculado
    _link(client, family, '444')
    family.put('/settings/telegram', json={'enabled': True, 'hour': 8, 'minute': 0})
    assert tg_service.run_daily_reminders(datetime(2026, 2, 2, 8, 0)) == 1
    assert sent == [('123:fake-token-de-teste', '444')]


# ── IA ───────────────────────────────────────────────────────────────────────

@pytest.fixture()
def fake_ai(monkeypatch):
    calls = {}

    def complete_json(self, system, user):
        calls.update(system=system, user=user, provider=self.provider, key=self.api_key, model=self.model)
        return calls.get('answer', {})
    monkeypatch.setattr(ai_service.AIClient, 'complete_json', complete_json)
    return calls


def test_ai_settings_never_return_key(client, family):
    r = family.put('/settings/ai', json={'provider': 'gemini', 'api_key': 'AIzaSy-chave-secreta-123'})
    assert r.status_code == 200
    body = family.get('/settings/ai').json()
    assert body['configured'] and 'chave-secreta' not in str(body) and body['key_masked'].startswith('AIza')
    stored = AiConfig.query.first().api_key
    assert 'chave-secreta' not in stored  # criptografada no banco
    session.remove()
    # trocar de provedor exige a nova chave; base_url só https
    assert family.put('/settings/ai', json={'provider': 'anthropic'}).status_code == 422
    assert family.put('/settings/ai', json={'provider': 'openai', 'api_key': 'sk-x' * 5,
                                            'base_url': 'http://127.0.0.1:8001'}).status_code == 422


def test_interpret_requires_config(client, family):
    r = family.post('/ai/interpret', json={'text': 'gastei 10 no pão'})
    assert r.status_code == 409 and r.json()['detail']['code'] == 'ai_not_configured'


def test_interpret_builds_checked_draft(client, family, other_family, fake_ai):
    card = family.post('/cards', json={'supports_credit': True, 'last_digits': '4321', 'bank': 'Inter',
                                       'due_day': 10, 'best_buy_day': 3}).json()['card']
    alien = other_family.post('/cards', json={'supports_credit': True, 'last_digits': '9999', 'bank': 'Nubank',
                                              'due_day': 10, 'best_buy_day': 3}).json()['card']
    family.put('/settings/ai', json={'provider': 'openai', 'api_key': 'sk-teste-1234567890', 'model': 'gpt-5.4-mini'})
    fake_ai['answer'] = {'intent': 'expense', 'reply': 'ok', 'expense': {
        'description': 'Mercado', 'amount': '87,5'.replace(',', '.'), 'payment_method': 'crédito',
        'card_id': card['id'], 'installments': 3, 'category': 'Inexistente', 'date': '2026-10-20'}}
    r = family.post('/ai/interpret', json={'text': 'gastei 87,50 no mercado no inter em 3x'})
    assert r.status_code == 200, r.text
    d = r.json()['draft']
    assert (d['payment_method'], d['card_id'], d['installments'], d['amount']) == ('Cartão de Crédito', card['id'], 3, 87.5)
    assert d['category'] != 'Inexistente' and d['missing'] == []
    assert fake_ai['key'] == 'sk-teste-1234567890' and 'Inter' in fake_ai['system']
    assert 'Nubank' not in fake_ai['system']  # só cartões da própria família vão para a IA
    # cartão de outra família é descartado
    fake_ai['answer']['expense']['card_id'] = alien['id']
    assert family.post('/ai/interpret', json={'text': 'x' * 5}).json()['draft']['card_id'] is None
    # o rascunho vira despesa só quando o usuário confirma (POST /expenses)
    assert family.post('/expenses', json=d['body']).status_code == 201


def test_interpret_other_intents_and_missing_amount(client, family, fake_ai):
    family.put('/settings/ai', json={'provider': 'anthropic', 'api_key': 'sk-ant-1234567890'})
    fake_ai['answer'] = {'intent': 'bills', 'reply': 'Suas contas:'}
    assert family.post('/ai/interpret', json={'text': 'o que vence essa semana?'}).json()['intent'] == 'bills'
    fake_ai['answer'] = {'intent': 'hackear', 'reply': 'x'}
    assert family.post('/ai/interpret', json={'text': 'bla bla'}).json()['intent'] == 'chat'
    fake_ai['answer'] = {'intent': 'expense', 'expense': {'description': 'Farmácia'}}
    assert family.post('/ai/interpret', json={'text': 'comprei remédio'}).json()['draft']['missing'] == ['amount']


def test_provider_errors_are_friendly(client, family, monkeypatch):
    family.put('/settings/ai', json={'provider': 'gemini', 'api_key': 'AIzaSy-0000000000'})

    def boom(*a, **k):
        raise ai_service.AIError(ai_service._http_message(401, '{"error": {"message": "API key not valid"}}'))
    monkeypatch.setattr(ai_service, '_post', boom)
    r = family.post('/ai/interpret', json={'text': 'gastei 10'})
    assert r.status_code == 502 and 'recusada' in r.json()['detail']
