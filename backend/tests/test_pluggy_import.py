"""Open Finance (Pluggy) — etapa 3: lançamentos do banco → "Revisar importados"."""
from datetime import date, timedelta

import pytest

from app.core.db import session
from app.core.security import decrypt_field
from app.models import BankConnection, BankTransaction, Expense, PluggyConfig
from app.services import open_finance as of
from app.services import pluggy as pg
from conftest import API
from test_pluggy import _save
from test_pluggy_items import ITEM, _ready, bank  # noqa: F401  (fixture)

TODAY = date.today()


def day(n_ago: int) -> str:
    return f'{(TODAY - timedelta(days=n_ago)).isoformat()}T15:00:00.000Z'


@pytest.fixture()
def txs(bank):  # noqa: F811
    """Acrescenta /transactions à Pluggy de mentira: {account_id: [lançamentos]}."""
    data: dict[str, list] = {}
    inner = pg._http

    def http(method, path, body=None, api_key=None):
        if path.startswith('/transactions?'):
            params = dict(p.split('=', 1) for p in path.split('?', 1)[1].split('&'))
            since = params['from']
            rows = [t for t in data.get(params['accountId'], []) if t['date'][:10] >= since]
            return {'total': len(rows), 'totalPages': 1, 'page': 1, 'results': rows}
        return inner(method, path, body, api_key)
    pg._http = http
    return data


def _connect(client, family, bank, txs, months=1, card_digits=None):  # noqa: F811
    _ready(client, family)
    card = None
    if card_digits:
        card = family.post('/cards', json={'supports_credit': True, 'last_digits': card_digits, 'bank': 'Nubank',
                                           'due_day': 10, 'best_buy_day': 3}).json()['card']
    bank['add'](ITEM)
    acc, cc = f'{ITEM[:8]}-acc', f'{ITEM[:8]}-cc'
    txs.setdefault(acc, [])
    txs.setdefault(cc, [])
    return acc, cc, card


