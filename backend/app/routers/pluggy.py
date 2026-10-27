"""Open Finance via Pluggy — etapa 1: chave de cada pessoa e recebimento do webhook.

- O admin libera por família (tenants.open_finance_enabled).
- Cada PESSOA cadastra o Client ID / Client Secret da própria conta Pluggy (só pelo
  login do site; o segredo nunca volta). Ao salvar, o FinFam testa a chave e cria na
  conta Pluggy da pessoa um webhook exclusivo: /pluggy/webhook/<hook_token>, com um
  cabeçalho secreto. O token na URL diz de quem é o aviso; o cabeçalho e o IP da
  Pluggy confirmam a origem.
- Etapa 2: conectar bancos pelo widget Pluggy Connect (o token vem daqui; a senha do
  banco vai direto do navegador para a Pluggy) e listar contas e cartões de cada banco,
  ligando cada cartão do banco a um cartão do FinFam.
- Importação e revisão: próximas etapas.
"""
from datetime import date
from typing import Literal
import hmac
import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func

from app.core import ratelimit
from app.core.config import settings
from app.core.deps import _LOCAL_HOSTS
from app.core.db import db
from app.core.deps import Ctx, Period, get_ctx, get_ctx_session, get_period
from app.core.security import decrypt_field, encrypt_field
from app.core.types import utcnow
from app.models import BankAccount, BankInvestment, Expense, BankConnection, BankTransaction, CreditCard, PluggyConfig, Tenant, User
from app.services import open_finance as of
from app.services import pluggy as pg
from app.services.categorizer import learn_rule
from app.services.expense_service import ExpenseError, tenant_banks, tenant_categories
from app.services.finance import BANKS

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
        'include_sandbox': settings.pluggy_include_sandbox,
        'connections': BankConnection.query.filter_by(user_id=ctx.user.id).count() if cfg else 0,
        'to_review': BankTransaction.query.filter_by(user_id=ctx.user.id, status='pending').count() if cfg else 0,
        'notify': bool(cfg.notify) if cfg else True,
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


def _drop_connections(cfg: PluggyConfig) -> None:
    """Desconecta todos os bancos da pessoa (na Pluggy, melhor esforço) e apaga os dados locais."""
    client = pg.PluggyClient(cfg.client_id, decrypt_field(cfg.client_secret))
    for conn in BankConnection.query.filter_by(user_id=cfg.user_id).all():
        try:
            client.delete_item(conn.item_id)
        except pg.PluggyError as exc:
            log.warning('Não consegui apagar o item %s na Pluggy: %s', conn.item_id, exc)
        db.session.delete(conn)


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
        if cfg.client_id != client_id:
            _drop_connections(cfg)  # os bancos eram da outra aplicação Pluggy
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
        _drop_connections(cfg)
        _drop_webhook(cfg)
        pg.forget(cfg.client_id)
        db.session.delete(cfg)
        db.session.commit()
    return {'message': 'Chave Pluggy removida.', 'config': _out(ctx, None)}


# ── Bancos conectados (etapa 2) ──────────────────────────────────────────────

def _client(ctx: Ctx) -> tuple[PluggyConfig, pg.PluggyClient]:
    _enabled_tenant(ctx)
    cfg = PluggyConfig.query.filter_by(user_id=ctx.user.id).first()
    if cfg is None:
        raise HTTPException(status_code=409, detail={
            'code': 'pluggy_not_configured', 'message': 'Cadastre primeiro a sua chave Pluggy.'})
    return cfg, pg.PluggyClient(cfg.client_id, decrypt_field(cfg.client_secret))


def _pluggy(fn, *args):
    """Chama a Pluggy e converte a falha em 502 com mensagem amigável (404 → 404)."""
    try:
        return fn(*args)
    except pg.PluggyError as exc:
        code = 404 if exc.status == 404 else 400 if exc.status == 400 else 502
        raise HTTPException(status_code=code, detail=str(exc) if code != 404 else 'Banco não encontrado na sua conta Pluggy.')


