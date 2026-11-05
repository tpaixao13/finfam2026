"""Histórico do Open Finance de cada família: o que a Pluggy avisou (webhook) e o que o FinFam leu de cada banco."""
import json
from datetime import timedelta

from app.core.db import db
from app.core.types import utcnow
from app.models import BankAccount, BankConnection, BankIncome, BankTransaction, PluggyEvent

KEEP_DAYS = 60
MAX_DETAIL = 8000
EVENTS = {   # nome do evento da Pluggy → o que aconteceu, em português
    'item/created': 'Banco conectado', 'item/updated': 'Banco atualizado na Pluggy',
    'item/error': 'Erro ao atualizar o banco', 'item/deleted': 'Banco removido na Pluggy',
    'item/waiting_user_input': 'O banco pede uma ação sua', 'item/waiting_user_action': 'O banco pede uma ação sua',
    'item/login_succeeded': 'Login no banco feito', 'transactions/created': 'Lançamentos novos no banco',
    'transactions/updated': 'Lançamentos alterados no banco', 'transactions/deleted': 'Lançamentos apagados no banco',
    'connector/status_updated': 'Situação do banco mudou na Pluggy',
}


def record(user_id: int, kind: str, title: str, detail=None, conn: BankConnection | None = None) -> None:
    """Guarda um evento (o chamador faz o commit) e descarta os da pessoa com mais de KEEP_DAYS dias."""
    text = json.dumps(detail, ensure_ascii=False, default=str)[:MAX_DETAIL] if detail is not None else None
    db.session.add(PluggyEvent(user_id=user_id, connection_id=conn.id if conn else None, kind=kind,
                               title=title[:200], detail=text))
    (PluggyEvent.query.filter(PluggyEvent.user_id == user_id, PluggyEvent.created_at < utcnow() - timedelta(days=KEEP_DAYS))
     .delete(synchronize_session=False))


def webhook(user_id: int, payload: dict, conn: BankConnection | None) -> None:
    event = str(payload.get('event') or '')
    record(user_id, 'webhook', f'Aviso da Pluggy: {EVENTS.get(event, event or "evento sem nome")}', payload, conn)


def read(conn: BankConnection, before: set, before_incomes: set) -> tuple[list, int]:
    """Leitura do banco: guarda o que chegou de novo. Devolve (saídas novas, quantas entradas novas)."""
    new = (BankTransaction.query.join(BankAccount).filter(BankAccount.connection_id == conn.id,
                                                         ~BankTransaction.id.in_(before or {0}))
           .order_by(BankTransaction.date).all())
    incomes = (BankIncome.query.join(BankAccount).filter(BankAccount.connection_id == conn.id,
                                                        ~BankIncome.id.in_(before_incomes or {0}))
               .order_by(BankIncome.date).all())
    parts = [f'{len(new)} lançamento(s) novo(s)' if new else '', f'{len(incomes)} entrada(s) nova(s)' if incomes else '']
    title = 'Leitura do banco: ' + (' e '.join(p for p in parts if p) or 'nada novo')
    detail = {'lancamentos': [{'data': t.date.isoformat(), 'valor': float(t.amount), 'descricao': t.description[:60],
                               'onde': 'cartão' if t.account.type == 'CREDIT' else 'conta', 'situacao': t.status,
                               'id_pluggy': t.pluggy_id} for t in new],
              'entradas': [{'data': i.date.isoformat(), 'valor': float(i.amount), 'descricao': i.description[:60],
                            'situacao': i.status} for i in incomes]}
    record(conn.user_id, 'read', title, detail if (new or incomes) else None, conn)
    return new, len(incomes)


def failed(conn: BankConnection, error: Exception) -> None:
    record(conn.user_id, 'error', f'Leitura do banco falhou: {str(error)[:150]}', None, conn)


def out(e: PluggyEvent) -> dict:
    try:
        detail = json.loads(e.detail) if e.detail else None
    except ValueError:
        detail = None
    return {'id': e.id, 'at': e.created_at.isoformat() + 'Z', 'kind': e.kind, 'title': e.title,
            'bank_name': e.connection.label if e.connection else None, 'connection_id': e.connection_id,
            'detail': detail}
