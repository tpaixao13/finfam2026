"""Saída do banco × conta a pagar: internet (valor fixo) e luz (valor variável, aprende o CNPJ)."""
from datetime import timedelta

from app.core.db import session
from app.models import BankConnection, BankTransaction, BillAlias, Expense
from app.services import bill_payments as bp
from test_pluggy_import import TODAY, _connect, _post_item, day, txs  # noqa: F401  (fixture)
from test_pluggy_items import bank  # noqa: F401  (fixture)

ENEL = {'paymentMethod': 'PIX', 'receiver': {'name': 'ENEL DISTRIBUICAO SAO PAULO',
                                             'documentNumber': {'type': 'CNPJ', 'value': '61.695.227/0001-93'}}}


def _bill(family, description, amount, days_ago, category='Moradia', method='PIX'):
    r = family.post('/expenses', json={'description': description, 'amount': str(amount), 'category': category,
                                       'payment_method': method, 'paid': False,
                                       'date': (TODAY - timedelta(days=days_ago)).isoformat()})
    assert r.status_code in (200, 201), r.text
    return r.json()['expenses'][0]['id']


def _resync():
    session.query(BankConnection).update({'needs_sync': True})
    session.commit()
    session.remove()


def test_fixed_bill_same_value_is_marked_paid(client, family, bank, txs):  # noqa: F811
    acc, _cc, _ = _connect(client, family, bank, txs)
    internet = _bill(family, 'Internet Vivo', '99.90', days_ago=8)  # pago 6 dias depois do vencimento
    txs[acc].append({'id': 't-net', 'type': 'DEBIT', 'amount': -99.9, 'date': day(2),
                     'description': 'PIX ENVIADO TELEFONICA BRASIL', 'paymentData': {'paymentMethod': 'PIX'}})
    _post_item(family)
    e = session.get(Expense, internet)
    assert e.paid is True and float(e.amount) == 99.9 and e.bank == 'Nubank'  # o banco que pagou
    dup = family.get('/pluggy/transactions?status=duplicate').json()['items']
    assert dup[0]['reason'] == 'Conta "Internet Vivo" marcada como paga pelo Nubank.' and dup[0]['expense']['id'] == internet
    session.remove()


def test_variable_bill_asks_once_then_learns(client, family, other_family, bank, txs):  # noqa: F811
    acc, _cc, _ = _connect(client, family, bank, txs)
    luz = _bill(family, 'Conta de luz', '200.00', days_ago=3)
    _bill(family, 'Aluguel', '2500.00', days_ago=3)            # valor longe: não é candidata
    txs[acc].append({'id': 't-enel', 'type': 'DEBIT', 'amount': -231.55, 'date': day(1),
                     'description': 'PIX ENVIADO ENEL', 'paymentData': ENEL})
    _post_item(family)
    item = family.get('/pluggy/transactions').json()['items'][0]  # nome diferente: pergunta
    assert item['description'] == 'PIX ENVIADO ENEL'
    assert [b['id'] for b in item['bills']] == [luz] and item['bills'][0]['match'] == 'near'
    assert not session.get(Expense, luz).paid
    # outra família não usa a minha conta nem o meu lançamento
    assert other_family.post(f"/pluggy/transactions/{item['id']}/pay-bill", json={'expense_id': luz}).status_code in (403, 404)
    r = family.post(f"/pluggy/transactions/{item['id']}/pay-bill", json={'expense_id': luz})
    assert r.status_code == 200, r.text
    assert 'marcada como paga' in r.json()['message'] and 'R$ 231,55' in r.json()['message']
    e = session.get(Expense, luz)
    assert e.paid is True and float(e.amount) == 231.55
    alias = BillAlias.query.one()
    assert alias.bill_key == 'conta de luz' and alias.payee_doc == '61695227000193'
    session.remove()

    # mês seguinte: a conta lançada de novo e o PIX com o mesmo CNPJ (outro texto) → paga sozinha
    luz2 = _bill(family, 'Conta de luz', '200.00', days_ago=0)
    txs[acc].append({'id': 't-enel2', 'type': 'DEBIT', 'amount': -188.1, 'date': day(0),
                     'description': 'PAGAMENTO CONVENIO', 'paymentData': ENEL})
    _resync()
    family.get('/pluggy/transactions')
    e2 = session.get(Expense, luz2)
    assert e2.paid is True and float(e2.amount) == 188.1
    tx = BankTransaction.query.filter_by(pluggy_id='t-enel2').one()
    assert tx.status == 'duplicate' and tx.expense_id == luz2
    session.remove()

    # desfazer: volta para a revisão, a conta volta a pendente com o valor de antes
    assert family.post('/pluggy/transactions/restore', json={'ids': [tx.id]}).status_code == 200
    e2 = session.get(Expense, luz2)
    assert e2.paid is False and float(e2.amount) == 200.0 and e2.bank is None  # banco de antes (nenhum)
    session.remove()


