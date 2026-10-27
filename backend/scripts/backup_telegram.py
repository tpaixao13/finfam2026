"""Backup do banco do FinFam 2026 enviado para um chat do Telegram (como no FinFam antigo).

Uso, na pasta backend do servidor:
    .venv/bin/python -m scripts.backup_telegram

Cron (todo dia às 07:00):
    0 7 * * * cd /home/finfam/finfam2026/backend && .venv/bin/python -m scripts.backup_telegram >> /tmp/finfam2026_backup.log 2>&1

Configuração no backend/.env:
    TELEGRAM_BOT_TOKEN=...   (o mesmo bot central; ou BACKUP_BOT_TOKEN para usar outro bot)
    BACKUP_CHAT_ID=...       (o seu chat com o bot; o arquivo tem os dados de todas as famílias)
    BACKUP_ALLOW_GROUP=1     (opcional: libera mandar para um grupo — só se o grupo tiver só você)

Diferenças para o script antigo:
- copia o banco com a API de backup do SQLite (cópia consistente mesmo com a API gravando);
- compacta (.db.gz) — fica bem menor e cabe no limite de 50 MB do Telegram;
- token e chat vêm do .env (nada fixo no código) e respeitam INSECURE_EXTERNAL_SSL.
"""
import gzip
import http.client
import json
import os
import shutil
import socket
import sqlite3
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

from app.core.config import settings
from app.services.market import _ssl_context

TELEGRAM_LIMIT = 49 * 1024 * 1024


def _env(name: str) -> str:
    import re
    return re.split(r'(?:^|\s+)#', os.environ.get(name, '').strip(), maxsplit=1)[0].strip()


def db_path() -> Path:
    url = settings.database_url
    if not url.startswith('sqlite:///'):
        raise SystemExit('Backup pelo Telegram só funciona com SQLite (DATABASE_URL=sqlite:///...).')
    return Path(url.removeprefix('sqlite:///'))


def snapshot(src: Path, folder: Path) -> Path:
    """Cópia consistente (sqlite3 backup) e compactada em gzip."""
    raw = folder / 'finfam2026.db'
    with sqlite3.connect(f'file:{src}?mode=ro', uri=True) as source, sqlite3.connect(raw) as target:
        source.backup(target)
    gz = folder / f'finfam2026_{datetime.now():%Y%m%d_%H%M}.db.gz'
    with open(raw, 'rb') as fin, gzip.open(gz, 'wb', compresslevel=9) as fout:
        shutil.copyfileobj(fin, fout)
    return gz


CONNECT_TIMEOUT = 15  # para abrir a conexão; o envio do arquivo tem até 120 s


class _IPv4FirstHTTPS(http.client.HTTPSConnection):
    """Tenta IPv4 primeiro e desiste rápido de cada endereço. Num Raspberry com IPv6 sem rota,
    a conexão padrão ficava minutos parada no endereço IPv6 do Telegram."""

    def connect(self):
        infos = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM)
        infos.sort(key=lambda info: info[0] != socket.AF_INET)
        last: OSError | None = None
        for family, kind, proto, _, addr in infos:
            sock = socket.socket(family, kind, proto)
            sock.settimeout(CONNECT_TIMEOUT)
            try:
                sock.connect(addr)
            except OSError as exc:
                sock.close()
                last = exc
                continue
            sock.settimeout(self.timeout)
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
            return
        raise last or OSError(f'Sem endereço para {self.host}')


class _IPv4FirstHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_IPv4FirstHTTPS, req, context=self._context)


def send_document(token: str, chat_id: str, file: Path, caption: str) -> None:
    boundary = uuid.uuid4().hex
    head = ''.join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'
                   for k, v in (('chat_id', chat_id), ('caption', caption))).encode()
    head += (f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{file.name}"\r\n'
             'Content-Type: application/gzip\r\n\r\n').encode()
    body = head + file.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
    req = urllib.request.Request(f'https://api.telegram.org/bot{token}/sendDocument', data=body, method='POST',
                                 headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
    opener = urllib.request.build_opener(_IPv4FirstHandler(context=_ssl_context()))
    try:
        with opener.open(req, timeout=120) as resp:
            result = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:  # o Telegram explica o erro no corpo (ex.: chat not found)
        try:
            result = json.loads(exc.read().decode())
        except ValueError:
            raise RuntimeError(f'Telegram respondeu {exc.code}') from None
    if not result.get('ok'):
        raise RuntimeError(result.get('description', result))


def main() -> int:
    ts = f'[{datetime.now():%Y-%m-%d %H:%M:%S}]'
    token = _env('BACKUP_BOT_TOKEN') or settings.telegram_bot_token
    chat_id = _env('BACKUP_CHAT_ID')
    if not token or not chat_id:
        print(f'{ts} ERRO: defina TELEGRAM_BOT_TOKEN (ou BACKUP_BOT_TOKEN) e BACKUP_CHAT_ID no backend/.env.')
        return 1
    if chat_id.startswith('-') and _env('BACKUP_ALLOW_GROUP') not in ('1', 'true', 'sim'):
        print(f'{ts} ERRO: BACKUP_CHAT_ID é de um grupo. Use o seu chat privado com o bot '
              '(ou BACKUP_ALLOW_GROUP=1 se o grupo tiver só você).')
        return 1
    src = db_path()
    if not src.is_file():
        print(f'{ts} ERRO: banco não encontrado em {src}')
        return 1
    with tempfile.TemporaryDirectory(prefix='finfam-backup-') as tmp:
        os.chmod(tmp, 0o700)
        gz = snapshot(src, Path(tmp))
        size = gz.stat().st_size
        if size > TELEGRAM_LIMIT:
            print(f'{ts} ERRO: backup com {size // 1024 // 1024} MB passa do limite do Telegram (50 MB).')
            return 1
        caption = (f'📦 Backup FinFam 2026 — {datetime.now():%d/%m/%Y %H:%M}\n'
                   f'{src.stat().st_size // 1024} KB (compactado: {size // 1024} KB). '
                   'Para restaurar: gunzip e substitua o gastos.db com a API parada.')
        try:
            send_document(token, chat_id, gz, caption)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode('utf-8', 'replace')
            print(f'{ts} ERRO HTTP {exc.code}: {detail[:300]}')
            return 1
        except Exception as exc:
            print(f'{ts} ERRO: {exc}')
            return 1
    print(f'{ts} Backup enviado ({size // 1024} KB compactado).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
