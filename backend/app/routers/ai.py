"""IA com a chave da própria família: configuração e interpretação de mensagens."""
import threading
import time
from collections import deque
from contextlib import contextmanager
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.db import db
from app.core.deps import Ctx, get_ctx, get_ctx_session, is_owner
from app.core.security import decrypt_field, encrypt_field
from app.models import AiConfig, CreditCard
from app.services import ai_service as ai
from app.services import expense_service as svc
from app.services.categorizer import categorize
from app.services.finance import PAYMENT_METHODS

router = APIRouter(tags=['ai'])


def _tenant_id(ctx: Ctx) -> int:
    if ctx.tenant is None:
        raise HTTPException(status_code=400, detail='Usuário sem família associada.')
    return ctx.tenant.id


def _config(ctx: Ctx) -> AiConfig | None:
    return AiConfig.query.filter_by(tenant_id=_tenant_id(ctx)).first()


def _mask(key: str | None) -> str:
    return f'••••{key[-4:]}' if key and len(key) > 12 else ('•' * 8 if key else '')


# ── Limite por família: no máximo 2 chamadas de IA ao mesmo tempo e 20 por minuto ──
_MAX_PARALLEL, _MAX_PER_MINUTE = 2, 20
_lock = threading.Lock()
_running: dict[int, int] = {}
_recent: dict[int, deque] = {}


@contextmanager
def _ai_slot(tenant_id: int):
    now = time.monotonic()
    with _lock:
        recent = _recent.setdefault(tenant_id, deque())
        while recent and now - recent[0] > 60:
            recent.popleft()
        if _running.get(tenant_id, 0) >= _MAX_PARALLEL or len(recent) >= _MAX_PER_MINUTE:
            raise HTTPException(status_code=429, detail='Muitas mensagens para a IA agora. Aguarde alguns segundos.')
        _running[tenant_id] = _running.get(tenant_id, 0) + 1
        recent.append(now)
    try:
        yield
    finally:
        with _lock:
            _running[tenant_id] -= 1


def _owner_only(ctx: Ctx) -> None:
    if not is_owner(ctx):
        raise HTTPException(status_code=403, detail='Só o dono da família altera a chave de IA.')


def _out(cfg: AiConfig | None) -> dict:
    providers = [{'id': k, **v} for k, v in ai.PROVIDERS.items()]
    if cfg is None:
        return {'configured': False, 'enabled': False, 'provider': None, 'model': None, 'base_url': None,
                'key_masked': '', 'providers': providers}
    return {'configured': True, 'enabled': bool(cfg.enabled), 'provider': cfg.provider, 'model': cfg.model,
            'base_url': cfg.base_url, 'key_masked': _mask(decrypt_field(cfg.api_key)), 'providers': providers}


class AiIn(BaseModel):
    provider: Literal['anthropic', 'gemini', 'openai']
    api_key: str | None = Field(None, max_length=300, description='Vazio = manter a atual')
    model: str | None = Field(None, max_length=80, pattern=r'^[A-Za-z0-9._:/@-]*$',
                              description='"/" só nos compatíveis (ex.: openai/gpt-4o-mini no OpenRouter)')
    base_url: str | None = Field(None, max_length=200, description='Só para compatíveis com OpenAI (https)')
    enabled: bool = True

    @field_validator('base_url')
    @classmethod
    def _https(cls, v):
        v = (v or '').strip().rstrip('/')
        if v and not v.startswith('https://'):
            raise ValueError('Use um endereço https://')
        return v or None

    @model_validator(mode='after')
    def _model_name(self):
        if self.model and ('..' in self.model or ('/' in self.model and self.provider != 'openai')):
            raise ValueError('Nome de modelo inválido.')
        return self


@router.get('/settings/ai', summary='Provedor de IA da família (a chave nunca é devolvida)')
def get_ai(ctx: Ctx = Depends(get_ctx)):
    return {**_out(_config(ctx)), 'can_edit': is_owner(ctx) and not ctx.via_api_token}


