"""Open Finance (Pluggy) — etapa 1: liberação pelo admin, chave por pessoa e webhook."""
import dataclasses

import pytest

from app.core.db import session
from app.models import PluggyConfig, Tenant
from app.routers import pluggy as router_mod
from app.services import pluggy as pg
from conftest import API, Family

CID = '9f3c1a2e-7b44-4d0e-a1f5-2c8e6d90b7aa'
CID2 = '11111111-2222-3333-4444-555555555555'


@pytest.fixture()
def fake_pluggy(monkeypatch):
    """Simula a API da Pluggy: guarda as chamadas; segredos 'errado...' são recusados."""
    calls = []

    def http(method, path, body=None, api_key=None):
        calls.append((method, path, body, api_key))
        if path == '/auth':
            if body['clientSecret'].startswith('errado'):
                raise pg.PluggyError('A Pluggy recusou o Client ID / Client Secret.', 401)
            return {'apiKey': f"key-{body['clientId'][:4]}"}
        if method == 'POST' and path == '/webhooks':
            return {'id': f'wh-{len(calls)}', **body}
        return None
    monkeypatch.setattr(pg, '_http', http)
    monkeypatch.setattr(router_mod, 'settings', dataclasses.replace(
        router_mod.settings, public_url='https://finfam.test', pluggy_webhook_ips=('52.67.145.81',)))
    pg._keys.clear()
    return calls


def _admin(client):
    tok = client.post(f'{API}/admin/login', json={'email': 'admin@finfam.test', 'password': 'admin-pass-123'}).json()
    return {'Authorization': f"Bearer {tok['access_token']}"}


def _enable(client, family, enabled=True, max_items=3):
    r = client.put(f"{API}/admin/tenants/{family.tenant['id']}/open-finance", headers=_admin(client),
                   json={'enabled': enabled, 'max_items': max_items})
    assert r.status_code == 200, r.text
    return r.json()['tenant']


def _save(family, secret='segredo-da-pluggy-123', consent=True, cid=CID):
    return family.put('/pluggy/config', json={'client_id': cid, 'client_secret': secret, 'consent': consent})


def test_admin_enables_per_family(client, family, other_family, fake_pluggy):
    assert family.get('/pluggy/config').json()['available'] is False
    assert _save(family).status_code == 403  # família não liberada
    t = _enable(client, family)
    assert t['open_finance'] is True and t['open_finance_max_items'] == 3
    assert family.get('/auth/me').json()['tenant']['open_finance'] is True
    assert other_family.get('/auth/me').json()['tenant']['open_finance'] is False
    assert client.put(f"{API}/admin/tenants/{family.tenant['id']}/open-finance", headers=family.headers,
                      json={'enabled': True}).status_code in (401, 403)  # só admin


def test_save_tests_key_creates_webhook_and_hides_secret(client, family, fake_pluggy):
    _enable(client, family)
    assert _save(family, consent=False).status_code == 422
    r = _save(family, secret='errado-123456')
    assert r.status_code == 422 and 'recusou' in r.json()['detail']
    r = _save(family)
    assert r.status_code == 200, r.text
    out = r.json()['config']
    assert out['configured'] and out['webhook'] and out['status'] == 'ok' and 'segredo' not in str(out)
    assert out['client_id_masked'].startswith('9f3c1a2e-') and CID not in str(out)
    cfg = PluggyConfig.query.first()
    assert 'segredo-da-pluggy' not in cfg.client_secret  # criptografado no banco
    hook = next(c for c in fake_pluggy if c[1] == '/webhooks')
    assert hook[2]['url'] == f'https://finfam.test/api/v1/pluggy/webhook/{cfg.hook_token}'
    assert hook[2]['headers']['X-FinFam-Hook'] and hook[3] == 'key-9f3c'
    session.remove()


def test_key_is_per_person(client, family, fake_pluggy):
    _enable(client, family)
    session.get(Tenant, family.tenant['id']).plan = 'anual'
    session.commit()
    session.remove()
    family.post('/family/members', json={'name': 'Greyce', 'email': 'g@test.com', 'password': 'senha-greyce',
                                         'confirm_password': 'senha-greyce'})
    g = Family.__new__(Family)
    g.client = client
    g.headers = {'Authorization': 'Bearer ' + client.post(f'{API}/auth/login', json={
        'email': 'g@test.com', 'password': 'senha-greyce'}).json()['access_token']}
    assert _save(family).status_code == 200
    assert g.get('/pluggy/config').json()['configured'] is False  # a chave do Tiago não é da Greyce
    assert _save(g, cid=CID2).status_code == 200
    assert PluggyConfig.query.count() == 2
    session.remove()


