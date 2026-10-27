"""Open Finance (Pluggy) — etapa 3: lançamentos do banco → "Revisar importados"."""
from datetime import date, timedelta

import pytest

from app.core.db import session
from app.core.security import decrypt_field
from app.models import BankConnection, BankTransaction, Expense, PluggyConfig, Tenant, User
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
    state = {'fail': False}
    data_state = state
    inner = pg._http

    def http(method, path, body=None, api_key=None):
        if path.startswith('/transactions?'):
            raise pg.PluggyError('A Pluggy respondeu 410.', 410)  # endpoint antigo desligado
        if path.startswith('/v2/transactions?'):
            params = dict(p.split('=', 1) for p in path.split('?', 1)[1].split('&'))
            if state['fail']:
                raise pg.PluggyError('A Pluggy respondeu 500.', 500)
            since = params.get('dateFrom', '')[:10]
            rows = [t for t in data.get(params['accountId'], []) if t['date'][:10] >= since]
            start = int(params.get('after', 0))  # páginas de 2 para exercitar o cursor
            nxt = f"?accountId={params['accountId']}&after={start + 2}" if start + 2 < len(rows) else None
            return {'results': rows[start:start + 2], 'next': nxt}
        return inner(method, path, body, api_key)
    pg._http = http
    data['__state__'] = data_state  # type: ignore[assignment]
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
        {'id': pix['id'], 'category': 'alimentação', 'description': 'Feira da Maria'},
        {'id': tv['id'], 'category': '  Eletrônicos  '}]})
    assert r.status_code == 200, r.text
    assert r.json()['imported'] == 2
    feira = Expense.query.filter_by(description='Feira da Maria').one()
    assert feira.payment_method == 'PIX' and feira.paid is True and feira.bank == 'Nubank'
    parcelas = Expense.query.filter_by(description='LOJA TV').order_by(Expense.installment_number).all()
    assert [float(p.amount) for p in parcelas] == [300.0, 300.0, 300.0]
    assert all(p.card_id == card['id'] and p.category == 'Eletrônicos' and not p.paid for p in parcelas)  # nova
    assert Expense.query.filter_by(description='Feira da Maria').one().category == 'Alimentação'  # grafia da lista
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


def test_bank_is_saved_even_if_transactions_fail(client, family, bank, txs):  # noqa: F811
    acc, _cc, _ = _connect(client, family, bank, txs)
    for i in range(5):  # mais de uma página
        txs[acc].append({'id': f't-{i}', 'type': 'DEBIT', 'amount': -(10.0 + i), 'date': day(i), 'description': f'Compra {i}'})
    txs['__state__']['fail'] = True
    r = family.post('/pluggy/items', json={'item_id': ITEM})
    assert r.status_code == 200, r.text  # banco e contas ficam salvos
    assert BankConnection.query.one().needs_sync is True
    session.remove()
    txs['__state__']['fail'] = False
    assert family.get('/pluggy/transactions').json()['counts']['pending'] == 5  # a próxima leitura traz tudo
    session.remove()


def test_real_bank_cases(client, family, bank, txs):  # noqa: F811
    """Casos vistos no banco real: boleto da conta não casa com despesa do cartão; saldo da fatura
    anterior é ignorado; reclassifica o que foi classificado sozinho; nome do banco no Meu Pluggy."""
    acc, cc, card = _connect(client, family, bank, txs, card_digits='5162')
    when = (TODAY - timedelta(days=2)).isoformat()
    family.post('/expenses', json={'description': 'Taxa Anuidade', 'amount': '17.25', 'category': 'Outros',
                                   'payment_method': 'Cartão de Crédito', 'card_id': card['id'], 'date': when})
    txs[acc].append({'id': 'b-1', 'type': 'DEBIT', 'amount': -17.25, 'date': day(1), 'description': 'PAGAMENTO DE BOLETO'})
    txs[cc] += [{'id': 'c-1', 'type': 'DEBIT', 'amount': 17.25, 'date': day(1), 'description': 'ANUIDADE NAC'},
                {'id': 'c-2', 'type': 'DEBIT', 'amount': 99.0, 'date': day(1), 'description': 'TOTAL DA FATURA ANTERIOR'}]
    conn = _post_item(family)['item']
    pend = [t['description'] for t in family.get('/pluggy/transactions').json()['items']]
    assert pend == ['PAGAMENTO DE BOLETO']  # não casou com a anuidade do cartão
    dup = family.get('/pluggy/transactions?status=duplicate').json()['items']
    assert [d['description'] for d in dup] == ['ANUIDADE NAC']
    ign = family.get('/pluggy/transactions?status=ignored').json()['items']
    assert [i['description'] for i in ign] == ['TOTAL DA FATURA ANTERIOR']

    # "Revisar" num duplicado o trava como pendente: a próxima leitura não o reclassifica
    family.post('/pluggy/transactions/restore', json={'ids': [dup[0]['id']]})
    session.query(BankConnection).update({'needs_sync': True})
    session.commit()
    session.remove()
    assert family.get('/pluggy/transactions').json()['counts']['pending'] == 2

    # nome do banco (Meu Pluggy vem como "MeuPluggy") vai nas despesas lançadas
    r = family.put(f"/pluggy/items/{conn['id']}", json={'bank_name': 'Caixa'})
    assert r.status_code == 200 and r.json()['item']['bank_name'] == 'Caixa'
    assert 'Caixa Econômica Federal' in family.get('/pluggy/items').json()['banks']
    boleto = next(t for t in family.get('/pluggy/transactions').json()['items'] if t['description'] == 'PAGAMENTO DE BOLETO')
    assert boleto['bank_name'] == 'Caixa'
    family.post('/pluggy/transactions/approve', json={'items': [{'id': boleto['id'], 'category': 'Outros'}]})
    assert Expense.query.filter_by(description='PAGAMENTO DE BOLETO').one().bank == 'Caixa'
    session.remove()


def test_auto_import_launches_and_pending_to_posted_does_not_double(client, family, bank, txs):  # noqa: F811
    acc, cc, card = _connect(client, family, bank, txs, card_digits='5162')
    txs[cc].append({'id': 'c-pend', 'type': 'DEBIT', 'amount': 80.0, 'date': day(1), 'description': 'IFOOD',
                    'status': 'PENDING'})
    txs[acc].append({'id': 't-fat', 'type': 'DEBIT', 'amount': -500.0, 'date': day(2), 'description': 'Pagamento de fatura'})
    conn = _post_item(family)['item']
    r = family.put(f"/pluggy/items/{conn['id']}", json={'auto_import': True})
    assert r.status_code == 200 and r.json()['item']['auto_import'] is True
    assert '1 lançamento(s) lançado(s) agora' in r.json()['message']
    assert Expense.query.filter_by(description='IFOOD').count() == 1
    assert Expense.query.filter_by(description='Pagamento de fatura').count() == 0  # ignorado continua fora
    session.remove()
    # o banco troca a compra pendente pela lançada (id novo) e chega uma nova: lança só a nova
    txs[cc][:] = [{'id': 'c-post', 'type': 'DEBIT', 'amount': 80.0, 'date': day(1), 'description': 'IFOOD'},
                  {'id': 'c-new', 'type': 'DEBIT', 'amount': 25.0, 'date': day(0), 'description': 'Padaria'}]
    session.query(BankConnection).update({'needs_sync': True})
    session.commit()
    session.remove()
    family.get('/pluggy/items')
    assert Expense.query.filter_by(description='IFOOD').count() == 1
    assert Expense.query.filter_by(description='Padaria').count() == 1
    lancados = family.get('/pluggy/transactions?status=imported').json()['items']
    assert {t['description'] for t in lancados} == {'Padaria'} | ({'IFOOD'} & {t['description'] for t in lancados})
    assert family.get('/pluggy/transactions?status=duplicate').json()['counts']['duplicate'] == 1  # IFOOD lançado
    session.remove()