@router.put('/settings/ai', summary='Salva provedor, modelo e chave de IA da família')
def save_ai(body: AiIn, ctx: Ctx = Depends(get_ctx_session)):
    _owner_only(ctx)
    cfg = _config(ctx)
    key = (body.api_key or '').strip()
    base_url = body.base_url if body.provider == 'openai' else None
    if base_url:
        try:
            ai.check_public_url(base_url)
        except ai.AIError as exc:
            raise HTTPException(status_code=422, detail=f'Endereço: {exc}')
    if cfg is None:
        if not key:
            raise HTTPException(status_code=422, detail='Chave de API: informe a chave do provedor.')
        cfg = AiConfig(tenant_id=_tenant_id(ctx))
        db.session.add(cfg)
    elif (cfg.provider != body.provider or (cfg.base_url or None) != base_url) and not key:
        # a chave só vai para um destino novo se quem trocou o destino souber a chave
        raise HTTPException(status_code=422, detail='Chave de API: ao trocar de provedor ou endereço, informe a chave de novo.')
    if key:
        cfg.api_key = encrypt_field(key)
    cfg.provider, cfg.enabled = body.provider, body.enabled
    cfg.model = (body.model or '').strip() or None
    cfg.base_url = base_url
    db.session.commit()
    return {'message': 'IA configurada!', 'ai': _out(cfg)}


@router.delete('/settings/ai', summary='Remove a chave de IA da família')
def delete_ai(ctx: Ctx = Depends(get_ctx_session)):
    _owner_only(ctx)
    cfg = _config(ctx)
    if cfg:
        db.session.delete(cfg)
        db.session.commit()
    return {'message': 'Chave de IA removida.', 'ai': _out(None)}


def _client(ctx: Ctx) -> ai.AIClient:
    cfg = _config(ctx)
    if cfg is None or not cfg.enabled:
        raise HTTPException(status_code=409, detail={
            'code': 'ai_not_configured',
            'message': 'A IA não está configurada. No site, abra Telegram e IA e cadastre a chave da família.'})
    return ai.AIClient.from_config(cfg)


@router.post('/settings/ai/test', summary='Testa a chave de IA com uma frase de exemplo')
def test_ai(ctx: Ctx = Depends(get_ctx_session)):
    client = _client(ctx)
    try:
        with _ai_slot(_tenant_id(ctx)):
            client.complete_json('Responda só com o JSON {"ok": true}.', 'teste')
    except ai.AIError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {'message': f'A IA respondeu! ({ai.PROVIDERS[client.provider]["label"]}, {client.model})'}


class InterpretIn(BaseModel):
    text: str = Field(min_length=2, max_length=500)


def _card_kind(c: CreditCard) -> str:
    kinds = [k for k, ok in (('crédito', c.supports_credit), ('débito', c.supports_debit)) if ok]
    return c.card_type.upper() if c.card_type in ('vr', 'va') else '/'.join(kinds) or c.card_type


def draft_out(d: ai.Draft, cards: dict[int, CreditCard], ctx: Ctx) -> dict:
    card = cards.get(d.card_id) if d.card_id else None
    person = next((u.name for u in ctx.members if u.id == d.user_id), None)
    return {'description': d.description, 'amount': float(d.amount) if d.amount else None,
            'payment_method': d.payment_method, 'category': d.category, 'date': d.date.isoformat(),
            'card_id': d.card_id, 'card_label': card.label if card else None, 'installments': d.installments,
            'user_id': d.user_id, 'user_name': person, 'missing': d.missing, 'body': d.as_body()}


@router.post('/ai/interpret', summary='Entende uma frase ("gastei 50 no mercado no crédito") — não grava nada')
def interpret(body: InterpretIn, ctx: Ctx = Depends(get_ctx)):
    client = _client(ctx)
    today = date.today()
    categories = svc.tenant_categories(ctx.uids)
    cards = {c.id: c for c in CreditCard.query.filter(CreditCard.user_id.in_(ctx.uids)).all()}
    card_list = [{'id': c.id, 'label': c.label, 'kind': _card_kind(c), 'supports_credit': bool(c.supports_credit)}
                 for c in cards.values()]
    prompt = ai.build_prompt(today=today, categories=categories, methods=PAYMENT_METHODS, cards=card_list,
                             members=[{'id': u.id, 'name': u.name} for u in ctx.members], me=ctx.user.name)
    try:
        with _ai_slot(_tenant_id(ctx)):
            result = ai.interpret(client, body.text.strip(), prompt)
    except ai.AIError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    def suggest(description: str) -> str | None:
        found, _ = categorize(description, ctx.uids, ctx.tenant_id) if ctx.tenant_id else (None, None)
        return found or svc.suggest_category_from_history(description, ctx.uids)

    out = {'intent': result['intent'], 'reply': result['reply'], 'draft': None}
    if result['intent'] == 'expense':
        draft = ai.to_draft(result['expense'], today=today, categories=categories, methods=PAYMENT_METHODS,
                            cards=card_list, member_ids=ctx.uids, suggest=suggest)
        out['draft'] = draft_out(draft, cards, ctx)
    return out
