"""Backup fora do Raspberry (criptografado) e monitor (avisa quando cai e quando volta)."""
import gzip
import sqlite3

from scripts import backup_offsite as bo
from scripts import monitor as mon


def test_backup_encrypts_checks_and_copies_to_folder(client, family, tmp_path, monkeypatch):
    dest = tmp_path / 'hd'
    dest.mkdir()
    monkeypatch.setenv('BACKUP_PASSWORD', 'senha-forte-do-backup')
    monkeypatch.setenv('BACKUP_DIR_EXTRA', str(dest))
    monkeypatch.delenv('BACKUP_CHAT_ID', raising=False)
    monkeypatch.delenv('BACKUP_RCLONE_REMOTE', raising=False)
    monkeypatch.setattr(bo, 'STATE', tmp_path / 'backup_state.json')
    assert bo.run() == 0
    files = list(dest.glob('finfam2026_*.enc'))
    assert len(files) == 1 and files[0].read_bytes().startswith(bo.MAGIC)
    assert b'SQLite format' not in files[0].read_bytes()  # criptografado
    gz = bo.decrypt(files[0], 'senha-forte-do-backup')
    raw = tmp_path / 'restaurado.db'
    raw.write_bytes(gzip.decompress(gz.read_bytes()))
    with sqlite3.connect(raw) as conn:
        assert conn.execute("SELECT email FROM users WHERE email = 'tiago@test.com'").fetchone()
    state = __import__('json').loads((tmp_path / 'backup_state.json').read_text())
    assert state['last_ok'] and state['last_destinations'] and not state['last_errors']
    try:
        bo.decrypt(files[0], 'senha-errada')
        raise AssertionError('abriu com a senha errada')
    except SystemExit:
        pass


def test_backup_without_password_or_destination_fails_loudly(tmp_path, monkeypatch):
    monkeypatch.setattr(bo, 'STATE', tmp_path / 's.json')
    monkeypatch.setenv('BACKUP_PASSWORD', 'curta')
    assert bo.run() == 1
    monkeypatch.setenv('BACKUP_PASSWORD', 'senha-forte-do-backup')
    for k in ('BACKUP_CHAT_ID', 'BACKUP_RCLONE_REMOTE', 'BACKUP_DIR_EXTRA'):
        monkeypatch.delenv(k, raising=False)
    assert bo.run() == 1


def test_monitor_alerts_after_two_fails_and_when_back():
    state, down, up = mon.transitions({}, {'api': 'a API não responde'})
    assert down == [] and state['api']['fails'] == 1  # uma falha só: pode ser um reinício
    state, down, up = mon.transitions(state, {'api': 'a API não responde'})
    assert down == ['a API não responde']
    state, down, up = mon.transitions(state, {'api': 'a API não responde'})
    assert down == []  # não repete
    state, down, up = mon.transitions(state, {'api': None})
    assert up == ['a API não responde'] and state['api']['fails'] == 0


def test_monitor_finds_admin_chat_by_email(client, family, monkeypatch):
    from app.core.db import session
    from app.models import TelegramLink
    session.add(TelegramLink(user_id=family.user['id'], chat_id='424242'))
    session.commit()
    monkeypatch.delenv('MONITOR_CHAT_ID', raising=False)
    monkeypatch.setenv('MONITOR_USER_EMAIL', 'tiago@test.com')
    assert mon.alert_chat() == '424242'
    sent = []
    from app.services import telegram as tg
    monkeypatch.setattr(tg, 'send_message', lambda token, chat, text, buttons=None: (sent.append((chat, text)), (True, ''))[1])
    assert mon.notify('teste') and sent == [('424242', 'teste')]
    assert mon.check_backup() is None or isinstance(mon.check_backup(), str)
    session.remove()


def test_admin_sees_system_health(client, family):
    from test_pluggy import _admin
    from conftest import API
    r = client.get(f'{API}/admin/health', headers=_admin(client))
    assert r.status_code == 200 and set(r.json()) == {'monitor', 'backup'}
    assert client.get(f'{API}/admin/health', headers=family.headers).status_code in (401, 403)


