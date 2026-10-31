"""Monitor do FinFam 2026: avisa no Telegram quando algo cai — e quando volta.

Uso, na pasta backend do servidor:
    .venv/bin/python -m scripts.monitor           # confere e avisa se algo mudou
    .venv/bin/python -m scripts.monitor --status  # só mostra a situação, sem avisar
    .venv/bin/python -m scripts.monitor --test    # manda uma mensagem de teste

Cron (a cada 5 minutos):
    */5 * * * * cd /home/finfam/finfam2026/backend && .venv/bin/python -m scripts.monitor >> /home/finfam/finfam2026_monitor.log 2>&1

Configuração no backend/.env (um dos dois):
    MONITOR_USER_EMAIL=voce@exemplo.com   avisos no Telegram que essa conta conectou ao bot
    MONITOR_CHAT_ID=...                   ou o id do chat direto
    MONITOR_API_URL=http://127.0.0.1:8001/api/v1   (padrão)

Confere: a API responde, os serviços (API e bot) estão de pé, os bancos conectados estão sendo relidos (nenhum há mais de 26 h sem leitura),
o último backup fora do Raspberry deu certo, sobra disco. Um problema só vira aviso depois de 2 checagens
seguidas (evita alarme por um reinício). Roda separado da API: avisa mesmo com a API caída
(manda direto pelo Bot API do Telegram).
"""
import argparse
import html
import json
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from app.core.config import INSTANCE_DIR, settings
from app.core.state_file import read_state, write_state
from scripts.backup_telegram import _env, chat_of

STATE = INSTANCE_DIR / 'monitor_state.json'
BACKUP_STATE = INSTANCE_DIR / 'backup_state.json'
SERVICES = ('finfam2026', 'finfam2026-bot')
FAILS_TO_ALERT = 2
SYNC_STALE_HOURS = 26      # o cron do Open Finance relê quem está há 24 h sem leitura (scripts.pluggy_sync)
BACKUP_STALE_HOURS = 30    # backup diário
DISK_MIN_FREE = 0.10


# ── checagens: cada uma devolve None (ok) ou o problema em uma frase ─────────

def check_api() -> str | None:
    url = (_env('MONITOR_API_URL') or 'http://127.0.0.1:8001/api/v1').rstrip('/') + '/health'
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            if resp.status == 200 and json.loads(resp.read()).get('status') == 'ok':
                return None
            return f'a API respondeu {resp.status}'
    except Exception as exc:
        return f'a API não responde ({type(exc).__name__})'


def check_service(name: str) -> str | None:
    if not shutil.which('systemctl'):
        return None  # fora do Raspberry (ex.: desenvolvimento): não confere
    r = subprocess.run(['systemctl', 'is-active', name], capture_output=True, text=True)
    state = r.stdout.strip()
    if state == 'active':
        return None
    if state in ('unknown', '') and 'not be found' in r.stderr + r.stdout:
        return None  # serviço não instalado (ex.: bot ainda não configurado)
    return f'o serviço {name} está {state or "parado"}'


def check_sync() -> str | None:
    """Bancos conectados (de famílias com Open Finance liberado) sem leitura há mais de 6 h."""
    from app.core.db import db
    from app.core.types import utcnow
    from app.models import BankConnection, Tenant, User
    try:
        limit = utcnow() - timedelta(hours=SYNC_STALE_HOURS)
        stale = (BankConnection.query.join(User, User.id == BankConnection.user_id)
                 .join(Tenant, Tenant.id == User.tenant_id)
                 .filter(Tenant.open_finance_enabled.is_(True), BankConnection.tx_synced_at.isnot(None),
                         BankConnection.tx_synced_at < limit).count())
        return f'{stale} banco(s) conectado(s) sem atualizar há mais de {SYNC_STALE_HOURS} h' if stale else None
    finally:
        db.session.remove()


def check_backup() -> str | None:
    state = read_state(BACKUP_STATE)
    if state is None:
        return None  # backup fora do Raspberry ainda não configurado: o admin vê no painel
    last = state.get('last_ok')
    if not last:
        return 'o backup fora do Raspberry nunca deu certo: ' + '; '.join(state.get('last_errors') or ['?'])
    age = datetime.now() - datetime.fromisoformat(last)
    if age > timedelta(hours=BACKUP_STALE_HOURS):
        why = '; '.join(state.get('last_errors') or []) or 'não rodou'
        return f'último backup fora do Raspberry há {age.days}d{age.seconds // 3600}h ({why})'
    return None


