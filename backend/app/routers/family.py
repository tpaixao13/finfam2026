"""Família (tenant): nome do grupo e membros, respeitando o limite do plano."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from app.core import ratelimit
from app.core.types import Email
from app.core.db import db, get_or_404
from app.core.deps import Ctx, get_ctx, get_ctx_session, is_owner
from app.models import User
from app.services import account_mail
from app.services.finance import user_color_map
from app.services.serializers import tenant_out, user_out

router = APIRouter(prefix='/family', tags=['family'])


class RenameIn(BaseModel):
    name: str = Field(min_length=2, max_length=60)


class MemberIn(BaseModel):
    name: str = Field(min_length=2, max_length=30)
    email: Email
    password: str = Field(min_length=8, max_length=256)
    confirm_password: str = Field(max_length=256)

    @model_validator(mode='after')
    def _match(self):
        if self.password != self.confirm_password:
            raise ValueError('As senhas não coincidem.')
        return self


class MemberUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=30)
    email: Email
    new_password: str | None = Field(default=None, min_length=8, max_length=256)
    confirm_password: str | None = Field(None, max_length=256)
    current_password: str | None = Field(None, max_length=256, description='Obrigatória ao trocar a própria senha ou e-mail')

    @model_validator(mode='after')
    def _match(self):
        if self.new_password and self.new_password != self.confirm_password:
            raise ValueError('As senhas não coincidem.')
        return self


def _require_tenant(ctx: Ctx):
    if ctx.tenant is None:
        raise HTTPException(status_code=400, detail='Usuário sem família associada.')
    return ctx.tenant


def _require_owner(ctx: Ctx, action: str):
    if not is_owner(ctx):
        raise HTTPException(status_code=403, detail=f'Só quem criou a família pode {action}.')


@router.get('', summary='Família, membros e limite do plano')
def get_family(ctx: Ctx = Depends(get_ctx)):
    colors = user_color_map(ctx.members)
    return {'tenant': tenant_out(ctx.tenant),
            'members': [user_out(u, colors[u.id]) for u in ctx.members],
            'member_count': len(ctx.members),
            'owner_id': min(u.id for u in ctx.members) if ctx.members else None,
            'member_limit': ctx.tenant.member_limit if ctx.tenant else 1}


@router.patch('', summary='Renomeia a família')
def rename(body: RenameIn, ctx: Ctx = Depends(get_ctx_session)):
    tenant = _require_tenant(ctx)
    _require_owner(ctx, 'renomear a família')
    tenant.name = body.name.strip()
    db.session.commit()
    return {'message': 'Nome do grupo atualizado!', 'tenant': tenant_out(tenant)}


@router.post('/members', status_code=201, summary='Adiciona um membro')
def add_member(body: MemberIn, request: Request, ctx: Ctx = Depends(get_ctx_session)):
    tenant = _require_tenant(ctx)
    _require_owner(ctx, 'adicionar pessoas')  # quem entra vê tudo da família
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
    db.session.flush()
    sent = account_mail.start(user, request, invited_by=ctx.user.name)
    db.session.commit()
    msg = f'{user.name} adicionado com sucesso!'
    if sent:
        msg += f' Enviamos um e-mail de boas-vindas para {email}: {user.name} entra depois de confirmar.'
    return {'message': msg, 'member': user_out(user)}


@router.post('/members/{user_id}/resend', summary='Manda de novo o e-mail de confirmação de um membro')
def resend_member(user_id: int, request: Request, ctx: Ctx = Depends(get_ctx_session)):
    tenant = _require_tenant(ctx)
    _require_owner(ctx, 'reenviar o convite')
    user = get_or_404(User.query.filter_by(id=user_id, tenant_id=tenant.id), 'Membro não encontrado.')
    if not account_mail.needs_verification(user):
        return {'message': f'O e-mail de {user.name} já está confirmado.'}
    ratelimit.check(request, 'member-resend', str(user.id), limit=3, window=3600)
    account_mail.send_verification(user, request, invited_by=ctx.user.name)
    return {'message': f'E-mail de confirmação reenviado para {user.email}.'}


@router.put('/members/{user_id}', summary='Edita nome, e-mail e (opcional) senha de um membro')
def edit_member(user_id: int, body: MemberUpdate, request: Request, ctx: Ctx = Depends(get_ctx_session)):
    tenant = _require_tenant(ctx)
    user = get_or_404(User.query.filter_by(id=user_id, tenant_id=tenant.id), 'Membro não encontrado.')
    email = body.email.strip().lower()
    changes_credentials = bool(body.new_password) or email != (user.email or '')
    if user.id == ctx.user.id:
        if changes_credentials and not ctx.user.check_password(body.current_password or ''):
            raise HTTPException(status_code=422, detail='Informe sua senha atual para trocar e-mail ou senha.')
    else:
        _require_owner(ctx, 'alterar os dados de outra pessoa')
    other = User.query.filter_by(email=email).first()
    if other and other.id != user.id:
        raise HTTPException(status_code=409, detail='E-mail já está em uso por outro membro.')
    email_changed = email != (user.email or '')
    user.name = body.name.strip()
    user.email = email
    revoked = user.change_password(body.new_password) if body.new_password else 0
    sent = account_mail.start(user, request, invited_by=None if user.id == ctx.user.id else ctx.user.name) \
        if email_changed else False
    db.session.commit()
    msg = f'Dados de {user.name} atualizados!'
    if revoked:
        msg += ' Telegram e tokens de API foram desconectados: conecte de novo.'
    if sent:
        msg += f' Enviamos um link para confirmar o novo e-mail ({email}); o próximo login só vale depois de confirmar.'
    return {'message': msg, 'member': user_out(user)}


@router.delete('/members/{user_id}', summary='Remove um membro (e todos os dados dele)')
def delete_member(user_id: int, ctx: Ctx = Depends(get_ctx_session)):
    tenant = _require_tenant(ctx)
    if user_id == ctx.user.id:
        raise HTTPException(status_code=422, detail='Você não pode remover a si mesmo.')
    _require_owner(ctx, 'remover pessoas')
    user = get_or_404(User.query.filter_by(id=user_id, tenant_id=tenant.id), 'Membro não encontrado.')
    name = user.name
    from app.services.pluggy_keys import release_key
    release_key(user.id)  # quem usa a chave Pluggy dela continua usando
    db.session.delete(user)
    db.session.commit()
    return {'message': f'{name} removido do grupo.'}
