"""Textos (HTML do Telegram) das respostas. Funções puras: recebem dados da API e devolvem texto."""
from datetime import date

from finfam_bot.consts import METHOD_LABEL
from finfam_bot.fmt import btn, brl, dec, dm, h, money, num, rows

HELP = ('<b>O que eu faço</b>\n'
        '/contas — contas a pagar (com botão para pagar)\n'
        '/agenda — contas desta semana, dia a dia\n'
        '/resumo — resumo do mês · /semana — como foi a semana\n'
        '/metas — progresso das metas\n'
        '/lancar — lançar um gasto passo a passo\n'
        '/investir — registrar um investimento (real, dólar, euro…)\n'
        '/carteira — seus investimentos (lançados e dos bancos)\n'
        '/banco ou /openfinance — bancos conectados: o que chegou, saldo, investimentos e se a fatura bate\n'
        '/revisar — revisar aqui o que chegou do banco (lançar, trocar categoria, ignorar)\n'
        '/cancelar — cancela o lançamento em andamento\n'
        '/desconectar — desliga este Telegram da sua conta\n\n'
        'Com a IA configurada no site, é só escrever, por exemplo:\n'
        '<i>gastei 87,50 no mercado no cartão Inter em 3x</i>\n'
        '<i>quanto gastei de alimentação este mês?</i>\n'
        '<i>o que comprei dia 20/10?</i> · <i>contas dos próximos 5 dias</i>\n'
        '<i>qual o valor da fatura do cartão de novembro?</i> · <i>chegou algo do banco?</i>\n'
        '<i>quais os meus investimentos?</i> · <i>qual o saldo da conta do C6?</i>\n'
        '<i>gastei 30 de farmácia pra Greyce</i> (lança para outra pessoa da família)\n\n'
        '📸 Mande a <b>foto do cupom</b> ou um <b>áudio</b> contando o gasto: eu leio e você confirma com ✅.')

MENU = [[btn('📅 Contas', 'm:contas'), btn('🗓 Agenda', 'm:agenda')],
        [btn('📊 Resumo do mês', 'm:resumo'), btn('📈 Semana', 'm:semana')],
        [btn('🎯 Metas', 'm:metas'), btn('💼 Carteira', 'm:carteira')],
        [btn('🏦 Banco', 'm:banco'), btn('📝 Revisar do banco', 'm:revisar')],
        [btn('➕ Lançar um gasto', 'm:lancar')]]


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


def portfolio_text(d: dict, bank: dict | None = None) -> str:
    in_bank = [i for i in (bank or {}).get('items', []) if i.get('currency', 'BRL') == 'BRL']
    bank_total = sum(i['balance'] for i in in_bank)
    lines = []
    if in_bank:
        lines.append(f'💰 <b>Total investido</b>: {brl(d["fixed_total"] + d.get("crypto_total", 0) + bank_total)}')
    lines.append(f'💼 <b>Carteira</b>: {brl(d["fixed_total"])}')
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
    if in_bank:
        lines += ['', f'🏦 <b>Nos bancos</b> (saldo do banco): {brl(bank_total)}']
        lines += [f'• {h(i["name"])} ({h(i["bank"])}): {brl(i["balance"])}' for i in in_bank[:15]]
    elif not d['fixed'] and not d.get('crypto'):
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


def months_back(month: int, year: int, count: int) -> list[tuple[int, int]]:
    """Os `count` meses até month/year, do mais antigo ao mais novo."""
    out = []
    for _ in range(count):
        out.append((month, year))
        month, year = (12, year - 1) if month == 1 else (month - 1, year)
    return out[::-1]


def spending_months_text(q: dict, rows: list[tuple[int, int, dict]]) -> str:
    """Resposta de "gastos com X nos últimos N meses": um total por mês."""
    what = q.get('category') or q.get('text') or ('no ' + q['payment_method'] if q.get('payment_method') else 'tudo')
    total = sum(r['total_amount'] for _, _, r in rows)
    count = sum(r['total'] for _, _, r in rows)
    if not count:
        return f'🔎 Nenhum gasto com <b>{h(what)}</b> nos últimos {len(rows)} meses.'
    lines = [f'🔎 <b>{h(what)}</b> nos últimos {len(rows)} meses: <b>{brl(total)}</b> em {count} lançamento(s)', '']
    for m, y, r in rows:
        label = f'{MONTHS[m - 1][:3]}/{y}'
        if not r['total']:
            lines.append(f'• {label}: —')
        elif r['total'] == 1 and r['items']:
            e = r['items'][0]
            lines.append(f'• {label}: {brl(r["total_amount"])} — {h(e["description"])} ({dm(e.get("due_date") or e.get("purchase_date"))})')
        else:
            lines.append(f'• {label}: {brl(r["total_amount"])} em {r["total"]} lançamento(s)')
    with_spending = [r['total_amount'] for _, _, r in rows if r['total']]
    lines += ['', f'💰 <b>Total</b>: {brl(total)}',
              f'📊 <b>Média por mês</b>: {brl(total / len(rows))} ({len(rows)} meses)']
    if len(with_spending) < len(rows):
        lines.append(f'Só nos {len(with_spending)} meses com gasto: {brl(sum(with_spending) / len(with_spending))}')
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