def test_backup_and_monitor_find_private_chat_by_email(client, family, tmp_path, monkeypatch):
    from app.core.db import session
    from app.models import TelegramLink
    from scripts import backup_telegram as bt
    monkeypatch.setattr(bo, 'STATE', tmp_path / 's.json')
    monkeypatch.setenv('BACKUP_PASSWORD', 'senha-forte-do-backup')
    for k in ('BACKUP_CHAT_ID', 'BACKUP_RCLONE_REMOTE', 'BACKUP_DIR_EXTRA', 'MONITOR_CHAT_ID'):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv('MONITOR_USER_EMAIL', '"tiago@test.com"')  # aspas no .env também valem
    assert mon.alert_target() == (None, 'a conta tiago@test.com não conectou o Telegram (Telegram e IA → Conectar Telegram)')
    session.add(TelegramLink(user_id=family.user['id'], chat_id='-100555'))  # um grupo não serve
    session.add(TelegramLink(user_id=family.user['id'], chat_id='777001'))
    session.commit()
    sent = []
    monkeypatch.setattr(bo, 'send_document', lambda token, chat, file, caption: sent.append(chat))
    assert bo.run() == 0 and sent == ['777001']
    monkeypatch.setenv('MONITOR_USER_EMAIL', 'ninguem@test.com')
    assert 'nenhuma conta' in mon.alert_target()[1]
    session.remove()


def test_old_backup_format_still_opens_and_output_is_private(tmp_path):
    import base64
    import hashlib
    import os
    import stat
    from cryptography.fernet import Fernet
    salt = os.urandom(16)
    key = base64.urlsafe_b64encode(hashlib.scrypt(b'senha-forte-do-backup', salt=salt, n=2 ** 15, r=8, p=1,
                                                  maxmem=64 * 1024 * 1024, dklen=32))
    old = tmp_path / 'velho.db.gz.enc'
    old.write_bytes(bo.OLD_MAGIC + salt + Fernet(key).encrypt(b'conteudo'))  # formato de 31/10 (FFBK1)
    out = bo.decrypt(old, 'senha-forte-do-backup')
    assert out.read_bytes() == b'conteudo' and stat.S_IMODE(out.stat().st_mode) == 0o600


def test_monitor_escapes_html_and_retries_when_telegram_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(mon, 'STATE', tmp_path / 'm.json')
    monkeypatch.setattr(mon, 'checks', lambda: {'backup': 'rclone falhou: <nil> & cia'})
    sent = []
    monkeypatch.setattr(mon, 'notify', lambda text: (sent.append(text), False)[1])  # Telegram recusou
    mon.run()
    mon.run()  # 2ª falha: tenta avisar, não consegue
    assert '&lt;nil&gt; &amp; cia' in sent[-1]
    mon.run()  # avisa de novo na próxima checagem
    assert len(sent) == 2


def test_deleting_family_removes_its_admin_backups(client, family, monkeypatch, tmp_path):
    from app.services import family_backup as fb
    from conftest import API
    monkeypatch.setattr(fb, 'BACKUP_DIR', tmp_path)
    mine = tmp_path / f"familia_{family.tenant['id']}_20261031_120000.json.gz"
    other = tmp_path / 'familia_99999_20261031_120000.json.gz'
    mine.write_bytes(b'x')
    other.write_bytes(b'y')
    r = family.post('/auth/delete-account', json={'password': 'senha-segura', 'confirm': 'EXCLUIR'})
    assert r.status_code == 200 and not mine.exists() and other.exists()
    old = tmp_path / 'familia_5_20200101_000000.json.gz'
    old.write_bytes(b'z')
    os_time = __import__('os')
    os_time.utime(old, (0, 0))
    assert bo.prune_family_backups(30) == 1 and not old.exists() and other.exists()
