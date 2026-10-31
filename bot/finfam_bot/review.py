"""Revisar o que chegou do banco (Open Finance), um por vez, com botões.

  /revisar  → saídas:   ✅ Lançar (categoria) / 🏷️ Trocar categoria / 🚫 Ignorar      (prefixo rv)
  /entradas → entradas: ✅ Lançar como renda / 🔁 Fixa ou variável / 🚫 Ignorar        (prefixo ri)

Os botões levam só o id e a posição na fila (`<prefixo>:<ação>:<id>:<posição>`); a escolha (categoria
ou tipo de renda) fica em `bot.reviews` e expira junto com os rascunhos. Toda ação passa pela API,
que só mexe no que a pessoa da conversa pode ver.
"""
import time

from finfam_bot.fmt import btn, h, rows
from finfam_bot.http import ApiError
from finfam_bot.views import income_text, review_text

OFF = '🏦 O Open Finance (bancos conectados) não está liberado para a sua família.'


class Review:
    """Saídas (despesas). As entradas (IncomeReview) mudam só o que difere."""
    prefix, next_path, ignore_path, approve_path = 'rv', '/pluggy/review/next', '/pluggy/transactions/ignore', \
        '/pluggy/transactions/approve'
    empty, change_label, pick_title = '✅ Nada para revisar do banco.', '🏷️ Trocar categoria', 'Qual categoria?'

    def __init__(self, bot):
        self.bot, self.tg, self.api = bot, bot.tg, bot.api
        self.shown = bot.reviews  # (prefixo, chat, id) → {'choice', 'choices', 'item', 'ts'}

    # ── o que muda entre saídas e entradas ──
    def choices(self, r: dict, item: dict) -> list[str]:
        return r['categories']

    def default(self, item: dict) -> str:
        return item.get('category') or 'Outros'

    def text(self, item: dict, r: dict, offset: int, choice: str, note: str) -> str:
        return review_text(item, r['total'], offset, choice, note)

    def ok_label(self, seen: dict) -> str:
        return f'✅ Lançar · {seen["choice"]}'

    def choice_label(self, choice: str) -> str:
        return choice

    def approve_item(self, item_id: int, seen: dict) -> dict:
        return {'id': item_id, 'category': seen['choice']}

    # ── ciclo ──
    def show(self, chat_id: int, offset: int = 0, message_id: int | None = None, note: str = '') -> None:
        try:
            r = self.api.get(chat_id, self.next_path, offset=offset)
        except ApiError as exc:
            if exc.status != 403 or exc.code == 'telegram_not_linked':
                raise
            return self._out(chat_id, message_id, OFF)
        item = r['item']
        if item is None and offset and r['total']:  # pulou todos: volta ao começo
            return self.show(chat_id, 0, message_id, note)
        if item is None:
            done = f'{note}\n\n' if note else ''
            return self._out(chat_id, message_id, f'{done}{self.empty}')
        seen = {'choice': self.default(item), 'choices': self.choices(r, item), 'item': item, 'ts': time.time()}
        self.shown[(self.prefix, chat_id, item['id'])] = seen
        self._out(chat_id, message_id, self.text(item, r, offset, seen['choice'], note),
                  self._buttons(item['id'], offset, seen, r['total']))

    def _buttons(self, item_id: int, offset: int, seen: dict, total: int) -> list[list[dict]]:
        key = f'{item_id}:{offset}'
        out = [[btn(self.ok_label(seen)[:40], f'{self.prefix}:ok:{key}')],
               [btn(self.change_label, f'{self.prefix}:cat:{key}'), btn('🚫 Ignorar', f'{self.prefix}:ig:{key}')]]
        if total > offset + 1:
            out.append([btn('⏭ Deixar para depois', f'{self.prefix}:go:{offset + 1}')])
        return out

    def _out(self, chat_id, message_id, text, buttons=None) -> None:
        if message_id:
            self.tg.edit(chat_id, message_id, text, buttons)
        else:
            self.tg.send(chat_id, text, buttons)

    # ── botões ──
    def button(self, cq: dict, arg: str, chat_id: int, message_id: int) -> None:
        action, _, rest = arg.partition(':')
        parts = rest.split(':')
        if action == 'go':
            self.tg.answer(cq['id'])
            return self.show(chat_id, _int(parts[0]), message_id)
        item_id, offset = _int(parts[0]), _int(parts[1] if len(parts) > 1 else 0)
        seen = self.shown.get((self.prefix, chat_id, item_id))
        if seen is None:  # bot reiniciou ou expirou: mostra de novo
            self.tg.answer(cq['id'], 'Atualizei a lista. Toque de novo.')
            return self.show(chat_id, offset, message_id)
        seen['ts'] = time.time()
        if action == 'cat':
            self.tg.answer(cq['id'])
            buttons = rows([btn(self.choice_label(c), f'{self.prefix}:set:{item_id}:{offset}:{i}')
                            for i, c in enumerate(seen['choices'])][:40], 2)
            return self.tg.edit(chat_id, message_id, self.pick_title,
                                buttons + [[btn('↩️ Voltar', f'{self.prefix}:go:{offset}')]])
        if action == 'set':
            index = int(parts[2]) if len(parts) > 2 and parts[2].isascii() and parts[2].isdigit() else -1
            if not 0 <= index < len(seen['choices']):
                return self.tg.answer(cq['id'])
            seen['choice'] = seen['choices'][index]
            action = 'ok'  # escolheu: já lança
        if action == 'ok':
            self.tg.answer(cq['id'], 'Lançando…')
            r = self.api.post(chat_id, self.approve_path, {'items': [self.approve_item(item_id, seen)]})
            self.shown.pop((self.prefix, chat_id, item_id), None)
            return self.show(chat_id, offset, message_id,
                             f'✅ {h(r.get("message", "Lançado!"))} ({h(self.choice_label(seen["choice"]))})')
        if action == 'ig':
            self.tg.answer(cq['id'], 'Ignorando…')
            self.api.post(chat_id, self.ignore_path, {'ids': [item_id]})
            self.shown.pop((self.prefix, chat_id, item_id), None)
            return self.show(chat_id, offset, message_id, '🚫 Ignorado.')
        self.tg.answer(cq['id'])


