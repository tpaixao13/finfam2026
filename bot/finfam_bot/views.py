"""Textos (HTML do Telegram) das respostas. Funções puras: recebem dados da API e devolvem texto."""
from datetime import date

from finfam_bot.consts import METHOD_LABEL
from finfam_bot.fmt import btn, brl, dec, dm, h, money, num, rows

HELP = ('<b>O que eu faço</b>\n'
        '/contas — contas a pagar (com botão para pagar)\n'
        '/resumo — resumo do mês\n'
        '/lancar — lançar um gasto passo a passo\n'
        '/investir — registrar um investimento (real, dólar, euro…)\n'
        '/carteira — seus investimentos em reais\n'
        '/cancelar — cancela o lançamento em andamento\n'
        '/desconectar — desliga este Telegram da sua conta\n\n'
        'Com a IA configurada no site, é só escrever, por exemplo:\n'
        '<i>gastei 87,50 no mercado no cartão Inter em 3x</i>\n'
        '<i>quanto gastei de alimentação este mês?</i>\n'
        '<i>o que comprei dia 20/10?</i> · <i>contas dos próximos 5 dias</i>\n'
        '<i>qual o valor da fatura do cartão de novembro?</i>')


def draft_text(d: dict, title: str = 'Confere o lançamento?') -> str:
    lines = [f'🧾 <b>{h(title)}</b>', '',
             f'<b>{h(d["description"])}</b> — {brl(d["amount"])}',
             f'Forma: {h(METHOD_LABEL.get(d["payment_method"], d["payment_method"]))}'
             + (f' · {h(d["card_label"])}' if d.get('card_label') else '')
             + (f' · {d["installments"]}x de {brl(d["amount"] / d["installments"])}' if d.get('installments') else ''),
             f'Categoria: {h(d["category"])}',
             f'Data: {dm(d["date"])}']
    if d.get('recurring'):
        lines.append(f'Repete: todo mês, por {d["recurring"]} meses (a partir desta data)')
    if d.get('bank') and not d.get('card_label'):
        lines.append(f'Banco: {h(d["bank"])}')
    if 'paid' in d:
        lines.append('Situação: ✅ pago' if d['paid'] else 'Situação: ⏳ pendente')
    if d.get('user_name'):
        lines.append(f'Pessoa: {h(d["user_name"])}')
    return '\n'.join(lines)


def invest_text(d: dict, symbol: str, fx_rate: float | None, today: date) -> str:
    """Confirmação do investimento. `fx_rate`: cotação usada (informada por quem lançou ou a de hoje)."""
    lines = ['📈 <b>Confere o investimento?</b>', '',
             f'<b>{h(d["investment_type"])}</b>' + (f' — {h(d["coin_label"])}' if d.get('coin_label') else ''),
             f'Valor: {h(money(symbol, d["amount"]))}']
    if fx_rate:
        label = 'cotação informada' if d.get('fx_buy_rate') else 'cotação de hoje'
        lines.append(f'≈ {brl(d["amount"] * fx_rate)} ({label}: R$ {dec(round(fx_rate, 4))})')
    if d.get('annual_rate'):
        lines.append(f'Rende: {dec(d["annual_rate"])}% ao ano')
    lines.append(f'Data: {today:%m/%Y}')
    return '\n'.join(lines)


def bills_view(b: dict, days: int) -> tuple[str, list[list[dict]]]:
    """Texto e botões (pagar / atualizar) de /contas."""
    text, buttons = [f'📅 <b>Contas a pagar</b> — total {brl(b["total"])}'], []
    for key, title in (('overdue', '⚠️ Em atraso'), ('today', '🔔 Vencem hoje'), ('upcoming', f'🗓 Próximos {days} dias')):
        items = b[key]
        if not items:
            continue
        text.append(f'\n<b>{title}</b> ({brl(b["totals"][key])})')
        for e in items[:15]:
            who = f' · {h(e["user_name"])}' if e.get('user_name') else ''
            text.append(f'• {dm(e["due_date"])} — {h(e["description"])} — {brl(e["amount"])}{who}')
            if len(buttons) < 10:
                buttons.append(btn(f'✅ {e["description"][:22]} {brl(e["amount"])}', f'pay:{e["id"]}'))
        if len(items) > 15:
            text.append(f'… e mais {len(items) - 15}.')
    text.append('\nToque para marcar como paga:' if len(text) > 1 else '\nNada pendente. Tudo em dia! 🎉')
    return '\n'.join(text), rows(buttons, 1) + [[btn('🔄 Atualizar', 'bills')]]


