from dataclasses import dataclass


@dataclass
class BankTransaction:
    day: int
    month: int
    year: int
    description: str
    amount: float          # sempre positivo (R$)
    payment_method: str    # 'PIX' | 'Cartão de Débito' | 'Cartão de Crédito'