def test_webhook_starts_background_sync(client, family, bank, txs, monkeypatch):  # noqa: F811
    acc, _cc, _ = _connect(client, family, bank, txs)
    _post_item(family)
    started = []
    monkeypatch.setattr(of, 'BACKGROUND', True)
    monkeypatch.setattr(of.threading, 'Thread', lambda target, args, **kw: type(
        'T', (), {'start': lambda self: started.append(args)})())
    cfg = PluggyConfig.query.first()
    url, headers = f'{API}/pluggy/webhook/{cfg.hook_token}', {
        'X-FinFam-Hook': decrypt_field(cfg.hook_secret), 'CF-Connecting-IP': '52.67.145.81'}
    uid = cfg.user_id
    session.remove()
    client.post(url, headers=headers, json={'event': 'transactions/created', 'itemId': ITEM})
    client.post(url, headers=headers, json={'event': 'transactions/created', 'itemId': ITEM})
    assert started == [(uid,)]  # a rajada vira uma leitura só
    assert of._bg_state[uid] == 'again'
    of._bg_state.clear()
    # a leitura em si (o que a thread roda)
    txs[acc].append({'id': 't-bg', 'type': 'DEBIT', 'amount': -9.0, 'date': day(0), 'description': 'Café'})
    monkeypatch.setattr(of, 'DEBOUNCE_SECONDS', 0)
    of._bg_state[uid] = 'running'
    of._bg_run(uid)
    assert BankTransaction.query.filter_by(pluggy_id='t-bg').count() == 1 and uid not in of._bg_state
    session.remove()


def test_bill_of():
    assert of.bill_of(date(2026, 10, 5), 3, 10) == (2026, 11)   # depois do fechamento → próxima
    assert of.bill_of(date(2026, 10, 2), 3, 10) == (2026, 10)
    assert of.bill_of(date(2026, 10, 5), 28, 5) == (2026, 11)   # fecha 28/10, vence 05/11
    assert of.bill_of(date(2026, 10, 30), 28, 5) == (2026, 12)
    assert of.bill_of(date(2026, 12, 20), 10, 17) == (2027, 1)


def test_card_bill_month_from_bank_and_bill_payment_reconciled(client, family, bank, txs):  # noqa: F811
    acc, cc, card = _connect(client, family, bank, txs, card_digits='5162')
    # o banco fecha dia 3 e vence dia 10; o cartão no FinFam estava com melhor dia 3/vence 10 do teste,
    # mude para algo errado para ver o banco corrigir
    family.put(f"/cards/{card['id']}", json={'supports_credit': True, 'last_digits': '5162', 'bank': 'Nubank',
                                              'due_day': 25, 'best_buy_day': 20})
    bank['accounts'][ITEM][1]['creditData'] = {'creditLimit': 5000, 'balanceCloseDate': '2026-11-03T03:00:00Z',
                                               'balanceDueDate': '2026-11-10T03:00:00Z'}
    d = TODAY - timedelta(days=2)
    iso = f'{d.isoformat()}T15:00:00.000Z'
    txs[cc].append({'id': 'c-cativa', 'type': 'DEBIT', 'amount': 1597.76, 'date': iso, 'description': 'CATIVA',
                    'status': 'PENDING', 'creditCardMetadata': {'installmentNumber': 9, 'totalInstallments': 10}})
    _post_item(family)
    c = next(c for c in family.get('/cards?light=true').json()['cards'] if c['id'] == card['id'])
    assert (c['best_buy_day'], c['due_day']) == (3, 10)  # vencimento e melhor dia vêm do banco
    tx = family.get('/pluggy/transactions').json()['items'][0]
    family.post('/pluggy/transactions/approve', json={'items': [{'id': tx['id'], 'category': 'Outros'}]})
    e = Expense.query.filter(Expense.description.like('CATIVA%'), Expense.installment_number == 9).one()
    assert (e.year, e.month) == of.bill_of(d, 3, 10)  # na fatura que o banco diz
    bill_y, bill_m = e.year, e.month
    session.remove()

    # a conta paga essa fatura: o FinFam reconhece e marca a fatura como paga
    pay = date(bill_y, bill_m, 9)
    txs[acc].append({'id': 't-pag', 'type': 'DEBIT', 'amount': -1597.76, 'date': f'{min(pay, TODAY).isoformat()}T12:00:00Z',
                     'description': 'PAGAMENTO CARTAO CREDITO BCE CARTAO MASTER'})
    if pay > TODAY:  # fatura futura: o pagamento ainda não aconteceu; simula a fatura no mês corrente
        session.query(Expense).filter(Expense.description.like('CATIVA%'), Expense.installment_number == 9).update(
            {'year': TODAY.year, 'month': TODAY.month}, synchronize_session=False)
        session.commit()
    session.query(BankConnection).update({'needs_sync': True})
    session.commit()
    session.remove()
    ign = family.get('/pluggy/transactions?status=ignored').json()['items']
    pag = next(t for t in ign if t['description'].startswith('PAGAMENTO CARTAO'))
    assert 'fatura marcada como paga' in pag['reason']
    assert Expense.query.filter(Expense.description.like('CATIVA%'), Expense.installment_number == 9).one().paid is True
    assert Expense.query.filter(Expense.description.like('PAGAMENTO%')).count() == 0
    session.remove()


def test_card_window_counts_by_bill(client, family, bank, txs):  # noqa: F811
    """'1 mês' no cartão: compra de antes do início que vence numa fatura do período entra."""
    acc, cc, _ = _connect(client, family, bank, txs, card_digits='5162')
    bank['accounts'][ITEM][1]['creditData'] = {'creditLimit': 5000, 'balanceCloseDate': '2026-11-03T03:00:00Z',
                                               'balanceDueDate': '2026-11-10T03:00:00Z'}
    start = of.months_back(TODAY, 1)
    # uma compra 10 dias antes do início (a fatura dela pode vencer depois do início) e uma bem antiga
    inside = start - timedelta(days=10)
    old = start - timedelta(days=50)
    txs[cc] += [{'id': 'c-in', 'type': 'DEBIT', 'amount': 30.0, 'date': f'{inside.isoformat()}T15:00:00Z', 'description': 'Antes mas na fatura'},
                {'id': 'c-old', 'type': 'DEBIT', 'amount': 40.0, 'date': f'{old.isoformat()}T15:00:00Z', 'description': 'Fatura velha'}]
    _post_item(family, months=1)
    got = {t['description']: t['bill'] for t in family.get('/pluggy/transactions').json()['items']}
    by, bm = of.bill_of(inside, 3, 10)
    keep = date(by, bm, 10) >= start
    assert ('Antes mas na fatura' in got) == keep
    assert 'Fatura velha' not in got
    session.remove()


