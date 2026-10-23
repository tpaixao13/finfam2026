"""Regras brasileiras de tributação de renda fixa: IOF regressivo (primeiros 29 dias)
e Imposto de Renda regressivo (Lei 11.033/2004), aplicados sobre a projeção diária."""

# Tipos isentos de IR para pessoa física (Lei 11.033/2004 e legislação correlata de LCI/LCA/CRI/CRA)
IR_EXEMPT_TYPES = {'LCI', 'LCA', 'CRI/CRA', 'Poupança'}

# Tipos que não seguem a tributação regressiva de renda fixa (ganho de capital / regras próprias)
NOT_FIXED_INCOME_TYPES = {'Ações', 'FIIs', 'Criptomoedas', 'Outros'}

# Tabela regressiva de IOF (Anexo do Decreto 6.306/2007) — % sobre o rendimento, incide só até o dia 29
_IOF_TABLE = [96, 93, 90, 86, 83, 80, 76, 73, 70, 66,
              63, 60, 56, 53, 50, 46, 43, 40, 36, 33,
              30, 26, 23, 20, 16, 13, 10, 6, 3, 0]


def ir_rate(days: int) -> float:
    """Alíquota de IR regressiva sobre o rendimento, conforme o prazo de aplicação."""
    if days <= 180:
        return 0.225
    if days <= 360:
        return 0.20
    if days <= 720:
        return 0.175
    return 0.15


def iof_rate(days: int) -> float:
    """Alíquota de IOF sobre o rendimento, zerada a partir do 30º dia."""
    if days <= 0:
        return _IOF_TABLE[0] / 100
    if days >= 30:
        return 0.0
    return _IOF_TABLE[days - 1] / 100


DIAS_UTEIS_ANO = 252  # convenção padrão do mercado para produtos atrelados a CDI/Selic


def daily_yield(amount: float, annual_rate: float, investment_type: str, days_elapsed: int) -> dict:
    """Projeta o rendimento de um dia útil (o próximo), bruto e líquido de IOF/IR.

    A taxa diária usa a convenção de 252 dias úteis/ano (padrão de mercado para CDI/Selic),
    não dias corridos. `days_elapsed` é o nº de dias CORRIDOS desde a aplicação até hoje —
    as tabelas de IOF/IR são definidas em dias corridos pela legislação; o imposto do dia
    projetado usa days_elapsed + 1, já que é o rendimento do dia seguinte que está sendo estimado.
    """
    daily_rate = (1 + annual_rate / 100) ** (1 / DIAS_UTEIS_ANO) - 1
    gross = amount * daily_rate

    exempt = investment_type in IR_EXEMPT_TYPES or investment_type in NOT_FIXED_INCOME_TYPES
    if exempt:
        return {
            'daily_rate': daily_rate,
            'gross': gross,
            'net': gross,
            'iof_rate': 0.0,
            'ir_rate': 0.0,
            'exempt': True,
        }

    day = max(0, days_elapsed) + 1
    iof = iof_rate(day)
    ir = ir_rate(day)

    after_iof = gross * (1 - iof)
    net = after_iof * (1 - ir)

    return {
        'daily_rate': daily_rate,
        'gross': gross,
        'net': net,
        'iof_rate': iof,
        'ir_rate': ir,
        'exempt': False,
    }


def net_of_taxes(gross_gain: float, investment_type: str, days_held: int) -> float:
    """Aplica IOF e IR regressivos sobre um ganho total acumulado, conforme o nº de dias
    corridos entre a aplicação e o resgate — é assim que o imposto incide de fato na prática
    (uma única alíquota sobre todo o ganho, definida pelo prazo total de permanência)."""
    if gross_gain <= 0:
        return gross_gain
    if investment_type in IR_EXEMPT_TYPES or investment_type in NOT_FIXED_INCOME_TYPES:
        return gross_gain

    days = max(0, days_held)
    after_iof = gross_gain * (1 - iof_rate(days))
    return after_iof * (1 - ir_rate(days))