def invoices_text(r: dict, bank: str | None = None) -> str:
    """Resposta de "qual o valor da fatura do cartão (da Caixa) de <mês>"."""
    period = f'{MONTHS[r["month"] - 1]}/{r["year"]}'
    if not r['invoices']:
        which = f' do cartão {h(bank)}' if bank else (' desse cartão' if r.get('filtered') else '')
        return f'💳 Nenhuma compra no crédito{which} na fatura de {period}.'
    if r.get('filtered') and len(r['invoices']) == 1:
        i = r['invoices'][0]
        due = f'\nVence {dm(i["due_date"])}' if i.get('due_date') else ''
        status = '✅ paga' if i['status'] == 'paga' else f'⏳ aberta (falta {brl(i["pending_total"])})'
        return (f'💳 <b>Fatura {h(i["label"])} — {period}</b>: <b>{brl(i["total"])}</b> em {i["count"]} compra(s)'
                f'{due} · {status}')
    lines = [f'💳 <b>Fatura de {period}</b>: <b>{brl(r["total"])}</b>'
             + (f' (falta pagar {brl(r["pending_total"])})' if r['pending_total'] and r['paid_total'] else ''), '']
    for i in r['invoices']:
        due = f' · vence {dm(i["due_date"])}' if i.get('due_date') else ''
        status = '✅ paga' if i['status'] == 'paga' else '⏳ aberta'
        lines.append(f'• {h(i["label"])}: {brl(i["total"])}{due} · {status}')
    return '\n'.join(lines)


def balances_text(r: dict | None) -> str:
    """Saldo das contas correntes conectadas (Open Finance)."""
    if not r or not r.get('enabled'):
        return '🏦 O saldo das contas vem dos bancos conectados (Open Finance), que não está liberado para a sua família.'
    if not r['items']:
        return '🏦 Nenhuma conta corrente conectada. Conecte em <b>Contas conectadas</b> no site.'
    lines = [f"💵 <b>Saldo nas contas</b>: {brl(r['total'])}"]
    for a in r['items']:
        when = f" · atualizado {dm(a['updated_at'][:10])}" if a.get('updated_at') else ''
        digits = f" •••• {h(a['last_digits'])}" if a.get('last_digits') else ''
        lines.append(f"• {h(a['bank'])}{digits}: {brl(a['balance'])}{when}")
    return '\n'.join(lines)


def bank_text(summary: dict, check: dict, site_url: str = '', balances: dict | None = None,
              investments: dict | None = None) -> str:
    """Bancos conectados: situação, o que falta revisar e se a fatura do mês bate com o banco."""
    c = summary['counts']
    banks = summary['banks']
    if not banks:
        return '🏦 Nenhum banco conectado. Conecte em <b>Contas conectadas</b> no site.'
    lines = ['🏦 <b>Bancos conectados</b>']
    for b in banks:
        when = dm(b['last_updated_at'][:10]) if b.get('last_updated_at') else '—'
        lines.append(f"• {h(b['name'])} — atualizado em {when}{' · lança sozinho' if b.get('auto_import') else ''}")
    lines.append('')
    if c['pending']:
        total = sum(i['amount'] for i in summary['items'])
        lines.append(f"📝 <b>{c['pending']} para revisar</b> ({brl(total)}):")
        lines += [f"• {dm(i['date'])} {h(i['description'][:35])} — {brl(i['amount'])}" for i in summary['items'][:8]]
        if c['pending'] > 8:
            lines.append(f"… e mais {c['pending'] - 8}.")
        if site_url:
            lines.append(f'Revise em {site_url}/bank/review')
    else:
        lines.append('✅ Nada para revisar.')
    rows_ = [r for r in check.get('cards', []) if r.get('linked') and r.get('bank_total') is not None]
    if rows_:
        lines += ['', f"💳 <b>Faturas de {check['month']:02d}/{check['year']}</b> (banco × FinFam)"]
        for r in rows_:
            ok = abs(r['diff']) < 0.01
            lines.append(f"• {h(r['label'])}: banco {brl(r['bank_total'])} · FinFam {brl(r['finfam_total'])} "
                         + ('✅' if ok else f"— compras batem ✅ (o banco desconta {brl(-r['diff'])} de crédito/ajuste)"
                            if r.get('items_match') else f"— diferença {brl(r['diff'])}"))
    accounts = (balances or {}).get('items') or []
    if accounts:
        lines += ['', f"💵 <b>Saldo nas contas</b>: {brl(balances['total'])}"]
        lines += [f"• {h(a['bank'])}{' •••• ' + h(a['last_digits']) if a.get('last_digits') else ''}: {brl(a['balance'])}"
                  for a in accounts]
    if (investments or {}).get('items'):
        lines += ['', f"📈 <b>Investimentos nos bancos</b>: {brl(investments['total'])} ({len(investments['items'])}) · detalhes em /carteira"]
    return '\n'.join(lines)


WEEKDAY_SHORT = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']