def test_meupluggy_case_bills_from_bank(client, family, bank, txs, monkeypatch):  # noqa: F811
    """Caso real (Meu Pluggy, sem dias de fechamento): CATIVA 7/10, 8/10 pagas pela conta; 9/10 em aberto."""
    acc, cc, card = _connect(client, family, bank, txs, card_digits='5162')
    due1, due2 = TODAY - timedelta(days=48), TODAY - timedelta(days=18)
    bills = [{'id': 'b-1', 'dueDate': f'{due1.isoformat()}T03:00:00Z', 'totalAmount': 1597.76},
             {'id': 'b-2', 'dueDate': f'{due2.isoformat()}T03:00:00Z', 'totalAmount': 1597.76}]
    inner = pg._http

    def http(method, path, body=None, api_key=None):
        if path.startswith('/bills?'):
            return {'results': bills}
        return inner(method, path, body, api_key)
    monkeypatch.setattr(pg, '_http', http)

    def iso(d):
        return f'{d.isoformat()}T15:00:00Z'
    txs[cc] += [
        {'id': 'c7', 'type': 'DEBIT', 'amount': 1597.76, 'date': iso(TODAY - timedelta(days=80)), 'description': 'CATIVA',
         'creditCardMetadata': {'installmentNumber': 7, 'totalInstallments': 10, 'billId': 'b-1'}},
        {'id': 'c8', 'type': 'DEBIT', 'amount': 1597.76, 'date': iso(TODAY - timedelta(days=53)), 'description': 'CATIVA',
         'creditCardMetadata': {'installmentNumber': 8, 'totalInstallments': 10, 'billId': 'b-2'}},
        {'id': 'c9', 'type': 'DEBIT', 'amount': 1597.76, 'date': iso(TODAY - timedelta(days=22)), 'description': 'CATIVA',
         'status': 'PENDING', 'creditCardMetadata': {'installmentNumber': 9, 'totalInstallments': 10}},
    ]
    txs[acc] += [
        {'id': 'p1', 'type': 'DEBIT', 'amount': -1597.76, 'date': iso(due1 - timedelta(days=2)),
         'description': 'PAGAMENTO CARTAO CREDITO BCE CARTAO MASTER'},
        {'id': 'p2', 'type': 'DEBIT', 'amount': -1597.76, 'date': iso(due2 + timedelta(days=1)),
         'description': 'PAGAMENTO CARTAO CREDITO BCE CARTAO MASTER'},
    ]
    _post_item(family, months=3)
    pend = family.get('/pluggy/transactions').json()['items']
    bills_seen = sorted(t['bill'] for t in pend)
    nxt = of.add_months(due2, 1)
    assert bills_seen == sorted([f'{due1.month:02d}/{due1.year}', f'{due2.month:02d}/{due2.year}',
                                 f'{nxt.month:02d}/{nxt.year}'])
    # lança as compras depois de o pagamento ter chegado: a conciliação é refeita
    family.post('/pluggy/transactions/approve', json={'items': [{'id': t['id'], 'category': 'Outros'} for t in pend]})
    rows = {e.installment_number: (e.month, e.paid) for e in Expense.query.filter(Expense.description.like('CATIVA%')).all()}
    assert rows == {7: (due1.month, True), 8: (due2.month, True), 9: (nxt.month, False),
                    10: (of.add_months(nxt, 1).month, False)}  # a 7/10 cria a série; 8 e 9 já estavam
    ign = family.get('/pluggy/transactions?status=ignored').json()['items']
    assert all('fatura marcada como paga' in t['reason'] for t in ign) and len(ign) == 2
    session.remove()


def test_imported_expense_moves_to_bank_bill(client, family, bank, txs, monkeypatch):  # noqa: F811
    acc, cc, card = _connect(client, family, bank, txs, card_digits='5162')
    d = TODAY - timedelta(days=5)
    txs[cc].append({'id': 'c9', 'type': 'DEBIT', 'amount': 1597.76, 'date': f'{d.isoformat()}T15:00:00Z',
                    'description': 'CATIVA', 'status': 'PENDING',
                    'creditCardMetadata': {'installmentNumber': 9, 'totalInstallments': 10}})
    _post_item(family)
    t = family.get('/pluggy/transactions').json()['items'][0]
    family.post('/pluggy/transactions/approve', json={'items': [{'id': t['id'], 'category': 'Outros'}]})
    session.remove()
    # o banco passa a informar as faturas (a última venceu um mês antes da compra)
    inner = pg._http
    monkeypatch.setattr(pg, '_http', lambda m, p, b=None, k=None: {'results': [
        {'id': 'b-x', 'dueDate': f'{of.add_months(d, -1).isoformat()}T03:00:00Z', 'totalAmount': 10}]}
        if p.startswith('/bills?') else inner(m, p, b, k))
    session.query(BankConnection).update({'needs_sync': True})
    session.commit()
    session.remove()
    family.get('/pluggy/transactions?status=imported')
    e = Expense.query.filter(Expense.description.like('CATIVA%'), Expense.installment_number == 9).one()
    nxt = of.add_months(of.add_months(d, -1), 1)
    while nxt < d + timedelta(days=7):
        nxt = of.add_months(nxt, 1)
    assert (e.year, e.month) == (nxt.year, nxt.month)
    session.remove()


