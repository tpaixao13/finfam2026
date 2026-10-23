"""Testes das ferramentas MCP com uma API falsa (sem rede)."""
import pytest

from finfam_mcp import server

CARDS = [
    {'id': 1, 'label': 'Nubank •••• 4821 (Crédito/Débito)', 'last_digits': '4821', 'is_virtual': False,
     'is_additional': False, 'supports_credit': True},
    {'id': 2, 'label': 'Nubank •••• 9034 (Virtual)', 'last_digits': '9034', 'is_virtual': True,
     'is_additional': False, 'supports_credit': True},
    {'id': 3, 'label': 'Itaú •••• 1177 (Crédito)', 'last_digits': '1177', 'is_virtual': False,
     'is_additional': False, 'supports_credit': True},
]
MEMBERS = [{'id': 10, 'name': 'Ana Silva'}, {'id': 11, 'name': 'Bruno Silva'}]


class FakeAPI:
    def __init__(self):
        self.posts = []

    def get(self, path, **q):
        if path == '/cards':
            return {'cards': CARDS}
        if path == '/auth/me':
            return {'members': MEMBERS}
        if path == '/expenses/suggest-category':
            return {'category': 'Alimentação'}
        raise AssertionError(path)

    def post(self, path, body=None):
        self.posts.append((path, body))
        return {'message': 'ok', 'expenses': [{'id': 99, 'category': body['category'], 'month': 5, 'year': 2026,
                                               'purchase_month': 5, 'purchase_year': 2026, 'due_date': '2026-05-12'}]}


@pytest.fixture()
def fake(monkeypatch):
    f = FakeAPI()
    monkeypatch.setattr(server, 'api', f)
    return f


def test_resolve_card_by_name_digits_and_id(fake):
    assert server._resolve_card('itau')['id'] == 3
    assert server._resolve_card('nubank')['id'] == 1  # prefere o cartão principal
    assert server._resolve_card('9034')['id'] == 2
    assert server._resolve_card(3)['id'] == 3
    with pytest.raises(server.FinFamError):
        server._resolve_card('santander')


def test_resolve_person_ignores_accents_and_case(fake):
    assert server._resolve_person('bruno') == 11
    with pytest.raises(server.FinFamError):
        server._resolve_person('Silva')  # ambíguo


def test_lancar_despesa_infers_method_and_category(fake):
    out = server.lancar_despesa('iFood pizza', 89.9, cartao='itau')
    path, body = fake.posts[-1]
    assert path == '/expenses'
    assert body['payment_method'] == 'Cartão de Crédito' and body['card_id'] == 3
    assert body['category'] == 'Alimentação' and body['amount'] == '89.90'
    assert '#99' in out


def test_errors_become_text(fake):
    assert server.lancar_despesa('x', 1, cartao='inexistente').startswith('Erro:')
