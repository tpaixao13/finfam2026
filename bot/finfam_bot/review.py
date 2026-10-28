"""/revisar — o que chegou do banco (Open Finance), um por vez: ✅ Lançar / 🏷️ Trocar categoria / 🚫 Ignorar.

Os botões levam só o id do lançamento e a posição na fila (`rv:<ação>:<id>:<posição>`); a categoria
escolhida fica em `bot.reviews` (expira junto com os rascunhos). Toda ação passa pela API, que só
mexe nos lançamentos de quem vinculou a conversa.
"""
import time

from finfam_bot.fmt import btn, h, rows
from finfam_bot.http import ApiError
from finfam_bot.views import review_text

PREFIX = 'rv'


class Review:
    def __init__(self, bot):
        self.bot, self.tg, self.api = bot, bot.tg, bot.api
        self.shown = bot.reviews  # (chat, id do lançamento) → {'category', 'cats', 'ts'}

    def show(self, chat_id: int, offset: int = 0, message_id: int | None = None, note: str = '') -> None:
        try:
            r = self.api.get(chat_id, '/pluggy/review/next', offset=offset)
        except ApiError as exc:
            if exc.status != 403 or exc.code == 'telegram_not_linked':
                raise
            return self._out(chat_id, message_id, '🏦 O Open Finance (bancos conectados) não está liberado para a sua família.')
        item = r['item']
        if item is None and offset and r['total']:  # pulou todos: volta ao começo
            return self.show(chat_id, 0, message_id, note)
        if item is None:
            done = f'{note}\n\n' if note else ''
            return self._out(chat_id, message_id, f'{done}✅ Nada para revisar do banco.')
        category = item.get('category') or 'Outros'
        self.shown[(chat_id, item['id'])] = {'category': category, 'cats': r['categories'], 'ts': time.time()}
        self._out(chat_id, message_id, review_text(item, r['total'], offset, category, note),
                  self._buttons(item['id'], offset, category, r['total']))

    def _buttons(self, tx_id: int, offset: int, category: str, total: int) -> list[list[dict]]:
        key = f'{tx_id}:{offset}'
        out = [[btn(f'✅ Lançar · {category}'[:40], f'{PREFIX}:ok:{key}')],
               [btn('🏷️ Trocar categoria', f'{PREFIX}:cat:{key}'), btn('🚫 Ignorar', f'{PREFIX}:ig:{key}')]]
        if total > offset + 1:
            out.append([btn('⏭ Deixar para depois', f'{PREFIX}:go:{offset + 1}')])
        return out

    def _out(self, chat_id, message_id, text, buttons=None) -> None:
        if message_id:
            self.tg.edit(chat_id, message_id, text, buttons)
        else:
            self.tg.send(chat_id, text, buttons)

    # botões ──────────────────────────────────────────────────────────────────
    def button(self, cq: dict, arg: str, chat_id: int, message_id: int) -> None:
        action, _, rest = arg.partition(':')
        parts = rest.split(':')
        if action == 'go':
            self.tg.answer(cq['id'])
            return self.show(chat_id, _int(parts[0]), message_id)
        tx_id, offset = _int(parts[0]), _int(parts[1] if len(parts) > 1 else 0)
        seen = self.shown.get((chat_id, tx_id))
        if seen is None:  # bot reiniciou ou expirou: mostra de novo
            self.tg.answer(cq['id'], 'Atualizei a lista. Toque de novo.')
            return self.show(chat_id, offset, message_id)
        seen['ts'] = time.time()
        if action == 'cat':
            self.tg.answer(cq['id'])
            cats = seen['cats']
            buttons = rows([btn(c, f'{PREFIX}:set:{tx_id}:{offset}:{i}') for i, c in enumerate(cats)][:40], 2)
            return self.tg.edit(chat_id, message_id, 'Qual categoria?',
                                buttons + [[btn('↩️ Voltar', f'{PREFIX}:go:{offset}')]])
        if action == 'set':
            index = int(parts[2]) if len(parts) > 2 and parts[2].isascii() and parts[2].isdigit() else -1
            if not 0 <= index < len(seen['cats']):
                return self.tg.answer(cq['id'])
            seen['category'] = seen['cats'][index]
            action = 'ok'  # escolheu a categoria: já lança
        if action == 'ok':
            self.tg.answer(cq['id'], 'Lançando…')
            r = self.api.post(chat_id, '/pluggy/transactions/approve',
                              {'items': [{'id': tx_id, 'category': seen['category']}]})
            self.shown.pop((chat_id, tx_id), None)
            return self.show(chat_id, offset, message_id, f'✅ {h(r.get("message", "Lançado!"))} ({h(seen["category"])})')
        if action == 'ig':
            self.tg.answer(cq['id'], 'Ignorando…')
            self.api.post(chat_id, '/pluggy/transactions/ignore', {'ids': [tx_id]})
            self.shown.pop((chat_id, tx_id), None)
            return self.show(chat_id, offset, message_id, '🚫 Ignorado (não vira despesa).')
        self.tg.answer(cq['id'])


def _int(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
