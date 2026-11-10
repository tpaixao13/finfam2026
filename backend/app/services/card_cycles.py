"""Fechamento REAL de cada fatura do cartão. O banco informa, a cada leitura, a data de fechamento e de
vencimento da fatura atual; o dia muda de mês para mês (fim de semana, feriado, regra do banco).
Guardamos cada uma (BankCycle) e usamos a data real para saber em que fatura cai uma compra — do banco,
do site ou do bot. Meses que o banco ainda não informou seguem o "melhor dia de compra" do cartão."""
from datetime import date, timedelta

from app.core.db import db
from app.models import BankAccount, BankCycle, CreditCard

MAX_CYCLE = timedelta(days=40)  # fechamento → vencimento, e de um fechamento para o seguinte
SURE_BEFORE = timedelta(days=26)  # sem o fechamento anterior: até aqui antes do fechamento é certo que é desta fatura


def remember(acc: BankAccount, close: date | None, due: date | None) -> None:
    """Guarda o fechamento/vencimento que o banco informou agora (um por fatura)."""
    if acc.type != 'CREDIT' or not close or not due or not timedelta(0) < due - close <= MAX_CYCLE:
        return
    if acc.id is None:
        db.session.flush()
    row = BankCycle.query.filter_by(account_id=acc.id, due_date=due).first()
    if row is None:
        db.session.add(BankCycle(account_id=acc.id, close_date=close, due_date=due))
    else:
        row.close_date = close


def bill_of_day(cycles: list[BankCycle], day: date) -> tuple[int, int] | None:
    """(ano, mês) da fatura da compra pela data real: a 1ª fatura que fecha no dia da compra ou depois.
    Só dentro do período que o banco já informou; fora dele (ou num mês que falta), None."""
    cycles = sorted(cycles, key=lambda c: c.close_date)
    for i, c in enumerate(cycles):
        if day <= c.close_date:
            prev = cycles[i - 1].close_date if i else None
            if (prev is None or c.close_date - prev > MAX_CYCLE) and c.close_date - day > SURE_BEFORE:
                return None  # antes do 1º fechamento conhecido (ou depois de um mês que falta): não sabemos
            return c.due_date.year, c.due_date.month
    return None


def card_cycles(card: CreditCard) -> list[BankCycle]:
    """Fechamentos que o banco informou para este cartão (ou para outro da mesma conta de crédito)."""
    ids = ([c.id for c in CreditCard.query.filter_by(account_id=card.account_id).all()]
           if card.account_id else [card.id])
    return (BankCycle.query.join(BankAccount, BankAccount.id == BankCycle.account_id)
            .filter(BankAccount.card_id.in_(ids)).order_by(BankCycle.close_date).all())


def bill_for_purchase(card: CreditCard, day: date) -> tuple[int, int]:
    """(ano, mês) da fatura de uma compra no crédito: pelo fechamento real do banco, se ele já informou
    esse mês; senão pelo melhor dia de compra (compra depois dele vai para a fatura seguinte)."""
    known = bill_of_day(card_cycles(card), day)
    if known:
        return known
    if day.day > card.best_buy_day:
        return (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
    return day.year, day.month


def cycle_of_bill(card: CreditCard, year: int, month: int) -> BankCycle | None:
    """O fechamento real da fatura que vence em mês/ano, se o banco informou."""
    return next((c for c in card_cycles(card) if (c.due_date.year, c.due_date.month) == (year, month)), None)
