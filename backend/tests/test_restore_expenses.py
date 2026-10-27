"""scripts.restore_expenses: devolve do backup o que o "Recomeçar pelo banco" apagou a mais."""
import gzip
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from app.core.config import settings
from app.core.db import session
from app.models import Expense, User
from scripts import restore_expenses as rs

TODAY = date.today()


def _backup(tmp_path: Path) -> Path:
    src = settings.database_url.removeprefix('sqlite:///')
    raw = tmp_path / 'b.db'
    with sqlite3.connect(src) as s, sqlite3.connect(raw) as t:
        s.backup(t)
    gz = tmp_path / 'b.db.gz'
    gz.write_bytes(gzip.compress(raw.read_bytes()))
    return gz


def test_restore_brings_back_only_what_is_missing(family, tmp_path, capsys):
    card = family.post('/cards', json={'supports_credit': True, 'last_digits': '6494', 'bank': 'Nubank',
                                       'due_day': 10, 'best_buy_day': 3}).json()['card']
    d = (TODAY - timedelta(days=3)).isoformat()
    for desc, method, kw in (('Virtual Uber', 'Cartão de Crédito', {'card_id': card['id']}),
                             ('Mercado', 'PIX', {'bank': 'Caixa'}),
                             ('Celular 3x', 'Cartão de Crédito', {'card_id': card['id'], 'installments': 3})):
        assert family.post('/expenses', json={'description': desc, 'amount': '90', 'category': 'Outros',
                                              'payment_method': method, 'date': d, **kw}).status_code == 201
    backup = _backup(tmp_path)
    before = {e.description: (e.card_id, e.year, e.month, e.paid, e.installment_group_id)
              for e in Expense.query.all() if e.installment_number in (None, 1)}
    # "recomeçar" apagou tudo; o banco trouxe o Mercado de volta (outro nome)
    for e in Expense.query.all():
        session.delete(e)
    session.commit()
    session.add(Expense(user_id=family.user['id'], description='PIX MERCADO DO ZE', amount=90, category='Outros',
                        payment_method='PIX', year=TODAY.year, month=(TODAY - timedelta(days=2)).month,
                        day=(TODAY - timedelta(days=2)).day, paid=True))
    session.commit()
    session.remove()
    email = family.user['email']
    since = (TODAY - timedelta(days=10)).isoformat()
    assert rs.main([str(backup), '--email', email, '--desde', since]) == 0
    out = capsys.readouterr().out
    assert '2 despesa(s) para devolver; 1 já voltaram' in out and 'Nada foi gravado' in out
    assert Expense.query.count() == 1
    session.remove()
    assert rs.main([str(backup), '--email', email, '--desde', since, '--aplicar']) == 0
    now = {e.description: (e.card_id, e.year, e.month, e.paid, e.installment_group_id)
           for e in Expense.query.all() if e.installment_number in (None, 1)}
    assert now['Virtual Uber'] == before['Virtual Uber']  # cartão e mês da fatura iguais
    assert now['Celular 3x'] == before['Celular 3x']
    assert 'Mercado' not in now  # o banco já trouxe
    # só a 1ª parcela estava no período; as futuras não foram apagadas no cenário real
    assert User.query.filter_by(email=email).count() == 1
    assert rs.main([str(backup), '--email', email, '--desde', since]) == 0
    assert '0 despesa(s) para devolver' in capsys.readouterr().out  # rodar de novo não duplica
    session.remove()
