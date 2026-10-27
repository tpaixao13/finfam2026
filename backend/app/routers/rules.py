"""Regras de categorização automática (palavra-chave → categoria)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.core import ratelimit
from app.core.db import db, get_or_404
from app.core.deps import Ctx, get_ctx, get_ctx_session
from app.models import CategoryRule
from app.services.categorizer import learn_from_history, rule_changes
from app.services.serializers import iso

router = APIRouter(prefix='/rules', tags=['rules'])


class RuleIn(BaseModel):
    keyword: str = Field(min_length=2, max_length=60)
    category: str = Field(min_length=1, max_length=50)


def _out(r: CategoryRule) -> dict:
    return {'id': r.id, 'keyword': r.keyword, 'category': r.category,
            'match_count': r.match_count, 'created_at': iso(r.created_at)}


def _tid(ctx: Ctx) -> int:
    if not ctx.tenant_id:
        raise HTTPException(status_code=400, detail='Usuário sem família associada.')
    return ctx.tenant_id


def _conflict(tid: int, keyword: str, exclude_id: int | None = None) -> bool:
    q = CategoryRule.query.filter(CategoryRule.tenant_id == tid, CategoryRule.keyword == keyword)
    if exclude_id:
        q = q.filter(CategoryRule.id != exclude_id)
    return q.first() is not None


@router.get('', summary='Lista regras da família')
def list_rules(ctx: Ctx = Depends(get_ctx)):
    rules = (CategoryRule.query.filter_by(tenant_id=_tid(ctx))
             .order_by(CategoryRule.match_count.desc(), CategoryRule.keyword).all())
    return [_out(r) for r in rules]


@router.post('', status_code=201, summary='Cria regra')
def create(body: RuleIn, ctx: Ctx = Depends(get_ctx)):
    tid, keyword = _tid(ctx), body.keyword.strip().upper()
    if _conflict(tid, keyword):
        raise HTTPException(status_code=409, detail=f'Já existe uma regra para "{keyword}".')
    rule = CategoryRule(tenant_id=tid, keyword=keyword, category=body.category.strip())
    db.session.add(rule)
    db.session.commit()
    return {'message': f'Regra criada: "{keyword}" → {rule.category}', 'rule': _out(rule)}


@router.post('/learn', summary='Cria regras a partir das despesas já lançadas pela família')
def learn(ctx: Ctx = Depends(get_ctx_session)):
    created = learn_from_history(ctx.uids, _tid(ctx))
    db.session.commit()
    return {'created': created, 'message': f'{created} regra(s) criada(s) pelo histórico.' if created
            else 'Nenhuma regra nova: o histórico não tem padrões claros que já não estejam nas regras.'}


class ApplyIn(BaseModel):
    only_others: bool = Field(True, description='Só despesas em "Outros" (falso = todas que alguma regra cobre)')
    dry_run: bool = Field(True, description='Só mostra o que mudaria, sem gravar')


@router.post('/apply', summary='Aplica as regras às despesas já lançadas (com prévia)')
def apply(body: ApplyIn, request: Request, ctx: Ctx = Depends(get_ctx_session)):
    ratelimit.check(request, 'rules-apply', str(ctx.user.id), limit=30, window=60)  # percorre todas as despesas
    changes = rule_changes(ctx.uids, _tid(ctx), body.only_others)
    sample = [{'id': e.id, 'description': e.description, 'date': f'{e.purchase_year or e.year}-{(e.purchase_month or e.month):02d}-{e.day:02d}',
               'amount': float(e.amount), 'from': e.category, 'to': cat, 'keyword': rule.keyword}
              for e, cat, rule in changes[:200]]
    if body.dry_run:
        return {'count': len(changes), 'sample': sample,
                'message': f'{len(changes)} despesa(s) mudariam de categoria.' if changes else 'Nenhuma despesa muda com as regras atuais.'}
    for e, cat, rule in changes:
        e.category = cat
        rule.match_count = (rule.match_count or 0) + 1
    db.session.commit()
    return {'count': len(changes), 'sample': sample, 'message': f'{len(changes)} despesa(s) atualizada(s) pelas regras.'}


@router.put('/{rule_id}', summary='Edita regra')
def update(rule_id: int, body: RuleIn, ctx: Ctx = Depends(get_ctx)):
    tid, keyword = _tid(ctx), body.keyword.strip().upper()
    rule = get_or_404(CategoryRule.query.filter_by(id=rule_id, tenant_id=tid), 'Regra não encontrada.')
    if _conflict(tid, keyword, exclude_id=rule.id):
        raise HTTPException(status_code=409, detail=f'Já existe outra regra para "{keyword}".')
    rule.keyword, rule.category = keyword, body.category.strip()
    db.session.commit()
    return {'message': 'Regra atualizada com sucesso.', 'rule': _out(rule)}


@router.delete('/{rule_id}', summary='Exclui regra')
def delete(rule_id: int, ctx: Ctx = Depends(get_ctx)):
    rule = get_or_404(CategoryRule.query.filter_by(id=rule_id, tenant_id=_tid(ctx)), 'Regra não encontrada.')
    keyword = rule.keyword
    db.session.delete(rule)
    db.session.commit()
    return {'message': f'Regra "{keyword}" excluída.'}
