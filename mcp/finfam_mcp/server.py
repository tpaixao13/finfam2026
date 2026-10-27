"""Servidor MCP do FinFam.

Expõe as finanças da família como ferramentas para um assistente (Claude Desktop,
um bot do Telegram com LLM etc.). Toda chamada passa pela API REST do FinFam,
autenticada com um token de API (FINFAM_API_TOKEN).
"""
import unicodedata
from datetime import date

from finfam_mcp.api import FinFamAPI, FinFamError

try:  # SDK mcp 2.x
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # SDK mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

INSTRUCTIONS = """Ferramentas do FinFam, o controle financeiro da família.
- Valores em reais (R$). Datas em AAAA-MM-DD; sem data = hoje.
- Antes de lançar no cartão, se não souber o nome do cartão, use `listar_cartoes`.
- Compras no crédito caem na fatura conforme o "melhor dia de compra" do cartão — a API calcula isso.
- Para "contas a pagar" (ex.: "próximos 5 dias"), use `contas_a_pagar(dias=5)`.
- "Valor do cartão/fatura de novembro?" → `faturas_do_mes(mes=11)`; detalhe de um cartão → `fatura_do_cartao`.
- "O que comprei dia X?" → `buscar_despesas(data=...)`. "Quanto gastei de X no mês?" → `buscar_despesas(categoria=...)`
  ou `gastos_por_categoria`. Para pagar, use `marcar_como_pago` com os IDs mostrados.
- Bancos conectados (Open Finance): "chegou algo do banco?" → `importados_do_banco`; "a fatura bate com o
  banco?" → `conferir_fatura_com_banco`.
- Confirme com o usuário antes de excluir algo."""

api = FinFamAPI()
mcp = _Server(name='FinFam', instructions=INSTRUCTIONS)


# ── Formatação ───────────────────────────────────────────────────────────────

def brl(value) -> str:
    return f'R$ {float(value or 0):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def dm(iso: str | None) -> str:
    if not iso:
        return '—'
    y, m, d = iso[:10].split('-')
    return f'{d}/{m}/{y}'


def _norm(text: str) -> str:
    text = unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode()
    return text.lower().strip()


def _expense_line(e: dict, date_field: str = 'due_date') -> str:
    series = f" [{e['series']['label']}]" if e.get('series') else ''
    where = e.get('card_label') or e['payment_method']
    status = {'pago': 'pago', 'pendente': 'pendente', 'vencida': 'VENCIDA'}[e['status']]
    return (f"#{e['id']} · {dm(e[date_field])} · {e['description']}{series} · {brl(e['amount'])} · "
            f"{e['category']} · {where} · {e.get('user_name') or ''} · {status}")


def _safe(fn):
    """Converte erros da API em texto para o assistente (sem derrubar a ferramenta)."""
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except FinFamError as exc:
            return f'Erro: {exc}'
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    wrapper.__annotations__ = fn.__annotations__
    wrapper.__wrapped__ = fn
    return wrapper


def _period(mes: int | None, ano: int | None) -> tuple[int, int]:
    today = date.today()
    return mes or today.month, ano or today.year


# ── Resolução de nomes (pessoa, cartão) ──────────────────────────────────────

def _members() -> list[dict]:
    return api.get('/auth/me')['members']


def _resolve_person(pessoa: str | None) -> int | None:
    if not pessoa:
        return None
    members = _members()
    wanted = _norm(pessoa)
    hits = [m for m in members if wanted in _norm(m['name'])]
    if len(hits) == 1:
        return hits[0]['id']
    names = ', '.join(m['name'] for m in members)
    raise FinFamError(f'Não identifiquei a pessoa "{pessoa}". Pessoas da família: {names}.')


def _cards(mes: int | None = None, ano: int | None = None) -> list[dict]:
    return api.get('/cards', month=mes, year=ano)['cards']


