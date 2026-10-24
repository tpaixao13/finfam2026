"""Investimentos (renda fixa com IR/IOF, cripto) e dados de mercado."""
import re
from datetime import date as _date, datetime, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from app.core.db import db, get_or_404
from app.core.deps import Ctx, get_ctx
from app.models import Investment, User
from app.services import market
from app.services.finance import (CRYPTO_COINS, CURRENCIES, FOREIGN_CURRENCIES, INVESTMENT_TYPES, month_label,
                                  month_offset)
from app.services.investment_tax import daily_yield, net_of_taxes
from app.services.serializers import investment_out, money

router = APIRouter(tags=['investments'])

_COINS_RE = re.compile(r'^[a-z0-9-]+(,[a-z0-9-]+)*$')
_COIN_IDS = {c for c, _ in CRYPTO_COINS}


class InvestmentIn(BaseModel):
    user_id: int | None = None
    description: str | None = Field(None, max_length=200)
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    investment_type: str
    annual_rate: Decimal = Field(Decimal(0), ge=0, le=999, max_digits=6, decimal_places=2)
    crypto_coin: str | None = Field(None, pattern=r'^[a-z0-9-]+$')
    crypto_buy_price: float | None = Field(None, gt=0, lt=1e12, allow_inf_nan=False)
    currency: Literal['BRL', 'USD', 'EUR', 'GBP', 'CHF', 'JPY', 'CAD', 'ARS'] = Field(
        'BRL', description='Moeda do valor informado em `amount`')
    fx_buy_rate: Decimal | None = Field(None, gt=0, max_digits=14, decimal_places=6,
                                        description='R$ por unidade na compra; vazio = cotação de hoje')
    month: int = Field(ge=1, le=12)
    year: int = Field(ge=2000, le=2100)

    @model_validator(mode='after')
    def _check(self):
        if self.investment_type not in INVESTMENT_TYPES:
            raise ValueError('Tipo de investimento inválido.')
        if self.investment_type == 'Criptomoedas' and not self.crypto_coin:
            raise ValueError('Selecione a criptomoeda.')
        if self.crypto_coin and self.crypto_coin not in _COIN_IDS:
            raise ValueError('Criptomoeda não suportada.')
        if self.investment_type == 'Criptomoedas' and self.currency != 'BRL':
            raise ValueError('Criptomoedas são registradas em reais.')
        return self


def _foreign_daily(amount_brl: float, annual_rate: float) -> dict:
    """Rendimento bruto de 1 dia útil, já em reais. IR/IOF brasileiros não se aplicam aqui."""
    gross = amount_brl * ((1 + annual_rate / 100) ** (1 / 252) - 1)
    return {'gross': gross, 'net': gross, 'iof_rate': 0.0, 'ir_rate': 0.0, 'exempt': False, 'foreign': True}


def valuation(inv: Investment, rates: dict[str, float]) -> dict:
    """Valor em reais: aplicado (pela cotação da compra), atual (pela de hoje) e ganho com câmbio."""
    amount = float(inv.amount)
    code = inv.currency or 'BRL'
    if code == 'BRL':
        return {'invested_brl': amount, 'value_brl': amount, 'fx_gain': 0.0, 'fx_rate': None, 'fx_is_live': True}
    buy = float(inv.fx_buy_rate or 0)
    now = rates.get(code)
    rate = now or buy
    return {'invested_brl': round(amount * buy, 2), 'value_brl': round(amount * rate, 2),
            'fx_gain': round(amount * (rate - buy), 2), 'fx_rate': rate, 'fx_is_live': now is not None}


@router.get('/investments', summary='Carteira com rendimento diário estimado, câmbio e Selic/CDI')
def list_investments(ctx: Ctx = Depends(get_ctx)):
    rows = (Investment.query.join(User).filter(Investment.user_id.in_(ctx.uids))
            .order_by(Investment.year.desc(), Investment.month.desc()).all())
    rates = market.fx_rates([i.currency for i in rows if i.currency])
    today = _date.today()
    fixed, crypto = [], []
    for inv in rows:
        if inv.crypto_coin:
            crypto.append(investment_out(inv))
            continue
        val = valuation(inv, rates)
        info = None
        if inv.annual_rate and (inv.currency or 'BRL') != 'BRL':
            info = _foreign_daily(val['value_brl'], float(inv.annual_rate))
        elif inv.annual_rate:
            applied = inv.created_at.date() if inv.created_at else today
            info = daily_yield(float(inv.amount), float(inv.annual_rate), inv.investment_type,
                               max(0, (today - applied).days))
        fixed.append({**investment_out(inv, info), **val})
    selic = market.selic_info()
    return {'fixed': fixed, 'crypto': crypto,
            'fixed_total': money(sum(i['value_brl'] for i in fixed)),
            'fx_gain_total': money(sum(i['fx_gain'] for i in fixed)),
            'crypto_total': money(sum(i['amount'] for i in crypto)), 'fx_rates': rates,
            'selic': selic['rate'], 'selic_date': selic['date'], 'selic_is_live': selic['is_live'],
            'cdi': market.cdi_rate(selic['rate']), 'rate_suggestions': market.rate_suggestions(selic['rate'])}


