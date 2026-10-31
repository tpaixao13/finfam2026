"""Aplicação FastAPI do FinFam 2026.

Toda a API fica sob /api/v1. Se o build do frontend existir (frontend/dist),
ele também é servido pela mesma aplicação, então em produção basta um processo.
"""
import asyncio
import os
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.core.bodylimit import BodyLimitMiddleware
from app.core.config import settings
from app.core.db import DBSessionMiddleware
from app.core.migrations import run_migrations
from app.routers import (admin, ai, auth, bank_incomes, cards, dashboard, expenses, family, goals, imports, incomes,
                         investments, pluggy, rules, settings as settings_router, telegram)

log = logging.getLogger('finfam')


class _HideHookToken(logging.Filter):
    """O log de acesso do uvicorn não grava o token do webhook da Pluggy (fica /pluggy/webhook/***)."""
    _rx = __import__('re').compile(r'(/pluggy/webhook/)[^\s"?]+')

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and any('/pluggy/webhook/' in str(a) for a in record.args):
            record.args = tuple(self._rx.sub(r'\1***', a) if isinstance(a, str) else a for a in record.args)
        return True


logging.getLogger('uvicorn.access').addFilter(_HideHookToken())

VERSION = '2026.9.0'
MAX_BODY_BYTES = 12 * 1024 * 1024
# O site só carrega o que é dele, as fontes do Google e o Pluggy Connect (script e janela do banco).
# Se um dia o Pluggy Connect parar de abrir por causa disto, CSP_REPORT_ONLY=1 no .env só avisa no console.
CSP = ("default-src 'self'; script-src 'self' https://cdn.pluggy.ai https://*.pluggy.ai; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' data: https://fonts.gstatic.com; "
       "img-src 'self' data: blob: https:; connect-src 'self' https://*.pluggy.ai wss://*.pluggy.ai; "
       "frame-src https://*.pluggy.ai; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
CSP_HEADER = ('Content-Security-Policy-Report-Only' if os.environ.get('CSP_REPORT_ONLY', '').split('#')[0].strip() == '1'
              else 'Content-Security-Policy')

_FIELD_LABELS = {
    'description': 'Descrição', 'amount': 'Valor', 'category': 'Categoria', 'payment_method': 'Forma de pagamento',
    'email': 'E-mail', 'password': 'Senha', 'name': 'Nome', 'day': 'Dia', 'month': 'Mês', 'year': 'Ano',
    'last_digits': 'Últimos 4 dígitos', 'title': 'Título', 'target_amount': 'Valor da meta',
    'keyword': 'Palavra-chave', 'new_password': 'Nova senha', 'installments': 'Parcelas',
}


def _scheduled_notices() -> None:
    """Resumo de domingo e avisos de metas (cada um decide se é a hora)."""
    from app.core.db import db
    from app.services import bot_notices
    now = datetime.now()
    try:
        for job in (bot_notices.run_weekly, bot_notices.run_goal_alerts):
            try:
                job(now)
            except Exception as exc:
                db.session.rollback()
                log.warning('Aviso automático %s falhou: %s', job.__name__, exc)
    finally:
        db.session.remove()


async def _reminder_loop():
    """Verifica a cada minuto se algum tenant deve receber o lembrete do Telegram."""
    from app.services.telegram import run_daily_reminders
    while True:
        for job in (run_daily_reminders, _scheduled_notices):
            try:
                await asyncio.to_thread(job)
            except Exception as exc:  # nunca derruba o loop
                log.warning('Lembretes do Telegram falharam: %s', exc)
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(_: FastAPI):
    run_migrations()
    task = asyncio.create_task(_reminder_loop()) if settings.scheduler_enabled else None
    yield
    if task:
        task.cancel()


def _validation_handler(_: Request, exc: RequestValidationError):
    """Erros de validação em formato simples: {detail: 'mensagem', errors: [...]}."""
    errors = []
    for err in exc.errors():
        loc = [str(p) for p in err.get('loc', []) if p not in ('body', 'query', 'path', 'form')]
        field = loc[-1] if loc else ''
        msg = str(err.get('msg', 'Valor inválido')).removeprefix('Value error, ')
        errors.append({'field': field, 'message': msg})
    first = errors[0] if errors else {'field': '', 'message': 'Dados inválidos.'}
    label = _FIELD_LABELS.get(first['field'], first['field'])
    detail = f"{label}: {first['message']}" if label else first['message']
    return JSONResponse(status_code=422, content={'detail': detail, 'errors': errors})


def create_app() -> FastAPI:
    app = FastAPI(
        title='FinFam API', version=VERSION, lifespan=lifespan,
        description='API do FinFam 2026 — controle financeiro familiar. '
                    'Autentique com `Authorization: Bearer <JWT do login>` ou `Bearer ffk_<token de API>`.',
        docs_url=None if settings.is_production and not settings.extra.get('docs') else '/api/docs',
        redoc_url=None, openapi_url=None if settings.is_production else '/api/openapi.json',
    )
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                       allow_methods=['*'], allow_headers=['*'], expose_headers=['Content-Disposition'])
    app.add_middleware(DBSessionMiddleware)

    @app.middleware('http')
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'DENY')
        response.headers.setdefault('Referrer-Policy', 'same-origin')
        response.headers.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=(), payment=()')
        if settings.public_url.startswith('https://'):
            response.headers.setdefault('Strict-Transport-Security', 'max-age=31536000')
        if response.headers.get('content-type', '').startswith('text/html') and not request.url.path.startswith('/api/docs'):
            response.headers.setdefault(CSP_HEADER, CSP)
        return response
    app.add_exception_handler(RequestValidationError, _validation_handler)
    # por último = o mais de fora: o corpo grande é cortado antes de qualquer outra coisa
    # (a maior entrada é a foto/áudio do bot, até 8 MB em base64)
    app.add_middleware(BodyLimitMiddleware, max_bytes=MAX_BODY_BYTES)

    prefix = settings.api_prefix
    for module in (auth, family, dashboard, expenses, incomes, cards, goals, rules, investments,
                   imports, settings_router, telegram, ai, bank_incomes, pluggy, admin):
        app.include_router(module.router, prefix=prefix)

    @app.get(f'{prefix}/health', tags=['meta'], summary='Status da API')
    def health():
        return {'status': 'ok', 'version': VERSION}

    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI):
    dist = settings.frontend_dist
    if not dist or not (dist / 'index.html').is_file():
        return
    index = dist / 'index.html'

    @app.get('/{full_path:path}', include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith('api/'):
            return JSONResponse(status_code=404, content={'detail': 'Rota não encontrada.'})
        if '\x00' in full_path:  # o sistema de arquivos recusa byte nulo (sem isso, erro 500)
            return FileResponse(index)
        candidate = (dist / full_path).resolve()
        if full_path and candidate.is_file() and dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index)


app = create_app()