def _resolve_card(cartao: str | int | None) -> dict | None:
    if cartao in (None, ''):
        return None
    cards = _cards()
    text = str(cartao).strip()
    if text.isdigit():
        by_id = [c for c in cards if c['id'] == int(text)]
        if by_id:
            return by_id[0]
        by_digits = [c for c in cards if c['last_digits'] == text]
        if len(by_digits) == 1:
            return by_digits[0]
    wanted = _norm(text)
    hits = [c for c in cards if wanted in _norm(c['label'])]
    if len(hits) > 1:  # prefere o principal (não virtual/adicional)
        main = [c for c in hits if not c['is_virtual'] and not c['is_additional']]
        hits = main if len(main) == 1 else hits
    if len(hits) == 1:
        return hits[0]
    options = '; '.join(f"#{c['id']} {c['label']}" for c in cards) or 'nenhum cartão cadastrado'
    raise FinFamError(f'Não identifiquei o cartão "{cartao}". Cartões: {options}.')


# ── Ferramentas: consulta ────────────────────────────────────────────────────

@mcp.tool()
@_safe
def resumo_do_mes(mes: int | None = None, ano: int | None = None) -> str:
    """Resumo financeiro do mês: renda recebida, gastos, sobra, cartão de crédito,
    situação de cada pessoa, pendências e os principais pontos de atenção."""
    month, year = _period(mes, ano)
    d = api.get('/dashboard', month=month, year=year)
    k = d['kpis']
    lines = [f"Resumo de {d['month_name']}/{d['year']}:",
             f"• Renda recebida: {brl(k['income'])}",
             f"• Gastos: {brl(k['expenses'])} (no crédito: {brl(k['credit'])})",
             f"• Sobra: {brl(k['balance'])}",
             f"• Pendente de pagamento no mês: {brl(d['pending_total'])} em {len(d['pending'])} conta(s)", '',
             'Por pessoa:']
    lines += [f"• {p['user']['name']}: renda {brl(p['income'])}, gastos {brl(p['expenses'])}, saldo {brl(p['balance'])}"
              for p in d['people']]
    if d['insights']:
        lines += ['', 'Pontos de atenção:']
        lines += [f"• {i['title']}: {i['message']}" for i in d['insights'][:5]]
    return '\n'.join(lines)


@mcp.tool()
@_safe
def contas_a_pagar(dias: int = 7, pessoa: str | None = None, incluir_atrasadas: bool = True,
                   max_itens: int = 15) -> str:
    """Contas não pagas: em atraso, que vencem hoje e nos próximos `dias` dias.
    Usa o vencimento real (compras no crédito vencem no dia de vencimento da fatura).
    Mostra o ID de cada conta, para usar em `marcar_como_pago`."""
    b = api.get('/expenses/bills', days_ahead=dias, include_overdue=str(incluir_atrasadas).lower(),
                user_id=_resolve_person(pessoa))
    out = [f"Contas a pagar (hoje {dm(b['date'])}) — total {brl(b['total'])}"]
    for key, title in (('overdue', 'Em atraso'), ('today', 'Vencem hoje'), ('upcoming', f'Próximos {dias} dias')):
        items = b[key]
        if key == 'overdue' and not incluir_atrasadas:
            continue
        out.append(f"\n{title}: {len(items)} conta(s), {brl(b['totals'][key])}")
        out += [f'  {_expense_line(e)}' for e in items[:max_itens]]
        if len(items) > max_itens:
            out.append(f'  … e mais {len(items) - max_itens}.')
    return '\n'.join(out)


@mcp.tool()
@_safe
def buscar_despesas(texto: str | None = None, mes: int | None = None, ano: int | None = None,
                    categoria: str | None = None, pessoa: str | None = None,
                    situacao: str | None = None, data: str | None = None, de: str | None = None,
                    ate: str | None = None, limite: int = 20) -> str:
    """Procura despesas e mostra o total.
    - Por DATA DA COMPRA (a real, não a da fatura): `data` (um dia, AAAA-MM-DD) ou `de`/`ate` (período).
      Ex.: "o que comprei dia 20/10?" → data="2026-10-20".
    - Por mês (competência): `mes`/`ano`. Com `texto` e sem `mes`, busca no ano inteiro; `mes`=0 também.
    - "quanto gastei de alimentação este mês?" → categoria="Alimentação" (veja `listas_de_referencia`).
    `situacao`: "pago" ou "pendente"."""
    month, year = _period(mes, ano)
    if mes == 0 or (texto and mes is None):
        month = 0
    if data:
        de = ate = data
    r = api.get('/expenses', month=month, year=year, search=texto, category=categoria,
                user_id=_resolve_person(pessoa), paid=situacao, purchase_from=de, purchase_to=ate or de,
                per_page=max(1, min(limite, 100)))
    if not r['items']:
        return 'Nenhuma despesa encontrada com esses filtros.'
    lines = [f"{r['total']} despesa(s), total {brl(r['total_amount'])}" +
             (f' (mostrando {len(r["items"])})' if r['total'] > len(r['items']) else '') + ':']
    lines += [_expense_line(e, 'purchase_date') for e in r['items']]
    return '\n'.join(lines)


