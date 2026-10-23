from datetime import date, datetime, timedelta

from app.core.db import session
from app.models import CreditCard
from conftest import API


def test_card_rules_accounts_and_invoice(family):
    r = family.post('/cards', json={'supports_credit': True, 'last_digits': '1234', 'bank': 'Nubank',
                                    'due_day': 5, 'best_buy_day': 10})
    assert r.status_code == 422 and 'melhor dia' in r.json()['detail']
    main = family.post('/cards', json={'supports_credit': True, 'supports_debit': True, 'last_digits': '1234',
                                       'bank': 'Nubank', 'due_day': 10, 'best_buy_day': 3,
                                       'credit_limit': '1000'}).json()['card']
    virtual = family.post('/cards', json={'supports_credit': True, 'last_digits': '5678', 'bank': 'Nubank',
                                          'due_day': 10, 'best_buy_day': 3, 'is_virtual': True}).json()['card']
    assert main['account_id'] == virtual['account_id'] is not None

    def buy(card_id, amount, method='Cartão de Crédito'):
        return family.post('/expenses', json={'description': 'Compra', 'amount': amount, 'category': 'Outros',
                                              'payment_method': method, 'card_id': card_id,
                                              'date': '2026-05-02'}).json()

    buy(main['id'], '300')
    buy(virtual['id'], '500')
    buy(main['id'], '80', method='Cartão de Débito')

    cards = family.get('/cards', params={'month': 5, 'year': 2026}).json()
    usage = {c['id']: c['usage'] for c in cards['cards']}
    assert usage[main['id']]['credit_total'] == 800  # limite compartilhado da conta
    assert usage[main['id']]['debit_total'] == 80
    assert usage[main['id']]['pct'] == 80.0
    assert cards['history']['labels'][-1] == 'Mai/2026'

    inv = family.get(f"/cards/{main['id']}/invoice", params={'month': 5, 'year': 2026}).json()
    assert inv['credit']['total'] == 800 and len(inv['by_card']) == 2
    assert inv['debit']['total'] == 80

    preview = family.get(f"/cards/{main['id']}/invoice-preview",
                         params={'day': 20, 'month': 5, 'year': 2026, 'amount': 50}).json()
    assert (preview['invoice_month'], preview['is_open'], preview['is_shared_limit']) == (6, False, True)

    paid = family.post(f"/cards/{virtual['id']}/invoice/pay", json={'month': 5, 'year': 2026}).json()
    assert paid['updated'] == 2
    inv = family.get(f"/cards/{main['id']}/invoice", params={'month': 5, 'year': 2026}).json()
    assert inv['credit']['pending_total'] == 0

    assert family.delete(f"/cards/{virtual['id']}").status_code == 200
    lst = family.get('/expenses', params={'month': 5, 'year': 2026}).json()
    assert lst['total'] == 3  # despesas mantidas


def test_vr_balance_carry_over(family):
    card = family.post('/cards', json={'special_type': 'vr', 'last_digits': '0001', 'bank': 'Outros',
                                       'monthly_amount': '600', 'renewal_day': 1}).json()['card']
    obj = session.get(CreditCard, card['id'])
    obj.created_at = datetime(2026, 3, 1)
    session.commit()
    session.remove()
    family.post('/expenses', json={'description': 'Almoço', 'amount': '400', 'category': 'Alimentação',
                                   'payment_method': 'VR', 'date': '2026-03-10'})  # associa sozinho ao VR
    family.post('/expenses', json={'description': 'Almoço', 'amount': '100', 'category': 'Alimentação',
                                   'payment_method': 'VR', 'date': '2026-04-10'})
    usage = {c['id']: c['usage'] for c in family.get('/cards', params={'month': 4, 'year': 2026}).json()['cards']}
    vr = usage[card['id']]['vr_balance']
    assert vr['carry_over'] == 200 and vr['available'] == 800 and vr['remaining'] == 700


