"""
categorizer.py — Engine de categorização automática.

Prioridade:
  1. Regras do tenant (keyword → categoria, case-insensitive)
  2. Histórico de despesas (padrão nas descrições passadas)
  3. None (usuário escolhe)

Retorna (categoria, fonte) onde fonte é 'rule' | 'history' | None.
"""

from sqlalchemy import func
from app.core.db import db
from app.models import CategoryRule, Expense


def categorize(description: str, uids: list, tenant_id: int) -> tuple[str | None, str | None]:
    """
    Retorna (category, source).
    source: 'rule' | 'history' | None
    """
    desc_upper = description.upper()

    # ── 1. Regras do tenant ──────────────────────────────────────────
    rules = (CategoryRule.query
             .filter_by(tenant_id=tenant_id)
             .order_by(CategoryRule.match_count.desc(), CategoryRule.keyword)
             .all())
    for rule in rules:
        if rule.keyword.upper() in desc_upper:
            return rule.category, 'rule'

    # ── 2. Histórico: categoria mais frequente para descrições similares ──
    # Usa os primeiros 20 caracteres significativos como chave de busca
    term = '%' + description[:20].lower().strip() + '%'
    row = (db.session.query(Expense.category, func.count('*').label('cnt'))
           .filter(
               Expense.user_id.in_(uids),
               func.lower(Expense.description).like(term),
           )
           .group_by(Expense.category)
           .order_by(func.count('*').desc())
           .first())
    if row:
        return row[0], 'history'

    return None, None


def categorize_batch(descriptions: list[str], uids: list,
                     tenant_id: int) -> list[tuple[str | None, str | None]]:
    """Categoriza uma lista de descrições de uma vez."""
    return [categorize(d, uids, tenant_id) for d in descriptions]


def bump_rule_count(keyword: str, tenant_id: int) -> None:
    """Incrementa o contador de uso de uma regra."""
    rule = CategoryRule.query.filter_by(
        tenant_id=tenant_id,
        keyword=keyword.upper(),
    ).first()
    if rule:
        rule.match_count += 1
        db.session.commit()


def create_rule_if_missing(keyword: str, category: str, tenant_id: int) -> bool:
    """
    Cria uma nova regra se não existir outra para o mesmo keyword.
    Retorna True se criou, False se já existia.
    """
    keyword = keyword.upper().strip()
    if not keyword:
        return False
    existing = CategoryRule.query.filter_by(
        tenant_id=tenant_id, keyword=keyword,
    ).first()
    if existing:
        return False
    db.session.add(CategoryRule(
        tenant_id=tenant_id,
        keyword=keyword,
        category=category,
    ))
    db.session.commit()
    return True


def is_duplicate(user_id: int, year: int, month: int, day: int,
                 amount: float, description: str) -> bool:
    """
    Verifica se já existe uma despesa com mesmos atributos chave.
    Usa os primeiros 40 caracteres da descrição como fingerprint.
    """
    desc_prefix = description[:40]
    return db.session.query(
        db.session.query(Expense)
        .filter(
            Expense.user_id == user_id,
            Expense.year == year,
            Expense.month == month,
            Expense.day == day,
            Expense.amount == round(amount, 2),
            Expense.description.like(desc_prefix + '%'),
        ).exists()
    ).scalar()
