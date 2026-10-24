"""Base dos fluxos guiados (/lancar e /investir): uma pergunta por vez, botões que editam a mesma mensagem.

Cada fluxo é uma conversa por chat guardada em `bot.flows[chat_id]`:
  {'kind', 'step', 'data', 'ts', 'trail' (escolhas já feitas), 'edit' (mensagem a reaproveitar)}

A subclasse define o que muda de um fluxo para o outro:
  pending(chat_id, f) → próximo passo que falta (ou None quando terminou)
  ask_<passo>(chat_id, f)   → a pergunta de cada passo
  read_<passo>(f, text)     → lê a resposta digitada; devolve a mensagem de erro, se houver
  pick_<campo>(chat_id, f, value) → trata o toque num botão; devolve o texto da escolha
  finish(chat_id)           → monta a confirmação quando não falta nada
"""
import time

from finfam_bot.fmt import btn, h

NOT_TEXT = 'Toque numa das opções acima (ou /cancelar).'


class Flow:
    kind: str            # valor de f['kind']
    prefix: str          # prefixo do callback_data dos botões do fluxo
    cancel_arg: str      # argumento do botão Cancelar
    cancelled: str       # mensagem ao cancelar
    expired: str         # mensagem quando o fluxo já não existe
    pick_step: dict[str, str] = {}  # campo do botão → passo em que ele vale

    def __init__(self, bot):
        self.bot = bot
        self.tg, self.api, self.flows = bot.tg, bot.api, bot.flows
        self.cancel = [btn('❌ Cancelar', f'{self.prefix}:{self.cancel_arg}')]

    # ── ciclo ──
    def begin(self, chat_id: int, data: dict, step: str, **extra) -> None:
        self.flows[chat_id] = {'kind': self.kind, 'step': step, 'data': data, 'ts': time.time(), **extra}

    def start(self, chat_id: int) -> None:
        self.begin(chat_id, {}, '')
        self.next(chat_id)

    def next(self, chat_id: int) -> None:
        """Avança para o próximo passo que ainda falta."""
        f = self.flows[chat_id]
        step = self.pending(chat_id, f)
        if step is None:
            return self.finish(chat_id)
        f['step'] = step
        self.ask_step(chat_id)

    def ask_step(self, chat_id: int) -> None:
        f = self.flows[chat_id]
        getattr(self, f'ask_{f["step"]}')(chat_id, f)

    def ask(self, chat_id: int, text: str, buttons: list | None = None) -> None:
        """Depois de um toque em botão, reaproveita a mesma mensagem (1 chamada em vez de 2)
        e mostra acima o que já foi escolhido."""
        f = self.flows.get(chat_id, {})
        trail = ' · '.join(f.get('trail', []))
        body = f'✔️ {h(trail)}\n\n{text}' if trail else text
        message_id = f.pop('edit', None)
        if message_id:
            self.tg.edit(chat_id, message_id, body, buttons)
        else:
            self.tg.send(chat_id, body, buttons)

    # ── entrada do usuário ──
    def text(self, chat_id: int, text: str) -> None:
        f = self.flows[chat_id]
        reader = getattr(self, f'read_{f["step"]}', None)
        error = reader(f, text) if reader else NOT_TEXT
        if error:
            return self.tg.send(chat_id, error)
        self.next(chat_id)

    def button(self, cq: dict, arg: str, chat_id: int, message_id: int) -> None:
        self.tg.answer(cq['id'])
        f = self.flows.get(chat_id)
        mine = f is not None and f.get('kind', 'expense') == self.kind
        if arg == self.cancel_arg or not mine:
            if mine:
                del self.flows[chat_id]
            return self.tg.edit(chat_id, message_id, self.cancelled if arg == self.cancel_arg else self.expired)
        f['ts'] = time.time()
        field, _, value = arg.partition(':')
        if f['step'] != self.pick_step.get(field):
            return  # botão de um passo antigo
        f['edit'] = message_id
        chosen = getattr(self, f'pick_{field}')(chat_id, f, value)
        if chosen is None:  # o botão só mudou a pergunta (ex.: "Outra data")
            return
        f.setdefault('trail', []).append(chosen)
        self.next(chat_id)


def store_number(f: dict, key: str, value: float | None, error: str) -> str | None:
    """Guarda o número lido em f['data'][key]; devolve `error` se não deu para ler."""
    if value is None:
        return error
    f['data'][key] = value
    return None
