"""Criação do schema + migrações idempotentes.

Mantém as mesmas migrações do FinFam original, para que um `gastos.db` antigo
seja atualizado automaticamente ao subir a API.
"""
import logging
import secrets
import shutil
from pathlib import Path

from sqlalchemy import text

from app.core.config import settings
from app.core.db import Base, get_engine
from app.core.security import hash_password

log = logging.getLogger('finfam.migrations')

_ALTERS = [
    'ALTER TABLE users ADD COLUMN password_hash TEXT',
    'ALTER TABLE expenses ADD COLUMN recurring_group_id INTEGER REFERENCES recurring_groups(id)',
    'ALTER TABLE expenses ADD COLUMN recurring_number INTEGER',
    'ALTER TABLE users ADD COLUMN avatar TEXT',
    'ALTER TABLE expenses ADD COLUMN paid INTEGER DEFAULT 0',
    'ALTER TABLE salaries ADD COLUMN company TEXT',
    'ALTER TABLE investments ADD COLUMN crypto_coin TEXT',
    'ALTER TABLE investments ADD COLUMN crypto_buy_price NUMERIC(18,8)',
    'ALTER TABLE users ADD COLUMN tenant_id INTEGER REFERENCES tenants(id)',
    'ALTER TABLE users ADD COLUMN email TEXT',
    'ALTER TABLE tenants ADD COLUMN telegram_enabled INTEGER DEFAULT 0',
    'ALTER TABLE tenants ADD COLUMN telegram_token TEXT',
    'ALTER TABLE tenants ADD COLUMN telegram_chat_id TEXT',
    'ALTER TABLE tenants ADD COLUMN telegram_hour INTEGER DEFAULT 8',
    'ALTER TABLE tenants ADD COLUMN telegram_minute INTEGER DEFAULT 0',
    'ALTER TABLE tenants ADD COLUMN trial_expires_at DATETIME',
    'ALTER TABLE tenants ADD COLUMN telegram_last_sent DATE',
    'ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0',
    'ALTER TABLE users ADD COLUMN last_seen DATETIME',
    "ALTER TABLE salaries ADD COLUMN income_type TEXT NOT NULL DEFAULT 'fixa'",
    'ALTER TABLE salaries ADD COLUMN salary_group_id INTEGER REFERENCES salary_groups(id)',
    'ALTER TABLE salaries ADD COLUMN payment_day INTEGER',
    'ALTER TABLE salaries ADD COLUMN payment_day_type TEXT',
    'ALTER TABLE salaries ADD COLUMN received INTEGER DEFAULT 1',
    "ALTER TABLE tenants ADD COLUMN plan TEXT DEFAULT 'trial'",
    'ALTER TABLE tenants ADD COLUMN extra_members INTEGER DEFAULT 0',
    'ALTER TABLE expenses ADD COLUMN card_id INTEGER REFERENCES credit_cards(id)',
    'ALTER TABLE credit_cards ADD COLUMN credit_limit NUMERIC(12,2)',
    "ALTER TABLE credit_cards ADD COLUMN card_type TEXT NOT NULL DEFAULT 'credit'",
    'ALTER TABLE credit_cards ADD COLUMN is_virtual INTEGER NOT NULL DEFAULT 0',
    'ALTER TABLE credit_cards ADD COLUMN is_additional INTEGER NOT NULL DEFAULT 0',
    'ALTER TABLE credit_cards ADD COLUMN monthly_amount NUMERIC(12,2)',
    'ALTER TABLE credit_cards ADD COLUMN renewal_day INTEGER',
    'ALTER TABLE credit_cards ADD COLUMN supports_credit INTEGER NOT NULL DEFAULT 0',
    'ALTER TABLE credit_cards ADD COLUMN supports_debit INTEGER NOT NULL DEFAULT 0',
    "UPDATE credit_cards SET supports_credit = 1 WHERE card_type = 'credit' AND supports_credit = 0",
    "UPDATE credit_cards SET supports_debit = 1 WHERE card_type = 'debit' AND supports_debit = 0",
    'ALTER TABLE credit_cards ADD COLUMN account_id INTEGER REFERENCES credit_accounts(id)',
    'ALTER TABLE expenses ADD COLUMN purchase_month INTEGER',
    'ALTER TABLE investments ADD COLUMN currency TEXT',
    'ALTER TABLE tenants ADD COLUMN open_finance_enabled INTEGER NOT NULL DEFAULT 0',
    'ALTER TABLE tenants ADD COLUMN open_finance_max_items INTEGER',
    'ALTER TABLE bank_connections ADD COLUMN import_from DATE',
    'ALTER TABLE bank_connections ADD COLUMN tx_synced_at DATETIME',
    'ALTER TABLE bank_connections ADD COLUMN display_name TEXT',
    'ALTER TABLE bank_connections ADD COLUMN auto_import INTEGER NOT NULL DEFAULT 0',
    'ALTER TABLE bank_accounts ADD COLUMN available_limit NUMERIC(14, 2)',
    'ALTER TABLE bank_accounts ADD COLUMN close_day INTEGER',
    'ALTER TABLE bank_accounts ADD COLUMN due_day INTEGER',
    'ALTER TABLE bank_accounts ADD COLUMN manual_close_day INTEGER',
    'ALTER TABLE bank_accounts ADD COLUMN close_source VARCHAR(8)',
    'ALTER TABLE tenants ADD COLUMN telegram_alerts INTEGER NOT NULL DEFAULT 1',
    'ALTER TABLE tenants ADD COLUMN telegram_weekly_sent DATE',
    'ALTER TABLE goals ADD COLUMN alert_sent TEXT',
    'ALTER TABLE bank_transactions ADD COLUMN bill_year INTEGER',
    'ALTER TABLE bank_transactions ADD COLUMN bill_month INTEGER',
    'ALTER TABLE bank_transactions ADD COLUMN bill_ref VARCHAR(64)',
    'ALTER TABLE bank_transactions ADD COLUMN card_digits VARCHAR(4)',
    'ALTER TABLE pluggy_configs ADD COLUMN notify INTEGER NOT NULL DEFAULT 1',
    "ALTER TABLE bank_transactions ADD COLUMN kind VARCHAR(10) NOT NULL DEFAULT 'expense'",
    'ALTER TABLE users ADD COLUMN totp_secret TEXT',
    'ALTER TABLE users ADD COLUMN totp_enabled INTEGER NOT NULL DEFAULT 0',
    'ALTER TABLE users ADD COLUMN totp_failures INTEGER NOT NULL DEFAULT 0',
    'ALTER TABLE users ADD COLUMN totp_locked INTEGER NOT NULL DEFAULT 0',
    'ALTER TABLE users ADD COLUMN totp_last_step INTEGER',
    'ALTER TABLE investments ADD COLUMN fx_buy_rate NUMERIC(14,6)',
    'ALTER TABLE expenses ADD COLUMN purchase_year INTEGER',
    # limpeza de dados órfãos (usuário/família já excluídos) — não podem ser herdados
    'DELETE FROM goals WHERE user_id NOT IN (SELECT id FROM users)',
    'DELETE FROM category_rules WHERE tenant_id NOT IN (SELECT id FROM tenants)',
    'UPDATE credit_cards SET account_id = NULL WHERE account_id IN '
    '(SELECT id FROM credit_accounts WHERE user_id NOT IN (SELECT id FROM users))',
    'DELETE FROM credit_accounts WHERE user_id NOT IN (SELECT id FROM users)',
    'UPDATE salaries SET salary_group_id = NULL WHERE salary_group_id IN '
    '(SELECT id FROM salary_groups WHERE user_id NOT IN (SELECT id FROM users))',
    'DELETE FROM salary_groups WHERE user_id NOT IN (SELECT id FROM users)',
]


