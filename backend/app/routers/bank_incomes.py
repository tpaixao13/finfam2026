"""Entradas que chegaram do banco (Open Finance) → Rendas: revisar, aprovar, ignorar.

Mesmas regras de visibilidade das saídas: as entradas dos bancos da pessoa e dos bancos que alguém
da família compartilhou. Só vira renda o que for aprovado (no site ou nos botões do bot).
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func

from app.core.db import db
from app.core.deps import Ctx, get_ctx_session, get_ctx_site_or_bot
from app.core.types import utcnow
from app.models import BankIncome, Salary
from app.routers.pluggy import _enabled_tenant, _owner_name, _seen_accounts
from app.services import bank_incomes as bi
from app.services import open_finance as of

router = APIRouter(prefix='/pluggy/incomes', tags=['open finance'])

STATUSES = ('pending', 'duplicate', 'ignored', 'imported')
Status = Literal['pending', 'duplicate', 'ignored', 'imported']


def _seen_inc(ctx: Ctx):
    return BankIncome.account_id.in_(_seen_accounts(ctx))


def _counts(ctx: Ctx) -> dict:
    counts = {s: 0 for s in STATUSES}
    for st, n in db.session.query(BankIncome.status, func.count(BankIncome.id)).filter(_seen_inc(ctx)).group_by(BankIncome.status):
        counts[st] = n
    return counts


def _out(inc: BankIncome, ctx: Ctx) -> dict:
    acc = inc.account
    salary = db.session.get(Salary, inc.salary_id) if inc.salary_id else None
    out = {'id': inc.id, 'date': inc.date.isoformat(), 'description': inc.description, 'amount': float(inc.amount),
           'counterpart': inc.counterpart, 'status': inc.status, 'reason': inc.reason,
           'bank_name': acc.connection.label, 'account_name': acc.name, 'account_last_digits': of.last4(acc.number),
           'owner_name': _owner_name(ctx, inc.user_id),
           'salary': ({'id': salary.id, 'company': salary.company, 'amount': float(salary.amount),
                       'month': salary.month, 'year': salary.year} if salary is not None else None)}
    if inc.status == 'pending':
        out['suggestion'] = bi.suggestion(inc)
    return out


def _mine(ctx: Ctx, ids: list[int]) -> list[BankIncome]:
    rows = BankIncome.query.filter(BankIncome.id.in_(ids), _seen_inc(ctx)).all()
    if len(rows) != len(set(ids)):
        raise HTTPException(status_code=404, detail='Entrada não encontrada.')
    return rows


@router.get('', summary='Entradas que chegaram do banco, por situação')
def list_incomes(status: Status = 'pending', ctx: Ctx = Depends(get_ctx_session)):
    _enabled_tenant(ctx)
    rows = (BankIncome.query.filter(_seen_inc(ctx), BankIncome.status == status)
            .order_by(BankIncome.date.desc(), BankIncome.id.desc()).limit(500).all())
    return {'counts': _counts(ctx), 'items': [_out(i, ctx) for i in rows]}


@router.get('/next', summary='Próxima entrada para revisar (uma por vez, para o bot)')
def next_income(offset: int = Query(0, ge=0, le=500), ctx: Ctx = Depends(get_ctx_site_or_bot)):
    _enabled_tenant(ctx)
    q = BankIncome.query.filter(_seen_inc(ctx), BankIncome.status == 'pending')
    total = q.count()
    inc = q.order_by(BankIncome.date, BankIncome.id).offset(offset).first() if offset < total else None
    return {'total': total, 'offset': offset, 'item': _out(inc, ctx) if inc else None}


class ApproveItem(BaseModel):
    id: int
    income_type: Literal['fixa', 'variavel'] = 'variavel'
    company: str | None = Field(None, max_length=200, description='Fonte da renda (empresa, pessoa)')
    use_expected: bool = Field(True, description='Se há renda prevista parecida no mês, marcar ela como recebida')


class ApproveIn(BaseModel):
    items: list[ApproveItem] = Field(min_length=1, max_length=200)


@router.post('/approve', summary='Lança como renda as entradas escolhidas')
def approve(body: ApproveIn, ctx: Ctx = Depends(get_ctx_site_or_bot)):
    _enabled_tenant(ctx)
    rows = {i.id: i for i in _mine(ctx, [it.id for it in body.items])}
    done = marked = 0
    for item in body.items:
        inc = rows[item.id]
        if inc.status != 'pending':  # ignorada (ex.: transferência) ou já em Rendas não vira renda de novo
            continue
        _, was_expected = bi.approve(inc, ctx.uids, item.income_type, item.company, item.use_expected)
        marked += was_expected
        done += not was_expected
    db.session.commit()
    parts = [f'{done} renda(s) lançada(s)' if done else '', f'{marked} renda(s) prevista(s) marcada(s) como recebida(s)' if marked else '']
    return {'message': (' e '.join(p for p in parts if p) or 'Nada para lançar') + '.', 'imported': done, 'marked': marked}


class IdsIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)


@router.post('/ignore', summary='Ignora as entradas escolhidas (não viram renda)')
def ignore(body: IdsIn, ctx: Ctx = Depends(get_ctx_site_or_bot)):
    _enabled_tenant(ctx)
    n = 0
    for inc in _mine(ctx, body.ids):
        if inc.status in ('pending', 'duplicate'):
            inc.status, inc.reason, inc.reviewed_at = 'ignored', 'Ignorada por você.', utcnow()
            n += 1
    db.session.commit()
    return {'message': f'{n} entrada(s) ignorada(s).'}


@router.post('/restore', summary='Volta entradas ignoradas/já em Rendas para a revisão')
def restore(body: IdsIn, ctx: Ctx = Depends(get_ctx_session)):
    _enabled_tenant(ctx)
    n = 0
    for inc in _mine(ctx, body.ids):
        if inc.status in ('ignored', 'duplicate'):
            inc.status, inc.reason, inc.salary_id, inc.transfer_tx_id, inc.reviewed_at = 'pending', None, None, None, utcnow()
            n += 1
    db.session.commit()
    return {'message': f'{n} entrada(s) de volta para revisar.'}
