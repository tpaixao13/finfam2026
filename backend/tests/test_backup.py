"""Backup do banco pelo Telegram (scripts.backup_telegram)."""
import gzip
import sqlite3

from scripts import backup_telegram as bk


def test_backup_sends_consistent_gzip(client, family, monkeypatch, tmp_path):
    family.post('/expenses', json={'description': 'Padaria', 'amount': '12', 'category': 'Alimentação',
                                   'payment_method': 'PIX'})
    sent = {}

    def fake_send(token, chat_id, file, caption):
        sent.update(token=token, chat=chat_id, name=file.name, data=file.read_bytes(), caption=caption)
    monkeypatch.setattr(bk, 'send_document', fake_send)
    monkeypatch.setenv('BACKUP_CHAT_ID', '123456')
    assert bk.main() == 0
    assert sent['chat'] == '123456' and sent['token'] == '123:fake-token-de-teste'
    assert sent['name'].startswith('finfam2026_') and sent['name'].endswith('.db.gz')
    restored = tmp_path / 'r.db'
    restored.write_bytes(gzip.decompress(sent['data']))
    with sqlite3.connect(restored) as db:
        assert db.execute("SELECT description FROM expenses").fetchall() == [('Padaria',)]


def test_backup_refuses_groups_and_missing_config(client, monkeypatch, capsys):
    monkeypatch.setattr(bk, 'send_document', lambda *a: (_ for _ in ()).throw(AssertionError('não devia enviar')))
    monkeypatch.delenv('BACKUP_CHAT_ID', raising=False)
    assert bk.main() == 1 and 'BACKUP_CHAT_ID' in capsys.readouterr().out
    monkeypatch.setenv('BACKUP_CHAT_ID', '-100200300')
    assert bk.main() == 1 and 'grupo' in capsys.readouterr().out
