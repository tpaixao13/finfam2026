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
    e = Expense.query.filter(Expense.description.like('CATIVA%')).one()
    assert (e.year, e.month) == of.bill_of(d, 3, 10)  # na fatura que o banco diz
    bill_y, bill_m = e.year, e.month
    session.remove()

    # a conta paga essa fatura: o FinFam reconhece e marca a fatura como paga
    pay = date(bill_y, bill_m, 9)
    txs[acc].append({'id': 't-pag', 'type': 'DEBIT', 'amount': -1597.76, 'date': f'{min(pay, TODAY).isoformat()}T12:00:00Z',
                     'description': 'PAGAMENTO CARTAO CREDITO BCE CARTAO MASTER'})
    if pay > TODAY:  # fatura futura: o pagamento ainda não aconteceu; simula a fatura no mês corrente
        session.query(Expense).filter(Expense.description.like('CATIVA%')).update(
            {'year': TODAY.year, 'month': TODAY.month}, synchronize_session=False)
        session.commit()
    session.query(BankConnection).update({'needs_sync': True})
    session.commit()
    session.remove()
    ign = family.get('/pluggy/transactions?status=ignored').json()['items']
    pag = next(t for t in ign if t['description'].startswith('PAGAMENTO CARTAO'))
    assert 'fatura marcada como paga' in pag['reason']
    assert Expense.query.filter(Expense.description.like('CATIVA%')).one().paid is True
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
    rows = {e.description: e for e in Expense.query.filter(Expense.description.like('CATIVA%')).all()}
    assert {(e.month, e.paid) for e in rows.values()} == {(due1.month, True), (due2.month, True), (nxt.month, False)}
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
    e = Expense.query.filter(Expense.description.like('CATIVA%')).one()
    nxt = of.add_months(of.add_months(d, -1), 1)
    while nxt < d + timedelta(days=7):
        nxt = of.add_months(nxt, 1)
    assert (e.year, e.month) == (nxt.year, nxt.month)
    session.remove()
