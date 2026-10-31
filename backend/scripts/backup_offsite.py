"""Backup automático do FinFam 2026 para FORA do Raspberry, criptografado.

Uso, na pasta backend do servidor:
    .venv/bin/python -m scripts.backup_offsite                 # faz o backup e manda para os destinos
    .venv/bin/python -m scripts.backup_offsite --decrypt ARQ   # ARQ.enc → ARQ (.db.gz) para restaurar

Cron (todo dia às 03:30):
    30 3 * * * cd /home/finfam/finfam2026/backend && .venv/bin/python -m scripts.backup_offsite >> /home/finfam/finfam2026_backup.log 2>&1

Configuração no backend/.env (use um ou mais destinos):
    BACKUP_PASSWORD=...          senha da criptografia (OBRIGATÓRIA; guarde fora do Raspberry — sem ela
                                 o backup não abre)
    BACKUP_USER_EMAIL=...        Telegram: o seu e-mail do FinFam — vai para o chat privado que você conectou ao bot
    BACKUP_CHAT_ID=...           (ou o id do chat direto; arquivo até 50 MB)
    BACKUP_RCLONE_REMOTE=...     rclone (Google Drive, OneDrive, Dropbox, S3…), ex.: gdrive:FinFamBackup
                                 (configure antes com `rclone config`, como o usuário finfam)
    BACKUP_DIR_EXTRA=...         uma pasta de outro disco (HD/pendrive USB montado), ex.: /mnt/hd/finfam
    BACKUP_KEEP_DAYS=30          quantos dias manter no rclone e na pasta (padrão 30)

O que faz: cópia consistente do SQLite (com a API rodando) → confere a integridade → compacta → criptografa
(AES, chave derivada da senha com scrypt) → envia. Grava o resultado em instance/backup_state.json, que o
monitor (scripts.monitor) usa para avisar se o backup parar de acontecer.
"""
import argparse
import base64
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import INSTANCE_DIR, settings
from scripts.backup_telegram import _env, chat_of, db_path, send_document, snapshot

MAGIC = b'FFBK1'
STATE = INSTANCE_DIR / 'backup_state.json'
TELEGRAM_LIMIT = 49 * 1024 * 1024


# ── criptografia ─────────────────────────────────────────────────────────────

def _key(password: str, salt: bytes) -> bytes:
    raw = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)
    return base64.urlsafe_b64encode(raw)


def encrypt(src: Path, password: str) -> Path:
    salt = os.urandom(16)
    out = src.with_name(src.name + '.enc')
    out.write_bytes(MAGIC + salt + Fernet(_key(password, salt)).encrypt(src.read_bytes()))
    return out


def decrypt(src: Path, password: str) -> Path:
    data = src.read_bytes()
    if not data.startswith(MAGIC):
        raise SystemExit('Arquivo não é um backup criptografado do FinFam.')
    salt, token = data[len(MAGIC):len(MAGIC) + 16], data[len(MAGIC) + 16:]
    try:
        plain = Fernet(_key(password, salt)).decrypt(token)
    except InvalidToken:
        raise SystemExit('Senha errada (ou arquivo corrompido).') from None
    out = src.with_suffix('') if src.suffix == '.enc' else src.with_name(src.name + '.db.gz')
    out.write_bytes(plain)
    return out


# ── cópia ────────────────────────────────────────────────────────────────────

def check_integrity(gz: Path, folder: Path) -> None:
    """Abre a cópia e roda PRAGMA integrity_check: não adianta guardar um backup que não abre."""
    import gzip
    raw = folder / 'check.db'
    with gzip.open(gz, 'rb') as fin, open(raw, 'wb') as fout:
        shutil.copyfileobj(fin, fout)
    with sqlite3.connect(raw) as conn:
        result = conn.execute('PRAGMA integrity_check').fetchone()[0]
    raw.unlink()
    if result != 'ok':
        raise RuntimeError(f'cópia do banco com problema de integridade: {result[:120]}')


# ── destinos ─────────────────────────────────────────────────────────────────

def to_telegram(file: Path, chat_id: str) -> str:
    if file.stat().st_size > TELEGRAM_LIMIT:
        raise RuntimeError('passa do limite de 50 MB do Telegram')
    if chat_id.startswith('-') and _env('BACKUP_ALLOW_GROUP') not in ('1', 'true', 'sim'):
        raise RuntimeError('BACKUP_CHAT_ID é de um grupo; use o seu chat privado com o bot')
    token = _env('BACKUP_BOT_TOKEN') or settings.telegram_bot_token
    send_document(token, chat_id, file, f'🔐 Backup FinFam 2026 (criptografado) — {datetime.now():%d/%m/%Y %H:%M}\n'
                                        'Para abrir: python -m scripts.backup_offsite --decrypt ARQUIVO')
    return 'Telegram'