def _conn(ctx: Ctx, conn_id: int) -> BankConnection:
    conn = db.session.get(BankConnection, conn_id)
    if conn is None or conn.user_id != ctx.user.id:  # só a própria pessoa mexe nos bancos dela
        raise HTTPException(status_code=404, detail='Banco conectado não encontrado.')
    return conn


def _account_out(acc: BankAccount) -> dict:
    card = db.session.get(CreditCard, acc.card_id) if acc.card_id else None
    return {
        'id': acc.id, 'type': acc.type, 'subtype': acc.subtype, 'name': acc.name,
        'last_digits': of.last4(acc.number), 'currency': acc.currency or 'BRL',
        'balance': float(acc.balance) if acc.balance is not None else None,
        'credit_limit': float(acc.credit_limit) if acc.credit_limit is not None else None,
        'available_limit': float(acc.available_limit) if acc.available_limit is not None else None,
        'close_day': acc.close_day, 'due_day': acc.due_day,
        'close_source': acc.close_source, 'manual_close_day': acc.manual_close_day,
        'card_id': acc.card_id,
        'card_label': (f"{card.nickname or card.bank or 'Cartão'} •••• {card.last_digits}" if card else None),
    }


def _conn_out(conn: BankConnection) -> dict:
    return {
        'id': conn.id, 'item_id': conn.item_id, 'bank_name': conn.label, 'connector_name': conn.bank_name,
        'auto_import': bool(conn.auto_import), 'bank_color': conn.bank_color,
        'status': conn.status, 'execution_status': conn.execution_status, 'error': conn.error_message,
        'last_updated_at': conn.last_updated_at.isoformat() + 'Z' if conn.last_updated_at else None,
        'accounts': [_account_out(a) for a in conn.accounts.order_by(BankAccount.type, BankAccount.id).all()],
    }


def _check_limit(ctx: Ctx) -> None:
    limit = ctx.tenant.open_finance_max_items if ctx.tenant else None
    if limit and BankConnection.query.filter_by(user_id=ctx.user.id).count() >= limit:
        raise HTTPException(status_code=409, detail={
            'code': 'open_finance_limit',
            'message': f'Você já conectou {limit} banco(s), o limite da sua família. Desconecte um para conectar outro.'})


class ConnectTokenIn(BaseModel):
    connection_id: int | None = Field(None, description='Para reconectar/atualizar a senha de um banco já conectado')


@router.post('/connect-token', summary='Token de 30 min para abrir o Pluggy Connect no navegador')
def connect_token(body: ConnectTokenIn, ctx: Ctx = Depends(get_ctx_session)):
    cfg, client = _client(ctx)
    item_id = None
    if body.connection_id:
        item_id = _conn(ctx, body.connection_id).item_id
    else:
        _check_limit(ctx)
    token = _pluggy(client.connect_token, str(ctx.user.id), webhook_url(cfg), item_id)
    return {'connect_token': token, 'item_id': item_id, 'include_sandbox': settings.pluggy_include_sandbox}


@router.get('/items', summary='Meus bancos conectados, com contas e cartões')
def list_items(ctx: Ctx = Depends(get_ctx_session)):
    _enabled_tenant(ctx)
    conns = BankConnection.query.filter_by(user_id=ctx.user.id).order_by(BankConnection.id).all()
    if any(c.needs_sync for c in conns):  # o webhook avisou novidade: relê só esses
        of.sync_stale(ctx.user.id)
    return {'items': [_conn_out(c) for c in conns],
            'banks': sorted(set(BANKS) | set(tenant_banks(ctx.uids)))}


class ItemUpdateIn(BaseModel):
    bank_name: str | None = Field(None, min_length=1, max_length=60,
                                  description='Nome do banco nas despesas lançadas (ex.: Caixa)')
    auto_import: bool | None = Field(None, description='Lançar sozinho o que chega deste banco')


