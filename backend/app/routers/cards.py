"""Cartões (crédito, débito, VR/VA), faturas e pagamento de fatura."""
from datetime import date as _date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.db import db, get_or_404
from app.core.deps import Ctx, Period, get_ctx, get_period
from app.models import CreditCard, Expense
from app.services import card_service as cs
from app.services.finance import CREDIT, MONTH_NAMES_FULL
from app.services.serializers import card_out, expense_out

router = APIRouter(prefix='/cards', tags=['cards'])


class CardIn(BaseModel):
    user_id: int | None = Field(None, description='Dono do cartão; padrão: quem está logado')
    special_type: Literal['vr', 'va'] | None = None
    supports_credit: bool = False
    supports_debit: bool = False
    nickname: str | None = Field(None, max_length=50)
    last_digits: str = Field(pattern=r'^\d{4}$')
    bank: str | None = Field(None, max_length=60)
    is_virtual: bool = False
    is_additional: bool = False
    due_day: int | None = Field(1, ge=0, le=31)
    best_buy_day: int | None = Field(1, ge=0, le=31)
    credit_limit: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=2)
    monthly_amount: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=2)
    renewal_day: int | None = Field(None, ge=1, le=31)


class PayInvoiceIn(BaseModel):
    month: int = Field(ge=1, le=12)
    year: int = Field(ge=2000, le=2100)


def _get(ctx: Ctx, card_id: int) -> CreditCard:
    return get_or_404(CreditCard.query.filter(CreditCard.id == card_id, CreditCard.user_id.in_(ctx.uids)),
                      'Cartão não encontrado.')


def _apply(card: CreditCard, body: CardIn):
    try:
        cs.apply_card_fields(card, **body.model_dump(exclude={'user_id'}))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get('', summary='Cartões com uso do mês, limite, saldo VR/VA e histórico')
def list_cards(period: Period = Depends(get_period), ctx: Ctx = Depends(get_ctx),
               light: bool = Query(False, description='Só os dados do cartão, sem uso/fatura (rápido)')):
    cards = (CreditCard.query.filter(CreditCard.user_id.in_(ctx.uids))
             .order_by(CreditCard.bank, CreditCard.last_digits).all())
    if light:
        return {'cards': [card_out(c) for c in cards]}
    return {
        'month': period.month, 'year': period.year, 'month_name': MONTH_NAMES_FULL[period.month - 1],
        'cards': [{**card_out(c), 'usage': cs.card_usage(c, period.month, period.year)} for c in cards],
        'history': cs.history_chart(cards, period.month, period.year) if cards else None,
    }


@router.post('', status_code=201, summary='Cadastra cartão')
def create(body: CardIn, ctx: Ctx = Depends(get_ctx)):
    card = CreditCard(user_id=ctx.member(body.user_id).id)
    _apply(card, body)
    db.session.add(card)
    db.session.commit()
    return {'message': f'Cartão •••• {card.last_digits} adicionado com sucesso!', 'card': card_out(card)}


@router.put('/{card_id}', summary='Edita cartão')
def update(card_id: int, body: CardIn, ctx: Ctx = Depends(get_ctx)):
    card = _get(ctx, card_id)
    _apply(card, body)
    db.session.commit()
    return {'message': 'Cartão atualizado com sucesso!', 'card': card_out(card)}


@router.delete('/{card_id}', summary='Exclui cartão (as despesas são mantidas, sem vínculo)')
def delete(card_id: int, ctx: Ctx = Depends(get_ctx)):
    card = _get(ctx, card_id)
    Expense.query.filter_by(card_id=card.id).update({'card_id': None})
    db.session.delete(card)
    db.session.commit()
    return {'message': 'Cartão excluído. As despesas vinculadas foram mantidas.'}