def test_pending_goes_to_open_bill_and_moved_expense_is_unpaid(client, family, bank, txs, monkeypatch):  # noqa: F811
    """Dados reais do Santander (Meu Pluggy): faturas vencem dia 12; a 9/10 pendente sem billId
    caía na fatura já fechada e era marcada como paga pelo pagamento dela."""
    acc, cc, card = _connect(client, family, bank, txs, card_digits='5162')
    closed = date(TODAY.year, TODAY.month, 12) if TODAY.day > 12 else of.add_months(date(TODAY.year, TODAY.month, 12), -1)
    prev = of.add_months(closed, -1)
    bills = [{'id': 'b-prev', 'dueDate': f'{prev.isoformat()}T03:00:00Z', 'totalAmount': 1597.76},
             {'id': 'b-closed', 'dueDate': f'{closed.isoformat()}T03:00:00Z', 'totalAmount': 1597.76}]
    inner = pg._http
    monkeypatch.setattr(pg, '_http', lambda m, p, b=None, k=None: {'results': bills} if p.startswith('/bills?')
                        else inner(m, p, b, k))

    def iso(d):
        return f'{d.isoformat()}T15:00:00Z'
    c8_date = closed - timedelta(days=38)
    c9_date = closed - timedelta(days=7)  # antes do vencimento da fechada, mas está pendente → em aberto
    txs[cc] += [{'id': 'c8', 'type': 'DEBIT', 'amount': 1597.76, 'date': iso(c8_date), 'description': 'CATIVA',
                 'creditCardMetadata': {'installmentNumber': 8, 'totalInstallments': 10, 'billId': 'b-closed'}},
                {'id': 'c9', 'type': 'DEBIT', 'amount': 1597.76, 'date': iso(c9_date), 'description': 'CATIVA',
                 'status': 'PENDING', 'creditCardMetadata': {'installmentNumber': 9, 'totalInstallments': 10}}]
    txs[acc].append({'id': 'pay', 'type': 'DEBIT', 'amount': -1597.76, 'date': iso(closed - timedelta(days=3)),
                     'description': 'PAGAMENTO CARTAO CREDITO BCE CARTAO MASTER'})
    _post_item(family, months=3)
    pend = family.get('/pluggy/transactions').json()['items']
    nxt = of.add_months(closed, 1)
    assert {t['installment']['number']: t['bill'] for t in pend} == {
        8: f'{closed.month:02d}/{closed.year}', 9: f'{nxt.month:02d}/{nxt.year}'}
    family.post('/pluggy/transactions/approve', json={'items': [{'id': t['id'], 'category': 'Outros'} for t in pend]})
    got = {e.installment_number: (e.month, e.paid) for e in Expense.query.filter(Expense.description.like('CATIVA%')).all()}
    assert got == {8: (closed.month, True), 9: (nxt.month, False), 10: (of.add_months(nxt, 1).month, False)}

    # série lançada uma fatura antes (regra antiga) e paga: volta para a certa, não paga
    for e in Expense.query.filter(Expense.description.like('CATIVA%')).all():
        e.month, e.year = of.add_months(date(e.year, e.month, 1), -1).month, of.add_months(date(e.year, e.month, 1), -1).year
        e.paid = True
    session.query(BankConnection).update({'needs_sync': True})
    session.commit()
    session.remove()
    family.get('/pluggy/transactions?status=imported')
    got = {e.installment_number: (e.month, e.paid) for e in Expense.query.filter(Expense.description.like('CATIVA%')).all()}
    assert got[8] == (closed.month, True) and got[9] == (nxt.month, False) and got[10][1] is False
    session.remove()


def test_caixa_suggestions_and_boleto_pays_bill(client, family, bank, txs, monkeypatch):  # noqa: F811
    """Caixa (Meu Pluggy): boleto/débito automático → PIX; categoria pela descrição; o boleto de
    R$ 17,25 paga a fatura do cartão de R$ 17,25 (só a anuidade) e não vira despesa."""
    acc, cc, card = _connect(client, family, bank, txs, card_digits='5162')
    due = TODAY - timedelta(days=17)
    monkeypatch.setattr(pg, '_http', (lambda inner: lambda m, p, b=None, k=None: {'results': [
        {'id': 'b-anuid', 'dueDate': f'{due.isoformat()}T03:00:00Z', 'totalAmount': 17.25}]}
        if p.startswith('/bills?') else inner(m, p, b, k))(pg._http))

    def iso(d):
        return f'{d.isoformat()}T15:00:00Z'
    txs[acc] += [
        {'id': 'a1', 'type': 'DEBIT', 'amount': -1900.55, 'date': iso(TODAY - timedelta(days=5)), 'description': 'DEBITO PRESTACAO HAB'},
        {'id': 'a2', 'type': 'DEBIT', 'amount': -35.94, 'date': iso(TODAY - timedelta(days=7)), 'description': 'MENSALIDADE CESTA SERVICO'},
        {'id': 'a3', 'type': 'DEBIT', 'amount': -200.0, 'date': iso(TODAY - timedelta(days=14)), 'description': 'PAGAMENTO DE BOLETO'},
        {'id': 'a4', 'type': 'DEBIT', 'amount': -17.25, 'date': iso(due - timedelta(days=1)), 'description': 'PAGAMENTO DE BOLETO'},
        {'id': 'a5', 'type': 'DEBIT', 'amount': -89.0, 'date': iso(TODAY - timedelta(days=3)), 'description': 'COMPRA CARTAO DEB - POSTO SHELL'},
        {'id': 'a6', 'type': 'DEBIT', 'amount': -29.9, 'date': iso(TODAY - timedelta(days=4)), 'description': 'DEB PIX QR COD DIN'},
    ]
    txs[cc].append({'id': 'c-an', 'type': 'DEBIT', 'amount': 17.25, 'date': iso(due - timedelta(days=20)),
                    'description': 'ANUIDADE NAC DIFERENCIADA', 'creditCardMetadata': {'billId': 'b-anuid',
                                                                                      'installmentNumber': 7, 'totalInstallments': 12}})
    _post_item(family, months=3)
    items = {(t['description'], t['amount']): t for t in family.get('/pluggy/transactions').json()['items']}
    sug = {k: (t['payment_method'], t['category']) for k, t in items.items()}
    assert sug[('DEBITO PRESTACAO HAB', 1900.55)] == ('PIX', 'Moradia')
    assert sug[('MENSALIDADE CESTA SERVICO', 35.94)] == ('PIX', 'Outros')
    assert sug[('PAGAMENTO DE BOLETO', 200.0)] == ('PIX', 'Outros')        # boleto comum: fica para revisar
    assert sug[('COMPRA CARTAO DEB - POSTO SHELL', 89.0)] == ('Cartão de Débito', 'Transporte')
    assert sug[('DEB PIX QR COD DIN', 29.9)][0] == 'PIX'
    assert ('PAGAMENTO DE BOLETO', 17.25) not in items                    # pagou a fatura de 17,25
    ign = family.get('/pluggy/transactions?status=ignored').json()['items']
    assert any(t['amount'] == 17.25 and 'Pagamento da fatura' in t['reason'] for t in ign)
    # lançando a anuidade, a fatura dela fica paga
    an = items[('ANUIDADE NAC DIFERENCIADA', 17.25)]
    family.post('/pluggy/transactions/approve', json={'items': [{'id': an['id'], 'category': 'Outros'}]})
    assert Expense.query.filter(Expense.description.like('ANUIDADE%'), Expense.installment_number == 7).one().paid is True
    session.remove()



def test_middle_installment_creates_rest_of_series(client, family, bank, txs):  # noqa: F811
    """Anuidade 7/12 lançada → 8/12..12/12 aparecem nas faturas seguintes; quando o banco lançar
    a 8/12, ela é reconhecida como já lançada."""
    acc, cc, card = _connect(client, family, bank, txs, card_digits='5162')
    d = TODAY - timedelta(days=5)
    txs[cc].append({'id': 'an7', 'type': 'DEBIT', 'amount': 17.25, 'date': f'{d.isoformat()}T15:00:00Z',
                    'description': 'ANUIDADE NAC DIFERENCIADA',
                    'creditCardMetadata': {'installmentNumber': 7, 'totalInstallments': 12}})
    _post_item(family)
    t = family.get('/pluggy/transactions').json()['items'][0]
    r = family.post('/pluggy/transactions/approve', json={'items': [{'id': t['id'], 'category': 'Outros'}]})
    assert r.json()['imported'] == 1
    rows = Expense.query.filter(Expense.description.like('ANUIDADE%')).order_by(Expense.installment_number).all()
    assert [e.installment_number for e in rows] == [7, 8, 9, 10, 11, 12]
    months = [e.year * 12 + e.month for e in rows]
    assert months == list(range(months[0], months[0] + 6))
    assert rows[0].installment_group.num_installments == 12
    session.remove()
    nxt = of.add_months(d, 1)
    txs[cc].append({'id': 'an8', 'type': 'DEBIT', 'amount': 17.25, 'date': f'{min(nxt, TODAY).isoformat()}T15:00:00Z',
                    'description': 'ANUIDADE NAC DIFERENCIADA',
                    'creditCardMetadata': {'installmentNumber': 8, 'totalInstallments': 12}})
    session.query(BankConnection).update({'needs_sync': True})
    session.commit()
    session.remove()
    dup = family.get('/pluggy/transactions?status=duplicate').json()['items']
    assert [t['installment']['number'] for t in dup] == [8]
    assert Expense.query.filter(Expense.description.like('ANUIDADE%')).count() == 6
    session.remove()


