"""Rendas (salários fixos e rendas variáveis)."""
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import and_, or_

from app.core.db import db, get_or_404
from app.core.deps import Ctx, Period, get_ctx, get_period
from app.models import Salary, SalaryGroup, User
from app.services.finance import MONTH_NAMES_SHORT, month_offset
from app.services.serializers import money, salary_out

router = APIRouter(prefix='/incomes', tags=['incomes'])


class _PayDay(BaseModel):
    payment_day_type: Literal['fixo', 'util'] | None = None
    payment_day: int | None = Field(None, ge=1, le=31)

    @model_validator(mode='after')
    def _check_day(self):
        if not self.payment_day_type:
            self.payment_day = None
        elif self.payment_day_type == 'util' and self.payment_day and not 1 <= self.payment_day <= 22:
            raise ValueError('Dia útil deve ser entre 1 e 22.')
        return self


class IncomeCreate(_PayDay):
    user_id: int | None = None
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    company: str | None = Field(None, max_length=200, description='Empresa ou fonte')
    income_type: Literal['fixa', 'variavel'] = 'fixa'
    month: int = Field(ge=1, le=12)
    year: int = Field(ge=2000, le=2100)
    recurring_months: int | None = Field(None, ge=2, le=24, description='Só renda fixa: repetir por N meses')
    received: bool = False


class IncomeUpdate(_PayDay):
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    company: str | None = Field(None, max_length=200)
    update_forward: bool = Field(False, description='Aplicar também aos meses seguintes da série')


def _get(ctx: Ctx, salary_id: int) -> Salary:
    return get_or_404(Salary.query.filter(Salary.id == salary_id, Salary.user_id.in_(ctx.uids)),
                      'Renda não encontrada.')


@router.get('', summary='Rendas do mês (fixas e variáveis)')
def list_incomes(period: Period = Depends(get_period), ctx: Ctx = Depends(get_ctx)):
    rows = (Salary.query.join(User)
            .filter(Salary.user_id.in_(ctx.uids), Salary.year == period.year, Salary.month == period.month)
            .order_by(User.name).all())
    fixed = [s for s in rows if s.income_type == 'fixa']
    variable = [s for s in rows if s.income_type == 'variavel']
    return {
        'month': period.month, 'year': period.year,
        'fixed': [salary_out(s) for s in fixed], 'variable': [salary_out(s) for s in variable],
        'fixed_total': money(sum(float(s.amount) for s in fixed)),
        'variable_total': money(sum(float(s.amount) for s in variable)),
        'received_total': money(sum(float(s.amount) for s in rows if s.received)),
        'pending_total': money(sum(float(s.amount) for s in rows if not s.received)),
    }


@router.post('', status_code=201, summary='Registra renda (com opção de repetir nos próximos meses)')
def create(body: IncomeCreate, ctx: Ctx = Depends(get_ctx)):
    user = ctx.member(body.user_id)
    common = dict(user_id=user.id, amount=body.amount, company=(body.company or '').strip() or None,
                  income_type=body.income_type, payment_day=body.payment_day,
                  payment_day_type=body.payment_day_type, received=body.received)
    if body.income_type == 'fixa' and body.recurring_months:
        n = body.recurring_months
        group = SalaryGroup(user_id=user.id)
        db.session.add(group)
        db.session.flush()
        created = []
        for i in range(n):
            m, y = month_offset(body.month, body.year, i)
            s = Salary(year=y, month=m, salary_group_id=group.id,
                       **{**common, 'received': body.received if i == 0 else False})
            db.session.add(s)
            created.append(s)
        db.session.commit()
        end_m, end_y = month_offset(body.month, body.year, n - 1)
        message = (f'Renda fixa criada por {n} meses ({MONTH_NAMES_SHORT[body.month - 1]}/{body.year}'
                   f' → {MONTH_NAMES_SHORT[end_m - 1]}/{end_y}).')
    else:
        s = Salary(year=body.year, month=body.month, **common)
        db.session.add(s)
        db.session.commit()
        created, message = [s], 'Renda adicionada com sucesso!'
    return {'message': message, 'incomes': [salary_out(s) for s in created]}


@router.put('/{salary_id}', summary='Edita uma renda (opcionalmente os meses seguintes da série)')
def update(salary_id: int, body: IncomeUpdate, ctx: Ctx = Depends(get_ctx)):
    salary = _get(ctx, salary_id)
    targets = [salary]
    if body.update_forward and salary.salary_group_id:
        targets = Salary.query.filter(
            Salary.salary_group_id == salary.salary_group_id, Salary.user_id.in_(ctx.uids),
            or_(Salary.year > salary.year, and_(Salary.year == salary.year, Salary.month >= salary.month)),
        ).all()
    for s in targets:
        s.amount = body.amount
        s.company = (body.company or '').strip() or None
        s.payment_day_type = body.payment_day_type
        s.payment_day = body.payment_day
    db.session.commit()
    return {'message': 'Renda atualizada com sucesso!', 'updated': len(targets), 'income': salary_out(salary)}


@router.post('/{salary_id}/toggle-received', summary='Alterna recebida/pendente')
def toggle_received(salary_id: int, ctx: Ctx = Depends(get_ctx)):
    salary = _get(ctx, salary_id)
    salary.received = not bool(salary.received)
    db.session.commit()
    return {'message': 'Marcada como recebida.' if salary.received else 'Marcada como pendente.',
            'income': salary_out(salary)}


@router.delete('/{salary_id}', summary='Remove uma renda')
def delete(salary_id: int, ctx: Ctx = Depends(get_ctx)):
    db.session.delete(_get(ctx, salary_id))
    db.session.commit()
    return {'message': 'Renda removida.'}