def summary_text(d: dict) -> str:
    k = d['kpis']
    lines = [f'📊 <b>Resumo de {h(d["month_name"])}/{d["year"]}</b>', '',
             f'Renda recebida: {brl(k["income"])}', f'Gastos: {brl(k["expenses"])} (crédito {brl(k["credit"])})',
             f'Sobra: <b>{brl(k["balance"])}</b>',
             f'Pendente no mês: {brl(d["pending_total"])} em {len(d["pending"])} conta(s)']
    if len(d.get('people', [])) > 1:
        lines.append('')
        lines += [f'• {h(p["user"]["name"])}: gastos {brl(p["expenses"])}, saldo {brl(p["balance"])}'
                  for p in d['people']]
    if d.get('insights'):
        lines += ['', '<b>Atenção</b>'] + [f'• {h(i["title"])}' for i in d['insights'][:3]]
    return '\n'.join(lines)


def portfolio_text(d: dict) -> str:
    lines = [f'💼 <b>Carteira</b>: {brl(d["fixed_total"])}']
    if d.get('fx_gain_total'):
        lines.append(f'Câmbio: {"+" if d["fx_gain_total"] >= 0 else ""}{brl(d["fx_gain_total"])}')
    if d.get('crypto'):
        lines.append(f'Cripto (aplicado): {brl(d["crypto_total"])}')
    lines.append('')
    for i in d['fixed'][:15]:
        foreign = i.get('currency', 'BRL') != 'BRL'
        extra = f' ({h(i["currency"])} {num(i["amount"])})' if foreign else ''
        label = h(i['description'] or i['investment_type'])
        lines.append(f'• {label}: {brl(i.get("value_brl", i["amount"]))}{extra}')
    for i in d.get('crypto', [])[:10]:
        lines.append(f'• {h(i["crypto_coin"])}: {brl(i["amount"])} aplicado')
    if not d['fixed'] and not d.get('crypto'):
        lines.append('Nenhum investimento ainda. Use /investir.')
    return '\n'.join(lines)


MONTHS = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro',
          'outubro', 'novembro', 'dezembro']


def _items(items: list[dict], date_field: str) -> list[str]:
    return [f'• {dm(e.get(date_field))} — {h(e["description"])} — {brl(e["amount"])}'
            + (f' · {h(e["user_name"])}' if e.get('user_name') else '') for e in items]


def spending_text(q: dict, r: dict) -> str:
    """Resposta de "quanto gastei com X no mês"."""
    what = q.get('category') or q.get('text') or ('no ' + q['payment_method'] if q.get('payment_method') else 'tudo')
    period = f'{MONTHS[q["month"] - 1]}/{q["year"]}'
    if not r['total']:
        return f'🔎 Nenhum gasto com <b>{h(what)}</b> em {period}.'
    lines = [f'🔎 <b>{h(what)}</b> em {period}: <b>{brl(r["total_amount"])}</b> em {r["total"]} lançamento(s)', '']
    lines += _items(r['items'], 'purchase_date')
    if r['total'] > len(r['items']):
        lines.append(f'… e mais {r["total"] - len(r["items"])}. Veja tudo no site.')
    return '\n'.join(lines)


def purchases_text(q: dict, r: dict) -> str:
    """Resposta de "o que comprei dia X / no período"."""
    period = dm(q['date_from']) if q['date_from'] == q['date_to'] else f'{dm(q["date_from"])} a {dm(q["date_to"])}'
    extra = f' ({h(q.get("category") or q.get("text"))})' if q.get('category') or q.get('text') else ''
    if not r['total']:
        return f'🧾 Nenhuma compra registrada em {period}{extra}.'
    lines = [f'🧾 Compras de {period}{extra}: <b>{brl(r["total_amount"])}</b> em {r["total"]} lançamento(s)', '']
    lines += _items(r['items'], 'purchase_date')
    if r['total'] > len(r['items']):
        lines.append(f'… e mais {r["total"] - len(r["items"])}. Veja tudo no site.')
    return '\n'.join(lines)


def invoices_text(r: dict) -> str:
    """Resposta de "qual o valor da fatura do cartão de <mês>"."""
    period = f'{MONTHS[r["month"] - 1]}/{r["year"]}'
    if not r['invoices']:
        return f'💳 Nenhuma compra no crédito na fatura de {period}.'
    lines = [f'💳 <b>Fatura de {period}</b>: <b>{brl(r["total"])}</b>'
             + (f' (falta pagar {brl(r["pending_total"])})' if r['pending_total'] and r['paid_total'] else ''), '']
    for i in r['invoices']:
        due = f' · vence {dm(i["due_date"])}' if i.get('due_date') else ''
        status = '✅ paga' if i['status'] == 'paga' else '⏳ aberta'
        lines.append(f'• {h(i["label"])}: {brl(i["total"])}{due} · {status}')
    return '\n'.join(lines)
