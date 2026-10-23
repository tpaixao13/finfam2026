"""Família (tenant): nome do grupo e membros, respeitando o limite do plano."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.core.types import Email
from app.core.db import db, get_or_404
from app.core.deps import Ctx, get_ctx
from app.models import User
from app.services.finance import user_color_map
from app.services.serializers import tenant_out, user_out

router = APIRouter(prefix='/family', tags=['family'])


class RenameIn(BaseModel):
    name: str = Field(min_length=2, max_length=60)


class MemberIn(BaseModel):
    name: str = Field(min_length=2, max_length=30)
    email: Email
    password: str = Field(min_length=8)
    confirm_password: str

    @model_validator(mode='after')
    def _match(self):
        if self.password != self.confirm_password:
            raise ValueError('As senhas não coincidem.')
        return self


class MemberUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=30)
    email: Email
    new_password: str | None = Field(default=None, min_length=8)
    confirm_password: str | None = None

    @model_validator(mode='after')
    def _match(self):
        if self.new_password and self.new_password != self.confirm_password:
            raise ValueError('As senhas não coincidem.')
        return self


def _require_tenant(ctx: Ctx):
    if ctx.tenant is None:
        raise HTTPException(status_code=400, detail='Usuário sem família associada.')
    return ctx.tenant


@router.get('', summary='Família, membros e limite do plano')
def get_family(ctx: Ctx = Depends(get_ctx)):
    colors = user_color_map(ctx.members)
    return {'tenant': tenant_out(ctx.tenant),
            'members': [user_out(u, colors[u.id]) for u in ctx.members],
            'member_count': len(ctx.members),
            'member_limit': ctx.tenant.member_limit if ctx.tenant else 1}


@router.patch('', summary='Renomeia a família')
def rename(body: RenameIn, ctx: Ctx = Depends(get_ctx)):
    tenant = _require_tenant(ctx)
    tenant.name = body.name.strip()
    db.session.commit()
    return {'message': 'Nome do grupo atualizado!', 'tenant': tenant_out(tenant)}


@router.post('/members', status_code=201, summary='Adiciona um membro')
def add_member(body: MemberIn, ctx: Ctx = Depends(get_ctx)):
    tenant = _require_tenant(ctx)
    if len(ctx.members) >= tenant.member_limit:
        raise HTTPException(status_code=403, detail=(
            f'Limite do plano {tenant.plan_label} atingido ({tenant.member_limit} usuários). '
            'Faça upgrade para adicionar mais membros.'))
    email = body.email.strip().lower()
    if User.query.filter_by(email=email).first():
        raise HTTPException(status_code=409, detail='E-mail já cadastrado.')
    user = User(name=body.name.strip(), email=email, tenant_id=tenant.id)
    user.set_password(body.password)
    db.session.add(user)
    db.session.commit()
    return {'message': f'{user.name} adicionado com sucesso!', 'member': user_out(user)}


@router.put('/members/{user_id}', summary='Edita nome, e-mail e (opcional) senha de um membro')
def edit_member(user_id: int, body: MemberUpdate, ctx: Ctx = Depends(get_ctx)):
    tenant = _require_tenant(ctx)
    user = get_or_404(User.query.filter_by(id=user_id, tenant_id=tenant.id), 'Membro não encontrado.')
    email = body.email.strip().lower()
    other = User.query.filter_by(email=email).first()
    if other and other.id != user.id:
        raise HTTPException(status_code=409, detail='E-mail já está em uso por outro membro.')
    user.name = body.name.strip()
    user.email = email
    if body.new_password:
        user.set_password(body.new_password)
    db.session.commit()
    return {'message': f'Dados de {user.name} atualizados!', 'member': user_out(user)}


@router.delete('/members/{user_id}', summary='Remove um membro (e todos os dados dele)')
def delete_member(user_id: int, ctx: Ctx = Depends(get_ctx)):
    tenant = _require_tenant(ctx)
    if user_id == ctx.user.id:
        raise HTTPException(status_code=422, detail='Você não pode remover a si mesmo.')
    user = get_or_404(User.query.filter_by(id=user_id, tenant_id=tenant.id), 'Membro não encontrado.')
    name = user.name
    db.session.delete(user)
    db.session.commit()
    return {'message': f'{name} removido do grupo.'}
