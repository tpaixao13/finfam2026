"""Importação de extratos/faturas: parse → pré-visualização → confirmação."""
from dataclasses import asdict

from app.core.db import db
from app.models import Expense
from app.services.categorizer import categorize_batch, create_rule_if_missing, is_duplicate

FORMATS = {
    'c6pdf': 'Fatura C6 Bank (PDF)',
    'ofx': 'Extrato OFX (qualquer banco)',
    'nubank_csv': 'Nubank (CSV)',
    'csv_generic': 'CSV genérico',
}


class ImportError_(ValueError):
    pass


def parse_file(fmt: str, file_bytes: bytes, pdf_password: str | None = None):
    """Retorna (transações, banco_sugerido)."""
    if fmt == 'c6pdf':
        from app.importers.c6 import parse_c6_pdf
        return parse_c6_pdf(file_bytes, password=pdf_password or None), 'C6 Bank'
    if fmt == 'ofx':
        from app.importers.ofx import parse_ofx
        return parse_ofx(file_bytes), None
    if fmt == 'nubank_csv':
        from app.importers.nubank import parse_nubank_csv
        return parse_nubank_csv(file_bytes), 'Nubank'
    if fmt == 'csv_generic':
        from app.importers.csv_generic import parse_csv_generic
        return parse_csv_generic(file_bytes), None
    raise ImportError_('Formato não reconhecido.')


def preview(fmt: str, file_bytes: bytes, user_id: int, uids: list, tenant_id: int,
            bank: str | None = None, pdf_password: str | None = None) -> dict:
    try:
        transactions, bank_hint = parse_file(fmt, file_bytes, pdf_password)
    except ImportError_:
        raise
    except Exception as exc:
        msg = str(exc).lower()
        if any(w in msg for w in ('password', 'encrypt', 'decrypt')):
            raise ImportError_('O PDF está protegido por senha. Informe a senha correta.')
        raise ImportError_(f'Erro ao processar o arquivo: {exc}')

    cats = categorize_batch([t.description for t in transactions], uids, tenant_id)
    items = []
    for i, (t, (category, source)) in enumerate(zip(transactions, cats)):
        row = asdict(t)
        row.update({
            'index': i, 'user_id': user_id, 'bank': bank_hint or bank or None,
            'category': category or 'Outros', 'category_source': source,
            'duplicate': is_duplicate(user_id, t.year, t.month, t.day, t.amount, t.description),
        })
        items.append(row)
    return {'items': items, 'bank': bank_hint or bank or None,
            'total': round(sum(t.amount for t in transactions), 2), 'count': len(items)}


def confirm(items: list[dict], uids: list, tenant_id: int | None) -> dict:
    """Grava os itens confirmados. Cada item pode pedir criação de regra (`create_rule`)."""
    count = 0
    for it in items:
        if it['user_id'] not in uids or it['amount'] <= 0 or not it['description'].strip():
            continue
        db.session.add(Expense(
            user_id=it['user_id'], description=it['description'].strip()[:200], amount=it['amount'],
            category=it.get('category') or 'Outros', payment_method=it.get('payment_method') or 'PIX',
            bank=it.get('bank') or None, year=it['year'], month=it['month'], day=it['day'],
        ))
        count += 1
    db.session.commit()

    rules = 0
    if tenant_id:
        for it in items:
            if not it.get('create_rule'):
                continue
            words = [w for w in it['description'].upper().split() if len(w) >= 3]
            if words and it.get('category') and create_rule_if_missing(words[0][:30], it['category'], tenant_id):
                rules += 1
    return {'imported': count, 'rules_created': rules}


def import_finfam_xlsx(file_obj, members: list) -> dict:
    """Importa um .xlsx exportado pelo próprio FinFam (colunas na ordem do export)."""
    import openpyxl
    try:
        wb = openpyxl.load_workbook(filename=file_obj, read_only=True, data_only=True)
        rows = list(wb.active.iter_rows(min_row=2, values_only=True))
    except Exception as exc:
        raise ImportError_(f'Erro ao ler o arquivo: {exc}')

    by_name = {u.name.strip().lower(): u for u in members}
    imported = skipped = 0
    for row in rows:
        if not row or not any(row):
            continue
        try:
            nome, dia, mes, ano, descricao, categoria, pagamento, banco, valor = (tuple(row) + (None,) * 9)[:9]
            user = by_name.get(str(nome or '').strip().lower())
            if isinstance(valor, (int, float)):
                amount = float(valor)
            else:
                amount = float(str(valor).replace('R$', '').strip().replace('.', '').replace(',', '.'))
            dia, mes, ano = int(dia), int(mes), int(ano)
            if not user or amount <= 0 or not (1 <= mes <= 12 and 2000 <= ano <= 2100 and 1 <= dia <= 31):
                skipped += 1
                continue
            db.session.add(Expense(
                user_id=user.id, description=str(descricao or '').strip()[:200], amount=amount,
                category=str(categoria or 'Outros').strip(), payment_method=str(pagamento or 'PIX').strip(),
                bank=str(banco or '').strip() or None, year=ano, month=mes, day=dia,
            ))
            imported += 1
        except (TypeError, ValueError):
            skipped += 1
    db.session.commit()
    return {'imported': imported, 'skipped': skipped}
