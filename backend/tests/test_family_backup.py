"""Admin: backup completo da família, zerar despesas e restaurar."""
import gzip
import json

import pytest

from app.core.db import session
from app.models import Expense, InstallmentGroup, Salary
from app.services import family_backup as fb
from conftest import API
from test_pluggy import _admin


@pytest.fixture(autouse=True)
def _backup_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(fb, 'BACKUP_DIR', tmp_path / 'backups')


def _seed(family):
    card = family.post('/cards', json={'supports_credit': True, 'last_digits': '6494', 'bank': 'Nubank',
                                       'due_day': 10, 'best_buy_day': 3}).json()['card']
    for body in ({'description': 'Celular', 'amount': '900', 'payment_method': 'Cartão de Crédito',
                  'card_id': card['id'], 'installments': 3, 'date': '2026-09-20'},
                 {'description': 'Mercado', 'amount': '120', 'payment_method': 'PIX', 'bank': 'Caixa',
                  'date': '2026-10-02', 'paid': True},
                 {'description': 'Internet', 'amount': '100', 'payment_method': 'PIX', 'recurring_months': 6,
                  'date': '2026-08-15'}):
        assert family.post('/expenses', json={'category': 'Outros', **body}).status_code == 201
    session.add(Salary(user_id=family.user['id'], company='Empresa', amount=5000, month=10, year=2026))
    session.commit()
    return card


def _snapshot():
    return sorted((e.description, float(e.amount), e.card_id, e.year, e.month, e.day, e.paid,
                   e.installment_number, e.recurring_number) for e in Expense.query.all())


def test_backup_wipe_and_restore(client, family, other_family):
    _seed(family)
    other_family.post('/expenses', json={'description': 'Da outra', 'amount': '10', 'category': 'Outros',
                                         'payment_method': 'PIX', 'date': '2026-10-01'})
    tid = family.tenant['id']
    adm = _admin(client)
    # só o admin
    assert client.post(f'{API}/admin/tenants/{tid}/backups', headers=family.headers).status_code in (401, 403)
    assert client.post(f'{API}/admin/tenants/{tid}/wipe-expenses', headers=family.headers,
                       json={'confirm': 'x'}).status_code in (401, 403)

    r = client.post(f'{API}/admin/tenants/{tid}/backups', headers=adm)
    assert r.status_code == 200, r.text
    name = r.json()['name']
    raw = client.get(f'{API}/admin/tenants/{tid}/backups/{name}', headers=adm)
    data = json.loads(gzip.decompress(raw.content))
    assert data['tenant']['id'] == tid and len(data['tables']['expenses']) == 1 + 3 + 6
    assert 'salaries' in data['tables'] and 'credit_cards' in data['tables']
    assert all('password_hash' not in u for u in data['tables']['users'])  # sem segredos
    assert all(e['user_id'] != other_family.user['id'] for e in data['tables']['expenses'])
    assert client.get(f'{API}/admin/tenants/{tid}/backups/familia_999_20260101_000000.json.gz',
                      headers=adm).status_code == 404

    mine = [e for e in _snapshot()]
    before = [e for e in mine if e[0] != 'Da outra']
    session.remove()
    r = client.post(f'{API}/admin/tenants/{tid}/wipe-expenses', headers=adm, json={'confirm': 'errado'})
    assert r.status_code == 422
    r = client.post(f'{API}/admin/tenants/{tid}/wipe-expenses', headers=adm,
                    json={'confirm': family.tenant['name'].upper()})
    assert r.status_code == 200, r.text
    assert r.json()['deleted'] == 10
    assert [e[0] for e in _snapshot()] == ['Da outra']  # a outra família fica
    assert Salary.query.count() == 1 and InstallmentGroup.query.count() == 0  # renda fica
    backups = r.json()['backups']
    assert [b['reason'] for b in backups][:1] == ['antes de zerar'] and backups[0]['expenses'] == 10
    session.remove()

    r = client.post(f'{API}/admin/tenants/{tid}/restore', headers=adm,
                    json={'name': backups[0]['name'], 'confirm': family.tenant['name']})
    assert r.status_code == 200, r.text
    assert r.json()['restored'] == 10
    assert [e for e in _snapshot() if e[0] != 'Da outra'] == before  # exatamente como estava
    grupo = Expense.query.filter_by(description='Celular').first().installment_group
    assert grupo is not None and grupo.num_installments == 3
    assert client.post(f'{API}/admin/tenants/{tid}/restore', headers=adm,
                       json={'name': '../x.json.gz', 'confirm': family.tenant['name']}).status_code == 422
    session.remove()


def test_wipe_reimports_from_connected_banks(client, family, bank, txs):  # noqa: F811
    from datetime import date, timedelta
    from test_pluggy_import import _connect, _post_item, day
    acc, _cc, _ = _connect(client, family, bank, txs)
    txs[acc] += [{'id': 't-1', 'type': 'DEBIT', 'amount': -50.0, 'date': day(40), 'description': 'Antigo'},
                 {'id': 't-2', 'type': 'DEBIT', 'amount': -20.0, 'date': day(2), 'description': 'Recente'}]
    _post_item(family)
    family.post('/expenses', json={'description': 'Recente à mão', 'amount': '20', 'category': 'Outros',
                                   'payment_method': 'PIX', 'date': (date.today() - timedelta(days=2)).isoformat()})
    r = client.post(f"{API}/admin/tenants/{family.tenant['id']}/wipe-expenses", headers=_admin(client),
                    json={'confirm': family.tenant['name'], 'months': 3})
    assert r.status_code == 200, r.text
    assert r.json()['imported'] == 2  # os dois voltam do banco para revisar
    pend = {t['description'] for t in family.get('/pluggy/transactions').json()['items']}
    assert pend == {'Antigo', 'Recente'}
    session.remove()


from test_pluggy_import import txs  # noqa: E402,F401  (fixtures)
from test_pluggy_items import bank  # noqa: E402,F401


def test_backup_leaves_out_invite_code_and_chat(client, family):
    from app.models import Tenant
    tenant = session.get(Tenant, family.user['tenant_id']) if 'tenant_id' in family.user else Tenant.query.first()
    data = fb.dump(tenant)
    row = data['tables']['tenants'][0]
    assert 'code' not in row and 'telegram_chat_id' not in row and 'name' in row
    session.remove()


def test_restore_skips_bank_rows_of_accounts_outside_family(client, family, other_family):
    from app.models import BankAccount, BankConnection, Tenant, User
    other_uid = other_family.user['id']
    conn = BankConnection(user_id=other_uid, item_id='11111111-2222-3333-4444-555555555555')
    session.add(conn)
    session.flush()
    acc = BankAccount(connection_id=conn.id, pluggy_account_id='acc-outra', type='CREDIT')
    session.add(acc)
    session.commit()
    me = session.get(User, family.user['id'])
    tenant = session.get(Tenant, me.tenant_id)
    forged = {'format': fb.FORMAT, 'version': fb.VERSION, 'tables': {'bank_transactions': [
        {'id': 999, 'account_id': acc.id, 'user_id': me.id, 'pluggy_id': 'forjado', 'date': '2026-10-01',
         'description': 'x', 'amount': '1.00', 'status': 'pending', 'kind': 'expense'}]}}
    fb.restore_expenses(tenant, forged)
    session.commit()
    from app.models import BankTransaction
    assert BankTransaction.query.filter_by(pluggy_id='forjado').first() is None
    session.remove()