def test_api_token_and_bot_cannot_manage_key(client, family, fake_pluggy):
    _enable(client, family)
    raw = family.post('/auth/tokens', json={'name': 'mcp'}).json()['token']
    tok = {'Authorization': f'Bearer {raw}'}
    assert client.get(f'{API}/pluggy/config', headers=tok).status_code == 403
    assert client.put(f'{API}/pluggy/config', headers=tok, json={'client_id': CID, 'client_secret': 'x' * 10,
                                                                  'consent': True}).status_code == 403


def _hook(client, token, secret, ip='52.67.145.81', body=None):
    return client.post(f'{API}/pluggy/webhook/{token}', json=body or {'event': 'item/updated', 'itemId': 'x'},
                       headers={'X-FinFam-Hook': secret, 'CF-Connecting-IP': ip})


def test_webhook_checks_token_secret_and_ip(client, family, fake_pluggy):
    from app.core.security import decrypt_field
    _enable(client, family)
    _save(family)
    cfg = PluggyConfig.query.first()
    token, secret = cfg.hook_token, decrypt_field(cfg.hook_secret)
    session.remove()
    assert _hook(client, token, secret).json() == {'ok': True}
    assert _hook(client, token, 'segredo-errado').status_code == 404
    assert _hook(client, 'token-que-nao-existe', secret).status_code == 404
    assert _hook(client, token, secret, ip='203.0.113.9').status_code == 404
    _enable(client, family, enabled=False)
    assert _hook(client, token, secret).json()['ignored'] == 'open_finance_disabled'  # 2XX: sem reenvio


def test_changing_key_rotates_webhook_and_delete_removes_it(client, family, fake_pluggy):
    _enable(client, family)
    _save(family)
    first = PluggyConfig.query.first().webhook_id
    session.remove()
    assert family.put('/pluggy/config', json={'client_id': CID2, 'consent': True}).status_code == 422  # id novo pede segredo
    assert _save(family, cid=CID2, secret='outro-segredo-123').status_code == 200
    assert ('DELETE', f'/webhooks/{first}', None, 'key-9f3c') in fake_pluggy
    second = PluggyConfig.query.first().webhook_id
    session.remove()
    assert family.delete('/pluggy/config').status_code == 200
    assert ('DELETE', f'/webhooks/{second}', None, 'key-1111') in fake_pluggy and PluggyConfig.query.count() == 0
    session.remove()


def test_no_public_url_saves_key_without_webhook(client, family, fake_pluggy, monkeypatch):
    monkeypatch.setattr(router_mod, 'settings', dataclasses.replace(router_mod.settings, public_url=''))
    _enable(client, family)
    r = _save(family)
    assert r.status_code == 200 and 'PUBLIC_URL' in r.json()['message'] and r.json()['config']['webhook'] is False


def test_admin_dashboard_shows_pluggy_summary(client, family, fake_pluggy):
    _enable(client, family)
    _save(family)
    rows = client.get(f'{API}/admin/dashboard', headers=_admin(client)).json()['tenants']
    row = next(t for t in rows if t['id'] == family.tenant['id'])
    assert row['open_finance'] is True and row['pluggy'] == {'configured': 1, 'errors': 0}


def test_client_caches_api_key_and_renews_on_401(monkeypatch):
    calls, state = [], {'n': 0}

    def http(method, path, body=None, api_key=None):
        calls.append((path, api_key))
        if path == '/auth':
            state['n'] += 1
            return {'apiKey': f"k{state['n']}"}
        if api_key == 'k1':
            raise pg.PluggyError('expirada', 401)
        return {'ok': True}
    monkeypatch.setattr(pg, '_http', http)
    pg._keys.clear()
    c = pg.PluggyClient(CID, 'segredo')
    assert c.api_key() == 'k1' and c.api_key() == 'k1'          # guardada
    assert c.call('GET', '/items') == {'ok': True}              # 401 → renova e repete
    assert [p for p, _ in calls].count('/auth') == 2 and calls[-1] == ('/items', 'k2')
    pg.forget(CID)
    assert CID not in pg._keys


