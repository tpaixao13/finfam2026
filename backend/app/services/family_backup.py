"""Backup, zerar e restaurar uma família (só o admin usa).

- Backup: TODOS os dados da família desde o início (despesas, rendas, cartões, investimentos,
  metas, bancos conectados...) num .json.gz. Segredos ficam de fora (senhas, 2FA, chaves de
  IA/Pluggy, tokens). Toda vez que zera ou restaura, o servidor guarda um backup antes.
- Zerar: apaga só as DESPESAS da família (com parcelas, recorrências e os lançamentos já
  importados do banco). O resto fica. Os bancos conectados reimportam o período escolhido.
- Restaurar: devolve as despesas exatamente como estavam no backup (troca as atuais).
"""
import gzip
import json
import re
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Date, DateTime, Numeric, insert, select

from app.core.config import INSTANCE_DIR
from app.core.db import Base, db
from app.core.types import utcnow
from app.models import (BankConnection, BankTransaction, CreditCard, Expense, InstallmentGroup, RecurringGroup,
                        Tenant, User)

FORMAT = 'finfam-family-backup'
VERSION = 1
BACKUP_DIR = INSTANCE_DIR / 'backups'
NAME = re.compile(r'familia_(\d+)_\d{8}_\d{6}\.json\.gz')

# nunca vão para o backup
SKIP_TABLES = {'api_tokens', 'ai_configs', 'pluggy_configs', 'telegram_links', 'telegram_link_codes'}
SECRET_COLUMN = re.compile(r'password|secret|totp|token|api_key', re.I)
# o que zerar apaga e restaurar devolve (nesta ordem de inserção)
RESTORE_TABLES = ('installment_groups', 'recurring_groups', 'expenses', 'bank_transactions')


class BackupError(ValueError):
    """Vira 422 para o admin."""


def member_ids(tenant: Tenant) -> list[int]:
    return [u.id for u in User.query.filter_by(tenant_id=tenant.id).all()]