@router.put('/items/{conn_id}', summary='Nome do banco e/ou lançamento automático')
def update_item(conn_id: int, body: ItemUpdateIn, ctx: Ctx = Depends(get_ctx_session)):
    _enabled_tenant(ctx)
    conn = _conn(ctx, conn_id)
    msg = []
    if body.bank_name is not None:
        conn.display_name = body.bank_name.strip()
        msg.append(f'Banco renomeado para {conn.label}.')
    launched = 0
    if body.auto_import is not None:
        conn.auto_import = body.auto_import
        if body.auto_import:
            launched = of.auto_approve(conn)  # o que já estava para revisar entra agora
            msg.append(f'{conn.label}: lançamento automático ligado.'
                       + (f' {launched} lançamento(s) lançado(s) agora.' if launched else ''))
        else:
            msg.append(f'{conn.label}: volta a passar pela revisão.')
    db.session.commit()
    return {'message': ' '.join(msg) or 'Nada mudou.', 'item': _conn_out(conn)}


class ItemIn(BaseModel):
    item_id: str = Field(pattern=r'^[A-Za-z0-9-]{1,64}$', description='id devolvido pelo Pluggy Connect')
    months: Literal[1, 3, 6, 12] | None = Field(None, description='Quanto do passado trazer (só ao conectar)')


@router.post('/items', summary='Registra o banco que acabou de ser conectado no Pluggy Connect')
def add_item(body: ItemIn, ctx: Ctx = Depends(get_ctx_session)):
    _cfg, client = _client(ctx)
    conn = BankConnection.query.filter_by(item_id=body.item_id).first()
    if conn is not None and conn.user_id != ctx.user.id:
        raise HTTPException(status_code=404, detail='Banco não encontrado na sua conta Pluggy.')
    item = _pluggy(client.item, body.item_id)  # só existe se for da conta Pluggy desta pessoa
    # A chave é da própria pessoa, então o item é da conta Pluggy dela. Conexões feitas pelo
    # painel da Pluggy vêm sem clientUserId (ou com outro texto); só recusa se foi aberta pelo
    # FinFam para outra pessoa (mesma aplicação Pluggy colada por duas pessoas).
    owner = str(item.get('clientUserId') or '')
    if owner.isdigit() and owner != str(ctx.user.id):
        raise HTTPException(status_code=404, detail='Banco não encontrado na sua conta Pluggy.')
    created = conn is None
    if created:
        _check_limit(ctx)
        conn = BankConnection(user_id=ctx.user.id, item_id=body.item_id,
                              import_from=of.months_back(date.today(), body.months or 1))
        db.session.add(conn)
        db.session.flush()
    of.apply_item(conn, item)
    if created:
        _pluggy(of.sync_accounts, conn, client)
        if of.same_accounts_elsewhere(conn):  # o mesmo banco de novo: desfaz aqui e na Pluggy
            db.session.rollback()
            try:
                client.delete_item(body.item_id)
            except pg.PluggyError as exc:
                log.warning('Não consegui apagar o item duplicado %s: %s', body.item_id, exc)
            raise HTTPException(status_code=409, detail='Esse banco já está conectado no FinFam.')
    count = _pluggy(of.sync, conn, client)
    db.session.commit()
    msg = f'{conn.label} conectado!' if created else f'{conn.label} atualizado.'
    if count:
        msg += f' {count} lançamento(s) para revisar.'
    return {'message': msg, 'item': _conn_out(conn)}


@router.post('/items/{conn_id}/refresh', summary='Pede ao banco as novidades agora')
def refresh_item(conn_id: int, ctx: Ctx = Depends(get_ctx_session)):
    _cfg, client = _client(ctx)
    conn = _conn(ctx, conn_id)
    try:
        item = client.update_item(conn.item_id)
    except pg.PluggyError as exc:
        if exc.status != 400:
            raise HTTPException(status_code=502, detail=str(exc))
        # Conexões do Meu Pluggy não aceitam pedido de atualização: a Pluggy atualiza sozinha.
        # Então relê agora o que já está lá (contas novas do Meu Pluggy aparecem aqui).
        count = _pluggy(of.sync, conn, client)
        db.session.commit()
        msg = f'{conn.label} relido.' + (f' {count} lançamento(s) novo(s) para revisar.' if count else '')
        return {'message': msg, 'item': _conn_out(conn)}
    of.apply_item(conn, item)
    conn.needs_sync = True  # quando a Pluggy terminar, o webhook avisa (ou a próxima listagem relê)
    db.session.commit()
    return {'message': f'Atualização de {conn.label} pedida. Pode levar alguns minutos.', 'item': _conn_out(conn)}


