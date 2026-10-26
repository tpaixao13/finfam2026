"""Open Finance via Pluggy — etapa 1: chave de cada pessoa e recebimento do webhook.

- O admin libera por família (tenants.open_finance_enabled).
- Cada PESSOA cadastra o Client ID / Client Secret da própria conta Pluggy (só pelo
  login do site; o segredo nunca volta). Ao salvar, o FinFam testa a chave e cria na
  conta Pluggy da pessoa um webhook exclusivo: /pluggy/webhook/<hook_token>, com um
  cabeçalho secreto. O token na URL diz de quem é o aviso; o cabeçalho e o IP da
  Pluggy confirmam a origem.
- Importação, contas e revisão: próximas etapas.
"""
import hmac
import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.db import db
from app.core.deps import Ctx, get_ctx_session
from app.core.security import decrypt_field, encrypt_field
from app.core.types import utcnow
from app.models import PluggyConfig, Tenant, User
from app.services import pluggy as pg

log = logging.getLogger('finfam.pluggy')
router = APIRouter(prefix='/pluggy', tags=['open finance'])

UUID = r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'


def _enabled_tenant(ctx: Ctx) -> Tenant:
    if ctx.tenant is None or not ctx.tenant.open_finance_enabled:
        raise HTTPException(status_code=403, detail={
            'code': 'open_finance_disabled',
            'message': 'Open Finance não está liberado para a sua família. Fale com o administrador.'})
    return ctx.tenant


def webhook_url(cfg: PluggyConfig) -> str | None:
    base = settings.public_url
    return f'{base}/api/v1/pluggy/webhook/{cfg.hook_token}' if base.startswith('https://') else None


def _mask(client_id: str) -> str:
    return f'{client_id[:8]}-••••-{client_id[-4:]}' if client_id else ''


def _out(ctx: Ctx, cfg: PluggyConfig | None) -> dict:
    tenant = ctx.tenant
    return {
        'available': bool(tenant and tenant.open_finance_enabled),
        'max_items': tenant.open_finance_max_items if tenant else None,
        'configured': cfg is not None,
        'client_id_masked': _mask(cfg.client_id) if cfg else '',
        'status': cfg.status if cfg else None,
        'last_error': cfg.last_error if cfg else None,
        'webhook': bool(cfg and cfg.webhook_id),
        'public_url_ok': settings.public_url.startswith('https://'),
    }


@router.get('/config', summary='Minha chave Pluggy (o segredo nunca é devolvido)')
def get_config(ctx: Ctx = Depends(get_ctx_session)):
    cfg = PluggyConfig.query.filter_by(user_id=ctx.user.id).first()
    return _out(ctx, cfg)


class ConfigIn(BaseModel):
    client_id: str = Field(pattern=UUID, description='Client ID da aplicação na Pluggy')
    client_secret: str | None = Field(None, min_length=8, max_length=200, description='Vazio = manter o atual')
    consent: bool = Field(description='Concorda em guardar os extratos importados (LGPD)')


def _drop_webhook(cfg: PluggyConfig) -> None:
    """Apaga o webhook na conta Pluggy (melhor esforço: a chave antiga pode já não valer)."""
    if not cfg.webhook_id:
        return
    try:
        pg.PluggyClient(cfg.client_id, decrypt_field(cfg.client_secret)).delete_webhook(cfg.webhook_id)
    except pg.PluggyError as exc:
        log.warning('Não consegui apagar o webhook %s da Pluggy: %s', cfg.webhook_id, exc)
    cfg.webhook_id = None