def _post_item(family, months=None):
    body = {'item_id': ITEM, **({'months': months} if months else {})}
    r = family.post('/pluggy/items', json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_import_classifies_what_arrives(client, family, bank, txs):  # noqa: F811
    acc, cc, card = _connect(client, family, bank, txs, card_digits='5162')
    family.post('/expenses', json={'description': 'Padaria', 'amount': '23.40', 'category': 'Alimentação',
                                   'payment_method': 'PIX', 'date': (TODAY - timedelta(days=2)).isoformat()})
    txs[acc] += [
        {'id': 't-pix', 'type': 'DEBIT', 'amount': -150.0, 'date': day(1), 'description': 'PIX Maria Mercado',
         'paymentData': {'paymentMethod': 'PIX'}, 'status': 'POSTED'},
        {'id': 't-pad', 'type': 'DEBIT', 'amount': -23.4, 'date': day(1), 'description': 'PADARIA DO ZE'},
        {'id': 't-fat', 'type': 'DEBIT', 'amount': -900.0, 'date': day(3), 'description': 'Pagamento de fatura',
         'category': 'Credit card payment'},
        {'id': 't-sal', 'type': 'CREDIT', 'amount': 5000.0, 'date': day(4), 'description': 'Salário'},
        {'id': 't-old', 'type': 'DEBIT', 'amount': -10.0, 'date': day(80), 'description': 'Antigo'},
    ]
    txs[cc] += [
        {'id': 'c-tv', 'type': 'DEBIT', 'amount': 300.0, 'date': day(5), 'description': 'LOJA TV 1/3',
         'status': 'PENDING', 'creditCardMetadata': {'installmentNumber': 1, 'totalInstallments': 3,
                                                     'totalAmount': 900.0, 'purchaseDate': day(5)}},
        {'id': 'c-pay', 'type': 'CREDIT', 'amount': -900.0, 'date': day(3), 'description': 'Pagamento recebido'},
    ]
    r = _post_item(family, months=1)
    assert '2 lançamento(s) para revisar' in r['message']
    assert BankConnection.query.first().import_from == of.months_back(TODAY, 1)

    pend = family.get('/pluggy/transactions').json()
    assert pend['counts'] == {'pending': 2, 'duplicate': 1, 'ignored': 1, 'imported': 0}
    by = {t['description']: t for t in pend['items']}
    assert by['PIX Maria Mercado']['payment_method'] == 'PIX' and by['PIX Maria Mercado']['amount'] == 150.0
    tv = by['LOJA TV 1/3']
    assert tv['payment_method'] == 'Cartão de Crédito' and tv['installment'] == {'number': 1, 'total': 3}
    assert tv['card_id'] == card['id'] and tv['bank_pending'] is True
    dup = family.get('/pluggy/transactions?status=duplicate').json()['items'][0]
    assert dup['description'] == 'PADARIA DO ZE' and dup['expense']['description'] == 'Padaria'
    ign = family.get('/pluggy/transactions?status=ignored').json()['items'][0]
    assert 'fatura' in ign['reason']
    assert family.get('/pluggy/config').json()['to_review'] == 2
    assert Expense.query.count() == 1  # nada foi lançado sozinho
    session.remove()


def test_approve_creates_expenses_like_the_site(client, family, other_family, bank, txs):  # noqa: F811
    acc, cc, card = _connect(client, family, bank, txs, card_digits='5162')
    txs[acc].append({'id': 't-pix', 'type': 'DEBIT', 'amount': -150.0, 'date': day(1),
                     'description': 'PIX Maria Mercado', 'paymentData': {'paymentMethod': 'PIX'}})
    txs[cc].append({'id': 'c-tv', 'type': 'DEBIT', 'amount': 300.0, 'date': day(5), 'description': 'LOJA TV',
                    'creditCardMetadata': {'installmentNumber': 1, 'totalInstallments': 3, 'totalAmount': 900.0,
                                           'purchaseDate': day(5)}})
    _post_item(family)
    items = {t['description']: t for t in family.get('/pluggy/transactions').json()['items']}
    pix, tv = items['PIX Maria Mercado'], items['LOJA TV']
    # outra família não aprova os meus lançamentos
    other = other_family.post('/pluggy/transactions/approve', json={'items': [{'id': pix['id'], 'category': 'Lazer'}]})
    assert other.status_code in (403, 404)
    r = family.post('/pluggy/transactions/approve', json={'items': [
        {'id': pix['id'], 'category': 'Alimentação', 'description': 'Feira da Maria'},
        {'id': tv['id'], 'category': 'Categoria Inventada'}]})
    assert r.status_code == 200, r.text
    assert r.json()['imported'] == 2
    feira = Expense.query.filter_by(description='Feira da Maria').one()
    assert feira.payment_method == 'PIX' and feira.paid is True and feira.bank == 'Nubank'
    parcelas = Expense.query.filter_by(description='LOJA TV').order_by(Expense.installment_number).all()
    assert [float(p.amount) for p in parcelas] == [300.0, 300.0, 300.0]
    assert all(p.card_id == card['id'] and p.category == 'Outros' and not p.paid for p in parcelas)
    parcela_ids = [p.id for p in parcelas]
    counts = family.get('/pluggy/transactions').json()['counts']
    assert counts['pending'] == 0 and counts['imported'] == 2
    # aprovar de novo não duplica
    family.post('/pluggy/transactions/approve', json={'items': [{'id': pix['id'], 'category': 'Lazer'}]})
    assert Expense.query.filter_by(description='Feira da Maria').count() == 1
    session.remove()

    # mês seguinte: a parcela 2/3 chega e é reconhecida como já lançada
    txs[cc].append({'id': 'c-tv2', 'type': 'DEBIT', 'amount': 300.0, 'date': day(0), 'description': 'LOJA TV',
                    'creditCardMetadata': {'installmentNumber': 2, 'totalInstallments': 3, 'purchaseDate': day(5)}})
    session.query(BankConnection).update({'needs_sync': True})
    session.commit()
    session.remove()
    dup = family.get('/pluggy/transactions?status=duplicate').json()['items']
    assert [d['description'] for d in dup] == ['LOJA TV']
    assert dup[0]['expense']['id'] == parcela_ids[1]
    session.remove()


def test_ignore_and_restore(client, family, bank, txs):  # noqa: F811
    acc, _cc, _ = _connect(client, family, bank, txs)
    txs[acc].append({'id': 't-1', 'type': 'DEBIT', 'amount': -50.0, 'date': day(1), 'description': 'Uber'})
    _post_item(family)
    tid = family.get('/pluggy/transactions').json()['items'][0]['id']
    assert family.post('/pluggy/transactions/ignore', json={'ids': [tid]}).status_code == 200
    assert family.get('/pluggy/transactions?status=ignored').json()['items'][0]['reason'] == 'Ignorado por você.'
    assert family.post('/pluggy/transactions/restore', json={'ids': [tid]}).status_code == 200
    assert family.get('/pluggy/transactions').json()['counts']['pending'] == 1
    assert family.post('/pluggy/transactions/ignore', json={'ids': [tid, 99999]}).status_code == 404
    session.remove()


def test_webhook_brings_new_transactions_and_drops_vanished(client, family, bank, txs):  # noqa: F811
    acc, cc, _ = _connect(client, family, bank, txs)
    txs[cc].append({'id': 'c-pend', 'type': 'DEBIT', 'amount': 80.0, 'date': day(1), 'description': 'IFOOD',
                    'status': 'PENDING'})
    _post_item(family)
    cfg = PluggyConfig.query.first()
    url, headers = f'{API}/pluggy/webhook/{cfg.hook_token}', {
        'X-FinFam-Hook': decrypt_field(cfg.hook_secret), 'CF-Connecting-IP': '52.67.145.81'}
    session.remove()
    # o banco troca a compra pendente pela lançada (id novo) e chega uma nova
    txs[cc][:] = [{'id': 'c-post', 'type': 'DEBIT', 'amount': 80.0, 'date': day(1), 'description': 'IFOOD',
                   'status': 'POSTED'},
                  {'id': 'c-new', 'type': 'DEBIT', 'amount': 35.0, 'date': day(0), 'description': 'Farmácia'}]
    assert client.post(url, headers=headers, json={'event': 'transactions/created', 'itemId': ITEM}).status_code == 200
    items = family.get('/pluggy/transactions').json()['items']
    assert sorted(t['description'] for t in items) == ['Farmácia', 'IFOOD']
    assert BankTransaction.query.filter_by(pluggy_id='c-pend').count() == 0
    session.remove()


def test_window_choice_and_cron_sync(client, family, bank, txs):  # noqa: F811
    acc, _cc, _ = _connect(client, family, bank, txs)
    txs[acc] += [{'id': 't-50', 'type': 'DEBIT', 'amount': -10.0, 'date': day(50), 'description': 'Dois meses'},
                 {'id': 't-200', 'type': 'DEBIT', 'amount': -10.0, 'date': day(200), 'description': 'Muito antigo'}]
    assert family.post('/pluggy/items', json={'item_id': ITEM, 'months': 2}).status_code == 422  # 1, 3, 6 ou 12
    _post_item(family, months=3)
    assert [t['description'] for t in family.get('/pluggy/transactions').json()['items']] == ['Dois meses']
    # o script do cron relê quem está há mais de N horas sem leitura
    txs[acc].append({'id': 't-0', 'type': 'DEBIT', 'amount': -12.0, 'date': day(0), 'description': 'Hoje'})
    session.query(BankConnection).update({'tx_synced_at': None})
    session.commit()
    assert of.sync_stale(max_age_hours=24) == 1
    assert of.sync_stale(max_age_hours=24) == 0  # acabou de ler
    session.remove()


def test_disconnect_keeps_approved_expenses(client, family, bank, txs):  # noqa: F811
    acc, _cc, _ = _connect(client, family, bank, txs)
    txs[acc].append({'id': 't-1', 'type': 'DEBIT', 'amount': -50.0, 'date': day(1), 'description': 'Uber'})
    txs[acc].append({'id': 't-2', 'type': 'DEBIT', 'amount': -20.0, 'date': day(1), 'description': 'Café'})
    conn = _post_item(family)['item']
    tid = next(t['id'] for t in family.get('/pluggy/transactions').json()['items'] if t['description'] == 'Uber')
    family.post('/pluggy/transactions/approve', json={'items': [{'id': tid, 'category': 'Transporte'}]})
    assert family.delete(f"/pluggy/items/{conn['id']}").status_code == 200
    assert BankTransaction.query.count() == 0  # dados do banco apagados
    assert Expense.query.filter_by(description='Uber').count() == 1  # a despesa aprovada é sua
    _save(family)  # chave continua
    session.remove()
