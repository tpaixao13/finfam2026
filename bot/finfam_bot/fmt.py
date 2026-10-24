"""Formatação de texto e botões do Telegram (sem regra de negócio)."""
import html


def h(text) -> str:
    return html.escape(str(text if text is not None else ''), quote=False)


def num(value, places: int = 2) -> str:
    """1234.5 → '1.234,50'."""
    return f'{float(value):,.{places}f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def dec(value) -> str:
    """12.5 → '12,5' (sem separador de milhar)."""
    return str(value).replace('.', ',')


def brl(value) -> str:
    return f'R$ {num(value or 0)}'


def money(symbol: str, value) -> str:
    return f'{symbol} {num(value)}'


def dm(iso: str | None) -> str:
    return f'{iso[8:10]}/{iso[5:7]}' if iso and len(iso) >= 10 else '—'


def rows(buttons: list[dict], per_row: int = 2) -> list[list[dict]]:
    return [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]


def btn(text: str, data: str) -> dict:
    return {'text': text, 'callback_data': data[:64]}