def test_series_follows_bank_bills_with_skipped_month(client, family, bank, txs, monkeypatch):  # noqa: F811
    """Santander real: a fatura de junho veio zerada (sem parcela). CATIVA 4/10..9/10 cada uma com
    o billId da sua fatura → a 10/10 cai depois da 9/10, não um mês antes."""
    acc, cc, card = _connect(client, family, bank, txs, card_digits='5162')
    D = date(TODAY.year, TODAY.month, 12)
    if D <= TODAY:
        D = of.add_months(D, 1)          # vencimento da fatura em aberto
    due = {k: of.add_months(D, -off) for k, off in ((4, 6), (5, 4), (6, 3), (7, 2), (8, 1))}  # D-5 (junho) zerada
    bills = [{'id': f'b{k}', 'dueDate': f'{d.isoformat()}T03:00:00Z', 'totalAmount': 1597.76} for k, d in due.items()]
    bills.append({'id': 'b-zero', 'dueDate': f'{of.add_months(D, -5).isoformat()}T03:00:00Z', 'totalAmount': 0})
    inner = pg._http
    monkeypatch.setattr(pg, '_http', lambda m, p, b=None, k=None: {'results': bills} if p.startswith('/bills?')
                        else inner(m, p, b, k))
    for k, d in due.items():
        txs[cc].append({'id': f'c{k}', 'type': 'DEBIT', 'amount': 1597.76, 'date': f'{(d - timedelta(days=37)).isoformat()}T15:00:00Z',
                        'description': 'CATIVA', 'creditCardMetadata': {'installmentNumber': k, 'totalInstallments': 10, 'billId': f'b{k}'}})
    txs[cc].append({'id': 'c9', 'type': 'DEBIT', 'amount': 1597.76, 'date': f'{(D - timedelta(days=37)).isoformat()}T15:00:00Z',
                    'description': 'CATIVA', 'status': 'PENDING', 'creditCardMetadata': {'installmentNumber': 9, 'totalInstallments': 10}})
    _post_item(family, months=12)
    pend = family.get('/pluggy/transactions').json()['items']
    assert len(pend) == 6
    r = family.post('/pluggy/transactions/approve', json={'items': [{'id': t['id'], 'category': 'Outros'} for t in pend]})
    assert r.json()['imported'] == 1 and r.json()['already'] == 5  # a 4/10 cria a série; as outras já estavam
    got = {e.installment_number: (e.year, e.month) for e in Expense.query.filter(Expense.description == 'CATIVA').all()}
    want = {k: (d.year, d.month) for k, d in due.items()}
    want[9] = (D.year, D.month)
    nd = of.add_months(D, 1)
    want[10] = (nd.year, nd.month)
    assert got == want
    session.remove()

    # série que já estava lançada errada (mês a mês, sem o mês pulado): a próxima leitura acerta
    for e in Expense.query.filter(Expense.description == 'CATIVA').all():
        base = of.add_months(due[4], e.installment_number - 4)
        e.year, e.month = base.year, base.month
    session.query(BankConnection).update({'needs_sync': True})
    session.commit()
    session.remove()
    family.get('/pluggy/items')
    got = {e.installment_number: (e.year, e.month) for e in Expense.query.filter(Expense.description == 'CATIVA').all()}
    assert got == want
    session.remove()


def test_c6_one_account_many_cards(client, family, bank, txs):  # noqa: F811
    """C6: físico, virtual e adicional (da Greyce) chegam como UM cartão; o final de cada compra
    (creditCardMetadata.cardNumber) põe no cartão certo — e a do adicional no nome dela."""
    session.get(Tenant, family.tenant['id']).plan = 'anual'
    session.commit()
    session.remove()
    family.post('/family/members', json={'name': 'Greyce', 'email': 'g@test.com', 'password': 'senha-greyce',
                                         'confirm_password': 'senha-greyce'})
    greyce_id = User.query.filter_by(email='g@test.com').one().id
    session.remove()
    acc, cc, fisico = _connect(client, family, bank, txs, card_digits='5162')
    virtual = family.post('/cards', json={'supports_credit': True, 'last_digits': '6494', 'bank': 'Nubank',
                                          'due_day': 10, 'best_buy_day': 3, 'is_virtual': True}).json()['card']
    body = {'supports_credit': True, 'last_digits': '2907', 'bank': 'Nubank', 'due_day': 10, 'best_buy_day': 3,
            'is_additional': True}
    body['user_id'] = greyce_id
    adicional = family.post('/cards', json=body).json()['card']
    assert virtual['account_id'] == fisico['account_id']  # o adicional da Greyce fica na conta dela

    def tx(i, digits, amount):
        return {'id': f'c-{i}', 'type': 'DEBIT', 'amount': amount, 'date': f'{(TODAY - timedelta(days=i)).isoformat()}T15:00:00Z',
                'description': f'COMPRA {i}', 'creditCardMetadata': {'cardNumber': digits}}
    txs[cc] += [tx(1, '6494', 10.0), tx(2, '2907', 20.0), tx(3, None, 30.0), tx(4, '9999', 40.0)]
    _post_item(family)
    items = family.get('/pluggy/transactions').json()['items']
    assert {t['description']: t['card_digits'] for t in items}['COMPRA 1'] == '6494'
    family.post('/pluggy/transactions/approve', json={'items': [{'id': t['id'], 'category': 'Outros'} for t in items]})
    got = {e.description: (e.card_id, e.user_id) for e in Expense.query.all()}
    assert got['COMPRA 1'][0] == virtual['id']
    assert got['COMPRA 2'] == (adicional['id'], greyce_id)   # no nome de quem tem o adicional
    assert got['COMPRA 3'][0] == fisico['id'] and got['COMPRA 4'][0] == fisico['id']  # sem final / desconhecido
    session.remove()