@mcp.tool()
@_safe
def gastos_por_categoria(mes: int | None = None, ano: int | None = None) -> str:
    """Quanto a família gastou em cada categoria no mês."""
    month, year = _period(mes, ano)
    r = api.get('/expenses/summary/by-category', month=month, year=year)
    if not r['items']:
        return f'Nenhum gasto em {month:02d}/{year}.'
    lines = [f'Gastos por categoria em {month:02d}/{year} (total {brl(r["total"])}):']
    lines += [f"• {i['category']}: {brl(i['total'])} ({i['count']} lançamento(s))" for i in r['items']]
    return '\n'.join(lines)


@mcp.tool()
@_safe
def listar_cartoes(mes: int | None = None, ano: int | None = None) -> str:
    """Cartões da família com fatura do mês, limite usado/disponível e saldo de VR/VA."""
    month, year = _period(mes, ano)
    cards = _cards(month, year)
    if not cards:
        return 'Nenhum cartão cadastrado.'
    lines = [f'Cartões em {month:02d}/{year}:']
    for c in cards:
        u = c['usage']
        if u.get('vr_balance'):
            v = u['vr_balance']
            extra = f"saldo {brl(v['remaining'])} de {brl(v['available'])}"
        else:
            extra = f"fatura {brl(u['credit_total'])}" if c['supports_credit'] else f"débito {brl(u['debit_total'])}"
            if u.get('limit'):
                extra += f", {u['pct']:.0f}% do limite, disponível {brl(u['available'])}"
            if c['supports_credit']:
                extra += f", melhor dia {c['best_buy_day']}, vence dia {c['due_day']}"
        lines.append(f"• #{c['id']} {c['label']} ({c['owner_name']}): {extra}")
    return '\n'.join(lines)


@mcp.tool()
@_safe
def faturas_do_mes(mes: int | None = None, ano: int | None = None, cartao: str | None = None) -> str:
    """Valor das faturas de cartão de crédito do mês (sem duplicar cartões que dividem limite) e o total.
    `cartao`: nome, banco ou final do cartão para ver só aquela fatura ("cartão da Caixa" → cartao="Caixa").
    Use para "qual o valor do cartão de crédito de novembro?"."""
    month, year = _period(mes, ano)
    try:
        card = _resolve_card(cartao)
        filters = {'card_id': card['id']} if card else {}
    except FinFamError:  # não achou um cartão só: tenta pelo banco
        filters = {'bank': next((c['bank'] for c in _cards() if c['bank'] and _norm(cartao) in _norm(c['bank'])), cartao)}
    r = api.get('/cards/invoices', month=month, year=year, **filters)
    if not r['invoices']:
        return f'Nenhuma compra no crédito na fatura de {month:02d}/{year}.'
    lines = [f"Faturas de {r['month_name']}/{r['year']}: total {brl(r['total'])} "
             f"(pago {brl(r['paid_total'])}, falta {brl(r['pending_total'])})"]
    lines += [f"• {i['label']}: {brl(i['total'])} em {i['count']} compra(s)"
              f"{', vence ' + dm(i['due_date']) if i['due_date'] else ''} — {i['status']}" for i in r['invoices']]
    return '\n'.join(lines)


@mcp.tool()
@_safe
def fatura_do_cartao(cartao: str, mes: int | None = None, ano: int | None = None) -> str:
    """Detalhe da fatura de um cartão (nome, últimos 4 dígitos ou ID) no mês."""
    card = _resolve_card(cartao)
    month, year = _period(mes, ano)
    inv = api.get(f"/cards/{card['id']}/invoice", month=month, year=year)
    cr = inv['credit']
    lines = [f"Fatura {inv['card']['label']} — {inv['month_name']}/{inv['year']} ({inv['status']}), vence dia {inv['card']['due_day']}",
             f"Total {brl(cr['total'])} · pago {brl(cr['paid_total'])} · falta {brl(cr['pending_total'])} · "
             f"parcelas futuras {brl(cr['future_installments'])}"]
    lines += [f'  {_expense_line(e, "purchase_date")}' for e in cr['expenses']]
    if inv['debit']['expenses']:
        lines.append(f"Débito no mês: {brl(inv['debit']['total'])}")
    return '\n'.join(lines)