def to_rclone(file: Path, remote: str, keep_days: int) -> str:
    if not shutil.which('rclone'):
        raise RuntimeError('rclone não instalado (sudo apt install rclone)')
    for cmd in (['rclone', 'copy', str(file), remote],
                ['rclone', 'delete', remote, '--min-age', f'{keep_days}d', '--include', 'finfam2026_*.enc']):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            raise RuntimeError(f'rclone falhou: {(r.stderr or r.stdout).strip()[:200]}')
    return f'rclone ({remote.split(":")[0]})'


def to_folder(file: Path, folder: Path, keep_days: int) -> str:
    if not folder.is_dir():
        raise RuntimeError(f'pasta {folder} não existe (o HD está montado?)')
    shutil.copy2(file, folder / file.name)
    os.chmod(folder / file.name, 0o600)
    limit = time.time() - keep_days * 86400
    for old in folder.glob('finfam2026_*.enc'):
        if old.stat().st_mtime < limit:
            old.unlink()
    return f'pasta {folder}'


def telegram_chat() -> str:
    """BACKUP_CHAT_ID, ou o chat privado da conta BACKUP_USER_EMAIL (ou MONITOR_USER_EMAIL)."""
    if _env('BACKUP_CHAT_ID'):
        return _env('BACKUP_CHAT_ID')
    email = _env('BACKUP_USER_EMAIL') or _env('MONITOR_USER_EMAIL')
    if not email:
        return ''
    chat, why = chat_of(email)
    if not chat:
        raise RuntimeError(why)
    return chat


def destinations(keep_days: int) -> list:
    out = []
    if _env('BACKUP_CHAT_ID') or _env('BACKUP_USER_EMAIL') or _env('MONITOR_USER_EMAIL'):
        out.append(('Telegram', lambda f: to_telegram(f, telegram_chat())))
    if _env('BACKUP_RCLONE_REMOTE'):
        out.append(('rclone', lambda f: to_rclone(f, _env('BACKUP_RCLONE_REMOTE'), keep_days)))
    if _env('BACKUP_DIR_EXTRA'):
        out.append(('pasta', lambda f: to_folder(f, Path(_env('BACKUP_DIR_EXTRA')), keep_days)))
    return out


def save_state(ok: list[str], errors: list[str], size: int) -> None:
    previous = {}
    try:
        previous = json.loads(STATE.read_text())
    except (OSError, ValueError):
        pass
    now = datetime.now().isoformat(timespec='seconds')
    state = {**previous, 'last_run': now, 'last_size': size, 'last_destinations': ok, 'last_errors': errors}
    if ok:
        state['last_ok'] = now
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1))


def run() -> int:
    ts = f'[{datetime.now():%Y-%m-%d %H:%M:%S}]'
    password = _env('BACKUP_PASSWORD')
    if len(password) < 12:
        print(f'{ts} ERRO: defina BACKUP_PASSWORD (12+ caracteres) no backend/.env.')
        save_state([], ['BACKUP_PASSWORD não definida'], 0)
        return 1
    keep = int(_env('BACKUP_KEEP_DAYS') or 30)
    targets = destinations(keep)
    if not targets:
        print(f'{ts} ERRO: nenhum destino (BACKUP_CHAT_ID, BACKUP_RCLONE_REMOTE ou BACKUP_DIR_EXTRA).')
        save_state([], ['nenhum destino configurado'], 0)
        return 1
    ok, errors, size = [], [], 0
    with tempfile.TemporaryDirectory(prefix='finfam-backup-') as tmp:
        os.chmod(tmp, 0o700)
        try:
            gz = snapshot(db_path(), Path(tmp))
            check_integrity(gz, Path(tmp))
            enc = encrypt(gz, password)
            gz.unlink()
            size = enc.stat().st_size
        except Exception as exc:
            print(f'{ts} ERRO ao copiar o banco: {exc}')
            save_state([], [f'cópia: {exc}'], 0)
            return 1
        for name, send in targets:
            try:
                ok.append(send(enc))
            except Exception as exc:
                errors.append(f'{name}: {exc}')
    save_state(ok, errors, size)
    print(f'{ts} Backup de {size // 1024} KB → ' + (', '.join(ok) or 'nenhum destino')
          + (f' | ERROS: {"; ".join(errors)}' if errors else ''))
    return 0 if ok and not errors else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--decrypt', metavar='ARQUIVO', help='abre um backup .enc (pede BACKUP_PASSWORD do .env)')
    args = parser.parse_args(argv)
    if args.decrypt:
        password = _env('BACKUP_PASSWORD') or input('Senha do backup: ')
        out = decrypt(Path(args.decrypt), password)
        print(f'Pronto: {out} (gunzip e substitua o banco com a API parada).')
        return 0
    return run()


if __name__ == '__main__':
    sys.exit(main())