def test_c6_cut_day_inferred_and_bill_payment_undone(client, family, bank, txs, monkeypatch):  # noqa: F811
    """C6 real: o corte é dia 7 (não informado); a fatura do mês passado veio inteira como
    "pendente" sem código; o pagamento sai da conta C6 como "C6 BANK" e tinha sido lançado."""
    acc, cc, card = _connect(client, family, bank, txs, card_digits='5162')
    D = date(TODAY.year, TODAY.month, 14)
    if D <= TODAY:
        D = of.add_months(D, 1)                      # fatura em aberto vence dia 14 do mês que vem
    known = [of.add_months(D, -k) for k in (4, 3, 2)]   # 3 faturas com código; a de D-1 sem código
    hidden = of.add_months(D, -1)
    bills = [{'id': f'b{i}', 'dueDate': f'{d.isoformat()}T03:00:00Z', 'totalAmount': 100.0 * (i + 1)}
             for i, d in enumerate(known)]
    bills.append({'id': 'b-hidden', 'dueDate': f'{hidden.isoformat()}T03:00:00Z', 'totalAmount': 700.0})
    inner = pg._http
    monkeypatch.setattr(pg, '_http', lambda m, p, b=None, k=None: {'results': bills} if p.startswith('/bills?')
                        else inner(m, p, b, k))

    def iso(d):
        return f'{d.isoformat()}T15:00:00Z'
    n = 0
    for i, due in enumerate(known):  # compras do dia 8 do mês anterior ao 7 do mês do vencimento
        for day in (8, 15, 20, 7):
            d = date(*(of.add_months(due, -1).year, of.add_months(due, -1).month, day)) if day != 7 else date(due.year, due.month, 7)
            n += 1
            txs[cc].append({'id': f'k{n}', 'type': 'DEBIT', 'amount': 10.0, 'date': iso(d), 'description': f'LOJA {n}',
                            'creditCardMetadata': {'billId': f'b{i}'}})
    prev = of.add_months(hidden, -1)
    for day, amount in ((9, 300.0), (25, 400.0)):   # fatura escondida (pendente, sem código)
        n += 1
        txs[cc].append({'id': f'h{n}', 'type': 'DEBIT', 'amount': amount, 'date': iso(date(prev.year, prev.month, day)),
                        'description': f'ESCONDIDA {n}', 'status': 'PENDING'})
    n += 1
    txs[cc].append({'id': f'o{n}', 'type': 'DEBIT', 'amount': 55.0, 'date': iso(date(hidden.year, hidden.month, 9)),
                    'description': 'ABERTA', 'status': 'PENDING'})
    pay_day = hidden - timedelta(days=6)
    txs[acc].append({'id': 'pay-c6', 'type': 'DEBIT', 'amount': -700.0, 'date': iso(min(pay_day, TODAY)), 'description': 'C6 BANK'})
    _post_item(family, months=6)
    items = family.get('/pluggy/transactions').json()['items']
    bills_seen = {t['description']: t['bill'] for t in items}
    assert bills_seen[f'ESCONDIDA {n - 2}'] == f'{hidden.month:02d}/{hidden.year}'   # não foi para a aberta
    assert bills_seen['ABERTA'] == f'{D.month:02d}/{D.year}'
    c = next(c for c in family.get('/cards?light=true').json()['cards'] if c['id'] == card['id'])
    assert c['best_buy_day'] == 7 and c['due_day'] == 14   # corte descoberto vai para o cartão
    # o pagamento "C6 BANK" é reconhecido mesmo sem "pagamento/fatura" no texto
    ign = family.get('/pluggy/transactions?status=ignored').json()['items']
    assert any(t['description'] == 'C6 BANK' and 'Pagamento da fatura' in t['reason'] for t in ign)
    session.remove()

    # caso já lançado como despesa (antes da correção): desfaz
    t = BankTransaction.query.filter_by(pluggy_id='pay-c6').one()
    e = Expense(user_id=t.user_id, description='C6 BANK', amount=700, category='Outros', payment_method='PIX',
                year=t.date.year, month=t.date.month, day=t.date.day, paid=True)
    session.add(e)
    session.flush()
    t.status, t.reason, t.expense_id, t.reviewed_at = 'imported', None, e.id, t.date
    session.commit()
    session.remove()
    family.get('/pluggy/transactions')
    assert Expense.query.filter_by(description='C6 BANK').count() == 0
    t = BankTransaction.query.filter_by(pluggy_id='pay-c6').one()
    assert t.status == 'ignored' and 'removida' in t.reason
    session.remove()


def test_issuer_in_description():
    from app.models import BankAccount, BankConnection
    conn = BankConnection(bank_name='MeuPluggy', display_name='Santander')
    acc = BankAccount(name='SANTANDER SX MASTER 0676', connection=conn)
    assert of._issuer_in(acc, 'BANCO SANTANDER (BRASIL) S.A.')
    assert not of._issuer_in(acc, 'PAGAMENTO BOLETO BCO BRADESCO S.A.')
    conn2 = BankConnection(bank_name='MeuPluggy', display_name='C6 Bank')
    assert of._issuer_in(BankAccount(name='BANDEIRADO 0982', connection=conn2), 'C6 BANK')


def test_invoice_check_summary_and_telegram_notice(client, family, bank, txs, monkeypatch):  # noqa: F811
    from app.models import TelegramLink
    from app.services import telegram as tg
    acc, cc, card = _connect(client, family, bank, txs, card_digits='5162')
    D = date(TODAY.year, TODAY.month, 12)
    if D <= TODAY:
        D = of.add_months(D, 1)
    closed = of.add_months(D, -1)
    bills = [{'id': 'b-c', 'dueDate': f'{closed.isoformat()}T03:00:00Z', 'totalAmount': 150.0}]
    inner = pg._http
    monkeypatch.setattr(pg, '_http', lambda m, p, b=None, k=None: {'results': bills} if p.startswith('/bills?')
                        else inner(m, p, b, k))
    d1 = closed - timedelta(days=20)
    txs[cc] += [{'id': 'x1', 'type': 'DEBIT', 'amount': 100.0, 'date': f'{d1.isoformat()}T15:00:00Z', 'description': 'MERCADO',
                 'creditCardMetadata': {'billId': 'b-c'}},
                {'id': 'x2', 'type': 'DEBIT', 'amount': 50.0, 'date': f'{d1.isoformat()}T16:00:00Z', 'description': 'FARMACIA',
                 'creditCardMetadata': {'billId': 'b-c'}}]
    _post_item(family, months=3)
    items = family.get('/pluggy/transactions').json()['items']
    family.post('/pluggy/transactions/approve', json={'items': [{'id': t['id'], 'category': 'Outros'} for t in items
                                                                if t['description'] == 'MERCADO']})
    family.post('/expenses', json={'description': 'Lançado à mão', 'amount': '30', 'category': 'Outros',
                                   'payment_method': 'Cartão de Crédito', 'card_id': card['id'], 'date': d1.isoformat()})
    e = Expense.query.filter_by(description='Lançado à mão').one()
    e.year, e.month = closed.year, closed.month
    session.commit()
    session.remove()
    r = family.get(f'/pluggy/invoice-check?month={closed.month}&year={closed.year}').json()
    row = next(c for c in r['cards'] if c['linked'])
    assert (row['bank_total'], row['bank_source'], row['finfam_total'], row['diff']) == (150.0, 'fatura', 130.0, -20.0)
    assert [i['description'] for i in row['only_bank']] == ['FARMACIA']
    assert [i['description'] for i in row['only_finfam']] == ['Lançado à mão']

    # resumo e conferência também com token de API (MCP), só leitura
    tok = family.post('/auth/tokens', json={'name': 'mcp'}).json()['token']
    hdr = {'Authorization': f'Bearer {tok}'}
    s = client.get(f'{API}/pluggy/summary', headers=hdr)
    assert s.status_code == 200 and s.json()['counts']['pending'] == 1 and s.json()['items'][0]['description'] == 'FARMACIA'
    assert client.get(f'{API}/pluggy/invoice-check', headers=hdr).status_code == 200
    assert client.post(f'{API}/pluggy/transactions/approve', headers=hdr, json={'items': [{'id': 1, 'category': 'x'}]}).status_code == 403

    # aviso no Telegram: só leitura sozinha (webhook/cron), com o que chegou de novo
    sent = []
    monkeypatch.setattr(tg, 'send_message', lambda token, chat, text: (sent.append((chat, text)), (True, ''))[1])
    session.add(TelegramLink(user_id=family.user['id'], chat_id='777', username='tiago'))
    session.query(BankConnection).update({'needs_sync': True})
    session.commit()
    session.remove()
    txs[cc].append({'id': 'x3', 'type': 'DEBIT', 'amount': 42.0, 'date': f'{TODAY.isoformat()}T12:00:00Z', 'description': 'PADARIA NOVA'})
    family.get('/pluggy/items')            # você na página: não avisa
    assert sent == []
    txs[cc].append({'id': 'x4', 'type': 'DEBIT', 'amount': 8.0, 'date': f'{TODAY.isoformat()}T13:00:00Z', 'description': 'CAFE'})
    session.query(BankConnection).update({'needs_sync': True})
    session.commit()
    assert of.sync_stale(family.user['id'], notify=True) == 1
    assert len(sent) == 1 and sent[0][0] == '777' and 'CAFE' in sent[0][1] and 'para revisar' in sent[0][1]
    family.put('/pluggy/notify', json={'notify': False})
    session.query(BankConnection).update({'needs_sync': True})
    session.commit()
    txs[cc].append({'id': 'x5', 'type': 'DEBIT', 'amount': 9.0, 'date': f'{TODAY.isoformat()}T14:00:00Z', 'description': 'SUCO'})
    of.sync_stale(family.user['id'], notify=True)
    assert len(sent) == 1  # desligado
    session.remove()