@mcp.tool()
@_safe
def rendas_do_mes(mes: int | None = None, ano: int | None = None) -> str:
    """Rendas (salários e variáveis) do mês, com o que já foi recebido."""
    month, year = _period(mes, ano)
    r = api.get('/incomes', month=month, year=year)
    rows = r['fixed'] + r['variable']
    if not rows:
        return f'Nenhuma renda lançada em {month:02d}/{year}.'
    lines = [f"Rendas {month:02d}/{year}: recebido {brl(r['received_total'])}, a receber {brl(r['pending_total'])}"]
    lines += [f"• #{s['id']} {s['user_name']} — {s['company'] or s['income_type']}: {brl(s['amount'])} "
              f"({'recebida' if s['received'] else 'a receber'}{', previsto ' + dm(s['expected_date']) if s['expected_date'] else ''})"
              for s in rows]
    return '\n'.join(lines)


STATUS_PT = {'pending': 'para revisar', 'duplicate': 'já no FinFam', 'ignored': 'ignorados', 'imported': 'lançados'}
_STATUS_IN = {_norm(v): k for k, v in STATUS_PT.items()} | {k: k for k in STATUS_PT} | {'revisar': 'pending', 'ignorado': 'ignored'}


@mcp.tool()
@_safe
def importados_do_banco(situacao: str = 'para revisar', limite: int = 20) -> str:
    """O que chegou dos bancos conectados (Open Finance/Pluggy): situação de cada banco e os lançamentos
    "para revisar" (padrão), "lançados", "já no FinFam" ou "ignorados" (pagamento de fatura etc.).
    Use para "chegou algo do banco?", "o que falta revisar?". Para aprovar, o usuário usa o site."""
    status = _STATUS_IN.get(_norm(situacao), 'pending')
    r = api.get('/pluggy/summary', status=status, limit=max(1, min(int(limite), 100)))
    c = r['counts']
    lines = [f"Bancos: {', '.join(b['name'] + (' (automático)' if b['auto_import'] else '') for b in r['banks']) or 'nenhum'}",
             f"Para revisar {c['pending']} · lançados {c['imported']} · já no FinFam {c['duplicate']} · ignorados {c['ignored']}"]
    if not r['items']:
        return '\n'.join(lines + [f'Nada em "{STATUS_PT[status]}".'])
    lines.append(f'{STATUS_PT[status].capitalize()}:')
    for i in r['items']:
        extra = f" · fatura {i['bill']}" if i['bill'] else ''
        why = f" — {i['reason']}" if status == 'ignored' and i['reason'] else ''
        lines.append(f"• {dm(i['date'])} {i['description']} {brl(i['amount'])} ({i['bank']}{extra}){why}")
    return '\n'.join(lines)


@mcp.tool()
@_safe
def conferir_fatura_com_banco(mes: int | None = None, ano: int | None = None) -> str:
    """Compara, por cartão conectado ao banco, o total da fatura do banco com o do FinFam no mês e lista
    o que está só de um lado. Use para "a fatura do C6 bate?", "por que o FinFam está diferente do banco?"."""
    month, year = _period(mes, ano)
    r = api.get('/pluggy/invoice-check', month=month, year=year)
    if not r['cards']:
        return 'Nenhum cartão conectado ao banco.'
    lines = [f'Faturas de {month:02d}/{year} — banco × FinFam:']
    for c in r['cards']:
        src = 'fatura do banco' if c['bank_source'] == 'fatura' else 'lançamentos até agora'
        if c['bank_source'] == 'sem_fatura':
            lines.append(f"• {c['label']}: o banco não informa em que fatura cada compra está; FinFam {brl(c['finfam_total'] or 0)}")
            continue
        if not c['linked']:
            lines.append(f"• {c['label']}: banco {brl(c['bank_total'])} ({src}) — cartão não ligado a um cartão do FinFam")
            continue
        status = 'bate ✅' if abs(c['diff']) < 0.01 else f"diferença {brl(c['diff'])}"
        lines.append(f"• {c['label']}: banco {brl(c['bank_total'])} ({src}) · FinFam {brl(c['finfam_total'])} — {status}")
        for i in c['only_bank'][:10]:
            lines.append(f"   só no banco (para revisar): {dm(i['date'])} {i['description']} {brl(i['amount'])}")
        for i in c['only_finfam'][:10]:
            lines.append(f"   só no FinFam: {dm(i['date'])} {i['description']} {brl(i['amount'])}")
    return '\n'.join(lines)


