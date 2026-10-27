"""Devolve despesas apagadas, a partir de um backup do banco (o .db.gz que chega no Telegram).

Feito para desfazer um "Recomeçar pelo banco" que apagou mais do que devia: traz de volta as
despesas da pessoa que estão no backup e não existem mais hoje — com cartão, mês da fatura,
parcelas, recorrência e "pago" exatamente como estavam. Pula as que o banco já trouxe de
novo (mesmo valor e data perto), para não duplicar.

Uso, na pasta backend do servidor:
    .venv/bin/python -m scripts.restore_expenses BACKUP.db.gz --email voce@x.com --desde 2026-10-01
    .venv/bin/python -m scripts.restore_expenses BACKUP.db.gz --email voce@x.com --desde 2026-10-01 --aplicar

Sem --aplicar só mostra o que faria. Opcional: --ate 2026-10-27 (padrão: hoje).
"""
import gzip
import shutil
import sqlite3
import sys
import tempfile
from datetime import date
from pathlib import Path

from sqlalchemy import text

from app.core.db import get_engine, session
from app.core.migrations import run_migrations
from app.models import BankConnection, Expense, User
from app.services.finance import CREDIT, DEBIT

BANK_METHODS = ('PIX', DEBIT, CREDIT)


def open_backup(path: Path, folder: Path) -> sqlite3.Connection:
    if path.suffix == '.gz':
        raw = folder / 'backup.db'
        with gzip.open(path, 'rb') as fin, open(raw, 'wb') as fout:
            shutil.copyfileobj(fin, fout)
        path = raw
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _when(row) -> date:
    return date(row['purchase_year'] or row['year'], row['purchase_month'] or row['month'],
                max(1, min(int(row['day'] or 1), 28)))


def _same_class(method_a: str, method_b: str) -> bool:
    return (method_a == CREDIT) == (method_b == CREDIT)


def already_back(row, current: list[Expense]) -> Expense | None:
    """Despesa de hoje que já representa a do backup (o banco trouxe de novo, ou nunca saiu)."""
    when, amount = _when(row), round(float(row['amount']), 2)
    k = row['installment_number']
    for e in current:
        if round(float(e.amount), 2) != amount or not _same_class(e.payment_method, row['payment_method']):
            continue
        e_when = date(e.purchase_year or e.year, e.purchase_month or e.month, max(1, min(e.day or 1, 28)))
        gap = abs((e_when - when).days)
        if gap <= 3 or (k and k > 1 and e.installment_number == k and gap <= 70):
            return e
    return None


def _still_there(row) -> bool:
    e = session.get(Expense, row['id'])
    return e is not None and e.user_id == row['user_id'] and e.description == row['description'] \
        and round(float(e.amount), 2) == round(float(row['amount']), 2)


def plan(backup: sqlite3.Connection, user: User, since: date, until: date) -> tuple[list, list]:
    """(a restaurar, já de volta) — linhas do backup que não existem mais hoje."""
    rows = backup.execute(
        f"SELECT * FROM expenses WHERE user_id = ? AND payment_method IN ({','.join('?' * len(BANK_METHODS))})",
        (user.id, *BANK_METHODS)).fetchall()
    missing = [r for r in rows if since <= _when(r) <= until and not _still_there(r)]
    current = Expense.query.filter(Expense.user_id == user.id).all()
    restore, back = [], []
    for r in sorted(missing, key=_when):
        hit = already_back(r, current)
        (back if hit else restore).append((r, hit))
    return restore, back


def _copy_row(conn, table: str, row) -> None:
    cols = [c for c in row.keys() if c in _columns(conn, table)]
    conn.execute(text(f'INSERT INTO {table} ({", ".join(cols)}) VALUES ({", ".join(":" + c for c in cols)})'),
                 {c: row[c] for c in cols})


_COLS: dict[str, set[str]] = {}


def _columns(conn, table: str) -> set[str]:
    if table not in _COLS:
        _COLS[table] = {r[1] for r in conn.execute(text(f'PRAGMA table_info({table})'))}
    return _COLS[table]


def apply(backup: sqlite3.Connection, rows: list) -> int:
    """Insere as despesas com o mesmo id (e o grupo de parcelas/recorrência se ele sumiu)."""
    engine = get_engine()
    with engine.begin() as conn:
        for r, _ in rows:
            for table, field in (('installment_groups', 'installment_group_id'), ('recurring_groups', 'recurring_group_id')):
                gid = r[field]
                if gid and not conn.execute(text(f'SELECT 1 FROM {table} WHERE id = :i'), {'i': gid}).first():
                    g = backup.execute(f'SELECT * FROM {table} WHERE id = ?', (gid,)).fetchone()
                    if g is not None:
                        _copy_row(conn, table, g)
            row = dict(r)
            if row['card_id'] and not conn.execute(text('SELECT 1 FROM credit_cards WHERE id = :i'),
                                                   {'i': row['card_id']}).first():
                row['card_id'] = None  # cartão apagado depois: volta sem cartão
            if conn.execute(text('SELECT 1 FROM expenses WHERE id = :i'), {'i': row['id']}).first():
                row.pop('id')  # o id foi reaproveitado por uma despesa nova: entra com id novo
            _copy_row(conn, 'expenses', row)
    return len(rows)


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith('--')]

    def opt(name):
        return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else None
    email, since = opt('--email'), opt('--desde')
    if len(args) < 1 or not email or not since:
        print(__doc__)
        return 2
    path = Path(args[0])
    if not path.exists():
        print(f'Arquivo não encontrado: {path}')
        return 1
    until = date.fromisoformat(opt('--ate')) if opt('--ate') else date.today()
    run_migrations()
    user = User.query.filter_by(email=email.strip().lower()).first()
    if user is None:
        print(f'Nenhum usuário com o e-mail {email}.')
        return 1
    with tempfile.TemporaryDirectory() as folder:
        backup = open_backup(path, Path(folder))
        restore, back = plan(backup, user, date.fromisoformat(since), until)
        print(f'{user.name}: {len(restore)} despesa(s) para devolver; {len(back)} já voltaram pelo banco.\n')
        for r, _ in restore:
            print(f"  + {_when(r):%d/%m} {r['payment_method'][:18]:18} R$ {float(r['amount']):>9.2f}  {r['description'][:50]}")
        for r, e in back:
            print(f"  = {_when(r):%d/%m} {r['payment_method'][:18]:18} R$ {float(r['amount']):>9.2f}  "
                  f"{r['description'][:30]}  (já está como: {e.description[:30]})")
        if '--aplicar' not in argv:
            print('\nNada foi gravado. Rode de novo com --aplicar para devolver as marcadas com +.')
            return 0
        n = apply(backup, restore)
        backup.close()
    # lançamentos do banco "para revisar" que agora casam com o que voltou: a próxima leitura reclassifica
    for c in BankConnection.query.filter_by(user_id=user.id).all():
        c.needs_sync = True
    session.commit()
    session.remove()
    print(f'\n{n} despesa(s) devolvida(s). Os lançamentos do banco "para revisar" serão relidos ao abrir a página.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