@router.post('/investments', status_code=201, summary='Registra investimento (em reais ou outra moeda)')
def create(body: InvestmentIn, ctx: Ctx = Depends(get_ctx)):
    is_crypto = body.investment_type == 'Criptomoedas'
    buy_price = body.crypto_buy_price
    if is_crypto and not buy_price:
        buy_price = market.crypto_price(body.crypto_coin)
    foreign = body.currency != 'BRL'
    fx_buy = body.fx_buy_rate
    if foreign and not fx_buy:
        fx_buy = market.fx_rates([body.currency]).get(body.currency)
        if not fx_buy:
            raise HTTPException(status_code=503, detail='Cotação indisponível agora: informe a cotação da compra.')
    inv = Investment(user_id=ctx.member(body.user_id).id, description=(body.description or '').strip() or None,
                     amount=body.amount, investment_type=body.investment_type, annual_rate=body.annual_rate,
                     crypto_coin=body.crypto_coin if is_crypto else None,
                     crypto_buy_price=buy_price if is_crypto else None,
                     currency=body.currency if foreign else None, fx_buy_rate=fx_buy if foreign else None,
                     year=body.year, month=body.month)
    db.session.add(inv)
    db.session.commit()
    return {'message': 'Investimento registrado com sucesso!', 'investment': investment_out(inv)}


@router.delete('/investments/{inv_id}', summary='Remove investimento')
def delete(inv_id: int, ctx: Ctx = Depends(get_ctx)):
    inv = get_or_404(Investment.query.filter(Investment.id == inv_id, Investment.user_id.in_(ctx.uids)),
                     'Investimento não encontrado.')
    db.session.delete(inv)
    db.session.commit()
    return {'message': 'Investimento removido.'}


@router.get('/charts/investments', tags=['charts'], summary='Projeção 12 meses da renda fixa (bruto e líquido)')
def projection(ctx: Ctx = Depends(get_ctx)):
    fixed = Investment.query.filter(Investment.user_id.in_(ctx.uids), Investment.crypto_coin.is_(None)).all()
    if not fixed:
        return {'labels': [], 'invested': [], 'projected': [], 'projected_net': []}
    now = datetime.now()
    months = [month_offset(now.month, now.year, i) for i in range(12)]
    rates = market.fx_rates([i.currency for i in fixed if i.currency])
    brl = {i.id: valuation(i, rates)['value_brl'] for i in fixed}  # outras moedas: cotação de hoje
    invested = round(sum(brl.values()), 2)
    gross_line, net_line = [], []
    for fm, fy in months:
        gross = net = 0.0
        point = _date(fy, fm, 1)
        for inv in fixed:
            monthly = (1 + float(inv.annual_rate) / 100) ** (1 / 12) - 1
            elapsed = max(0, (fy - inv.year) * 12 + (fm - inv.month))
            amount = brl[inv.id]
            value = amount * (1 + monthly) ** elapsed
            gross += value
            if inv.currency and inv.currency != 'BRL':  # IR/IOF brasileiros não se aplicam
                net += value
                continue
            applied = inv.created_at.date() if inv.created_at else point
            net += amount + net_of_taxes(value - amount, inv.investment_type, max(0, (point - applied).days))
        gross_line.append(round(gross, 2))
        net_line.append(round(net, 2))
    return {'labels': [month_label(m, y) for m, y in months], 'invested': [invested] * 12,
            'projected': gross_line, 'projected_net': net_line}


# ── Mercado ──────────────────────────────────────────────────────────────────

@router.get('/market/selic', tags=['market'], summary='Selic, CDI e taxas sugeridas por tipo')
def selic(_: Ctx = Depends(get_ctx)):
    info = market.selic_info()
    return {**info, 'cdi': market.cdi_rate(info['rate']), 'suggestions': market.rate_suggestions(info['rate'])}


@router.get('/market/fx', tags=['market'], summary='Cotação atual (R$) das moedas aceitas nos investimentos')
def fx(_: Ctx = Depends(get_ctx)):
    return {'rates': market.fx_rates(FOREIGN_CURRENCIES),
            'currencies': [{'code': c, 'name': n, 'symbol': sym} for c, n, sym in CURRENCIES]}


@router.get('/market/crypto-price', tags=['market'], summary='Cotação em BRL (CoinGecko)')
def crypto_price(coins: str = Query('bitcoin', max_length=200), _: Ctx = Depends(get_ctx)):
    if not _COINS_RE.match(coins) or not set(coins.split(',')) <= _COIN_IDS:
        raise HTTPException(status_code=422, detail='Moeda inválida.')
    coins = ','.join(sorted(set(coins.split(','))))  # mesma consulta = mesmo cache
    data = market.crypto_prices(coins)
    if not data:
        raise HTTPException(status_code=503, detail='Não foi possível obter a cotação.')
    return data


@router.get('/market/crypto-history', tags=['market'], summary='Histórico de preço em BRL')
def crypto_history(coin: str = Query('bitcoin', pattern=r'^[a-z0-9-]+$'), days: int = Query(30, ge=1, le=365),
                   _: Ctx = Depends(get_ctx)):
    if coin not in _COIN_IDS:
        raise HTTPException(status_code=422, detail='Moeda inválida.')
    data = market.crypto_history(coin, days)
    if not data or 'prices' not in data:
        raise HTTPException(status_code=503, detail='Histórico indisponível.')
    labels, values = [], []
    for ts, price in data['prices']:
        labels.append(datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime('%d/%m'))
        values.append(round(price, 2))
    return {'labels': labels, 'values': values}