@mcp.tool()
@_safe
def alertas() -> str:
    """Alertas ativos: atrasos, limite de cartão, saldo negativo, metas em risco."""
    r = api.get('/alerts')
    if not r['alerts']:
        return 'Nenhum alerta. Está tudo em dia.'
    icon = {'danger': '🔴', 'warning': '🟡', 'info': 'ℹ️', 'success': '✅'}
    return '\n'.join(f"{icon.get(a['type'], '•')} {a['title']}: {a['message']}" for a in r['alerts'])


@mcp.tool()
@_safe
def metas() -> str:
    """Metas financeiras ativas com progresso e orientação."""
    r = api.get('/goals')
    if not r['goals']:
        return 'Nenhuma meta ativa.'
    return '\n'.join(f"• {g['emoji']} {g['title']} ({g['type_label']}): {brl(g['current'])} de {brl(g['target_amount'])} "
                     f"— {g['status_label']}. {g['coaching']}" for g in r['goals'])


@mcp.tool()
@_safe
def listas_de_referencia() -> str:
    """Pessoas da família, categorias, formas de pagamento e cartões aceitos nas ferramentas."""
    meta = api.get('/meta')
    opts = api.get('/expenses/options')
    lines = ['Pessoas: ' + ', '.join(m['name'] for m in _members()),
             'Categorias: ' + ', '.join(opts['categories']),
             'Formas de pagamento: ' + ', '.join(meta['payment_methods']),
             'Cartões: ' + ('; '.join(f"#{c['id']} {c['label']}" for c in _cards()) or 'nenhum')]
    return '\n'.join(lines)


# ── Ferramentas: ações ───────────────────────────────────────────────────────

@mcp.tool()
@_safe
def lancar_despesa(descricao: str, valor: float, forma_pagamento: str = 'PIX', categoria: str | None = None,
                   data: str | None = None, cartao: str | None = None, parcelas: int | None = None,
                   recorrente_meses: int | None = None, pessoa: str | None = None, pago: bool = False) -> str:
    """Lança uma despesa/compra.
    - forma_pagamento: PIX, Cartão de Crédito, Cartão de Débito, Dinheiro, VR ou VA (aceita "crédito", "débito").
    - categoria: se omitida, é sugerida pelas regras/histórico (ou "Outros").
    - data: AAAA-MM-DD da compra (padrão: hoje).
    - cartao: nome, últimos 4 dígitos ou ID (para crédito/débito/VR/VA).
    - parcelas: só no crédito; `valor` é o TOTAL da compra.
    - recorrente_meses: repete a despesa por N meses (ex.: assinatura)."""
    aliases = {'credito': 'Cartão de Crédito', 'cartao de credito': 'Cartão de Crédito',
               'debito': 'Cartão de Débito', 'cartao de debito': 'Cartão de Débito',
               'pix': 'PIX', 'dinheiro': 'Dinheiro', 'vr': 'VR', 'va': 'VA'}
    method = aliases.get(_norm(forma_pagamento), forma_pagamento)
    card = _resolve_card(cartao)
    if card and method == 'PIX':  # cartão informado sem forma de pagamento explícita
        method = 'Cartão de Crédito' if card['supports_credit'] else 'Cartão de Débito'
    if not categoria:
        sug = api.get('/expenses/suggest-category', description=descricao) if len(descricao.strip()) >= 3 else {}
        categoria = sug.get('category') or 'Outros'
    body = {'description': descricao, 'amount': f'{float(valor):.2f}', 'category': categoria,
            'payment_method': method, 'date': data, 'card_id': card['id'] if card else None,
            'installments': parcelas, 'recurring_months': recorrente_meses,
            'user_id': _resolve_person(pessoa), 'paid': pago}
    r = api.post('/expenses', {k: v for k, v in body.items() if v is not None})
    first = r['expenses'][0]
    info = [r['message'], f"Categoria: {first['category']}."]
    if first['month'] != first['purchase_month'] or first['year'] != first['purchase_year']:
        info.append(f"Entrou na fatura de {first['month']:02d}/{first['year']} (vence {dm(first['due_date'])}).")
    info.append('IDs: ' + ', '.join(f"#{e['id']}" for e in r['expenses'][:12]) + ('…' if len(r['expenses']) > 12 else ''))
    return ' '.join(info)


