"""Open Finance (Pluggy) — etapa 2: conectar bancos e listar contas e cartões."""
import dataclasses

import pytest

from app.core.db import session
from app.core.security import decrypt_field
from app.models import BankAccount, BankConnection, PluggyConfig, Tenant
from app.routers import pluggy as router_mod
from app.services import pluggy as pg
from conftest import API, Family
from test_pluggy import CID, CID2, _enable, _save

ITEM = 'aaaaaaaa-1111-2222-3333-444444444444'
ITEM2 = 'bbbbbbbb-1111-2222-3333-444444444444'


@pytest.fixture()
def bank(monkeypatch):
    """Pluggy de mentira com itens por Client ID (um item só existe na conta que o criou)."""
    state = {'calls': [], 'items': {}, 'accounts': {}}

    def http(method, path, body=None, api_key=None):
        state['calls'].append((method, path, body, api_key))
        if path == '/auth':
            return {'apiKey': f"key-{body['clientId'][:4]}"}
        if method == 'POST' and path == '/webhooks':
            return {'id': 'wh-1'}
        if method == 'POST' and path == '/connect_token':
            return {'accessToken': f'ct-{api_key}'}
        if path.startswith('/items/'):
            item_id = path.split('/')[2]
            item = state['items'].get(item_id)
            if item is None or item['_key'] != api_key:
                raise pg.PluggyError('A Pluggy respondeu 404.', 404)
            if method == 'DELETE':
                state['items'].pop(item_id)
                return None
            if method == 'PATCH':
                item['status'] = 'UPDATING'
            return {k: v for k, v in item.items() if k != '_key'}
        if path.startswith('/accounts?itemId='):
            return {'results': state['accounts'].get(path.split('=')[1], [])}
        return None
    monkeypatch.setattr(pg, '_http', http)
    monkeypatch.setattr(router_mod, 'settings', dataclasses.replace(
        router_mod.settings, public_url='https://finfam.test', pluggy_webhook_ips=('52.67.145.81',)))
    pg._keys.clear()

    def add(item_id, cid=CID, owner=None, accounts=None):
        state['items'][item_id] = {'_key': f'key-{cid[:4]}', 'id': item_id, 'status': 'UPDATED',
                                   'clientUserId': owner, 'lastUpdatedAt': '2026-10-26T12:00:00.000Z',
                                   'connector': {'name': 'Nubank', 'primaryColor': '820ad1'}}
        state['accounts'][item_id] = accounts if accounts is not None else [
            {'id': f'{item_id[:8]}-acc', 'type': 'BANK', 'subtype': 'CHECKING_ACCOUNT', 'name': 'Conta corrente',
             'number': '0001/12345-6', 'balance': 1520.35, 'currencyCode': 'BRL'},
            {'id': f'{item_id[:8]}-cc', 'type': 'CREDIT', 'subtype': 'CREDIT_CARD', 'name': 'Nubank Mastercard',
             'number': '5162', 'balance': 830.1, 'currencyCode': 'BRL', 'creditData': {'creditLimit': 5000}},
        ]
    state['add'] = add
    return state


def _ready(client, family, max_items=3):
    _enable(client, family, max_items=max_items)
    assert _save(family).status_code == 200


def test_connect_token_and_add_item(client, family, bank):
    _ready(client, family)
    card = family.post('/cards', json={'supports_credit': True, 'last_digits': '5162', 'bank': 'Nubank',
                                       'due_day': 10, 'best_buy_day': 3}).json()['card']
    r = family.post('/pluggy/connect-token', json={})
    assert r.status_code == 200, r.text
    assert r.json()['connect_token'] == 'ct-key-9f3c'
    sent = next(c for c in bank['calls'] if c[1] == '/connect_token')[2]
    assert sent['options']['clientUserId'] == str(family.user['id'])
    assert sent['options']['webhookUrl'].startswith('https://finfam.test/api/v1/pluggy/webhook/')

    bank['add'](ITEM, owner=str(family.user['id']))
    r = family.post('/pluggy/items', json={'item_id': ITEM})
    assert r.status_code == 200, r.text
    item = r.json()['item']
    assert item['bank_name'] == 'Nubank' and item['bank_color'] == '#820ad1' and item['status'] == 'UPDATED'
    conta, cartao = sorted(item['accounts'], key=lambda a: a['type'])
    assert conta['type'] == 'BANK' and conta['balance'] == 1520.35
    assert cartao['type'] == 'CREDIT' and cartao['last_digits'] == '5162' and cartao['credit_limit'] == 5000
    assert cartao['card_id'] == card['id']  # ligado sozinho pelos 4 últimos dígitos
    # registrar de novo não duplica
    assert family.post('/pluggy/items', json={'item_id': ITEM}).status_code == 200
    assert BankConnection.query.count() == 1 and BankAccount.query.count() == 2
    assert family.get('/pluggy/config').json()['connections'] == 1
    session.remove()