@router.get('/{card_id}/invoice', summary='Fatura do cartão no mês (consolida cartões da mesma conta)')
def invoice(card_id: int, period: Period = Depends(get_period), ctx: Ctx = Depends(get_ctx)):
    card = _get(ctx, card_id)
    inv = cs.get_invoice(card, period.month, period.year, sibling_cards=cs.sibling_cards(card, ctx.uids))
    today = _date.today()
    out = lambda rows: [expense_out(e, today) for e in rows]  # noqa: E731
    gradient, text_color = cs.card_gradient(card)
    return {
        'card': card_out(card), 'month': period.month, 'year': period.year,
        'month_name': MONTH_NAMES_FULL[period.month - 1], 'gradient': gradient, 'text_color': text_color,
        'status': inv['status'],
        'credit': {'expenses': out(inv['expenses']), 'total': round(inv['total'], 2),
                   'paid_total': round(inv['paid_total'], 2), 'pending_total': round(inv['pending_total'], 2),
                   'by_category': inv['chart'], 'future_installments': round(inv['future_total'], 2)},
        'debit': {'expenses': out(inv['debit_expenses']), 'total': round(inv['debit_total'], 2),
                  'by_category': inv['debit_chart']},
        'by_card': [{'card': card_out(g['card']), 'expenses': out(g['expenses']), 'total': round(g['total'], 2),
                     'paid_total': round(g['paid_total'], 2), 'pending': round(g['pending'], 2)}
                    for g in inv['expenses_by_card']],
        'category_icons': cs.CATEGORY_ICONS,
    }


@router.post('/{card_id}/invoice/pay', summary='Marca a fatura de crédito do mês como paga')
def pay_invoice(card_id: int, body: PayInvoiceIn, ctx: Ctx = Depends(get_ctx)):
    card = _get(ctx, card_id)
    ids = ([c.id for c in CreditCard.query.filter(CreditCard.account_id == card.account_id,
                                                  CreditCard.user_id.in_(ctx.uids))]
           if card.account_id else [card.id])
    updated = (Expense.query
               .filter(Expense.card_id.in_(ids), Expense.month == body.month, Expense.year == body.year,
                       Expense.payment_method == CREDIT, Expense.paid.isnot(True))
               .update({'paid': True}, synchronize_session=False))
    db.session.commit()
    message = (f'{updated} despesa(s) marcada(s) como paga(s).' if updated
               else 'Nenhuma despesa pendente nesta fatura.')
    return {'message': message, 'updated': updated}


@router.get('/{card_id}/invoice-preview', summary='Em qual fatura cai uma compra e quanto do limite usa')
def invoice_preview(card_id: int, day: int = Query(ge=1, le=31), month: int = Query(ge=1, le=12),
                    year: int = Query(ge=2000, le=2100), amount: float = Query(0, ge=0),
                    ctx: Ctx = Depends(get_ctx)):
    card = _get(ctx, card_id)
    try:
        purchase = _date(year, month, day)
    except ValueError:
        raise HTTPException(status_code=422, detail='Data inválida.')
    inv_year, inv_month = cs.get_invoice_reference(purchase, card.best_buy_day)
    is_open = purchase.day <= card.best_buy_day
    limit = card.effective_limit
    usage = None
    if limit:
        usage = (cs.account_credit_usage(card.account_id, inv_month, inv_year) if card.account_id
                 else cs.credit_invoice_total(card.id, inv_month, inv_year))
    total_cards = card.account.cards.count() if card.account_id and card.account else 1
    return {
        'invoice_label': cs.get_invoice_label(inv_year, inv_month), 'invoice_month': inv_month,
        'invoice_year': inv_year, 'is_open': is_open,
        'days_to_closing': (card.best_buy_day - purchase.day) if is_open else None,
        'due_day': card.due_day, 'current_usage': usage, 'credit_limit': limit,
        'projected_usage': (usage + amount) if usage is not None else None,
        'account_label': card.account.display_label if card.account_id and card.account else None,
        'account_total_cards': total_cards, 'is_shared_limit': total_cards > 1,
    }