def test_installment_cents_merge_and_refund(client, family, bank, txs, monkeypatch):  # noqa: F811
    """C6 real: TELLERINA 1/3 de 166,68 e as seguintes de 166,66 — a mesma parcela é reconhecida
    (fica o valor do banco), séries duplicadas antigas são juntadas, e estorno abate a fatura."""
    from app.models import InstallmentGroup
    acc, cc, card = _connect(client, family, bank, txs, card_digits='5162')

    def iso(d):
        return f'{d.isoformat()}T15:00:00Z'
    d1 = TODAY - timedelta(days=70)
    txs[cc] += [{'id': 't1', 'type': 'DEBIT', 'amount': 166.68, 'date': iso(d1), 'description': 'TELLERINA COMERCIO DE',
                 'creditCardMetadata': {'installmentNumber': 1, 'totalInstallments': 3}}]
    _post_item(family, months=6)
    t = family.get('/pluggy/transactions').json()['items'][0]
    family.post('/pluggy/transactions/approve', json={'items': [{'id': t['id'], 'category': 'Outros'}]})
    session.remove()
    txs[cc] += [{'id': 't2', 'type': 'DEBIT', 'amount': 166.66, 'date': iso(of.add_months(d1, 1)), 'description': 'TELLERINA COMERCIO DE',
                 'creditCardMetadata': {'installmentNumber': 2, 'totalInstallments': 3}},
                {'id': 'r1', 'type': 'CREDIT', 'amount': -104.48, 'date': iso(TODAY - timedelta(days=3)),
                 'description': 'ESTORNO LOJA X'},
                {'id': 'p1', 'type': 'CREDIT', 'amount': -500.0, 'date': iso(TODAY - timedelta(days=4)),
                 'description': 'PAGAMENTO RECEBIDO'}]
    session.query(BankConnection).update({'needs_sync': True})
    session.commit()
    session.remove()
    dup = family.get('/pluggy/transactions?status=duplicate').json()['items']
    assert [d['installment']['number'] for d in dup] == [2]          # a 2/3 de 166,66 é a mesma parcela
    rows = Expense.query.filter(Expense.description.like('TELLERINA%')).order_by(Expense.installment_number).all()
    assert len(rows) == 3 and float(rows[1].amount) == 166.66          # ficou o valor do banco
    pend = family.get('/pluggy/transactions').json()['items']
    est = next(p for p in pend if p['refund'])
    assert est['description'] == 'ESTORNO LOJA X' and not any('PAGAMENTO' in p['description'] for p in pend)
    family.post('/pluggy/transactions/approve', json={'items': [{'id': est['id'], 'category': 'Outros'}]})
    e = Expense.query.filter(Expense.amount < 0).one()
    assert float(e.amount) == -104.48 and e.card_id == card['id']
    session.remove()

    # séries duplicadas que já existiam (antes da correção): são juntadas na próxima leitura
    g1 = Expense.query.filter(Expense.description.like('TELLERINA%')).first().installment_group_id
    rows = Expense.query.filter_by(installment_group_id=g1).order_by(Expense.installment_number).all()
    g2 = InstallmentGroup(user_id=rows[0].user_id, description='TELLERINA COMERCIO DE', total_amount=499.98,
                          num_installments=3, bank='Nubank')
    session.add(g2)
    session.flush()
    for r in rows[1:]:  # cópia das parcelas 2 e 3 numa segunda série
        session.add(Expense(user_id=r.user_id, description=r.description, amount=166.66, category='Outros',
                            payment_method='Cartão de Crédito', year=r.year, month=r.month, day=r.day,
                            card_id=r.card_id, installment_group_id=g2.id, installment_number=r.installment_number))
    session.query(BankConnection).update({'needs_sync': True})
    session.commit()
    assert Expense.query.filter(Expense.description.like('TELLERINA%')).count() == 5
    session.remove()
    family.get('/pluggy/items')
    rows = Expense.query.filter(Expense.description.like('TELLERINA%')).order_by(Expense.installment_number).all()
    assert [r.installment_number for r in rows] == [1, 2, 3] and len({r.installment_group_id for r in rows}) == 1
    linked = {t.expense_id for t in BankTransaction.query.filter(BankTransaction.pluggy_id.in_(('t1', 't2'))).all()}
    assert linked <= {r.id for r in rows}                               # as que vieram do banco ficaram
    session.remove()


def test_c6_debit_card_purchase_is_not_bill_payment(client, family, bank, txs):  # noqa: F811
    """C6: "DÉBITO DE CARTÃO" na conta é compra no débito (a Pluggy diz "Credit card payment")."""
    acc, cc, _ = _connect(client, family, bank, txs)
    txs[acc] += [
        {'id': 'd1', 'type': 'DEBIT', 'amount': -3.2, 'date': day(3), 'description': 'DÉBITO DE CARTÃO',
         'category': 'Credit card payment'},
        {'id': 'd2', 'type': 'DEBIT', 'amount': -900.0, 'date': day(4), 'description': 'PAGAMENTO DE FATURA',
         'category': 'Credit card payment'},
    ]
    _post_item(family)
    items = {t['amount']: t for t in family.get('/pluggy/transactions').json()['items']}
    assert items[3.2]['payment_method'] == 'Cartão de Débito'
    assert 900.0 not in items
    session.remove()