@router.delete('/items/{conn_id}', summary='Desconecta o banco (na Pluggy também) e apaga os dados dele')
def delete_item(conn_id: int, ctx: Ctx = Depends(get_ctx_session)):
    cfg = PluggyConfig.query.filter_by(user_id=ctx.user.id).first()
    conn = _conn(ctx, conn_id)
    if cfg:
        _pluggy(pg.PluggyClient(cfg.client_id, decrypt_field(cfg.client_secret)).delete_item, conn.item_id)
    name = conn.label
    db.session.delete(conn)
    db.session.commit()
    return {'message': f'{name} desconectado. Os dados vindos dele foram apagados.'}


class AccountIn(BaseModel):
    card_id: int | None = Field(None, description='Cartão do FinFam que corresponde a este cartão do banco (vazio = nenhum)')


@router.put('/accounts/{account_id}', summary='Liga o cartão do banco a um cartão do FinFam')
def map_account(account_id: int, body: AccountIn, ctx: Ctx = Depends(get_ctx_session)):
    _enabled_tenant(ctx)
    acc = db.session.get(BankAccount, account_id)
    if acc is None or acc.connection.user_id != ctx.user.id:
        raise HTTPException(status_code=404, detail='Conta não encontrada.')
    if acc.type != 'CREDIT':
        raise HTTPException(status_code=422, detail='Conta: só cartões de crédito do banco são ligados a cartões.')
    if body.card_id is not None:
        card = db.session.get(CreditCard, body.card_id)
        if card is None or card.user_id not in ctx.uids:
            raise HTTPException(status_code=404, detail='Cartão não encontrado.')
        if not (card.card_type == 'credit' or card.supports_credit):
            raise HTTPException(status_code=422, detail='Cartão: escolha um cartão de crédito.')
    acc.card_id = body.card_id
    limit = of.apply_card_limit(acc)
    db.session.commit()
    msg = 'Cartão ligado.' + (' O limite agora vem do banco.' if limit else '') if body.card_id else 'Cartão desligado.'
    return {'message': msg, 'account': _account_out(acc)}


class CloseDayIn(BaseModel):
    close_day: int | None = Field(None, ge=1, le=31, description='Dia de fechamento da fatura (vazio = o FinFam calcula)')


@router.put('/accounts/{account_id}/close-day', summary='Dia de fechamento do cartão, quando o banco não informa')
def set_close_day(account_id: int, body: CloseDayIn, ctx: Ctx = Depends(get_ctx_session)):
    """Vale só enquanto o banco não informar o fechamento; quando informar, o do banco manda.
    Relê o banco na hora para refazer o mês da fatura das compras."""
    _enabled_tenant(ctx)
    acc = db.session.get(BankAccount, account_id)
    if acc is None or acc.connection.user_id != ctx.user.id:
        raise HTTPException(status_code=404, detail='Conta não encontrada.')
    if acc.type != 'CREDIT':
        raise HTTPException(status_code=422, detail='Fechamento: só cartões de crédito têm fatura.')
    acc.manual_close_day = body.close_day
    from_bank = acc.close_source == 'bank'
    of.apply_close_day(acc, 'bank' if from_bank else None)
    of.apply_card_limit(acc)
    conn = acc.connection
    conn.needs_sync = True
    db.session.commit()
    client = of.client_for(ctx.user.id)
    if client is not None and not from_bank:
        try:
            of.sync(conn, client)
            db.session.commit()
        except pg.PluggyError as exc:
            db.session.rollback()
            log.warning('Releitura após mudar o fechamento falhou: %s', exc)
    if from_bank:
        msg = f'Guardado, mas o banco já informa o fechamento (dia {acc.close_day}) e ele é que vale.'
    elif body.close_day:
        msg = f'Fechamento dia {body.close_day} guardado. Vale até o banco passar a informar o dele.'
    else:
        msg = 'Fechamento manual removido: o FinFam volta a calcular pelas compras.'
    return {'message': msg, 'account': _account_out(acc)}


