from datetime import datetime, timedelta

from app.core.types import utcnow
from app.core.db import session
from app.models import Tenant
from conftest import API


def test_register_login_me(client, family):
    r = client.post(f'{API}/auth/login', json={'email': 'TIAGO@test.com', 'password': 'senha-segura'})
    assert r.status_code == 200
    token = r.json()['access_token']
    me = client.get(f'{API}/auth/me', headers={'Authorization': f'Bearer {token}'}).json()
    assert me['user']['name'] == 'Tiago'
    assert me['tenant']['name'] == 'Família Tiago'
    assert me['tenant']['plan'] == 'trial'
    assert me['tenant']['days_left'] >= 89


def test_login_errors(client, family):
    assert client.post(f'{API}/auth/login', json={'email': 'tiago@test.com', 'password': 'errada'}).status_code == 401
    r = client.post(f'{API}/auth/login', json={'email': 'admin@finfam.test', 'password': 'admin-pass-123'})
    assert r.status_code == 403  # admin usa /admin/login
    r = client.post(f'{API}/auth/register', json={'name': 'X', 'email': 'nao-email', 'password': '1',
                                                  'confirm_password': '2'})
    assert r.status_code == 422 and 'detail' in r.json()


def test_duplicate_email(client, family):
    r = client.post(f'{API}/auth/register', json={'name': 'Tiago', 'email': 'tiago@test.com',
                                                  'password': 'senha-segura', 'confirm_password': 'senha-segura', 'accept_terms': True})
    assert r.status_code == 409


def test_requires_auth(client):
    assert client.get(f'{API}/dashboard').status_code == 401
    assert client.get(f'{API}/dashboard', headers={'Authorization': 'Bearer lixo'}).status_code == 401


def test_expired_plan_blocks_but_me_works(client, family):
    tenant = session.get(Tenant, family.tenant['id'])
    tenant.trial_expires_at = utcnow() - timedelta(days=1)
    session.commit()
    session.remove()
    r = family.get('/dashboard')
    assert r.status_code == 402
    assert r.json()['detail']['code'] == 'plan_expired'
    assert family.get('/auth/me').status_code == 200
    assert family.get('/plans').status_code == 200


def test_api_tokens(client, family):
    r = family.post('/auth/tokens', json={'name': 'Bot do Telegram'})
    assert r.status_code == 201
    raw = r.json()['token']
    assert raw.startswith('ffk_')
    bot = {'Authorization': f'Bearer {raw}'}
    me = client.get(f'{API}/auth/me', headers=bot).json()
    assert me['via_api_token'] is True
    # token de API não cria outros tokens
    assert client.post(f'{API}/auth/tokens', headers=bot, json={'name': 'x2'}).status_code == 403
    listed = family.get('/auth/tokens').json()
    assert listed[0]['prefix'] == raw[:12] and 'token' not in listed[0]
    assert family.delete(f"/auth/tokens/{listed[0]['id']}").status_code == 200
    assert client.get(f'{API}/auth/me', headers=bot).status_code == 401


def test_change_password(client, family):
    r = family.post('/auth/change-password', json={'current_password': 'errada', 'new_password': 'nova-senha-1',
                                                   'confirm_password': 'nova-senha-1'})
    assert r.status_code == 422
    r = family.post('/auth/change-password', json={'current_password': 'senha-segura',
                                                   'new_password': 'nova-senha-1', 'confirm_password': 'nova-senha-1'})
    assert r.status_code == 200
    assert client.post(f'{API}/auth/login', json={'email': 'tiago@test.com', 'password': 'nova-senha-1'}).status_code == 200


def test_avatar_upload(client, family):
    png = b'\x89PNG\r\n\x1a\n' + b'0' * 50
    r = family.post('/auth/avatar', files={'file': ('foto.png', png, 'image/png')})
    assert r.status_code == 200
    url = r.json()['user']['avatar_url']
    assert client.get(url).status_code == 200
    r = family.post('/auth/avatar', files={'file': ('x.exe', b'MZ', 'application/octet-stream')})
    assert r.status_code == 422