@mcp.tool()
@_safe
def marcar_como_pago(ids: list[int], pago: bool = True) -> str:
    """Marca despesas como pagas (ou pendentes com pago=false) pelos IDs."""
    return api.post('/expenses/pay', {'ids': ids, 'paid': pago})['message']


@mcp.tool()
@_safe
def pagar_fatura(cartao: str, mes: int | None = None, ano: int | None = None) -> str:
    """Marca como paga toda a fatura de crédito do cartão no mês (inclui cartões da mesma conta)."""
    card = _resolve_card(cartao)
    month, year = _period(mes, ano)
    return api.post(f"/cards/{card['id']}/invoice/pay", {'month': month, 'year': year})['message']


@mcp.tool()
@_safe
def editar_despesa(id: int, descricao: str | None = None, valor: float | None = None, categoria: str | None = None,
                   data: str | None = None, pago: bool | None = None, aplicar_em_todas: bool = False) -> str:
    """Altera uma despesa pelo ID. `aplicar_em_todas` propaga descrição/categoria às demais parcelas/recorrências."""
    body = {'description': descricao, 'amount': f'{float(valor):.2f}' if valor is not None else None,
            'category': categoria, 'date': data, 'paid': pago, 'apply_to_group': aplicar_em_todas}
    r = api.patch(f'/expenses/{id}', {k: v for k, v in body.items() if v is not None})
    return f"{r['message']} Agora: {_expense_line(r['expense'], 'purchase_date')}"


@mcp.tool()
@_safe
def excluir_despesa(id: int, confirmado: bool = False) -> str:
    """Exclui UMA despesa pelo ID. Só execute com confirmado=true DEPOIS que o usuário confirmar."""
    if not confirmado:
        return 'Confirme com o usuário e chame de novo com confirmado=true.'
    return api.delete(f'/expenses/{id}')['message']


@mcp.tool()
@_safe
def registrar_renda(valor: float, fonte: str | None = None, tipo: str = 'fixa', mes: int | None = None,
                    ano: int | None = None, recebida: bool = True, pessoa: str | None = None) -> str:
    """Registra uma renda. tipo: "fixa" (salário) ou "variavel" (freela, bônus...)."""
    month, year = _period(mes, ano)
    r = api.post('/incomes', {'amount': f'{float(valor):.2f}', 'company': fonte, 'month': month, 'year': year,
                              'income_type': 'variavel' if _norm(tipo).startswith('var') else 'fixa',
                              'received': recebida, 'user_id': _resolve_person(pessoa)})
    return r['message']


def main(argv: list[str] | None = None) -> None:
    import argparse
    parser = argparse.ArgumentParser(description='Servidor MCP do FinFam')
    parser.add_argument('--http', action='store_true', help='Usar Streamable HTTP em vez de stdio')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args(argv)
    import os
    if args.http and args.host not in ('127.0.0.1', '::1', 'localhost') \
            and os.environ.get('FINFAM_MCP_ALLOW_REMOTE') != '1':
        parser.error('O modo --http não tem autenticação própria: use --host 127.0.0.1 '
                     '(ou defina FINFAM_MCP_ALLOW_REMOTE=1 atrás de um proxy com autenticação).')
    if not args.http:
        mcp.run('stdio')
        return
    try:
        mcp.run('streamable-http', host=args.host, port=args.port)
    except TypeError:  # SDK 1.x: host/porta ficam nas settings
        mcp.settings.host, mcp.settings.port = args.host, args.port
        mcp.run('streamable-http')