@router.post('/accounts/{account_id}/card', summary='Cria no FinFam o cartão do banco (limite e datas vêm do banco)')
def create_card(account_id: int, ctx: Ctx = Depends(get_ctx_session)):
    _enabled_tenant(ctx)
    acc = db.session.get(BankAccount, account_id)
    if acc is None or acc.connection.user_id != ctx.user.id:
        raise HTTPException(status_code=404, detail='Conta não encontrada.')
    try:
        card = of.create_card_from(acc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.session.commit()
    return {'message': f'Cartão {card.bank} •••• {card.last_digits} criado e ligado: vence dia {card.due_day}, '
                       f'melhor dia de compra {card.best_buy_day}.', 'account': _account_out(acc)}


# ── Revisar importados (etapa 3) ─────────────────────────────────────────────

STATUSES = ('pending', 'duplicate', 'ignored', 'imported')


def _tx_out(tx: BankTransaction, ctx: Ctx) -> dict:
    acc = tx.account
    match = db.session.get(Expense, tx.expense_id) if tx.expense_id else None
    out = {
        'id': tx.id, 'date': tx.date.isoformat(), 'description': tx.description, 'amount': float(tx.amount),
        'refund': tx.kind == 'refund',
        'installment': ({'number': tx.installment_number, 'total': tx.total_installments}
                        if tx.total_installments and tx.total_installments > 1 else None),
        'status': tx.status, 'reason': tx.reason, 'bank_pending': tx.bank_status == 'PENDING',
        'bill': f'{tx.bill_month:02d}/{tx.bill_year}' if tx.bill_month and tx.bill_year else None,
        'bank_name': acc.connection.label, 'account_type': acc.type,
        'account_name': acc.name, 'account_last_digits': of.last4(acc.number),
        'card_id': acc.card_id, 'card_digits': tx.card_digits,
        'expense': ({'id': match.id, 'description': match.description, 'amount': float(match.amount),
                     'date': of._expense_date(match).isoformat()} if match else None),
    }
    if tx.status == 'pending':
        out.update(of.suggestion(tx, ctx.uids, ctx.tenant.id if ctx.tenant else None))
    return out


def _my_tx(ctx: Ctx, ids: list[int]) -> list[BankTransaction]:
    rows = BankTransaction.query.filter(BankTransaction.id.in_(ids), BankTransaction.user_id == ctx.user.id).all()
    if len(rows) != len(set(ids)):
        raise HTTPException(status_code=404, detail='Lançamento não encontrado.')
    return rows


@router.get('/transactions', summary='Lançamentos que chegaram do banco, por situação')
def list_transactions(status: Literal['pending', 'duplicate', 'ignored', 'imported'] = 'pending',
                      ctx: Ctx = Depends(get_ctx_session)):
    _enabled_tenant(ctx)
    of.sync_stale(ctx.user.id)  # traz o que o webhook avisou
    if of.reconcile_payments(ctx.user.id):
        db.session.commit()
    counts = {s: 0 for s in STATUSES}
    for st, n in (db.session.query(BankTransaction.status, func.count(BankTransaction.id))
                  .filter(BankTransaction.user_id == ctx.user.id).group_by(BankTransaction.status)):
        counts[st] = n
    rows = (BankTransaction.query.filter_by(user_id=ctx.user.id, status=status)
            .order_by(BankTransaction.date.desc(), BankTransaction.id.desc()).limit(500).all())
    return {'counts': counts, 'categories': tenant_categories(ctx.uids),
            'items': [_tx_out(t, ctx) for t in rows]}


class ApproveItem(BaseModel):
    id: int
    category: str = Field(min_length=1, max_length=50, description='Da lista ou uma nova')
    description: str | None = Field(None, max_length=200)
    payment_method: Literal['PIX', 'Cartão de Débito'] | None = None


class ApproveIn(BaseModel):
    items: list[ApproveItem] = Field(min_length=1, max_length=200)


@router.post('/transactions/approve', summary='Lança como despesa os lançamentos escolhidos')
def approve_transactions(body: ApproveIn, ctx: Ctx = Depends(get_ctx_session)):
    _enabled_tenant(ctx)
    rows = {t.id: t for t in _my_tx(ctx, [i.id for i in body.items])}
    allowed = tenant_categories(ctx.uids)
    done = known = 0
    # mais antigos primeiro: a parcela 6/12 cria a série e a 7/12 do mesmo lote é reconhecida
    items = [i for i in body.items if i.id in rows]
    for item in sorted(items, key=lambda i: (rows[i.id].date, rows[i.id].installment_number or 0)):
        tx = rows[item.id]
        if tx.status == 'imported':
            continue
        category = of.valid_category(item.category, allowed)
        if tx.kind != 'refund' and category != of.suggestion(tx, ctx.uids, ctx.tenant_id)['category']:
            learn_rule(ctx.tenant_id, tx.description, category)  # trocou a sugestão: a próxima já vem certa
        try:
            of.approve(tx, ctx.uids, category, item.description, item.payment_method)
        except ExpenseError as exc:
            db.session.rollback()
            raise HTTPException(status_code=422, detail=f'{tx.description}: {exc}')
        if tx.status == 'imported':
            done += 1
        else:
            known += 1
    of.reconcile_payments(ctx.user.id)  # pagamento de fatura que chegou antes das compras
    db.session.commit()
    msg = f'{done} despesa(s) lançada(s) no FinFam.' if done else 'Nada para lançar.'
    if known:
        msg += f' {known} já estava(m) no FinFam (outra parcela da mesma compra).'
    return {'message': msg, 'imported': done, 'already': known}


class IdsIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)