def test_item_must_belong_to_my_pluggy_account(client, family, other_family, bank):
    _ready(client, family)
    bank['add'](ITEM, cid=CID2)  # item de outra aplicação Pluggy
    assert family.post('/pluggy/items', json={'item_id': ITEM}).status_code == 404
    bank['add'](ITEM2, owner='999')  # mesma aplicação, mas criado para outra pessoa
    assert family.post('/pluggy/items', json={'item_id': ITEM2}).status_code == 404
    assert family.post('/pluggy/items', json={'item_id': '../webhooks'}).status_code == 422
    assert BankConnection.query.count() == 0
    session.remove()


def test_only_the_owner_sees_and_manages(client, family, other_family, bank):
    _ready(client, family)
    session.get(Tenant, family.tenant['id']).plan = 'anual'
    session.commit()
    session.remove()
    family.post('/family/members', json={'name': 'Greyce', 'email': 'g@test.com', 'password': 'senha-greyce',
                                         'confirm_password': 'senha-greyce'})
    g = Family.__new__(Family)
    g.client = client
    g.headers = {'Authorization': 'Bearer ' + client.post(f'{API}/auth/login', json={
        'email': 'g@test.com', 'password': 'senha-greyce'}).json()['access_token']}
    bank['add'](ITEM)
    conn = family.post('/pluggy/items', json={'item_id': ITEM}).json()['item']
    acc = next(a for a in conn['accounts'] if a['type'] == 'CREDIT')
    assert g.get('/pluggy/items').json()['items'] == []
    assert g.post(f"/pluggy/items/{conn['id']}/refresh").status_code == 409  # sem chave
    _save(g, cid=CID2)
    assert g.post(f"/pluggy/items/{conn['id']}/refresh").status_code == 404
    assert g.delete(f"/pluggy/items/{conn['id']}").status_code == 404
    assert g.put(f"/pluggy/accounts/{acc['id']}", json={'card_id': None}).status_code == 404
    assert g.post('/pluggy/connect-token', json={'connection_id': conn['id']}).status_code == 404
    assert other_family.get('/pluggy/items').status_code == 403  # família sem Open Finance
    # API token não conecta banco
    tok = family.post('/auth/tokens', json={'name': 'mcp'}).json()['token']
    assert client.get(f'{API}/pluggy/items', headers={'Authorization': f'Bearer {tok}'}).status_code == 403
    session.remove()


def test_limit_per_person(client, family, bank):
    _ready(client, family, max_items=1)
    bank['add'](ITEM)
    assert family.post('/pluggy/items', json={'item_id': ITEM}).status_code == 200
    r = family.post('/pluggy/connect-token', json={})
    assert r.status_code == 409 and 'limite' in r.json()['detail']['message']
    bank['add'](ITEM2)
    assert family.post('/pluggy/items', json={'item_id': ITEM2}).status_code == 409
    # reconectar o banco que já existe continua liberado
    conn_id = BankConnection.query.first().id
    assert family.post('/pluggy/connect-token', json={'connection_id': conn_id}).status_code == 200
    sent = [c for c in bank['calls'] if c[1] == '/connect_token'][-1][2]
    assert sent['itemId'] == ITEM
    session.remove()


def test_map_card_refresh_and_disconnect(client, family, other_family, bank):
    _ready(client, family)
    bank['add'](ITEM)
    conn = family.post('/pluggy/items', json={'item_id': ITEM}).json()['item']
    acc = next(a for a in conn['accounts'] if a['type'] == 'CREDIT')
    conta = next(a for a in conn['accounts'] if a['type'] == 'BANK')
    assert acc['card_id'] is None  # nenhum cartão com o final 5162
    card = family.post('/cards', json={'supports_credit': True, 'last_digits': '0001', 'bank': 'Nubank',
                                       'due_day': 10, 'best_buy_day': 3}).json()['card']
    other = other_family.post('/cards', json={'supports_credit': True, 'last_digits': '0002', 'bank': 'X',
                                              'due_day': 10, 'best_buy_day': 3}).json()['card']
    assert family.put(f"/pluggy/accounts/{acc['id']}", json={'card_id': other['id']}).status_code == 404
    assert family.put(f"/pluggy/accounts/{conta['id']}", json={'card_id': card['id']}).status_code == 422
    r = family.put(f"/pluggy/accounts/{acc['id']}", json={'card_id': card['id']})
    assert r.status_code == 200 and r.json()['account']['card_label'].endswith('0001')
    # excluir o cartão desliga a conta
    assert family.delete(f"/cards/{card['id']}").status_code == 200
    assert family.get('/pluggy/items').json()['items'][0]['accounts'][1]['card_id'] is None

    r = family.post(f"/pluggy/items/{conn['id']}/refresh")
    assert r.status_code == 200 and r.json()['item']['status'] == 'UPDATING'
    assert ('PATCH', f'/items/{ITEM}', {}, 'key-9f3c') in bank['calls']

    r = family.delete(f"/pluggy/items/{conn['id']}")
    assert r.status_code == 200
    assert ITEM not in bank['items'] and BankConnection.query.count() == 0 and BankAccount.query.count() == 0
    session.remove()