def test_safety_two_bills_same_value_or_far_value_ask(client, family, bank, txs):  # noqa: F811
    acc, _cc, _ = _connect(client, family, bank, txs)
    a = _bill(family, 'Academia', '120.00', days_ago=2)
    b = _bill(family, 'Curso de inglês', '120.00', days_ago=4, category='Educação')
    card = _bill(family, 'Celular', '120.00', days_ago=2, method='Cartão de Crédito')  # crédito vai pela fatura
    txs[acc] += [{'id': 't-120', 'type': 'DEBIT', 'amount': -120.0, 'date': day(1), 'description': 'PIX JOAO'},
                 {'id': 't-big', 'type': 'DEBIT', 'amount': -1200.0, 'date': day(1), 'description': 'PIX ACADEMIA'}]
    _post_item(family)
    items = {t['description']: t for t in family.get('/pluggy/transactions').json()['items']}
    assert {x['id'] for x in items['PIX JOAO']['bills']} == {a, b}      # duas possíveis: pergunta
    assert items['PIX ACADEMIA']['bills'] == []                         # 10x o valor: nenhuma
    assert not any(session.get(Expense, i).paid for i in (a, b))
    r = family.post(f"/pluggy/transactions/{items['PIX JOAO']['id']}/pay-bill", json={'expense_id': card})
    assert r.status_code == 422
    session.remove()


def test_words_and_alias_matching():
    assert bp.words('PIX ENVIADO - Telefônica Brasil S.A. 12345') == {'telefonica', 'brasil'}
    alias = BillAlias(bill_key='internet', payee_words='brasil telefonica vivo')
    tx = BankTransaction(description='TELEFONICA BRASIL VIVO FIBRA')
    assert bp.alias_matches(alias, tx, bp.tx_words(tx))
    tx = BankTransaction(description='PIX MARIA BRASIL')
    assert not bp.alias_matches(alias, tx, bp.tx_words(tx))


def test_pay_bill_isolation_and_api_token(client, family, other_family, bank, txs):  # noqa: F811
    from conftest import API
    acc, _cc, _ = _connect(client, family, bank, txs)
    luz = _bill(family, 'Conta de luz', '200.00', days_ago=3)
    txs[acc].append({'id': 't-x', 'type': 'DEBIT', 'amount': -231.55, 'date': day(1),
                     'description': 'PIX ENVIADO ENEL', 'paymentData': ENEL})
    _post_item(family)
    tx_id = family.get('/pluggy/transactions').json()['items'][0]['id']
    # token de API (MCP/scripts) não paga conta
    raw = family.post('/auth/tokens', json={'name': 'mcp'}).json()['token']
    r = client.post(f'{API}/pluggy/transactions/{tx_id}/pay-bill', headers={'Authorization': f'Bearer {raw}'},
                    json={'expense_id': luz})
    assert r.status_code == 403
    # minha saída + despesa de outra família: 404 e nada muda
    theirs = _bill(other_family, 'Conta de luz', '200.00', days_ago=3)
    assert family.post(f'/pluggy/transactions/{tx_id}/pay-bill', json={'expense_id': theirs}).status_code == 404
    assert not session.get(Expense, theirs).paid and not session.get(Expense, luz).paid
    session.remove()


def test_undo_keeps_what_the_person_edited_after(client, family, bank, txs):  # noqa: F811
    acc, _cc, _ = _connect(client, family, bank, txs)
    vivo = _bill(family, 'Vivo', '95.00', days_ago=4)
    txs[acc].append({'id': 't-vivo', 'type': 'DEBIT', 'amount': -95.0, 'date': day(1), 'description': 'PIX VIVO'})
    _post_item(family)
    e = session.get(Expense, vivo)
    assert e.paid and e.bank == 'Nubank'
    e.amount, e.bank = 97, 'Inter'  # a pessoa corrigiu depois
    session.commit()
    tx = BankTransaction.query.filter_by(pluggy_id='t-vivo').one()
    assert family.post('/pluggy/transactions/restore', json={'ids': [tx.id]}).status_code == 200
    e = session.get(Expense, vivo)
    assert e.paid is False and float(e.amount) == 97.0 and e.bank == 'Inter'
    session.remove()
