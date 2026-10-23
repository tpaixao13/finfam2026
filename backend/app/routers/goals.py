"""Metas financeiras."""
from datetime import date as _date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.types import utcnow
from app.core.db import db, get_or_404
from app.core.deps import Ctx, Period, get_ctx, get_period
from app.models import Goal
from app.services.goal_service import calculate, calculate_all
from app.services.serializers import goal_out

router = APIRouter(prefix='/goals', tags=['goals'])

GoalType = Literal['saving', 'spending_limit', 'debt_reduction']


class GoalIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    type: GoalType
    target_amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    category: str | None = Field(None, max_length=50)
    due_date: _date | None = None
    start_date: _date | None = None
    user_id: int | None = None


class GoalUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=120)
    type: GoalType | None = None
    target_amount: Decimal | None = Field(None, gt=0, max_digits=12, decimal_places=2)
    category: str | None = Field(None, max_length=50)
    due_date: _date | None = None


def _get(ctx: Ctx, goal_id: int) -> Goal:
    return get_or_404(Goal.query.filter(Goal.id == goal_id, Goal.user_id.in_(ctx.uids)),
                      'Meta não encontrada.')


@router.get('', summary='Metas ativas com progresso e coaching')
def list_goals(period: Period = Depends(get_period), ctx: Ctx = Depends(get_ctx)):
    goals = (Goal.query.filter(Goal.user_id.in_(ctx.uids), Goal.active == True)  # noqa: E712
             .order_by(Goal.created_at).all())
    return {'month': period.month, 'year': period.year,
            'goals': [goal_out(d) for d in calculate_all(goals, ctx.uids, period.month, period.year)]}


@router.post('', status_code=201, summary='Cria meta')
def create(body: GoalIn, ctx: Ctx = Depends(get_ctx)):
    goal = Goal(user_id=ctx.member(body.user_id).id, title=body.title.strip(), type=body.type,
                target_amount=body.target_amount, category=(body.category or '').strip() or None,
                due_date=body.due_date, start_date=body.start_date or _date.today())
    db.session.add(goal)
    db.session.commit()
    now = datetime.now()
    return {'message': f'Meta "{goal.title}" criada com sucesso!',
            'goal': goal_out(calculate(goal, ctx.uids, now.month, now.year))}


@router.patch('/{goal_id}', summary='Edita meta')
def update(goal_id: int, body: GoalUpdate, ctx: Ctx = Depends(get_ctx)):
    goal = _get(ctx, goal_id)
    sent = body.model_fields_set
    if body.title:
        goal.title = body.title.strip()
    if body.type:
        goal.type = body.type
    if body.target_amount:
        goal.target_amount = body.target_amount
    if 'category' in sent:
        goal.category = (body.category or '').strip() or None
    if 'due_date' in sent:
        goal.due_date = body.due_date
    db.session.commit()
    now = datetime.now()
    return {'message': f'Meta "{goal.title}" atualizada!',
            'goal': goal_out(calculate(goal, ctx.uids, now.month, now.year))}


@router.post('/{goal_id}/complete', summary='Marca meta como concluída')
def complete(goal_id: int, ctx: Ctx = Depends(get_ctx)):
    goal = _get(ctx, goal_id)
    goal.completed_at = utcnow()
    db.session.commit()
    return {'message': f'🎉 Meta "{goal.title}" concluída!'}


@router.delete('/{goal_id}', summary='Arquiva meta')
def archive(goal_id: int, ctx: Ctx = Depends(get_ctx)):
    goal = _get(ctx, goal_id)
    goal.active = False
    db.session.commit()
    return {'message': f'Meta "{goal.title}" arquivada.'}
