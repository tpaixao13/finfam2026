"""Saída do banco (PIX, débito, boleto) × conta a pagar lançada no FinFam (internet, luz, telefone…).

Três camadas, da mais segura para a menos:
1. o que a pessoa já confirmou ("é a conta Energia"): CNPJ/CPF de quem recebeu ou as palavras que o
   banco usa para essa conta (BillAlias) → marca como paga sozinho;
2. palavras em comum entre a conta e a descrição do banco → só sugere (a pessoa confirma);
3. travas sempre: conta ainda pendente, vencimento perto do pagamento, valor perto do lançado;
   mais de uma conta possível → pergunta.
Conta fixa com o mesmo valor (centavos de diferença) também é marcada sozinha, se for a única.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.core.db import db
from app.core.types import utcnow
from app.models import BankAccount, BankTransaction, BillAlias, Expense, User
from app.services.finance import CREDIT, brl

WINDOW_DAYS = 10                    # vencimento até 10 dias antes/depois do pagamento
MIN_RATIO, MAX_RATIO = Decimal('0.5'), Decimal('1.5')   # valor pago × valor lançado
EXACT = Decimal('0.05')
ALIAS_MIN_SHARE = 0.6               # parte das palavras aprendidas que precisa aparecer
NOT_BILLS = (CREDIT, 'VR', 'VA')    # crédito vai pela fatura; VR/VA não saem do banco
_GENERIC = {
    'pix', 'enviado', 'enviada', 'envio', 'transferencia', 'transf', 'ted', 'doc', 'pagamento', 'pagto', 'pgto',
    'pag', 'conta', 'contas', 'boleto', 'boletos', 'debito', 'automatico', 'deb', 'aut', 'ltda', 'eireli', 'sa',
    'cia', 'das', 'dos', 'para', 'com', 'banco', 'compra', 'cartao', 'titulo', 'convenio', 'cobranca', 'qrcode',
    'qr', 'code', 'mensal', 'mes', 'fatura', 'ref', 'referente', 'ag', 'cc', 'agendado', 'agendamento', 'nao',
}
RANK = {'alias': 0, 'exact': 1, 'words': 2, 'near': 3}
REASON_PREFIX = 'Conta "'           # motivo de uma saída que marcou uma conta como paga (settle)


def _plain(text: str | None) -> str:
    """Sem acento e em minúsculas."""
    return unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode().lower()


def words(text: str | None) -> set[str]:
    """Palavras que identificam: sem acento, minúsculas, 3+ letras, sem as genéricas e sem números."""
    return {w for w in re.findall(r'[a-z0-9]+', _plain(text)) if len(w) >= 3 and not w.isdigit() and w not in _GENERIC}


def bill_key(e: Expense) -> str:
    """A "conta" como a família a chama (a mesma em todos os meses da recorrência)."""
    return ' '.join(re.findall(r'[a-z]+', _plain(e.description)))[:200]


def pays_bill(tx: BankTransaction) -> bool:
    """Esta saída marcou uma conta a pagar como paga (settle)."""
    return (tx.reason or '').startswith(REASON_PREFIX)


def tx_words(tx: BankTransaction) -> set[str]:
    return words(tx.description) | words(tx.payee_name)


def due_of(e: Expense) -> date:
    from app.services.serializers import expense_due_date
    return expense_due_date(e)


def _family(user_id: int) -> tuple[list[int], int | None]:
    user = db.session.get(User, user_id)
    if user is None or not user.tenant_id:
        return [user_id], None
    return [u.id for u in User.query.filter_by(tenant_id=user.tenant_id).all()], user.tenant_id


@dataclass
class Pool:
    """Contas pendentes e apelidos da família, carregados uma vez (a lista de revisão tem muitos itens)."""
    bills: list = field(default_factory=list)
    aliases: list = field(default_factory=list)
    uids: list = field(default_factory=list)

    @classmethod
    def of(cls, user_id: int) -> 'Pool':
        uids, tenant_id = _family(user_id)
        bills = (Expense.query.filter(Expense.user_id.in_(uids), Expense.paid.isnot(True),
                                      Expense.payment_method.notin_(NOT_BILLS), Expense.amount > 0).all())
        aliases = BillAlias.query.filter_by(tenant_id=tenant_id).all() if tenant_id else []
        return cls(bills, aliases, uids)


def alias_matches(alias: BillAlias, tx: BankTransaction, seen: set[str]) -> bool:
    if alias.payee_doc and tx.payee_doc:
        return alias.payee_doc == tx.payee_doc
    known = set((alias.payee_words or '').split())
    return bool(known) and len(known & seen) / len(known) >= ALIAS_MIN_SHARE


def is_exact(e: Expense, amount: Decimal) -> bool:
    return abs(Decimal(str(e.amount)) - amount) <= EXACT


def in_range(e: Expense, amount: Decimal) -> bool:
    ref = Decimal(str(e.amount))
    return is_exact(e, amount) or ref * MIN_RATIO <= amount <= ref * MAX_RATIO


def candidates(tx: BankTransaction, pool: Pool | None = None) -> list[tuple[str, Expense]]:
    """Contas que esta saída pode ter pago, da mais provável para a menos: [(como, conta)]."""
    if tx.kind != 'expense' or not tx.account or tx.account.type != 'BANK' or not tx.amount:
        return []
    pool = pool or Pool.of(tx.user_id)
    amount, seen = Decimal(str(tx.amount)), tx_words(tx)
    taken = {r[0] for r in db.session.query(BankTransaction.expense_id)
             .filter(BankTransaction.user_id.in_(pool.uids), BankTransaction.expense_id.isnot(None),
                     BankTransaction.id != (tx.id or 0))}
    out = []
    for e in pool.bills:
        if e.paid or e.id in taken or not in_range(e, amount):
            continue
        gap = abs((due_of(e) - tx.date).days)
        if gap > WINDOW_DAYS:
            continue
        key = bill_key(e)
        if any(a.bill_key == key and alias_matches(a, tx, seen) for a in pool.aliases):
            how = 'alias'
        elif is_exact(e, amount):
            how = 'exact'
        elif words(e.description) & seen:
            how = 'words'
        else:
            how = 'near'
        out.append((RANK[how], gap, e.id, how, e))
    return [(how, e) for *_, how, e in sorted(out, key=lambda r: r[:3])]


def auto_match(tx: BankTransaction, pool: Pool | None = None) -> tuple[Expense, str] | None:
    """A conta que pode ser marcada sem perguntar, ou None (nenhuma ou mais de uma possível)."""
    found = candidates(tx, pool)
    by_alias = [e for how, e in found if how == 'alias']
    if by_alias:  # a mesma conta em dois meses pendentes: a do vencimento mais perto (já vem primeiro)
        return (by_alias[0], 'alias') if len({bill_key(e) for e in by_alias}) == 1 else None
    exact = [e for how, e in found if how == 'exact']
    return (exact[0], 'exact') if len(exact) == 1 else None


def settle(tx: BankTransaction, e: Expense) -> None:
    """Liga a saída à conta, marca como paga e fica com o valor e o banco que pagaram."""
    paid_amount, before = Decimal(str(tx.amount)), Decimal(str(e.amount))
    note = ''
    if paid_amount != before:
        tx.bill_prev_amount, e.amount = before, paid_amount
        note = f' Valor ajustado de {brl(before)} para {brl(paid_amount)}.'
    bank = (tx.account.connection.label if tx.account and tx.account.connection else '')[:60]
    if bank and e.bank != bank:
        tx.bill_prev_bank, e.bank = e.bank or '', bank
    e.paid = True
    tx.status, tx.expense_id = 'duplicate', e.id
    tx.reason = f'{REASON_PREFIX}{e.description}" marcada como paga' + (f' pelo {bank}' if bank else '') + f'.{note}'


def unsettle(tx: BankTransaction) -> None:
    """Desfaz settle (a saída voltou para a revisão): a conta volta a pendente, com o valor e o banco de
    antes — só o que ainda está como o banco deixou (se a pessoa editou depois, fica o que ela pôs)."""
    if not (tx.expense_id and pays_bill(tx)):
        return
    e = db.session.get(Expense, tx.expense_id)
    if e is not None:
        e.paid = False
        if tx.bill_prev_amount is not None and Decimal(str(e.amount)) == Decimal(str(tx.amount)):
            e.amount = tx.bill_prev_amount
        paid_by = tx.account.connection.label[:60] if tx.account and tx.account.connection else None
        if tx.bill_prev_bank is not None and e.bank == paid_by:
            e.bank = tx.bill_prev_bank or None
    tx.bill_prev_amount = tx.bill_prev_bank = None


def learn(tx: BankTransaction, e: Expense) -> None:
    """Guarda como o banco chama esta conta (CNPJ de quem recebeu, ou as palavras da descrição)."""
    _, tenant_id = _family(e.user_id)
    if not tenant_id:
        return
    key = bill_key(e)
    payee = ' '.join(sorted(words(tx.payee_name) or words(tx.description)))[:200] or None
    if not (tx.payee_doc or payee):
        return
    row = (BillAlias.query.filter_by(tenant_id=tenant_id, bill_key=key, payee_doc=tx.payee_doc).first() if tx.payee_doc
           else BillAlias.query.filter_by(tenant_id=tenant_id, bill_key=key, payee_doc=None, payee_words=payee).first())
    if row is None:
        row = BillAlias(tenant_id=tenant_id, bill_key=key, payee_doc=tx.payee_doc, payee_words=payee)
        db.session.add(row)
    row.used_at = utcnow()


def confirm(tx: BankTransaction, e: Expense) -> str:
    """A pessoa disse "é esta conta": marca como paga e aprende para os próximos meses."""
    settle(tx, e)
    learn(tx, e)
    tx.reviewed_at = utcnow()
    db.session.flush()
    return tx.reason


def reconcile_pending(user_id: int) -> int:
    """Saídas ainda para revisar que já dá para ligar sozinho (a conta pode ter sido lançada depois)."""
    pool, n = Pool.of(user_id), 0
    rows = (BankTransaction.query.join(BankAccount)
            .filter(BankTransaction.user_id.in_(pool.uids), BankTransaction.status == 'pending',
                    BankTransaction.reviewed_at.is_(None), BankAccount.type == 'BANK').all())
    for tx in rows:
        found = auto_match(tx, pool)
        if found:
            settle(tx, found[0])
            n += 1
    if n:
        db.session.flush()
    return n


def options(tx: BankTransaction, pool: Pool | None = None, limit: int = 5) -> list[dict]:
    """Para a revisão: contas que esta saída pode ter pago (a primeira é a sugerida)."""
    return [{'id': e.id, 'description': e.description, 'amount': float(e.amount), 'category': e.category,
             'due': due_of(e).isoformat(), 'match': how}
            for how, e in candidates(tx, pool)[:limit]]
