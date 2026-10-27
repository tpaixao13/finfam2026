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
        if path == '/pluggy/summary':
            self.summary_q = q
            return {'counts': {'pending': 1, 'imported': 3, 'duplicate': 0, 'ignored': 2},
                    'banks': [{'name': 'C6 Bank', 'auto_import': True}],
                    'items': [{'date': '2026-10-24', 'description': 'COOP', 'amount': 38.78, 'bank': 'C6 Bank',
                               'bill': '11/2026', 'reason': None}]}
        if path == '/pluggy/invoice-check':
            return {'cards': [{'label': 'C6 Bank •••• 0982', 'linked': True, 'bank_total': 7264.41, 'bank_source': 'fatura',
                               'finfam_total': 5865.6, 'diff': -1398.81,
                               'only_bank': [{'date': '2026-10-25', 'description': 'MERCADO', 'amount': 100.0}],
                               'only_finfam': []},
                              {'label': 'Inter •••• 7770', 'linked': False, 'bank_total': 0, 'bank_source': 'lancamentos',
                               'finfam_total': None, 'diff': None, 'only_bank': [], 'only_finfam': []}]}
        if path == '/investments':
            return {'fixed': [{'investment_type': 'CDB', 'description': 'CDB liquidez', 'amount': 1000, 'value_brl': 1000}],
                    'crypto': [], 'fixed_total': 1000, 'crypto_total': 0}
        if path == '/pluggy/investments':
            return {'enabled': True, 'total': 1529.56, 'items': [
                {'name': 'CDB - BANCO C6 S.A.', 'bank': 'C6 Bank', 'type': 'FIXED_INCOME', 'subtype': 'CDB', 'balance': 1201.24,
                 'currency': 'BRL', 'rate': 101.5, 'rate_type': 'CDI', 'due_date': '2029-10-16'},
                {'name': 'HASH11', 'bank': 'C6 Bank', 'type': 'EQUITY', 'subtype': 'STOCK', 'balance': 328.32,
                 'currency': 'BRL', 'rate': None, 'rate_type': None, 'due_date': None}]}
        if path == '/pluggy/balances':
            return {'enabled': True, 'total': 1832.44, 'items': [
                {'bank': 'C6 Bank', 'last_digits': '5665', 'balance': 1832.44, 'updated_at': '2026-10-27T15:00:00Z'}]}
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


def test_open_finance_tools(fake):
    out = server.importados_do_banco('lançados')
    assert fake.summary_q['status'] == 'imported' and 'C6 Bank (automático)' in out and 'COOP' in out
    assert 'fatura 11/2026' in out
    server.importados_do_banco()
    assert fake.summary_q['status'] == 'pending'
    out = server.conferir_fatura_com_banco(11, 2026)
    assert 'diferença' in out and 'só no banco' in out and 'não ligado' in out


def test_investments_and_balances(fake):
    out = server.investimentos()
    assert 'Total investido: R$ 2.529,56' in out
    assert 'Renda Fixa (2): R$ 2.201,24 · 87,0%' in out and 'Ações (1): R$ 328,32 · 13,0%' in out
    assert 'C6 Bank (2)' in out and '101,5% do CDI' in out and 'vence 16/10/2029' in out
    assert 'C6 Bank •••• 5665: R$ 1.832,44' in server.saldo_das_contas()
