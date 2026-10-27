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


def test_connect_tries_ipv4_first_and_skips_dead_address(monkeypatch):
    """Raspberry com IPv6 sem rota: não pode ficar minutos parado no endereço IPv6."""
    import socket
    tried = []

    class FakeSock:
        def __init__(self, family, *a):
            self.family = family

        def settimeout(self, t):
            self.t = t

        def connect(self, addr):
            tried.append((self.family, addr[0]))
            if addr[0] == '10.0.0.1':
                raise TimeoutError('sem rota')

        def close(self):
            pass

    monkeypatch.setattr(bk.socket, 'getaddrinfo', lambda *a, **k: [
        (socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('2001:db8::1', 443, 0, 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.1', 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.2', 443)),
    ])
    monkeypatch.setattr(bk.socket, 'socket', FakeSock)

    class Ctx:
        def wrap_socket(self, sock, server_hostname):
            return ('tls', sock, server_hostname)
    conn = bk._IPv4FirstHTTPS('api.telegram.org', 443, timeout=120, context=Ctx())
    conn.connect()
    assert [a for _, a in tried] == ['10.0.0.1', '10.0.0.2']  # IPv4 antes; o IPv6 nem foi tentado
    assert conn.sock[2] == 'api.telegram.org' and conn.sock[1].t == 120