@router.put('/config', summary='Salva e testa minha chave Pluggy; cria o webhook na minha conta Pluggy')
def save_config(body: ConfigIn, ctx: Ctx = Depends(get_ctx_session)):
    _enabled_tenant(ctx)
    if not body.consent:
        raise HTTPException(status_code=422, detail='Consentimento: é preciso concordar para usar o Open Finance.')
    cfg = PluggyConfig.query.filter_by(user_id=ctx.user.id).first()
    client_id = body.client_id.lower()
    secret = (body.client_secret or '').strip()
    if not secret and (cfg is None or cfg.client_id != client_id):
        raise HTTPException(status_code=422, detail='Client Secret: informe o segredo da aplicação Pluggy.')
    secret = secret or decrypt_field(cfg.client_secret)
    client = pg.PluggyClient(client_id, secret)
    try:
        client.api_key(fresh=True)  # testa a chave antes de guardar
    except pg.PluggyError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if cfg is None:
        cfg = PluggyConfig(user_id=ctx.user.id, hook_token=secrets.token_urlsafe(24),
                           hook_secret=encrypt_field(secrets.token_urlsafe(32)), client_id=client_id,
                           client_secret=encrypt_field(secret))
        db.session.add(cfg)
    else:
        _drop_webhook(cfg)  # credencial nova → webhook e segredo novos
        pg.forget(cfg.client_id)
        cfg.client_id, cfg.client_secret = client_id, encrypt_field(secret)
        cfg.hook_secret = encrypt_field(secrets.token_urlsafe(32))
    cfg.consent_at, cfg.status, cfg.last_error = utcnow(), 'ok', None

    message = 'Chave Pluggy aceita!'
    url = webhook_url(cfg)
    if url:
        try:
            cfg.webhook_id = client.create_webhook(url, decrypt_field(cfg.hook_secret))
            message += ' Aviso de novidades (webhook) configurado na sua conta Pluggy.'
        except pg.PluggyError as exc:
            cfg.status, cfg.last_error = 'error', f'Webhook: {exc}'
            message += ' Mas não consegui criar o webhook: a importação automática fica pendente.'
    else:
        message += ' O webhook não foi criado: o administrador precisa definir PUBLIC_URL (https) no servidor.'
    db.session.commit()
    return {'message': message, 'config': _out(ctx, cfg)}


@router.delete('/config', summary='Remove minha chave Pluggy (e o webhook na minha conta Pluggy)')
def delete_config(ctx: Ctx = Depends(get_ctx_session)):
    cfg = PluggyConfig.query.filter_by(user_id=ctx.user.id).first()
    if cfg:
        _drop_webhook(cfg)
        pg.forget(cfg.client_id)
        db.session.delete(cfg)
        db.session.commit()
    return {'message': 'Chave Pluggy removida.', 'config': _out(ctx, None)}


# ── Webhook (chamado pela Pluggy) ────────────────────────────────────────────

def _caller_ip(request: Request) -> str:
    # atrás do cloudflared o IP real vem no CF-Connecting-IP
    return (request.headers.get('cf-connecting-ip') or (request.client.host if request.client else '')).strip()


@router.post('/webhook/{hook_token}', include_in_schema=False)
async def webhook(hook_token: str, request: Request):
    """Responde rápido (a Pluggy exige 2XX em até 10 s). 404 não é reenviado pela Pluggy,
    por isso é a resposta para token/segredo/IP inválidos."""
    allowed = settings.pluggy_webhook_ips
    if allowed and _caller_ip(request) not in allowed:
        return JSONResponse(status_code=404, content={'detail': 'Não encontrado.'})
    cfg = PluggyConfig.query.filter_by(hook_token=hook_token).first() if len(hook_token) <= 48 else None
    given = request.headers.get('x-finfam-hook', '')
    if cfg is None or not hmac.compare_digest(given.encode(), (decrypt_field(cfg.hook_secret) or '').encode()):
        return JSONResponse(status_code=404, content={'detail': 'Não encontrado.'})
    raw = await request.body()
    if len(raw) > 256 * 1024:
        return JSONResponse(status_code=413, content={'detail': 'Grande demais.'})
    try:
        event = (await request.json()).get('event')
    except Exception:
        event = None
    user = db.session.get(User, cfg.user_id)
    tenant = db.session.get(Tenant, user.tenant_id) if user and user.tenant_id else None
    if tenant is None or not tenant.open_finance_enabled:
        return {'ok': True, 'ignored': 'open_finance_disabled'}  # 2XX: a Pluggy não fica reenviando
    log.info('Webhook Pluggy de user %s: %s', cfg.user_id, str(event)[:60])
    # etapa 3: enfileirar a importação deste evento
    return {'ok': True}
