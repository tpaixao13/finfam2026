"""python -m finfam_bot — bot central do FinFam (long polling; não precisa abrir porta)."""
import logging
import time

from finfam_bot.bot import Bot
from finfam_bot.config import Config
from finfam_bot.http import FinFam, Telegram

COMMANDS = [('contas', 'Contas a pagar'), ('resumo', 'Resumo do mês'), ('lancar', 'Lançar um gasto'),
            ('cancelar', 'Cancelar o lançamento em andamento'), ('ajuda', 'O que o bot faz'),
            ('desconectar', 'Desligar este Telegram da conta')]


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
    offset = None
    while True:
        updates = tg.updates(offset)
        if updates is None:
            time.sleep(5)
            continue
        for update in updates:
            offset = update['update_id'] + 1
            bot.handle(update)
        if not updates:
            time.sleep(0.2)


if __name__ == '__main__':
    main()
