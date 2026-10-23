"""Cria uma família de demonstração com dados realistas (para desenvolvimento).

Uso (dentro de backend/):  python -m scripts.seed_demo
Login: demo@finfam.app / demo12345
"""
import random
from datetime import date, timedelta
from decimal import Decimal

from app.core.db import session
from app.core.migrations import run_migrations
from app.core.types import utcnow
from app.models import CategoryRule, Expense, CreditCard, Goal, Investment, Salary, Tenant, User
from app.services.card_service import apply_card_fields
from app.services.expense_service import ExpenseData, create_expense
from app.services.finance import month_offset

EMAIL = 'demo@finfam.app'


def _card(user, **kw):
    card = CreditCard(user_id=user.id)
    base = dict(special_type=None, supports_credit=False, supports_debit=False, nickname=None, bank=None,
                due_day=10, best_buy_day=3, credit_limit=None, is_virtual=False, is_additional=False,
                monthly_amount=None, renewal_day=None)
    base.update(kw)
    apply_card_fields(card, **base)
    session.add(card)
    session.flush()
    return card


def seed():
    run_migrations()
    if User.query.filter_by(email=EMAIL).first():
        print('Família demo já existe.')
        return
    rnd = random.Random(42)
    today = date.today()
    tenant = Tenant(name='Família Silva', code='demo-silva', plan='anual',
                    trial_expires_at=utcnow() + timedelta(days=300))
    session.add(tenant)
    session.flush()
    ana = User(name='Ana Silva', email=EMAIL, tenant_id=tenant.id)
    ana.set_password('demo12345')
    bruno = User(name='Bruno Silva', email='bruno@finfam.app', tenant_id=tenant.id)
    bruno.set_password('demo12345')
    session.add_all([ana, bruno])
    session.flush()

    nubank = _card(ana, supports_credit=True, supports_debit=True, last_digits='4821', bank='Nubank',
                   due_day=12, best_buy_day=5, credit_limit=Decimal('6000'))
    _card(ana, supports_credit=True, last_digits='9034', bank='Nubank', is_virtual=True, due_day=12, best_buy_day=5)
    itau = _card(bruno, supports_credit=True, last_digits='1177', bank='Itaú', due_day=20, best_buy_day=13,
                 credit_limit=Decimal('4500'))
    vr = _card(bruno, special_type='vr', last_digits='5510', bank='Outros', monthly_amount=Decimal('880'), renewal_day=1)
    session.commit()

    uids = [ana.id, bruno.id]
    for offset in range(-5, 1):
        m, y = month_offset(today.month, today.year, offset)
        session.add(Salary(user_id=ana.id, year=y, month=m, amount=Decimal('7800'), company='Hospital Central',
                           income_type='fixa', payment_day=5, payment_day_type='util', received=offset <= 0))
        session.add(Salary(user_id=bruno.id, year=y, month=m, amount=Decimal('5200'), company='Construtora Norte',
                           income_type='fixa', payment_day=30, payment_day_type='fixo', received=offset < 0))
    session.add(Salary(user_id=bruno.id, year=today.year, month=today.month, amount=Decimal('950'),
                       company='Freelance', income_type='variavel', received=True))
    session.commit()

    def add(user, desc, amount, cat, method, d, card=None, paid=False, **kw):
        create_expense(uids, ExpenseData(user_id=user.id, description=desc, amount=Decimal(str(amount)), category=cat,
                                         payment_method=method, day=d.day, month=d.month, year=d.year,
                                         card_id=card.id if card else None, paid=paid, **kw))

    start = month_offset(today.month, today.year, -5)
    first = date(start[1], start[0], 1)
    add(ana, 'Aluguel', 2400, 'Moradia', 'PIX', first.replace(day=10), recurring_months=8)
    add(bruno, 'Internet fibra', 119.9, 'Internet', 'PIX', first.replace(day=15), recurring_months=8)
    add(ana, 'Plano de saúde', 640, 'Saúde', 'PIX', first.replace(day=8), recurring_months=8)
    add(ana, 'Notebook', 4200, 'Educação', 'Cartão de Crédito', today - timedelta(days=70), card=nubank, installments=10)
    add(bruno, 'Geladeira', 3600, 'Moradia', 'Cartão de Crédito', today - timedelta(days=40), card=itau, installments=12)

    shops = [('Supermercado Pão de Açúcar', 'Alimentação', 180, 420), ('iFood', 'Alimentação', 35, 110),
             ('Posto Shell', 'Transporte', 150, 260), ('Uber', 'Transporte', 18, 60), ('Farmácia Drogasil', 'Saúde', 25, 140),
             ('Cinema', 'Lazer', 60, 120), ('Restaurante', 'Alimentação', 80, 220), ('Salão', 'Beleza', 70, 180)]
    for back in range(150, -1, -1):
        d = today - timedelta(days=back)
        if rnd.random() < 0.55:
            name, cat, lo, hi = rnd.choice(shops)
            who = rnd.choice([ana, bruno])
            amount = round(rnd.uniform(lo, hi), 2)
            if who is bruno and cat == 'Alimentação' and rnd.random() < 0.5:
                add(who, name, amount, cat, 'VR', d, card=vr, paid=True)
            elif rnd.random() < 0.6:
                add(who, name, amount, cat, 'Cartão de Crédito', d, card=nubank if who is ana else itau,
                    paid=back > 45)
            else:
                add(who, name, amount, cat, rnd.choice(['PIX', 'Cartão de Débito']), d,
                    card=nubank if who is ana and rnd.random() < .5 else None, paid=back > 2)
    # alguns vencimentos próximos e um atrasado
    add(ana, 'Conta de luz', 287.4, 'Moradia', 'PIX', today + timedelta(days=2))
    add(bruno, 'IPVA parcela', 412.0, 'Transporte', 'PIX', today - timedelta(days=4))
    add(ana, 'Escola de inglês', 520.0, 'Educação', 'PIX', today)

    # o passado já foi pago; ficam pendentes só o mês atual e o futuro
    Expense.query.filter(Expense.user_id.in_(uids),
                         Expense.year * 100 + Expense.month < today.year * 100 + today.month).update(
        {'paid': True}, synchronize_session=False)
    session.add_all([
        Goal(user_id=ana.id, title='Reserva de emergência', type='saving', target_amount=Decimal('20000'),
             start_date=today - timedelta(days=60), due_date=today + timedelta(days=240)),
        Goal(user_id=bruno.id, title='Delivery sob controle', type='spending_limit', target_amount=Decimal('900'),
             category='Alimentação', start_date=today.replace(day=1)),
        Investment(user_id=ana.id, description='CDB liquidez diária', amount=Decimal('12000'), investment_type='CDB',
                   annual_rate=Decimal('14.65'), year=today.year, month=max(1, today.month - 3)),
        Investment(user_id=bruno.id, description='Tesouro Selic 2029', amount=Decimal('8000'),
                   investment_type='Tesouro Selic', annual_rate=Decimal('14.75'), year=today.year, month=1),
        CategoryRule(tenant_id=tenant.id, keyword='IFOOD', category='Alimentação', match_count=12),
        CategoryRule(tenant_id=tenant.id, keyword='UBER', category='Transporte', match_count=9),
    ])
    session.commit()
    print(f'Família demo criada. Login: {EMAIL} / demo12345')


if __name__ == '__main__':
    seed()
    session.remove()