def agenda_text(r: dict) -> str:
    """/agenda: contas a pagar dia a dia (faturas de cartão somadas num item)."""
    lines = [f'🗓 <b>Agenda da semana</b> — {brl(r["total"])} a pagar']
    empty = []
    for d in r['days']:
        day = date.fromisoformat(d['date'])
        label = 'Hoje' if d['date'] == r['start'] else f'{WEEKDAY_SHORT[day.weekday()]} {dm(d["date"])}'
        if not d['items']:
            empty.append(label)
            continue
        lines += ['', f'<b>{label}</b> — {brl(d["total"])}']
        for i in d['items'][:10]:
            late = ' ⚠️ vencida' if i.get('status') == 'vencida' else ''
            who = f' · {h(i["user_name"])}' if i.get('user_name') else ''
            extra = f' ({i["count"]} compras)' if i.get('type') == 'invoice' else ''
            lines.append(f'• {h(i["label"])}{extra} — {brl(i["amount"])}{who}{late}')
    if len(empty) == len(r['days']):
        lines.append('\nNenhuma conta nos próximos 7 dias. 🎉')
    elif empty:
        lines.append(f'\nSem contas: {", ".join(empty)}.')
    return '\n'.join(lines)


def _bar(pct: float) -> str:
    full = max(0, min(10, round(pct / 10)))
    return '▓' * full + '░' * (10 - full)


def goals_text(r: dict) -> str:
    """/metas: progresso de cada meta ativa."""
    if not r['goals']:
        return '🎯 Nenhuma meta ativa. Crie em <b>Metas</b> no site (economizar, limite de gastos ou quitar dívida).'
    lines = [f'🎯 <b>Metas</b> — {MONTHS[r["month"] - 1]}/{r["year"]}']
    for g in r['goals']:
        lines.append('')
        what = f' ({h(g["category"])})' if g.get('category') else ''
        lines.append(f'{g["emoji"]} <b>{h(g["title"])}</b>{what}')
        if g['type'] == 'spending_limit':
            lines.append(f'{_bar(g["pct_used"])} {g["pct_used"]:.0f}% usado · {brl(g["current"])} de {brl(g["target_amount"])}')
            lines.append(f'{h(g["status_label"])} · ' + (f'passou {brl(g["over_amount"])}' if g['over_amount']
                                                         else f'restam {brl(g["remaining"])}'))
        else:
            lines.append(f'{_bar(g["pct"])} {g["pct"]:.0f}% · {brl(g["current"])} de {brl(g["target_amount"])}')
            due = f' · até {dm(g["due_date"])}/{g["due_date"][:4]}' if g.get('due_date') else ''
            lines.append(f'{h(g["status_label"])}{due}')
    return '\n'.join(lines)


def week_text(r: dict) -> str:
    """/semana e o resumo automático de domingo."""
    lines = [f'📈 <b>Sua semana</b> ({dm(r["start"])} a {dm(r["end"])})', '']
    if not r['count']:
        return lines[0] + '\n\nNenhum gasto lançado nesta semana.'
    lines.append(f'Gastos: <b>{brl(r["total"])}</b> em {r["count"]} lançamento(s)')
    if r['average']:
        diff = r['total'] - r['average']
        pct = abs(diff) / r['average'] * 100
        trend = f'🔺 {pct:.0f}% acima' if diff > 0 else f'🔻 {pct:.0f}% abaixo' if diff < 0 else 'igual'
        lines.append(f'Média das semanas anteriores: {brl(r["average"])} ({trend})')
    lines += ['', '<b>Maiores gastos</b>']
    lines += [f'• {h(t["description"])} — {brl(t["amount"])} ({h(t["category"])})' for t in r['top']]
    if r.get('rise'):
        x = r['rise']
        lines += ['', f'📊 <b>{h(x["category"])}</b> foi a que mais subiu: {brl(x["amount"])} '
                      f'(média {brl(x["average"])}/semana)']
    return '\n'.join(lines)


def review_text(item: dict, total: int, offset: int, category: str, note: str = '') -> str:
    """Um lançamento do banco para revisar (/revisar)."""
    head = f'{note}\n\n' if note else ''
    digits = item.get('card_digits') or item.get('account_last_digits')
    where = item['bank_name'] + (f' (de {item["owner_name"]})' if item.get('owner_name') else '') + ((' · cartão' + (f' •••• {digits}' if digits else ''))
                                 if item['account_type'] == 'CREDIT' else ' · conta')
    lines = [f'{head}🏦 <b>Revisar do banco</b> — {offset + 1} de {total}', '',
             f'<b>{h(item["description"])}</b> — {brl(item["amount"])}' + (' ↩️ estorno' if item.get('refund') else ''),
             f'{dm(item["date"])} · {h(where)}' + (f' · fatura {h(item["bill"])}' if item.get('bill') else '')]
    if item.get('installment'):
        lines.append(f'Parcela {item["installment"]["number"]}/{item["installment"]["total"]}')
    if item.get('bank_pending'):
        lines.append('⏳ Ainda pendente no banco')
    lines.append(f'Categoria: <b>{h(category)}</b>')
    return '\n'.join(lines)