def _exec_each(conn, statements):
    for sql in statements:
        try:
            conn.execute(text(sql))
            conn.commit()
        except Exception:
            conn.rollback()  # coluna já existe


def _add_email_verified(conn) -> None:
    """Coluna nova users.email_verified_at: quem já tinha conta antes da confirmação por e-mail
    fica confirmado (só na primeira vez — depois a coluna já existe e nada muda)."""
    try:
        conn.execute(text('ALTER TABLE users ADD COLUMN email_verified_at DATETIME'))
    except Exception:
        conn.rollback()
        return
    conn.execute(text('UPDATE users SET email_verified_at = CURRENT_TIMESTAMP WHERE email_verified_at IS NULL'))
    conn.commit()


def _dedupe_admins(conn):
    """O FinFam original perdeu o UNIQUE de users.email numa migração antiga e passou a
    inserir um admin novo a cada reinício. Mantém só o admin mais antigo de cada e-mail
    (o que o login antigo usava) e recria o índice único."""
    conn.execute(text(
        'DELETE FROM users WHERE is_admin = 1 AND tenant_id IS NULL AND email IS NOT NULL '
        'AND id NOT IN (SELECT MIN(id) FROM users WHERE is_admin = 1 GROUP BY email) '
        'AND id NOT IN (SELECT user_id FROM expenses) AND id NOT IN (SELECT user_id FROM salaries)'))
    conn.commit()
    try:
        conn.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email ON users(email)'))
        conn.commit()
    except Exception as exc:  # ainda há e-mails repetidos entre usuários comuns
        conn.rollback()
        log.warning('Não foi possível criar o índice único de e-mail: %s', exc)


