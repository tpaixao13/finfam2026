"""Banco de dados: engine, sessão por request e Base declarativa.

A sessão é um `scoped_session` cujo escopo é o request HTTP atual (via ContextVar),
ou a thread atual fora de requests (scripts, agendador, testes). Isso permite que
os services usem `db.session` e `Model.query` como no projeto Flask original.
"""
import threading
import uuid
from contextvars import ContextVar

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, scoped_session, sessionmaker

from app.core.config import settings

_request_scope: ContextVar[str | None] = ContextVar('finfam_db_scope', default=None)


def _scopefunc():
    return _request_scope.get() or threading.get_ident()


def _make_engine(url: str):
    kwargs = {}
    if url.startswith('sqlite'):
        kwargs['connect_args'] = {'check_same_thread': False}
    return create_engine(url, **kwargs)


engine = _make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine)
session = scoped_session(SessionLocal, scopefunc=_scopefunc)


class Base(DeclarativeBase):
    pass


Base.query = session.query_property()


class _DB:
    """Atalho `db.session` — mantém a mesma forma de uso dos services originais."""
    session = session


db = _DB()


def get_or_404(query, detail: str = 'Registro não encontrado.'):
    obj = query.first()
    if obj is None:
        raise HTTPException(status_code=404, detail=detail)
    return obj


class DBSessionMiddleware:
    """Abre um escopo de sessão por request e o descarta (rollback + close) ao final."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        token = _request_scope.set(uuid.uuid4().hex)
        try:
            await self.app(scope, receive, send)
        finally:
            session.remove()
            _request_scope.reset(token)


def get_engine():
    return engine

