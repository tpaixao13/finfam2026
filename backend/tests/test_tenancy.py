"""Isolamento entre famílias: nada de uma família pode ser lido/alterado por outra."""


def test_cross_tenant_access_is_404(family, other_family):
    e = family.post('/expenses', json={'description': 'Segredo', 'amount': '10', 'category': 'Outros',
                                       'payment_method': 'PIX'}).json()['expenses'][0]
    card = family.post('/cards', json={'supports_credit': True, 'last_digits': '9999', 'bank': 'Inter',
                                       'due_day': 10, 'best_buy_day': 3}).json()['card']
    goal = family.post('/goals', json={'title': 'Viagem', 'type': 'saving', 'target_amount': '1000'}).json()['goal']
    inc = family.post('/incomes', json={'amount': '5000', 'month': 5, 'year': 2026}).json()['incomes'][0]
    rule = family.post('/rules', json={'keyword': 'uber', 'category': 'Transporte'}).json()['rule']

    assert other_family.get(f"/expenses/{e['id']}").status_code == 404
    assert other_family.patch(f"/expenses/{e['id']}", json={'amount': '1'}).status_code == 404
    assert other_family.delete(f"/expenses/{e['id']}").status_code == 404
    assert other_family.post('/expenses/pay', json={'ids': [e['id']]}).json()['changed'] == 0
    assert other_family.get(f"/cards/{card['id']}/invoice").status_code == 404
    assert other_family.delete(f"/cards/{card['id']}").status_code == 404
    assert other_family.delete(f"/goals/{goal['id']}").status_code == 404
    assert other_family.delete(f"/incomes/{inc['id']}").status_code == 404
    assert other_family.delete(f"/rules/{rule['id']}").status_code == 404
    assert other_family.get('/expenses', params={'month': 0}).json()['total'] == 0
    assert other_family.get('/rules').json() == []


def test_cannot_write_for_other_family_member(family, other_family):
    r = other_family.post('/expenses', json={'description': 'x', 'amount': '10', 'category': 'Outros',
                                             'payment_method': 'PIX', 'user_id': family.user['id']})
    assert r.status_code == 422
    card = family.post('/cards', json={'supports_credit': True, 'last_digits': '1111', 'bank': 'Inter',
                                       'due_day': 10, 'best_buy_day': 3}).json()['card']
    r = other_family.post('/expenses', json={'description': 'x', 'amount': '10', 'category': 'Outros',
                                             'payment_method': 'Cartão de Crédito', 'card_id': card['id']})
    assert r.status_code == 422  # cartão de outra família não é encontrado