def _seed_admin(conn):
    """Cria o admin se não existir. Nunca sobrescreve a senha de um admin existente."""
    existing = conn.execute(text('SELECT id FROM users WHERE email = :e ORDER BY id'),
                            {'e': settings.admin_email}).first()
    if existing:
        conn.execute(text('UPDATE users SET is_admin = 1 WHERE id = :id'), {'id': existing[0]})
        conn.commit()
        return
    password = settings.admin_initial_password
    if not password:
        password = secrets.token_urlsafe(16)
        print(f'[FinFam] ADMIN_INITIAL_PASSWORD não definida. Senha gerada para '
              f'{settings.admin_email}: {password}')
    conn.execute(text("INSERT INTO users (name, email, password_hash, is_admin) VALUES ('Admin', :e, :pw, 1)"),
                 {'e': settings.admin_email, 'pw': hash_password(password)})
    conn.commit()


def _migrate_credit_accounts(conn):
    """Agrupa cartões de crédito sem conta por (user_id, bank) em uma CreditAccount."""
    rows = conn.execute(text(
        'SELECT DISTINCT user_id, bank FROM credit_cards '
        'WHERE account_id IS NULL AND supports_credit = 1 AND bank IS NOT NULL'
    )).fetchall()
    for uid, bank in rows:
        params = {'uid': uid, 'bank': bank}
        account_id = conn.execute(text(
            'SELECT id FROM credit_accounts WHERE user_id = :uid AND bank = :bank'), params).scalar()
        if not account_id:
            max_limit = conn.execute(text(
                'SELECT MAX(credit_limit) FROM credit_cards '
                'WHERE user_id = :uid AND bank = :bank AND supports_credit = 1'), params).scalar()
            conn.execute(text(
                'INSERT INTO credit_accounts (user_id, bank, credit_limit, created_at) '
                'VALUES (:uid, :bank, :lim, CURRENT_TIMESTAMP)'), {**params, 'lim': max_limit})
            account_id = conn.execute(text(
                'SELECT id FROM credit_accounts WHERE user_id = :uid AND bank = :bank'), params).scalar()
        conn.execute(text(
            'UPDATE credit_cards SET account_id = :aid WHERE user_id = :uid AND bank = :bank '
            'AND supports_credit = 1 AND account_id IS NULL'), {**params, 'aid': account_id})
    conn.commit()


_AVATAR_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}


def _import_legacy_avatars(conn) -> int:
    """Fotos do FinFam antigo (LEGACY_UPLOAD_DIR/avatars/user_N.png): copia para a pasta
    nova com nome aleatório e atualiza o usuário. Só LÊ a pasta antiga; roda a cada início
    e ignora quem já tem a foto na pasta nova."""
    legacy = settings.legacy_upload_dir
    if not legacy:
        return 0
    src_dir, dst_dir = legacy / 'avatars', settings.upload_dir / 'avatars'
    if not src_dir.is_dir():
        log.warning('LEGACY_UPLOAD_DIR sem a pasta avatars: %s', src_dir)
        return 0
    dst_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for uid, avatar in conn.execute(text('SELECT id, avatar FROM users WHERE avatar IS NOT NULL')).fetchall():
        name = Path(avatar).name
        if (dst_dir / name).is_file():
            continue
        src = src_dir / name
        if not src.is_file() or src.suffix.lower() not in _AVATAR_EXT or src.stat().st_size > 5 * 1024 * 1024:
            continue
        new_name = f'{secrets.token_urlsafe(16)}{src.suffix.lower()}'
        shutil.copyfile(src, dst_dir / new_name)
        conn.execute(text('UPDATE users SET avatar = :a WHERE id = :id'), {'a': new_name, 'id': uid})
        copied += 1
    conn.commit()
    if copied:
        log.info('%s foto(s) de perfil copiada(s) do FinFam antigo.', copied)
    return copied


def run_migrations() -> None:
    from app import models  # noqa: F401  registra as tabelas no metadata

    engine = get_engine()
    Base.metadata.create_all(engine)
    if engine.dialect.name != 'sqlite':
        return  # migrações legadas são específicas do SQLite
    with engine.connect() as conn:
        _exec_each(conn, _ALTERS)
        _add_email_verified(conn)
        try:
            _migrate_credit_accounts(conn)
        except Exception as exc:  # não impede a API de subir
            conn.rollback()
            log.warning('Migração de contas de crédito falhou: %s', exc)
        try:
            _dedupe_admins(conn)
        except Exception as exc:
            conn.rollback()
            log.warning('Limpeza de admins duplicados falhou: %s', exc)
        try:
            _import_legacy_avatars(conn)
        except Exception as exc:
            conn.rollback()
            log.warning('Cópia das fotos do FinFam antigo falhou: %s', exc)
        try:
            _seed_admin(conn)
        except Exception as exc:
            conn.rollback()
            log.warning('Seed do admin falhou: %s', exc)