def test_webhook_updates_status_and_resyncs(client, family, bank):
    _ready(client, family)
    bank['add'](ITEM)
    family.post('/pluggy/items', json={'item_id': ITEM})
    cfg = PluggyConfig.query.first()
    url, headers = f'{API}/pluggy/webhook/{cfg.hook_token}', {
        'X-FinFam-Hook': decrypt_field(cfg.hook_secret), 'CF-Connecting-IP': '52.67.145.81'}
    session.remove()

    r = client.post(url, headers=headers, json={'event': 'item/error', 'itemId': ITEM,
                                                'error': {'message': 'Senha inválida'}})
    assert r.status_code == 200
    item = family.get('/pluggy/items').json()['items'][0]
    assert item['status'] == 'LOGIN_ERROR' and item['error'] == 'Senha inválida'

    bank['accounts'][ITEM][0]['balance'] = 99.9
    client.post(url, headers=headers, json={'event': 'item/updated', 'itemId': ITEM})
    item = family.get('/pluggy/items').json()['items'][0]  # relê o banco porque o webhook avisou
    assert item['status'] == 'UPDATED' and item['error'] is None
    assert next(a for a in item['accounts'] if a['type'] == 'BANK')['balance'] == 99.9
    calls = len(bank['calls'])
    family.get('/pluggy/items')
    assert len(bank['calls']) == calls  # sem novidade, não chama a Pluggy

    client.post(url, headers=headers, json={'event': 'item/deleted', 'itemId': ITEM})
    assert family.get('/pluggy/items').json()['items'] == []
    session.remove()


def test_removing_key_disconnects_banks(client, family, bank):
    _ready(client, family)
    bank['add'](ITEM)
    family.post('/pluggy/items', json={'item_id': ITEM})
    assert family.delete('/pluggy/config').status_code == 200
    assert ITEM not in bank['items'] and BankConnection.query.count() == 0
    session.remove()


def test_changing_pluggy_app_drops_old_banks(client, family, bank):
    _ready(client, family)
    bank['add'](ITEM)
    family.post('/pluggy/items', json={'item_id': ITEM})
    assert _save(family, secret='segredo-da-pluggy-123').status_code == 200  # mesma app: mantém
    assert BankConnection.query.count() == 1
    session.remove()
    assert _save(family, cid=CID2).status_code == 200  # outra app: os bancos eram da antiga
    assert BankConnection.query.count() == 0
    session.remove()


def test_register_item_connected_in_pluggy_dashboard(client, family, bank):
    """Conexão feita no painel da Pluggy (clientUserId vazio ou texto livre) pode ser registrada pelo ID."""
    _ready(client, family)
    bank['add'](ITEM, owner='painel-pluggy')
    assert family.post('/pluggy/items', json={'item_id': ITEM, 'months': 3}).status_code == 200
    bank['add'](ITEM2, owner=None, accounts=[{'id': 'inter-acc', 'type': 'BANK', 'name': 'Conta Inter',
                                              'number': '7777', 'balance': 5, 'currencyCode': 'BRL'}])
    assert family.post('/pluggy/items', json={'item_id': ITEM2}).status_code == 200
    assert BankConnection.query.count() == 2
    session.remove()