def test_family_member_limit(client, family):
    member = {'name': 'Greyce', 'email': 'greyce@test.com', 'password': 'senha-segura',
              'confirm_password': 'senha-segura'}
    r = family.post('/family/members', json=member)
    assert r.status_code == 403  # trial = 1 membro
    tenant = session.get(Tenant, family.tenant['id'])
    tenant.plan = 'anual'
    session.commit()
    session.remove()
    r = family.post('/family/members', json=member)
    assert r.status_code == 201
    data = family.get('/family').json()
    assert data['member_count'] == 2 and data['member_limit'] == 2
    gid = r.json()['member']['id']
    r = family.put(f'/family/members/{gid}', json={'name': 'Greyce P', 'email': 'greyce@test.com'})
    assert r.status_code == 200
    assert family.delete(f"/family/members/{family.user['id']}").status_code == 422
    assert family.delete(f'/family/members/{gid}').status_code == 200


def test_terms_must_be_accepted(client, family):
    r = client.post(f'{API}/auth/register', json={'name': 'Joana', 'email': 'joana@test.com', 'password': 'senha-segura',
                                                  'confirm_password': 'senha-segura'})
    assert r.status_code == 422 and 'Termos' in r.json()['detail']
    me = family.get('/auth/me').json()
    assert me['terms']['accepted'] is True and me['terms']['version']
    from app.core.db import session
    from app.models import Tenant
    session.get(Tenant, family.tenant['id']).plan = 'anual'
    session.commit()
    family.post('/family/members', json={'name': 'Greyce', 'email': 'gt@test.com', 'password': 'senha-segura',
                                         'confirm_password': 'senha-segura'})
    tok = client.post(f'{API}/auth/login', json={'email': 'gt@test.com', 'password': 'senha-segura'}).json()['access_token']
    g = {'Authorization': f'Bearer {tok}'}
    assert client.get(f'{API}/auth/me', headers=g).json()['terms']['accepted'] is False  # convidada: aceita no 1º acesso
    assert client.post(f'{API}/auth/accept-terms', headers=g).json()['terms']['accepted'] is True
    session.remove()


def test_delete_account_member_owner_and_family(client, family, other_family):
    from app.core.db import session
    from app.models import Expense, Tenant, User
    session.get(Tenant, family.tenant['id']).plan = 'anual'
    session.commit()
    family.post('/family/members', json={'name': 'Greyce', 'email': 'gd@test.com', 'password': 'senha-segura',
                                         'confirm_password': 'senha-segura'})
    tok = client.post(f'{API}/auth/login', json={'email': 'gd@test.com', 'password': 'senha-segura'}).json()['access_token']
    g = {'Authorization': f'Bearer {tok}'}
    client.post(f'{API}/expenses', headers=g, json={'description': 'Farmácia', 'amount': '30', 'category': 'Saúde',
                                                   'payment_method': 'PIX'})
    body = {'password': 'senha-segura', 'confirm': 'EXCLUIR'}
    # quem criou a família, com outras pessoas, não sai sozinho
    assert family.post('/auth/delete-account', json=body).status_code == 409
    # confirmações
    assert client.post(f'{API}/auth/delete-account', headers=g, json={**body, 'confirm': 'sim'}).status_code == 422
    assert client.post(f'{API}/auth/delete-account', headers=g, json={**body, 'password': 'errada'}).status_code == 422
    assert client.post(f'{API}/auth/delete-account', headers=g, json={**body, 'scope': 'family'}).status_code == 403
    # token de API não exclui conta
    raw = family.post('/auth/tokens', json={'name': 'mcp'}).json()['token']
    assert client.post(f'{API}/auth/delete-account', headers={'Authorization': f'Bearer {raw}'}, json=body).status_code == 403
    # a Greyce sai: ela e os gastos dela somem; a família continua
    r = client.post(f'{API}/auth/delete-account', headers=g, json=body)
    assert r.status_code == 200 and r.json()['deleted'] == 'me'
    assert User.query.filter_by(email='gd@test.com').first() is None
    assert Expense.query.filter_by(description='Farmácia').count() == 0
    assert client.get(f'{API}/auth/me', headers=g).status_code == 401
    session.remove()
    # agora sozinho: quem criou exclui a conta e a família vai junto; a outra família não é tocada
    r = family.post('/auth/delete-account', json=body)
    assert r.status_code == 200 and r.json()['deleted'] == 'family'
    assert session.get(Tenant, family.tenant['id']) is None and User.query.filter_by(email='tiago@test.com').first() is None
    assert other_family.get('/auth/me').status_code == 200
    session.remove()
