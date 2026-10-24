"""Constantes do domínio e agregações básicas (somas de despesas/rendas por mês)."""
from sqlalchemy import func

from app.core.db import db
from app.models import Expense, Salary

MONTH_NAMES_SHORT = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun',
                     'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
MONTH_NAMES_FULL = ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
                    'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro']

CATEGORIES = ['Alimentação', 'Beleza', 'Educação', 'Lazer', 'Moradia',
              'Saúde', 'Internet', 'Telefone', 'Transporte', 'Outros']

CREDIT = 'Cartão de Crédito'
DEBIT = 'Cartão de Débito'
PAYMENT_METHODS = ['PIX', DEBIT, CREDIT, 'Dinheiro', 'VR', 'VA']
BANK_PAYMENTS = {'PIX', DEBIT, CREDIT}

BANKS = ['Nubank', 'Itaú', 'Bradesco', 'Banco do Brasil', 'Caixa Econômica Federal',
         'Santander', 'Inter', 'C6 Bank', 'BTG Pactual', 'Sicredi', 'Sicoob', 'Safra',
         'Original', 'Neon', 'PicPay', 'XP Investimentos', 'Outros']

INVESTMENT_TYPES = ['Tesouro Selic', 'Tesouro IPCA+', 'Tesouro Prefixado', 'CDB', 'LCI', 'LCA',
                    'CRI/CRA', 'Poupança', 'Fundo de Renda Fixa', 'Fundo Multimercado', 'Ações',
                    'FIIs', 'Debêntures', 'COE', 'Criptomoedas', 'Outros']

# Moedas aceitas nos investimentos (código ISO, nome, símbolo). Cotação em R$ por unidade.
CURRENCIES = [('BRL', 'Real', 'R$'), ('USD', 'Dólar americano', 'US$'), ('EUR', 'Euro', '€'),
              ('GBP', 'Libra esterlina', '£'), ('CHF', 'Franco suíço', 'CHF'), ('JPY', 'Iene', '¥'),
              ('CAD', 'Dólar canadense', 'C$'), ('ARS', 'Peso argentino', 'AR$')]
FOREIGN_CURRENCIES = [c for c, _, _ in CURRENCIES if c != 'BRL']

CRYPTO_COINS = [
    ('bitcoin', 'Bitcoin (BTC)'), ('ethereum', 'Ethereum (ETH)'), ('binancecoin', 'BNB (BNB)'),
    ('solana', 'Solana (SOL)'), ('ripple', 'XRP (XRP)'), ('cardano', 'Cardano (ADA)'),
    ('dogecoin', 'Dogecoin (DOGE)'), ('polkadot', 'Polkadot (DOT)'), ('avalanche-2', 'Avalanche (AVAX)'),
    ('chainlink', 'Chainlink (LINK)'), ('litecoin', 'Litecoin (LTC)'), ('matic-network', 'Polygon (MATIC)'),
]

VARIABLE_INCOME_SOURCES = ['Freelance', 'Dividendos', 'Aluguel', 'Bônus',
                           'Comissão', 'Décimo Terceiro', 'PLR', 'Outros']

CATEGORY_COLORS = {
    'Alimentação': '#FF6384', 'Beleza': '#f72585', 'Educação': '#FFCE56', 'Lazer': '#FF9F40',
    'Moradia': '#9966FF', 'Saúde': '#4BC0C0', 'Internet': '#4361ee', 'Telefone': '#43aa8b',
    'Transporte': '#36A2EB', 'Outros': '#C9CBCF',
}
PAYMENT_COLORS = {
    'PIX': '#32bcad', DEBIT: '#0d6efd', CREDIT: '#6f42c1',
    'Dinheiro': '#198754', 'VR': '#fd7e14', 'VA': '#20c997',
}
USER_COLORS = ['#0d6efd', '#dc3545', '#198754', '#ffc107', '#0dcaf0', '#6c757d']


def user_color_map(users: list) -> dict[int, str]:
    return {u.id: USER_COLORS[i % len(USER_COLORS)] for i, u in enumerate(users)}


def month_offset(base_month: int, base_year: int, offset: int) -> tuple[int, int]:
    """(mês, ano) deslocados `offset` meses a partir da base."""
    m = (base_month - 1 + offset) % 12 + 1
    y = base_year + (base_month - 1 + offset) // 12
    return m, y


def month_label(month: int, year: int) -> str:
    return f'{MONTH_NAMES_SHORT[month - 1]}/{year}'


def brl(value) -> str:
    return f'R$ {float(value):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def sum_expenses_month(uids: list, year: int, month: int, *extra_filters) -> float:
    q = (db.session.query(func.sum(Expense.amount))
         .filter(Expense.user_id.in_(uids), Expense.year == year,
                 Expense.month == month, *extra_filters))
    return float(q.scalar() or 0)


def sum_salaries_month(uids: list, year: int, month: int) -> float:
    """Rendas do mês já marcadas como recebidas."""
    return float(
        db.session.query(func.sum(Salary.amount))
        .filter(Salary.user_id.in_(uids), Salary.year == year, Salary.month == month,
                Salary.received == True)  # noqa: E712
        .scalar() or 0
    )


def sum_salaries_month_projected(uids: list, year: int, month: int) -> float:
    """Rendas do mês (recebidas + pendentes); usado nos insights."""
    total = float(
        db.session.query(func.sum(Salary.amount))
        .filter(Salary.user_id.in_(uids), Salary.year == year, Salary.month == month)
        .scalar() or 0
    )
    return total if total > 0 else sum_salaries_month(uids, year, month)


def pct_delta(cur: float, prev: float) -> float | None:
    if prev == 0:
        return None
    return round((cur - prev) / prev * 100, 1)