@router.post('/transactions/ignore', summary='Ignora os lançamentos escolhidos (não viram despesa)')
def ignore_transactions(body: IdsIn, ctx: Ctx = Depends(get_ctx_session)):
    _enabled_tenant(ctx)
    n = 0
    for tx in _my_tx(ctx, body.ids):
        if tx.status in ('pending', 'duplicate'):
            tx.status, tx.reason, tx.reviewed_at = 'ignored', 'Ignorado por você.', utcnow()
            n += 1
    db.session.commit()
    return {'message': f'{n} lançamento(s) ignorado(s).'}


@router.post('/transactions/restore', summary='Volta lançamentos ignorados/duplicados para a revisão')
def restore_transactions(body: IdsIn, ctx: Ctx = Depends(get_ctx_session)):
    _enabled_tenant(ctx)
    n = 0
    for tx in _my_tx(ctx, body.ids):
        if tx.status in ('ignored', 'duplicate'):
            tx.status, tx.reason, tx.expense_id, tx.reviewed_at = 'pending', None, None, utcnow()
            n += 1
    db.session.commit()
    return {'message': f'{n} lançamento(s) de volta para revisar.'}


class NotifyIn(BaseModel):
    notify: bool


@router.put('/notify', summary='Liga/desliga o aviso no Telegram quando chega algo do banco')
def set_notify(body: NotifyIn, ctx: Ctx = Depends(get_ctx_session)):
    cfg = PluggyConfig.query.filter_by(user_id=ctx.user.id).first()
    if cfg is None:
        raise HTTPException(status_code=409, detail='Cadastre primeiro a sua chave Pluggy.')
    cfg.notify = body.notify
    db.session.commit()
    return {'message': 'Aviso no Telegram ligado.' if body.notify else 'Aviso no Telegram desligado.',
            'config': _out(ctx, cfg)}


# ── Consultas (também para o MCP, com token de API: só leitura) ──────────────

@router.get('/invoice-check', summary='Fatura do banco × FinFam, por cartão conectado, no mês')
def invoice_check(period: Period = Depends(get_period), ctx: Ctx = Depends(get_ctx)):
    _enabled_tenant(ctx)
    return {'month': period.month, 'year': period.year,
            'cards': of.invoice_check(ctx.user.id, period.month, period.year)}