INCOME_TYPE_LABEL = {'fixa': 'Renda fixa (todo mês)', 'variavel': 'Renda variável'}


class IncomeReview(Review):
    """Entradas do banco (PIX recebido, salário, rendimento) → Rendas."""
    prefix, next_path, ignore_path, approve_path = 'ri', '/pluggy/incomes/next', '/pluggy/incomes/ignore', \
        '/pluggy/incomes/approve'
    empty, change_label, pick_title = '✅ Nenhuma entrada para revisar.', '🔁 Fixa ou variável', 'Que tipo de renda?'

    def choices(self, r: dict, item: dict) -> list[str]:
        return list(INCOME_TYPE_LABEL)

    def default(self, item: dict) -> str:
        return (item.get('suggestion') or {}).get('income_type') or 'variavel'

    def text(self, item: dict, r: dict, offset: int, choice: str, note: str) -> str:
        return income_text(item, r['total'], offset, INCOME_TYPE_LABEL[choice], note)

    def ok_label(self, seen: dict) -> str:
        expected = (seen['item'].get('suggestion') or {}).get('expected')
        if expected:
            return f'✅ Recebida: {expected.get("company") or "renda prevista"}'
        return f'✅ Lançar como renda {"fixa" if seen["choice"] == "fixa" else "variável"}'

    def choice_label(self, choice: str) -> str:
        return INCOME_TYPE_LABEL.get(choice, choice)

    def approve_item(self, item_id: int, seen: dict) -> dict:
        sug = seen['item'].get('suggestion') or {}
        return {'id': item_id, 'income_type': seen['choice'], 'company': sug.get('company')}


def _int(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
