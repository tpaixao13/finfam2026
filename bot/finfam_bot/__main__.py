"""python -m finfam_bot — bot central do FinFam (long polling; não precisa abrir porta)."""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from finfam_bot.bot import Bot
from finfam_bot.config import Config
from finfam_bot.http import FinFam, Telegram

COMMANDS = [('menu', 'Menu com botões'), ('contas', 'Contas a pagar'), ('agenda', 'Contas desta semana, dia a dia'),
            ('resumo', 'Resumo do mês'), ('semana', 'Como foi a semana'), ('metas', 'Progresso das metas'),
            ('lancar', 'Lançar um gasto'), ('investir', 'Registrar um investimento'),
            ('carteira', 'Seus investimentos'), ('banco', 'O que chegou dos bancos conectados'),
            ('revisar', 'Revisar o que chegou do banco'), ('entradas', 'Entradas do banco para Rendas'),
            ('openfinance', 'Open Finance: bancos, saldo, investimentos e faturas'),
            ('cancelar', 'Cancelar o lançamento em andamento'), ('ajuda', 'O que o bot faz'),
            ('desconectar', 'Desligar este Telegram da conta')]


class Dispatcher:
    """Atende várias conversas ao mesmo tempo (uma IA lenta não trava as outras famílias),
    mas mantém a ordem das mensagens dentro de cada conversa."""

    def __init__(self, handle, workers: int = 8):
        self.handle, self.pool = handle, ThreadPoolExecutor(max_workers=workers)
        self.locks: dict[int, threading.Lock] = {}
        self.guard = threading.Lock()

    def _lock(self, chat_id) -> threading.Lock:
        with self.guard:
            if len(self.locks) > 1000:
                self.locks = {k: v for k, v in self.locks.items() if v.locked()}
            return self.locks.setdefault(chat_id, threading.Lock())

    def submit(self, update: dict) -> None:
        msg = update.get('message') or (update.get('callback_query') or {}).get('message') or {}
        lock = self._lock(msg.get('chat', {}).get('id'))

        def run():
            with lock:
                self.handle(update)
        self.pool.submit(run)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    log = logging.getLogger('finfam.bot')
    cfg = Config.from_env()
    tg = Telegram(cfg.telegram_token)
    me = tg.call('getMe')
    if not me:
        raise SystemExit('Token do Telegram inválido (getMe falhou). Confira TELEGRAM_BOT_TOKEN.')
    tg.call('deleteWebhook')  # long polling e webhook não convivem
    tg.call('setMyCommands', commands=[{'command': c, 'description': d} for c, d in COMMANDS])
    bot = Bot(tg, FinFam(cfg.api_url, cfg.bot_api_key), cfg.site_url)
    log.info('Bot @%s no ar, falando com %s', me.get('username'), cfg.api_url)
    dispatcher = Dispatcher(bot.handle)
    offset = None
    while True:
        updates = tg.updates(offset)
        if updates is None:
            time.sleep(5)
            continue
        for update in updates:
            offset = update['update_id'] + 1
            dispatcher.submit(update)
        if not updates:
            time.sleep(0.2)


if __name__ == '__main__':
    main()