@router.get('/balances', summary='Saldo das minhas contas correntes/poupança conectadas (só consulta)')
def bank_balances(ctx: Ctx = Depends(get_ctx)):
    """Saldo como o banco informou na última leitura. Sem Open Finance liberado: lista vazia."""
    if ctx.tenant is None or not ctx.tenant.open_finance_enabled:
        return {'enabled': False, 'items': [], 'total': 0}
    rows = (BankAccount.query.join(BankConnection)
            .filter(BankConnection.user_id == ctx.user.id, BankAccount.type == 'BANK', BankAccount.balance.isnot(None))
            .order_by(BankConnection.id, BankAccount.id).all())
    items = [{'id': a.id, 'bank': a.connection.label, 'name': a.name, 'subtype': a.subtype,
              'last_digits': of.last4(a.number), 'balance': float(a.balance), 'currency': a.currency or 'BRL',
              'updated_at': (a.connection.last_updated_at.isoformat() + 'Z') if a.connection.last_updated_at else None}
             for a in rows]
    return {'enabled': True, 'items': items,
            'total': round(sum(i['balance'] for i in items if i['currency'] == 'BRL'), 2)}


@router.get('/investments', summary='Investimentos que os meus bancos informam (só consulta)')
def bank_investments(ctx: Ctx = Depends(get_ctx)):
    """Saldo como o banco informa na última leitura. Sem Open Finance liberado: lista vazia."""
    if ctx.tenant is None or not ctx.tenant.open_finance_enabled:
        return {'enabled': False, 'items': [], 'total': 0}
    rows = (BankInvestment.query.join(BankConnection).filter(BankConnection.user_id == ctx.user.id)
            .filter(func.coalesce(BankInvestment.status, '') != 'TOTAL_WITHDRAWAL')
            .order_by(BankConnection.id, BankInvestment.name).all())
    items = [{'id': i.id, 'name': i.name, 'bank': i.connection.label, 'type': i.type, 'subtype': i.subtype,
              'issuer': i.issuer, 'balance': float(i.balance or 0),
              'amount_invested': float(i.amount_invested) if i.amount_invested is not None else None,
              'amount_withdrawal': float(i.amount_withdrawal) if i.amount_withdrawal is not None else None,
              'currency': i.currency or 'BRL', 'rate': float(i.rate) if i.rate is not None else None,
              'rate_type': i.rate_type, 'annual_rate': float(i.annual_rate) if i.annual_rate is not None else None,
              'due_date': i.due_date.isoformat() if i.due_date else None,
              'updated_at': i.updated_at.isoformat() + 'Z' if i.updated_at else None}
             for i in rows if i.balance]
    return {'enabled': True, 'items': items,
            'total': round(sum(i['balance'] for i in items if i['currency'] == 'BRL'), 2)}


@router.get('/summary', summary='Resumo do que chegou dos bancos (sem reler o banco)')
def summary(status: Literal['pending', 'duplicate', 'ignored', 'imported'] = 'pending', limit: int = 50,
            ctx: Ctx = Depends(get_ctx)):
    _enabled_tenant(ctx)
    counts = {s: 0 for s in STATUSES}
    for st, n in (db.session.query(BankTransaction.status, func.count(BankTransaction.id))
                  .filter(BankTransaction.user_id == ctx.user.id).group_by(BankTransaction.status)):
        counts[st] = n
    rows = (BankTransaction.query.filter_by(user_id=ctx.user.id, status=status)
            .order_by(BankTransaction.date.desc()).limit(max(1, min(limit, 200))).all())
    conns = BankConnection.query.filter_by(user_id=ctx.user.id).all()
    return {'counts': counts,
            'banks': [{'name': c.label, 'status': c.status, 'auto_import': bool(c.auto_import),
                       'last_updated_at': c.last_updated_at.isoformat() + 'Z' if c.last_updated_at else None} for c in conns],
            'items': [{'id': t.id, 'date': t.date.isoformat(), 'description': t.description, 'amount': float(t.amount),
                       'bank': t.account.connection.label, 'account_type': t.account.type, 'reason': t.reason,
                       'bill': f'{t.bill_month:02d}/{t.bill_year}' if t.bill_month else None} for t in rows]}