def test_incomes(family):
    r = family.post('/incomes', json={'amount': '5000', 'company': 'ACME', 'month': 11, 'year': 2026,
                                      'recurring_months': 3, 'payment_day_type': 'util', 'payment_day': 5})
    assert r.status_code == 201 and '3 meses' in r.json()['message']
    first = r.json()['incomes'][0]
    assert first['expected_date'] == '2026-11-09'  # 5º dia útil (02/11 feriado)
    r = family.put(f"/incomes/{r.json()['incomes'][1]['id']}", json={'amount': '5500', 'update_forward': True})
    assert r.json()['updated'] == 2
    jan = family.get('/incomes', params={'month': 1, 'year': 2027}).json()
    assert jan['fixed_total'] == 5500 and jan['pending_total'] == 5500
    family.post(f"/incomes/{jan['fixed'][0]['id']}/toggle-received")
    jan = family.get('/incomes', params={'month': 1, 'year': 2027}).json()
    assert jan['received_total'] == 5500
    r = family.post('/incomes', json={'amount': '300', 'income_type': 'variavel', 'company': 'Freelance',
                                      'month': 1, 'year': 2027, 'payment_day_type': 'util', 'payment_day': 30})
    assert r.status_code == 422


def test_goals(family):
    today = date.today()
    r = family.post('/goals', json={'title': 'Mercado', 'type': 'spending_limit', 'target_amount': '100',
                                    'category': 'Alimentação'})
    gid = r.json()['goal']['id']
    family.post('/expenses', json={'description': 'Feira', 'amount': '120', 'category': 'Alimentação',
                                   'payment_method': 'PIX', 'date': today.isoformat()})
    goals = family.get('/goals').json()['goals']
    assert goals[0]['status'] == 'over_limit' and goals[0]['over_amount'] == 20
    assert family.patch(f'/goals/{gid}', json={'target_amount': '500'}).status_code == 200
    assert family.get('/goals').json()['goals'][0]['status'] == 'on_track'
    family.post(f'/goals/{gid}/complete')
    assert family.get('/goals').json()['goals'][0]['status'] == 'completed'
    family.delete(f'/goals/{gid}')
    assert family.get('/goals').json()['goals'] == []


def test_rules_conflict(family):
    assert family.post('/rules', json={'keyword': 'uber', 'category': 'Transporte'}).status_code == 201
    assert family.post('/rules', json={'keyword': 'UBER', 'category': 'Lazer'}).status_code == 409
    rid = family.get('/rules').json()[0]['id']
    assert family.put(f'/rules/{rid}', json={'keyword': '99app', 'category': 'Transporte'}).status_code == 200


def test_dashboard_alerts_charts(family):
    today = date.today()
    family.post('/incomes', json={'amount': '1000', 'month': today.month, 'year': today.year, 'received': True})
    family.post('/expenses', json={'description': 'Aluguel', 'amount': '1200', 'category': 'Moradia',
                                   'payment_method': 'PIX', 'date': (today - timedelta(days=40)).isoformat()})
    family.post('/expenses', json={'description': 'Aluguel', 'amount': '990', 'category': 'Moradia',
                                   'payment_method': 'PIX', 'date': today.isoformat()})
    d = family.get('/dashboard').json()
    assert d['kpis']['income'] == 1000 and d['kpis']['expenses'] == 990
    assert d['people'][0]['user']['name'] == 'Tiago'
    assert len(d['pending']) == 1 and isinstance(d['insights'], list)
    a = family.get('/alerts').json()
    assert a['danger'] >= 1 and any('atraso' in x['title'] for x in a['alerts'])
    assert all(x['action_url'].startswith('/') for x in a['alerts'])
    me = family.get('/auth/me').json()
    assert me['alert_count'] >= 1
    for path in ('/charts/categories', '/charts/monthly', '/charts/people', '/charts/daily',
                 '/charts/payment-methods', '/charts/paid-vs-pending', '/charts/investments',
                 '/expenses/summary/by-category'):
        assert family.get(path).status_code == 200, path
    assert family.get('/charts/monthly', params={'months': 3}).json()['expenses'][-1] == 990


def test_investments(family, monkeypatch):
    from app.services import market
    monkeypatch.setattr(market, 'fetch_json', lambda *a, **k: None)  # sem rede nos testes
    r = family.post('/investments', json={'amount': '1000', 'investment_type': 'CDB', 'annual_rate': '12',
                                          'month': 1, 'year': 2026})
    assert r.status_code == 201
    r = family.post('/investments', json={'amount': '1000', 'investment_type': 'Criptomoedas',
                                          'month': 1, 'year': 2026})
    assert r.status_code == 422
    data = family.get('/investments').json()
    assert data['fixed_total'] == 1000 and data['selic_is_live'] is False
    assert data['fixed'][0]['daily_yield']['gross'] > 0
    proj = family.get('/charts/investments').json()
    assert proj['projected'][-1] > 1000 >= proj['projected_net'][0] - 1000