def test_bank_investments_read_only(client, family, other_family, bank, txs, monkeypatch):  # noqa: F811
    """Investimentos do banco aparecem só para quem conectou, com o saldo do banco; resgatado some."""
    acc, cc, _ = _connect(client, family, bank, txs)
    inv = [{'id': 'inv-cdb', 'name': 'CDB C6 110% CDI', 'type': 'FIXED_INCOME', 'subtype': 'CDB', 'balance': 2215.37,
            'amountOriginal': 2200, 'currencyCode': 'BRL', 'rate': 110, 'rateType': 'CDI',
            'dueDate': '2028-10-15T00:00:00.000Z', 'status': 'ACTIVE', 'issuer': 'Banco C6'},
           {'id': 'inv-old', 'name': 'CDB resgatado', 'type': 'FIXED_INCOME', 'balance': 0, 'status': 'TOTAL_WITHDRAWAL'}]
    monkeypatch.setattr(pg, '_http', (lambda inner: lambda m, p, b=None, k=None: {'results': list(inv)}
        if p.startswith('/investments?') else inner(m, p, b, k))(pg._http))
    _post_item(family)
    r = family.get('/pluggy/investments').json()
    assert r['enabled'] and r['total'] == 2215.37
    [it] = r['items']
    assert (it['name'], it['subtype'], it['rate'], it['rate_type'], it['due_date'], it['amount_invested']) == \
        ('CDB C6 110% CDI', 'CDB', 110.0, 'CDI', '2028-10-15', 2200.0)
    assert other_family.get('/pluggy/investments').json()['items'] == []
    inv.pop(0)  # resgatou: na próxima leitura some
    conn = BankConnection.query.one()
    of.sync(conn, of.client_for(conn.user_id))
    session.commit()
    assert family.get('/pluggy/investments').json()['items'] == []
    session.remove()


def test_manual_close_day_until_bank_informs(client, family, bank, txs, monkeypatch):  # noqa: F811
    """Fechamento digitado vale enquanto o banco não informa; quando informa, o do banco manda."""
    acc, cc, card = _connect(client, family, bank, txs, card_digits='5162')
    due = of.add_months(date(TODAY.year, TODAY.month, 15), 1)
    monkeypatch.setattr(pg, '_http', (lambda inner: lambda m, p, b=None, k=None: {'results': [
        {'id': 'b-open', 'dueDate': f'{due.isoformat()}T03:00:00Z', 'totalAmount': 10.0}]}
        if p.startswith('/bills?') else inner(m, p, b, k))(pg._http))
    _post_item(family)
    cc_out = next(a for c in family.get('/pluggy/items').json()['items'] for a in c['accounts'] if a['type'] == 'CREDIT')
    assert cc_out['close_day'] is None and cc_out['due_day'] == 15
    r = family.put(f"/pluggy/accounts/{cc_out['id']}/close-day", json={'close_day': 9})
    assert r.status_code == 200, r.text
    assert (r.json()['account']['close_day'], r.json()['account']['close_source']) == (9, 'manual')
    c = next(c for c in family.get('/cards?light=true').json()['cards'] if c['id'] == card['id'])
    assert (c['best_buy_day'], c['due_day']) == (9, 15)
    # o banco passa a informar: o dele manda (o digitado fica guardado)
    for a in bank['accounts'][ITEM]:
        if a['type'] == 'CREDIT':
            a['creditData'] = {'creditLimit': 5000, 'balanceCloseDate': f'{TODAY.year}-{TODAY.month:02d}-07T03:00:00Z',
                               'balanceDueDate': f'{TODAY.year}-{TODAY.month:02d}-14T03:00:00Z'}
    family.put(f"/pluggy/accounts/{cc_out['id']}/close-day", json={'close_day': 9})
    a = next(a for c in family.get('/pluggy/items').json()['items'] for a in c['accounts'] if a['type'] == 'CREDIT')
    conn = BankConnection.query.one()
    of.sync(conn, of.client_for(conn.user_id))
    session.commit()
    a = next(a for c in family.get('/pluggy/items').json()['items'] for a in c['accounts'] if a['type'] == 'CREDIT')
    assert (a['close_day'], a['close_source'], a['manual_close_day']) == (7, 'bank', 9)
    assert family.put(f"/pluggy/accounts/{a['id']}/close-day", json={'close_day': 40}).status_code == 422
    session.remove()


def test_category_rules_learn_and_count(client, family, bank, txs):  # noqa: F811
    from app.models import CategoryRule
    from app.services.categorizer import keyword_of
    assert keyword_of('DL*UBERRIDES') == 'UBERRIDES'
    assert keyword_of('99APP *99APP SAO PAULO BRA') == '99APP'
    assert keyword_of('COOP DIADEMA BRA') == 'COOP'
    assert keyword_of('IFD*57.834.531 ELAINE') == 'ELAINE'
    assert keyword_of('MERCADOLIVRE*OBEROINF') == 'OBEROINF'
    assert keyword_of('DÉBITO DE CARTÃO') is None
    acc, cc, _ = _connect(client, family, bank, txs)
    txs[cc] += [{'id': 'r1', 'type': 'DEBIT', 'amount': 26.9, 'date': day(3), 'description': 'TELLERINA COMERCIO DE BRA'},
                {'id': 'r2', 'type': 'DEBIT', 'amount': 31.0, 'date': day(2), 'description': 'TELLERINA COMERCIO DE BRA'}]
    _post_item(family)
    items = family.get('/pluggy/transactions').json()['items']
    first = next(t for t in items if t['amount'] == 26.9)
    assert first['category'] == 'Outros'
    family.post('/pluggy/transactions/approve', json={'items': [{'id': first['id'], 'category': 'Lazer'}]})
    rule = CategoryRule.query.filter_by(keyword='TELLERINA').one()
    assert rule.category == 'Lazer'
    session.remove()
    second = next(t for t in family.get('/pluggy/transactions').json()['items'] if t['amount'] == 31.0)
    assert second['category'] == 'Lazer'            # aprendeu
    family.post('/pluggy/transactions/approve', json={'items': [{'id': second['id'], 'category': 'Lazer'}]})
    assert CategoryRule.query.filter_by(keyword='TELLERINA').one().match_count >= 1   # "Usos" sobe
    session.remove()
    # histórico: 3 despesas iguais viram regra
    for i in range(3):
        family.post('/expenses', json={'description': f'PADARIA NOBREGA {i}', 'amount': 10, 'category': 'Alimentação',
                                       'payment_method': 'PIX', 'day': 1, 'month': TODAY.month, 'year': TODAY.year})
    r = family.post('/rules/learn').json()
    assert r['created'] >= 1
    assert any(x['keyword'] == 'PADARIA' and x['category'] == 'Alimentação' for x in family.get('/rules').json())
    session.remove()