def check_disk() -> str | None:
    usage = shutil.disk_usage(INSTANCE_DIR if INSTANCE_DIR.exists() else Path('.'))
    free = usage.free / usage.total
    return f'disco quase cheio: {free:.0%} livre ({usage.free // 2 ** 20} MB)' if free < DISK_MIN_FREE else None


def checks() -> dict:
    out = {'api': check_api(), 'sync': check_sync(), 'backup': check_backup(), 'disk': check_disk()}
    out.update({f'service:{s}': check_service(s) for s in SERVICES})
    return out


# ── avisos ───────────────────────────────────────────────────────────────────

def alert_target() -> tuple[str | None, str]:
    """(chat, motivo se não achou): MONITOR_CHAT_ID, ou o chat privado da conta MONITOR_USER_EMAIL."""
    if _env('MONITOR_CHAT_ID'):
        return _env('MONITOR_CHAT_ID'), ''
    if not _env('MONITOR_USER_EMAIL'):
        return None, 'MONITOR_USER_EMAIL (ou MONITOR_CHAT_ID) não está no backend/.env'
    return chat_of(_env('MONITOR_USER_EMAIL'))


def alert_chat() -> str | None:
    return alert_target()[0]


def notify(text: str) -> bool:
    from app.services.telegram import send_message
    (chat, why), token = alert_target(), settings.telegram_bot_token
    if not token:
        print('Sem destino para o aviso: TELEGRAM_BOT_TOKEN não está no backend/.env.')
        return False
    if not chat:
        print(f'Sem destino para o aviso: {why}.')
        return False
    ok, err = send_message(token, chat, text)
    if not ok:
        print(f'Aviso não foi para o Telegram: {err}')
    return ok


def transitions(previous: dict, current: dict) -> tuple[dict, list[str], list[str]]:
    """(novo estado, problemas novos, problemas resolvidos). Problema só conta depois de 2 checagens seguidas."""
    state, down, up = {}, [], []
    now = datetime.now().isoformat(timespec='seconds')
    for name, problem in current.items():
        old = previous.get(name) or {}
        if problem:
            fails = old.get('fails', 0) + 1
            alerted = old.get('alerted', False)
            if fails >= FAILS_TO_ALERT and not alerted:
                down.append(problem)
                alerted = True
            state[name] = {'fails': fails, 'alerted': alerted, 'problem': problem, 'since': old.get('since') or now}
        else:
            if old.get('alerted'):
                up.append(old.get('problem') or name)
            state[name] = {'fails': 0, 'alerted': False, 'problem': None, 'since': None}
    state['_checked_at'] = now
    return state, down, up


def run(send: bool = True) -> int:
    previous = read_state(STATE) or {}
    current = checks()
    state, down, up = transitions(previous, current)
    write_state(STATE, state)
    ts = f'[{datetime.now():%Y-%m-%d %H:%M:%S}]'
    problems = [p for p in current.values() if p]
    print(f'{ts} ' + ('tudo ok' if not problems else 'problemas: ' + '; '.join(problems)))
    if send and down and not notify('🚨 <b>FinFam 2026 — problema</b>\n\n' + _bullets(down)):
        # o aviso não saiu: tenta de novo na próxima checagem (não marca como avisado)
        for name in current:
            if state[name]['alerted'] and not (previous.get(name) or {}).get('alerted'):
                state[name]['alerted'] = False
        write_state(STATE, state)
    if send and up:
        notify('✅ <b>FinFam 2026 — voltou ao normal</b>\n\n' + _bullets(up))
    return 1 if problems else 0


def _bullets(items: list[str]) -> str:
    """Lista para o Telegram (HTML): o texto vem de erros de ferramentas e pode ter < e &."""
    return '\n'.join(f'• {html.escape(p, quote=False)}' for p in items)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--status', action='store_true', help='só mostra a situação, sem avisar')
    parser.add_argument('--test', action='store_true', help='manda uma mensagem de teste no Telegram')
    args = parser.parse_args(argv)
    if args.test:
        return 0 if notify('🔔 Teste do monitor do FinFam 2026: os avisos chegam aqui.') else 1
    return run(send=not args.status)


if __name__ == '__main__':
    sys.exit(main())