def test_imports_csv(family):
    csv = 'data,descricao,valor\n10/05/2026,PADARIA BOM PAO,-25.50\n11/05/2026,SALARIO,3000\n'
    r = family.post('/imports/preview', files={'file': ('extrato.csv', csv.encode(), 'text/csv')},
                    data={'format': 'csv_generic', 'user_id': str(family.user['id'])})
    assert r.status_code == 200, r.text
    items = r.json()['items']
    assert len(items) == 1 and items[0]['amount'] == 25.5
    items[0]['create_rule'] = True
    items[0]['category'] = 'Alimentação'
    keep = {k: items[0][k] for k in ('user_id', 'day', 'month', 'year', 'description', 'amount',
                                     'category', 'payment_method', 'bank', 'create_rule')}
    r = family.post('/imports/confirm', json={'items': [keep]})
    assert r.json() == {'imported': 1, 'rules_created': 1, 'message': r.json()['message']}
    assert family.get('/rules').json()[0]['keyword'] == 'PADARIA'

    export = family.get('/expenses/export', params={'month': 5, 'year': 2026}).content
    r = family.post('/imports/finfam-xlsx', files={'file': ('despesas.xlsx', export, 'application/octet-stream')})
    assert r.json()['imported'] == 1


def test_telegram_settings_masked(family):
    r = family.put('/settings/telegram', json={'enabled': True, 'token': '123456:ABCDEF', 'chat_id': '987654321',
                                               'hour': 9, 'minute': 30})
    tg = r.json()['telegram']
    assert tg['configured'] and tg['token_masked'].endswith('CDEF') and '123456' not in tg['token_masked']
    r = family.put('/settings/telegram', json={'enabled': False, 'hour': 8, 'minute': 0})
    assert r.json()['telegram']['configured'] is True  # vazio mantém os valores


def test_daily_reminder_text(family):
    from app.models import User
    from app.services.telegram import build_daily_reminder
    today = date.today()
    family.post('/expenses', json={'description': 'Conta de luz', 'amount': '150', 'category': 'Moradia',
                                   'payment_method': 'PIX', 'date': today.isoformat()})
    users = User.query.filter_by(tenant_id=family.tenant['id']).all()
    text = build_daily_reminder(users)
    session.remove()
    assert 'Vencimentos de hoje' in text and 'Conta de luz' in text


def test_admin_flow(client, family):
    r = client.post(f'{API}/admin/login', json={'email': 'admin@finfam.test', 'password': 'admin-pass-123'})
    assert r.status_code == 200
    admin = {'Authorization': f"Bearer {r.json()['access_token']}"}
    assert family.get('/admin/dashboard').status_code == 403
    assert client.get(f'{API}/dashboard', headers=admin).status_code == 403
    d = client.get(f'{API}/admin/dashboard', headers=admin).json()
    assert d['counts']['tenants'] == 1 and d['counts']['trial_active'] == 1
    tid = family.tenant['id']
    assert client.post(f'{API}/admin/tenants/{tid}/extra-members', headers=admin).status_code == 422
    r = client.post(f'{API}/admin/tenants/{tid}/plan', headers=admin, json={'plan': 'vitalicio'})
    assert r.json()['tenant']['member_limit'] == 4 and r.json()['tenant']['expires_at'] is None
    r = client.post(f'{API}/admin/tenants/{tid}/extra-members', headers=admin)
    assert r.json()['tenant']['member_limit'] == 5
    client.post(f'{API}/admin/tenants/{tid}/revoke', headers=admin)
    assert family.get('/dashboard').status_code == 402
    r = client.post(f"{API}/admin/users/{family.user['id']}/reset-password", headers=admin,
                    json={'new_password': 'resetada-123'})
    assert r.status_code == 200
    assert client.delete(f"{API}/admin/users/{family.user['id']}", headers=admin).status_code == 200
    assert client.get(f'{API}/admin/dashboard', headers=admin).json()['counts']['tenants'] == 0


def test_openapi_and_meta(client, family):
    spec = client.get('/api/openapi.json').json()
    assert '/api/v1/expenses/bills' in spec['paths']
    meta = family.get('/meta').json()
    assert 'Cartão de Crédito' in meta['payment_methods'] and len(meta['banks']) > 10
