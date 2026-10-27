"""
categorizer.py — Engine de categorização automática.

Prioridade:
  1. Regras do tenant (keyword → categoria, case-insensitive)
  2. Histórico de despesas (padrão nas descrições passadas)
  3. None (usuário escolhe)

Retorna (categoria, fonte) onde fonte é 'rule' | 'history' | None.
"""

import re
from collections import Counter, defaultdict

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


# ── Aprender regras ──────────────────────────────────────────────────────────

# palavras que não identificam o estabelecimento (prefixos de maquininha, cidade, banco, marketplace)
_GENERIC = set('''
DL EC IFD MP PG PAG PAGTO PGTO PAGAMENTO PAGAMENTOS PIX TED DOC TRANSF TRANSFERENCIA TRANSFERÊNCIA COMPRA COMPRAS
CARTAO CARTÃO DEBITO DÉBITO CREDITO CRÉDITO DEB CRED BRA BRASIL SAO SÃO PAULO RIO JANEIRO DIADEMA SANTO ANDRE
BERNARDO CAMPO CAMPOS BELO HORIZONTE LTDA EIRELI COMERCIO COMÉRCIO SERVICOS SERVIÇOS DAS DOS PARC PARCELA
BOLETO TARIFA ESTORNO REFUND APLICACAO APLICAÇÃO THE AND LOJA LOJAS SHOP STORE ONLINE WWW COM NET
MERCADOLIVRE MERCADOPAGO MERCADO PAYPAL PICPAY AMAZON SHOPEE ALIEXPRESS SHEIN PAGSEGURO PAGBANK STONE CIELO
SUMUP REDE GETNET INFINITEPAY TON ASAAS SUPER ATACADO
'''.split())
_TOKEN = re.compile(r'[0-9A-ZÀ-Ü]+')


def keyword_of(description: str) -> str | None:
    """A palavra que identifica o estabelecimento: "DL*UBERRIDES" → UBERRIDES, "COOP DIADEMA BRA" → COOP,
    "99APP *99APP SAO PAULO" → 99APP. Nada útil (ex.: "DÉBITO DE CARTÃO") → None."""
    for tok in _TOKEN.findall((description or '').upper()):
        if len(tok) >= 3 and not tok.isdigit() and tok not in _GENERIC:
            return tok[:30]
    return None


def _first_rule(description: str, tenant_id: int) -> CategoryRule | None:
    desc_upper = (description or '').upper()
    for rule in (CategoryRule.query.filter_by(tenant_id=tenant_id)
                 .order_by(CategoryRule.match_count.desc(), CategoryRule.keyword).all()):
        if rule.keyword.upper() in desc_upper:
            return rule
    return None


def count_rule_use(tenant_id: int | None, description: str, category: str) -> None:
    """Despesa salva com a categoria que a regra daria: +1 em "Usos" (sem commit)."""
    if not tenant_id or not category:
        return
    rule = _first_rule(description, tenant_id)
    if rule is not None and rule.category == category:
        rule.match_count = (rule.match_count or 0) + 1


def learn_rule(tenant_id: int | None, description: str, category: str) -> CategoryRule | None:
    """A pessoa escolheu outra categoria: cria (ou corrige) a regra da palavra principal (sem commit)."""
    keyword = keyword_of(description)
    if not tenant_id or not keyword or not category or category == 'Outros':
        return None
    rule = CategoryRule.query.filter_by(tenant_id=tenant_id, keyword=keyword).first()
    if rule is None:
        rule = CategoryRule(tenant_id=tenant_id, keyword=keyword, category=category[:50])
        db.session.add(rule)
    elif rule.category != category:
        rule.category = category[:50]
    db.session.flush()
    return rule


def learn_from_history(uids: list, tenant_id: int, min_count: int = 3, share: float = 0.8) -> int:
    """Cria regras a partir das despesas da família: palavra que aparece em 3+ despesas e em 80%+
    delas com a mesma categoria (fora "Outros"). Não mexe em regra que já existe. Devolve quantas criou."""
    by_word: dict[str, Counter] = defaultdict(Counter)
    rows = db.session.query(Expense.description, Expense.category).filter(Expense.user_id.in_(uids or [0])).all()
    for desc, cat in rows:
        word = keyword_of(desc or '')
        if word and cat:
            by_word[word][cat] += 1
    have = {r.keyword.upper() for r in CategoryRule.query.filter_by(tenant_id=tenant_id).all()}
    created = 0
    for word, cats in sorted(by_word.items()):
        total = sum(cats.values())
        cat, n = cats.most_common(1)[0]
        if total >= min_count and n >= share * total and cat != 'Outros' and word not in have:
            db.session.add(CategoryRule(tenant_id=tenant_id, keyword=word, category=cat[:50], match_count=n))
            created += 1
    db.session.flush()
    return created


def rule_changes(uids: list, tenant_id: int, only_others: bool = True) -> list[tuple[Expense, str, CategoryRule]]:
    """Despesas da família que as regras mudariam: (despesa, categoria nova, regra). A 1ª regra que casa
    vale, na mesma ordem da sugestão. only_others: só as que estão em "Outros" (ou sem categoria)."""
    rules = (CategoryRule.query.filter_by(tenant_id=tenant_id)
             .order_by(CategoryRule.match_count.desc(), CategoryRule.keyword).all())
    if not rules:
        return []
    q = Expense.query.filter(Expense.user_id.in_(uids or [0]))
    if only_others:
        q = q.filter((Expense.category == 'Outros') | (Expense.category.is_(None)) | (Expense.category == ''))
    out = []
    for e in q.all():
        desc = (e.description or '').upper()
        rule = next((r for r in rules if r.keyword.upper() in desc), None)
        if rule is not None and rule.category != e.category:
            out.append((e, rule.category, rule))
    return out
