"""Regras de categorização automática (palavra-chave → categoria)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.db import db, get_or_404
from app.core.deps import Ctx, get_ctx, get_ctx_session
from app.models import CategoryRule
from app.services.categorizer import learn_from_history
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
