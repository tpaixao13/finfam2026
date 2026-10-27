"""Open Finance: relê os bancos conectados (garantia caso algum aviso da Pluggy se perca).

Uso, na pasta backend do servidor (ex.: no cron, de hora em hora):
    .venv/bin/python -m scripts.pluggy_sync            # bancos com novidade + os sem leitura há 24 h
    .venv/bin/python -m scripts.pluggy_sync --horas 6  # muda o "há quanto tempo"

Nada é lançado como despesa: os lançamentos ficam em "Revisar importados".
"""
import logging
import sys

from app.core.db import session
from app.core.migrations import run_migrations
from app.models import Tenant, User
from app.services import open_finance as of


def main(argv: list[str]) -> int:
    hours = 24
    if '--horas' in argv:
        try:
            hours = max(1, int(argv[argv.index('--horas') + 1]))
        except (IndexError, ValueError):
            print(__doc__)
            return 2
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    run_migrations()
    total = 0
    enabled = {t.id for t in Tenant.query.filter_by(open_finance_enabled=True).all()}
    users = {u.id for u in User.query.filter(User.tenant_id.in_(enabled)).all()} if enabled else set()
    for user_id in sorted(users):
        total += of.sync_stale(user_id, max_age_hours=hours, notify=True)
    session.remove()
    print(f'Open Finance: {total} lançamento(s) novo(s) para revisar.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
