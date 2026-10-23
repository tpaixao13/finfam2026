"""Regressão da auditoria de segurança (V1–V4 e endurecimentos)."""
import io

import openpyxl

from app.core.db import session
from app.models import CategoryRule, CreditAccount, Goal, Tenant, User
from conftest import API, Family


def _upgrade(family, plan='anual'):
    tenant = session.get(Tenant, family.tenant['id'])
    tenant.plan = plan
    session.commit()
    session.remove()


def _add_member(family, email='greyce@test.com'):
    r = family.post('/family/members', json={'name': 'Greyce', 'email': email, 'password': 'senha-greyce',
                                             'confirm_password': 'senha-greyce'})
    assert r.status_code == 201, r.text
    return r.json()['member']


def _login(client, email, password):
    r = client.post(f'{API}/auth/login', json={'email': email, 'password': password})
    return r


# ── V1: sessão (JWT) vinculada à senha ───────────────────────────────────────

def test_jwt_invalid_after_password_change(client, family):
    old = dict(family.headers)
    r = family.post('/auth/change-password', json={'current_password': 'senha-segura', 'new_password': 'outra-senha-1',
                                                   'confirm_password': 'outra-senha-1'})
    assert r.status_code == 200 and r.json()['access_token']
    assert client.get(f'{API}/auth/me', headers=old).status_code == 401
    fresh = {'Authorization': f"Bearer {r.json()['access_token']}"}
    assert client.get(f'{API}/auth/me', headers=fresh).status_code == 200


def test_jwt_rejected_when_user_row_belongs_to_someone_else(client, family):
    """Simula o id reaproveitado: mesmo id, outra senha → o JWT antigo não vale."""
    user = session.get(User, family.user['id'])
    user.set_password('senha-de-outra-pessoa')
    session.commit()
    session.remove()
    assert family.get('/auth/me').status_code == 401


def test_deleted_user_id_is_not_reused(client, family):
    _upgrade(family)
    member = _add_member(family)
    assert family.delete(f"/family/members/{member['id']}").status_code == 200
    newcomer = Family(client, name='Nova', email='nova@test.com')
    assert newcomer.user['id'] > member['id']


# ── V2: token de API não mexe em conta/credenciais ───────────────────────────

def test_api_token_cannot_touch_account(client, family):
    raw = family.post('/auth/tokens', json={'name': 'bot'}).json()['token']
    bot = {'Authorization': f'Bearer {raw}'}
    uid = family.user['id']
    checks = [
        client.put(f'{API}/family/members/{uid}', headers=bot,
                   json={'name': 'X', 'email': 'atacante@x.com', 'new_password': 'tomada-123',
                         'confirm_password': 'tomada-123'}),
        client.post(f'{API}/family/members', headers=bot,
                    json={'name': 'X', 'email': 'x2@x.com', 'password': '12345678', 'confirm_password': '12345678'}),
        client.post(f'{API}/auth/change-password', headers=bot,
                    json={'current_password': 'senha-segura', 'new_password': 'tomada-123',
                          'confirm_password': 'tomada-123'}),
        client.get(f'{API}/auth/tokens', headers=bot),
        client.put(f'{API}/settings/telegram', headers=bot, json={'enabled': True, 'hour': 8, 'minute': 0}),
    ]
    assert [r.status_code for r in checks] == [403] * len(checks)
    # o token continua útil para o que foi feito: dados financeiros
    assert client.get(f'{API}/expenses/bills', headers=bot).status_code == 200
    assert _login(client, 'tiago@test.com', 'senha-segura').status_code == 200


def test_self_edit_requires_current_password(client, family):
    uid = family.user['id']
    body = {'name': 'Tiago', 'email': 'novo@test.com'}
    assert family.put(f'/family/members/{uid}', json=body).status_code == 422
    assert family.put(f'/family/members/{uid}', json={**body, 'current_password': 'senha-segura'}).status_code == 200
    assert family.put(f'/family/members/{uid}', json={'name': 'Tiago P', 'email': 'novo@test.com'}).status_code == 200