def test_refresh_meupluggy_item_rereads_instead(client, family, bank, monkeypatch):
    """Item do Meu Pluggy: PATCH dá 400; o FinFam relê contas (banco novo no Meu Pluggy aparece)."""
    _ready(client, family)
    bank['add'](ITEM)
    conn = family.post('/pluggy/items', json={'item_id': ITEM}).json()['item']
    inner = pg._http

    def http(method, path, body=None, api_key=None):
        if method == 'PATCH':
            raise pg.PluggyError('A Pluggy respondeu 400: MeuPluggy item cant be updated', 400)
        return inner(method, path, body, api_key)
    monkeypatch.setattr(pg, '_http', http)
    bank['accounts'][ITEM].append({'id': 'novo-banco-acc', 'type': 'BANK', 'name': 'Conta do banco novo',
                                   'number': '9999', 'balance': 10, 'currencyCode': 'BRL'})
    r = family.post(f"/pluggy/items/{conn['id']}/refresh")
    assert r.status_code == 200, r.text
    assert 'relido' in r.json()['message']
    assert len(r.json()['item']['accounts']) == 3
    session.remove()


def test_same_bank_twice_is_refused_but_two_meupluggy_banks_are_ok(client, family, bank):
    _ready(client, family)
    bank['add'](ITEM)
    assert family.post('/pluggy/items', json={'item_id': ITEM}).status_code == 200
    assert family.post('/pluggy/connect-token', json={}).status_code == 200
    opts = [c for c in bank['calls'] if c[1] == '/connect_token'][-1][2]['options']
    assert 'avoidDuplicates' not in opts
    # o mesmo banco conectado de novo (contas iguais, item novo): recusado e apagado na Pluggy
    bank['add'](ITEM2, accounts=[dict(a, id=a['id'] + '-2') for a in bank['accounts'][ITEM]])
    r = family.post('/pluggy/items', json={'item_id': ITEM2})
    assert r.status_code == 409 and 'já está conectado' in r.json()['detail']
    assert ITEM2 not in bank['items'] and BankConnection.query.count() == 1
    # outro banco (Meu Pluggy: uma conexão por banco) entra normalmente
    bank['add'](ITEM2, accounts=[{'id': 'outro-acc', 'type': 'BANK', 'name': 'Conta Inter', 'number': '7777',
                                  'balance': 5, 'currencyCode': 'BRL'}])
    assert family.post('/pluggy/items', json={'item_id': ITEM2}).status_code == 200
    assert BankConnection.query.count() == 2
    session.remove()


def test_card_limit_comes_from_bank_and_create_card(client, family, bank):
    _ready(client, family)
    card = family.post('/cards', json={'supports_credit': True, 'last_digits': '5162', 'bank': 'Nubank',
                                       'due_day': 10, 'best_buy_day': 3, 'credit_limit': '1000'}).json()['card']
    bank['add'](ITEM)
    conn = family.post('/pluggy/items', json={'item_id': ITEM}).json()['item']
    cards = {c['id']: c for c in family.get('/cards?light=true').json()['cards']}
    assert cards[card['id']]['effective_limit'] == 5000.0  # ligado sozinho → limite do banco

    # outro banco: cartão que ainda não existe no FinFam → "Criar cartão"
    bank['add'](ITEM2, accounts=[{'id': 'inter-cc', 'type': 'CREDIT', 'subtype': 'CREDIT_CARD', 'name': 'Inter Black',
                                  'number': '7777', 'balance': 10, 'currencyCode': 'BRL',
                                  'creditData': {'creditLimit': 8000, 'availableCreditLimit': 7500,
                                                 'balanceCloseDate': '2026-10-03T03:00:00.000Z',
                                                 'balanceDueDate': '2026-10-10T03:00:00.000Z'}}])
    item2 = family.post('/pluggy/items', json={'item_id': ITEM2}).json()['item']
    acc = item2['accounts'][0]
    assert acc['close_day'] == 3 and acc['due_day'] == 10 and acc['available_limit'] == 7500.0
    r = family.post(f"/pluggy/accounts/{acc['id']}/card")
    assert r.status_code == 200, r.text
    assert r.json()['account']['card_id'] is not None
    new = next(c for c in family.get('/cards?light=true').json()['cards'] if c['last_digits'] == '7777')
    assert (new['due_day'], new['best_buy_day'], new['effective_limit']) == (10, 3, 8000.0)
    assert family.post(f"/pluggy/accounts/{acc['id']}/card").status_code == 422  # já ligado
    conta = next(a for a in conn['accounts'] if a['type'] == 'BANK')
    assert family.post(f"/pluggy/accounts/{conta['id']}/card").status_code == 422  # conta não vira cartão
    session.remove()


def test_card_days_when_bank_closes_after_due_day():
    from app.models import BankAccount
    from app.services import open_finance as of
    assert of.card_days(BankAccount(close_day=28, due_day=5)) == (5, 4)
    assert of.card_days(BankAccount(close_day=None, due_day=None)) == (10, 3)