# ── Webhook (chamado pela Pluggy) ────────────────────────────────────────────

def _caller_ip(request: Request) -> str:
    """Atrás do cloudflared (na mesma máquina) o IP real vem no CF-Connecting-IP. Só vale quando
    o pedido chega pela própria máquina: quem acessa a porta direto não escolhe o IP que tem."""
    host = request.client.host if request.client else ''
    if host in _LOCAL_HOSTS:
        return (request.headers.get('cf-connecting-ip') or host).strip()
    return host


def _hook_refused(request: Request) -> JSONResponse:
    """404 (a Pluggy não reenvia) e conta a tentativa: muitas erradas do mesmo IP → 429."""
    ratelimit.check(request, 'pluggy-hook', _caller_ip(request), limit=30, window=600)
    return JSONResponse(status_code=404, content={'detail': 'Não encontrado.'})


@router.post('/webhook/{hook_token}', include_in_schema=False)
async def webhook(hook_token: str, request: Request):
    """Responde rápido (a Pluggy exige 2XX em até 10 s). 404 não é reenviado pela Pluggy,
    por isso é a resposta para token/segredo/IP inválidos."""
    allowed = settings.pluggy_webhook_ips
    if allowed and _caller_ip(request) not in allowed:
        return _hook_refused(request)
    cfg = PluggyConfig.query.filter_by(hook_token=hook_token).first() if len(hook_token) <= 48 else None
    given = request.headers.get('x-finfam-hook', '')
    secret = (decrypt_field(cfg.hook_secret) or '') if cfg is not None else ''
    if not secret or not given or not hmac.compare_digest(given.encode(), secret.encode()):
        return _hook_refused(request)
    raw = await request.body()
    if len(raw) > 256 * 1024:
        return JSONResponse(status_code=413, content={'detail': 'Grande demais.'})
    try:
        payload = await request.json()
        payload = payload if isinstance(payload, dict) else {}
    except Exception:
        payload = {}
    event = payload.get('event')
    user = db.session.get(User, cfg.user_id)
    tenant = db.session.get(Tenant, user.tenant_id) if user and user.tenant_id else None
    if tenant is None or not tenant.open_finance_enabled:
        return {'ok': True, 'ignored': 'open_finance_disabled'}  # 2XX: a Pluggy não fica reenviando
    log.info('Webhook Pluggy de user %s: %s', cfg.user_id, str(event)[:60])
    if _item_event(cfg.user_id, str(event or ''), str(payload.get('itemId') or ''), payload.get('error')):
        of.schedule_sync(cfg.user_id)  # relê já (lança sozinho se a conexão estiver no automático)
    # etapa 3: enfileirar a importação dos lançamentos (transactions/*)
    return {'ok': True}


_EVENT_STATUS = {'item/error': 'LOGIN_ERROR', 'item/waiting_user_input': 'WAITING_USER_INPUT',
                 'item/waiting_user_action': 'WAITING_USER_ACTION', 'item/login_succeeded': 'UPDATING'}


def _item_event(user_id: int, event: str, item_id: str, error) -> bool:
    """Só anota (sem chamar a Pluggy: o webhook tem de responder rápido). Devolve True
    quando há novidade para reler (a leitura roda em segundo plano)."""
    if not (event.startswith('item/') or event.startswith('transactions/')) or not item_id:
        return False
    conn = BankConnection.query.filter_by(item_id=item_id[:64], user_id=user_id).first()
    if conn is None:
        return False
    if event.startswith('transactions/'):
        conn.needs_sync = True  # lançamentos novos/alterados: a próxima leitura traz
    elif event == 'item/deleted':
        db.session.delete(conn)
    elif event in ('item/updated', 'item/created'):
        conn.status, conn.error_message, conn.needs_sync = 'UPDATED', None, True
        conn.last_updated_at = utcnow()
    elif event in _EVENT_STATUS:
        conn.status = _EVENT_STATUS[event]
        if isinstance(error, dict) and error.get('message'):
            conn.error_message = str(error['message'])[:300]
    news = event != 'item/deleted' and bool(conn.needs_sync)
    db.session.commit()
    return news