def test_only_owner_changes_other_members_credentials(client, family):
    _upgrade(family)
    greyce = _add_member(family)
    g = Family.__new__(Family)
    g.client = client
    g.headers = {'Authorization': f"Bearer {_login(client, 'greyce@test.com', 'senha-greyce').json()['access_token']}"}
    owner_id = family.user['id']
    r = g.put(f'/family/members/{owner_id}', json={'name': 'Tiago', 'email': 'greyce-controla@x.com'})
    assert r.status_code == 403
    assert g.delete(f'/family/members/{owner_id}').status_code == 403
    # o dono pode redefinir a senha de quem esqueceu
    r = family.put(f"/family/members/{greyce['id']}", json={'name': 'Greyce', 'email': 'greyce@test.com',
                                                           'new_password': 'nova-senha-g', 'confirm_password': 'nova-senha-g'})
    assert r.status_code == 200


# ── V3: nada fica órfão ao excluir pessoa ou família ─────────────────────────

def test_member_deletion_removes_goals_and_credit_accounts(client, family):
    _upgrade(family)
    member = _add_member(family)
    g = {'Authorization': f"Bearer {_login(client, 'greyce@test.com', 'senha-greyce').json()['access_token']}"}
    client.post(f'{API}/goals', headers=g, json={'title': 'Segredo', 'type': 'saving', 'target_amount': '100'})
    client.post(f'{API}/cards', headers=g, json={'supports_credit': True, 'last_digits': '1111', 'bank': 'Inter',
                                                 'due_day': 10, 'best_buy_day': 3, 'credit_limit': '87000'})
    assert family.delete(f"/family/members/{member['id']}").status_code == 200
    assert Goal.query.filter_by(user_id=member['id']).count() == 0
    assert CreditAccount.query.filter_by(user_id=member['id']).count() == 0
    session.remove()


def test_tenant_deletion_removes_category_rules(client, family):
    family.post('/rules', json={'keyword': 'clinica', 'category': 'Saúde'})
    admin = {'Authorization': 'Bearer ' + client.post(f'{API}/admin/login', json={
        'email': 'admin@finfam.test', 'password': 'admin-pass-123'}).json()['access_token']}
    assert client.delete(f"{API}/admin/tenants/{family.tenant['id']}", headers=admin).status_code == 200
    assert CategoryRule.query.filter_by(tenant_id=family.tenant['id']).count() == 0
    session.remove()


# ── V4: exportação não gera fórmulas ─────────────────────────────────────────

def test_xlsx_export_neutralizes_formulas(client, family):
    family.post('/expenses', json={'description': '=HYPERLINK("http://x","clique")', 'amount': '10',
                                   'category': 'Outros', 'payment_method': 'PIX', 'date': '2026-05-10'})
    content = family.get('/expenses/export', params={'month': 5, 'year': 2026}).content
    cell = openpyxl.load_workbook(io.BytesIO(content)).active['E2'].value
    assert cell.startswith("'=")


# ── Endurecimentos ───────────────────────────────────────────────────────────

def test_login_rate_limit(client, family, monkeypatch):
    monkeypatch.setenv('RATE_LIMIT_ENABLED', '1')
    codes = [_login(client, 'tiago@test.com', 'errada').status_code for _ in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429


def test_security_headers_and_crypto_whitelist(client, family):
    r = family.get('/meta')
    assert r.headers['X-Content-Type-Options'] == 'nosniff'
    assert family.get('/market/crypto-price', params={'coins': 'qualquer-coisa'}).status_code == 422


def test_avatar_name_is_not_predictable(client, family):
    r = family.post('/auth/avatar', files={'file': ('foto.png', b'\x89PNG\r\n\x1a\n' + b'0' * 20, 'image/png')})
    url = r.json()['user']['avatar_url']
    assert f"user_{family.user['id']}" not in url