def _member(client, family, name='Greyce', email='g@test.com'):
    session.get(Tenant, family.tenant['id']).plan = 'anual'
    session.commit()
    session.remove()
    family.post('/family/members', json={'name': name, 'email': email, 'password': 'senha-greyce',
                                         'confirm_password': 'senha-greyce'})
    g = Family.__new__(Family)
    g.client = client
    g.headers = {'Authorization': 'Bearer ' + client.post(f'{API}/auth/login', json={
        'email': email, 'password': 'senha-greyce'}).json()['access_token']}
    return g


def test_family_shares_one_key(client, family, other_family, fake_pluggy):
    from app.core.security import decrypt_field
    _enable(client, family)
    g = _member(client, family)
    assert g.get('/pluggy/config').json()['family_key'] is None
    assert g.post('/pluggy/config/family', json={'consent': True}).status_code == 404  # ninguém cadastrou
    assert _save(family).status_code == 200
    offer = g.get('/pluggy/config').json()
    assert offer['configured'] is False and offer['family_key']['owner_name'] == 'Tiago'
    assert CID not in str(offer) and 'segredo' not in str(offer)
    assert g.post('/pluggy/config/family', json={'consent': False}).status_code == 422
    hooks = sum(1 for c in fake_pluggy if c[1] == '/webhooks')
    r = g.post('/pluggy/config/family', json={'consent': True})
    assert r.status_code == 200, r.text
    out = r.json()['config']
    assert out['configured'] and out['shared_from_name'] == 'Tiago' and out['webhook']  # avisos pela chave do Tiago
    assert sum(1 for c in fake_pluggy if c[1] == '/webhooks') == hooks  # sem webhook novo
    assert family.get('/pluggy/config').json()['shared_with'] == ['Greyce']
    assert g.post('/pluggy/config/family', json={'consent': True}).status_code == 409  # já tem
    # outra família não enxerga nem usa a chave
    _enable(client, other_family)
    assert other_family.get('/pluggy/config').json()['family_key'] is None
    assert other_family.post('/pluggy/config/family', json={'consent': True}).status_code == 404

    # Tiago troca o segredo (mesma aplicação): a cópia da Greyce acompanha
    r = _save(family, secret='segredo-novo-456')
    assert 'pessoa(s) da família' in r.json()['message']
    copy = PluggyConfig.query.filter(PluggyConfig.shared_from.isnot(None)).one()
    assert decrypt_field(copy.client_secret) == 'segredo-novo-456'
    session.remove()

    # Tiago remove a chave: a Greyce continua, agora como dona da chave (com webhook)
    assert family.delete('/pluggy/config').status_code == 200
    mine = g.get('/pluggy/config').json()
    assert mine['configured'] and mine['shared_from_name'] is None and mine['webhook']
    session.remove()


def test_webhook_of_family_key_reaches_member_bank(client, family, fake_pluggy):
    from app.core.security import decrypt_field
    from app.models import BankConnection
    _enable(client, family)
    g = _member(client, family)
    _save(family)
    g.post('/pluggy/config/family', json={'consent': True})
    greyce = PluggyConfig.query.filter(PluggyConfig.shared_from.isnot(None)).one().user_id
    session.add(BankConnection(user_id=greyce, item_id='item-da-greyce', status='UPDATED'))
    session.commit()
    cfg = PluggyConfig.query.filter_by(shared_from=None).one()
    token, secret = cfg.hook_token, decrypt_field(cfg.hook_secret)
    session.remove()
    _hook(client, token, secret, body={'event': 'item/error', 'itemId': 'item-da-greyce', 'error': {'message': 'Senha'}})
    assert BankConnection.query.filter_by(item_id='item-da-greyce').one().status == 'LOGIN_ERROR'
    session.remove()


def test_removing_member_hands_key_over_and_copy_cannot_take_dashboard_items(client, family, fake_pluggy):
    _enable(client, family)
    g = _member(client, family)
    family.post('/family/members', json={'name': 'Bia', 'email': 'b@test.com', 'password': 'senha-greyce',
                                         'confirm_password': 'senha-greyce'})
    assert _save(g, cid=CID2).status_code == 200  # a chave da família é da Greyce
    family_cfg = family.get('/pluggy/config').json()
    assert family_cfg['family_key']['owner_name'] == 'Greyce'
    assert family.post('/pluggy/config/family', json={'consent': True}).status_code == 200
    greyce_id = PluggyConfig.query.filter_by(shared_from=None).one().user_id
    session.remove()
    assert family.delete(f'/family/members/{greyce_id}').status_code == 200
    mine = family.get('/pluggy/config').json()
    assert mine['configured'] and mine['shared_from_name'] is None and mine['webhook']  # o Tiago herdou
    session.remove()