def _plain(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _rows_of(table, tenant: Tenant, uids: list[int], conn_ids: list[int]):
    """Linhas da família: pela família (tenant), pela pessoa (user_id) ou pelo banco conectado."""
    cols = table.c
    if table.name == 'tenants':
        cond = cols.id == tenant.id
    elif 'tenant_id' in cols:
        cond = cols.tenant_id == tenant.id
    elif 'user_id' in cols:
        cond = cols.user_id.in_(uids or [0])
    elif 'connection_id' in cols:
        cond = cols.connection_id.in_(conn_ids or [0])
    else:
        return []  # tabela sem dono (ex.: admins, cotações): não é da família
    keep = [c for c in cols if not SECRET_COLUMN.search(c.name)]
    return [{c.name: _plain(v) for c, v in zip(keep, row)}
            for row in db.session.execute(select(*keep).where(cond)).all()]


def dump(tenant: Tenant) -> dict:
    """Tudo da família, tabela por tabela (sem segredos)."""
    uids = member_ids(tenant)
    conn_ids = [c.id for c in BankConnection.query.filter(BankConnection.user_id.in_(uids or [0])).all()]
    data = {}
    for table in Base.metadata.sorted_tables:
        if table.name in SKIP_TABLES:
            continue
        rows = _rows_of(table, tenant, uids, conn_ids)
        if rows:
            data[table.name] = rows
    return {'format': FORMAT, 'version': VERSION, 'created_at': utcnow().isoformat(),
            'tenant': {'id': tenant.id, 'name': tenant.name}, 'members': uids, 'tables': data}


def save(tenant: Tenant, reason: str = 'manual') -> Path:
    """Grava o backup no servidor (instance/backups) e devolve o arquivo."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    payload = dump(tenant)
    payload['reason'] = reason
    path = BACKUP_DIR / f'familia_{tenant.id}_{utcnow():%Y%m%d_%H%M%S}.json.gz'
    while path.exists():  # dois no mesmo segundo
        time.sleep(0.2)
        path = BACKUP_DIR / f'familia_{tenant.id}_{utcnow():%Y%m%d_%H%M%S}.json.gz'
    path.write_bytes(gzip.compress(json.dumps(payload, ensure_ascii=False).encode()))
    return path


def list_saved(tenant: Tenant) -> list[dict]:
    if not BACKUP_DIR.exists():
        return []
    out = []
    for p in sorted(BACKUP_DIR.glob(f'familia_{tenant.id}_*.json.gz'), reverse=True):
        try:
            meta = json.loads(gzip.decompress(p.read_bytes()))
        except (OSError, ValueError):
            continue
        tables = meta.get('tables', {})
        out.append({'name': p.name, 'created_at': meta.get('created_at'), 'reason': meta.get('reason'),
                    'size': p.stat().st_size, 'expenses': len(tables.get('expenses', []))})
    return out


def load_saved(tenant: Tenant, name: str) -> dict:
    m = NAME.fullmatch(name or '')
    if not m or int(m.group(1)) != tenant.id:
        raise BackupError('Backup não encontrado para esta família.')
    path = BACKUP_DIR / name
    if not path.exists():
        raise BackupError('Backup não encontrado para esta família.')
    data = json.loads(gzip.decompress(path.read_bytes()))
    if data.get('format') != FORMAT or data.get('tenant', {}).get('id') != tenant.id:
        raise BackupError('Arquivo não é um backup desta família.')
    return data


# ── zerar ────────────────────────────────────────────────────────────────────

def wipe_expenses(tenant: Tenant) -> int:
    """Apaga despesas, grupos de parcelas/recorrências e lançamentos importados da família."""
    uids = member_ids(tenant) or [0]
    BankTransaction.query.filter(BankTransaction.user_id.in_(uids)).delete(synchronize_session=False)
    n = Expense.query.filter(Expense.user_id.in_(uids)).delete(synchronize_session=False)
    InstallmentGroup.query.filter(InstallmentGroup.user_id.in_(uids)).delete(synchronize_session=False)
    RecurringGroup.query.filter(RecurringGroup.user_id.in_(uids)).delete(synchronize_session=False)
    db.session.flush()
    return n


def resync_later(tenant: Tenant) -> None:
    """Depois de restaurar: a próxima leitura reclassifica o que veio do banco (sem mudar o período)."""
    for c in BankConnection.query.filter(BankConnection.user_id.in_(member_ids(tenant) or [0])).all():
        c.needs_sync = True
    db.session.flush()


def reimport_from(tenant: Tenant, start: date | None) -> int:
    """Faz os bancos conectados trazerem de novo desde `start` (None = não reimporta agora)."""
    conns = BankConnection.query.filter(BankConnection.user_id.in_(member_ids(tenant) or [0])).all()
    for c in conns:
        c.tx_synced_at = None
        # None: não traz o passado; daqui pra frente o banco continua mandando o que é novo
        c.import_from, c.needs_sync = (start, True) if start is not None else (date.today(), False)
    db.session.flush()
    return len(conns)


# ── restaurar ────────────────────────────────────────────────────────────────

def _typed(table, row: dict) -> dict:
    out = {}
    for name, value in row.items():
        col = table.c.get(name)
        if col is None:
            continue  # coluna que não existe mais
        if value is not None and isinstance(col.type, DateTime):
            value = datetime.fromisoformat(value)
        elif value is not None and isinstance(col.type, Date):
            value = date.fromisoformat(value[:10])
        elif value is not None and isinstance(col.type, Numeric):
            value = Decimal(str(value))
        out[name] = value
    return out


def restore_expenses(tenant: Tenant, data: dict) -> int:
    """Troca as despesas atuais da família pelas do backup (ids preservados quando livres)."""
    uids = set(member_ids(tenant))
    tables = {t.name: t for t in Base.metadata.sorted_tables}
    wipe_expenses(tenant)
    remap: dict[str, dict[int, int]] = {name: {} for name in RESTORE_TABLES}
    cards = {c.id for c in CreditCard.query.filter(CreditCard.user_id.in_(uids or {0})).all()}
    accounts = {a for (a,) in db.session.execute(select(tables['bank_accounts'].c.id)).all()}
    restored = 0
    for name in RESTORE_TABLES:
        table = tables[name]
        for raw in data.get('tables', {}).get(name, []):
            row = _typed(table, raw)
            if row.get('user_id') not in uids:
                continue  # só pessoas que ainda são da família
            if name == 'expenses':
                for field, src in (('installment_group_id', 'installment_groups'), ('recurring_group_id', 'recurring_groups')):
                    if row.get(field) is not None:
                        row[field] = remap[src].get(row[field], row[field])
                if row.get('card_id') not in cards:
                    row['card_id'] = None  # cartão apagado depois do backup
            if name == 'bank_transactions':
                if row.get('account_id') not in accounts or \
                        BankTransaction.query.filter_by(pluggy_id=row.get('pluggy_id')).first():
                    continue  # banco desconectado ou o lançamento já voltou
                if row.get('expense_id') is not None:
                    row['expense_id'] = remap['expenses'].get(row['expense_id'], row['expense_id'])
            old_id = row.get('id')
            if old_id is not None and db.session.execute(select(table.c.id).where(table.c.id == old_id)).first():
                row.pop('id')  # id ocupado por outra família: entra com id novo
            new_id = db.session.execute(insert(table).values(**row)).inserted_primary_key[0]
            if old_id is not None:
                remap[name][old_id] = new_id
            if name == 'expenses':
                restored += 1
    db.session.flush()
    return restored
