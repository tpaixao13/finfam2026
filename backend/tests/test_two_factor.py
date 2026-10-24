"""Verificação em duas etapas (TOTP): ativar, login, bloqueio após 3 erros, admin."""
import time

from app.core import totp
from conftest import API

PW = 'senha-segura'


def _code(secret, ahead=0):
    return totp.code_for(secret, time.time() + 30 * ahead)


def _enable(family):
    setup = family.post('/auth/2fa/setup').json()
    assert setup['otpauth_uri'].startswith('otpauth://totp/FinFam') and setup['qr_svg'].startswith('data:image/svg')
    assert family.post('/auth/2fa/enable', json={'code': '000000' if _code(setup['secret']) != '000000' else '111111'}).status_code == 422
    r = family.post('/auth/2fa/enable', json={'code': _code(setup['secret'])})
    assert r.status_code == 200 and r.json()['user']['two_factor_enabled'] is True
    return setup['secret']


def _login(client, email='tiago@test.com', password=PW):
    return client.post(f'{API}/auth/login', json={'email': email, 'password': password})


def _admin(client):
    tok = client.post(f'{API}/admin/login', json={'email': 'admin@finfam.test', 'password': 'admin-pass-123'}).json()
    return {'Authorization': f"Bearer {tok['access_token']}"}


def test_login_asks_code_and_challenge_is_not_a_session(client, family):
    secret = _enable(family)
    r = _login(client)
    assert r.status_code == 200 and r.json()['two_factor_required'] and 'access_token' not in r.json()
    challenge = r.json()['challenge']
    assert client.get(f'{API}/auth/me', headers={'Authorization': f'Bearer {challenge}'}).status_code == 401
    ok = client.post(f'{API}/auth/login/2fa', json={'challenge': challenge, 'code': _code(secret, 1)})
    assert ok.status_code == 200 and ok.json()['access_token']
    # o mesmo código não vale duas vezes
    again = client.post(f'{API}/auth/login/2fa', json={'challenge': _login(client).json()['challenge'],
                                                       'code': _code(secret, 1)})
    assert again.status_code == 401


def test_three_wrong_codes_lock_until_admin_unlocks(client, family):
    secret = _enable(family)
    wrong = '123456' if _code(secret) != '123456' else '654321'
    challenge = _login(client).json()['challenge']
    r1 = client.post(f'{API}/auth/login/2fa', json={'challenge': challenge, 'code': wrong})
    r2 = client.post(f'{API}/auth/login/2fa', json={'challenge': challenge, 'code': wrong})
    assert (r1.status_code, r2.status_code) == (401, 401) and 'Mais 1 tentativa' in r2.json()['detail']
    r3 = client.post(f'{API}/auth/login/2fa', json={'challenge': challenge, 'code': wrong})
    assert r3.status_code == 423 and r3.json()['detail']['code'] == '2fa_locked'
    # bloqueado: nem o código certo, nem a senha certa entram
    assert client.post(f'{API}/auth/login/2fa', json={'challenge': challenge, 'code': _code(secret, 1)}).status_code == 423
    assert _login(client).status_code == 423
    assert _login(client, password='errada').status_code == 401  # senha errada não revela o bloqueio
    # admin vê e desbloqueia
    admin = _admin(client)
    users = [u for t in client.get(f'{API}/admin/dashboard', headers=admin).json()['tenants'] for u in t['members']]
    me = next(u for u in users if u['email'] == 'tiago@test.com')
    assert me['two_factor_locked'] and me['two_factor_failures'] == 3
    assert client.post(f"{API}/admin/users/{me['id']}/2fa/unlock", headers=admin).status_code == 200
    ok = client.post(f'{API}/auth/login/2fa', json={'challenge': _login(client).json()['challenge'], 'code': _code(secret, 1)})
    assert ok.status_code == 200


def test_admin_removes_2fa(client, family):
    _enable(family)
    admin = _admin(client)
    uid = family.user['id']
    assert client.delete(f'{API}/admin/users/{uid}/2fa', headers=admin).status_code == 200
    r = _login(client)
    assert r.status_code == 200 and r.json()['access_token'] and not r.json()['user']['two_factor_enabled']


def test_disable_needs_password_and_code(client, family):
    secret = _enable(family)
    assert family.post('/auth/2fa/disable', json={'password': 'errada', 'code': _code(secret, 1)}).status_code == 422
    bad = '000000' if _code(secret, 1) != '000000' else '111111'
    assert family.post('/auth/2fa/disable', json={'password': PW, 'code': bad}).status_code == 422
    assert family.post('/auth/2fa/disable', json={'password': PW, 'code': _code(secret, 1)}).status_code == 200
    assert family.get('/auth/2fa').json()['enabled'] is False


def test_only_the_site_session_manages_2fa(client, family):
    raw = family.post('/auth/tokens', json={'name': 'mcp'}).json()['token']
    api = {'Authorization': f'Bearer {raw}'}
    assert client.post(f'{API}/auth/2fa/setup', headers=api).status_code == 403
    assert client.post(f'{API}/auth/2fa/disable', headers=api, json={'password': PW, 'code': '123456'}).status_code == 403
