"""Chave Pluggy compartilhada na família: quem usa a cópia de quem, o webhook e o fim do compartilhamento.

Sem HTTP: o router só chama estas funções. A chave ORIGINAL é a de `shared_from IS NULL`; as cópias
apontam para ela (mesma aplicação Pluggy, mesma família). Só vira "chave da família" se o dono deixar
(`allow_family`); quando ele tira a permissão, remove a chave, troca de aplicação ou sai da família, as
cópias são revogadas e os bancos conectados com elas são desconectados (estão na conta Pluggy DELE).
"""
import logging

from app.core.config import settings
from app.core.db import db
from app.core.deps import Ctx
from app.core.security import decrypt_field, encrypt_field
from app.models import PluggyConfig, User
from app.services import pluggy as pg

log = logging.getLogger('finfam.pluggy')


def webhook_url(cfg: PluggyConfig) -> str | None:
    base = settings.public_url
    return f'{base}/api/v1/pluggy/webhook/{cfg.hook_token}' if base.startswith('https://') else None



def family_key(ctx: Ctx) -> PluggyConfig | None:
    """A chave original (não cópia) de outra pessoa da família — a mais antiga, sem erro de preferência."""
    others = [u.id for u in ctx.members if u.id != ctx.user.id]
    if not others:
        return None
    return (PluggyConfig.query.filter(PluggyConfig.user_id.in_(others), PluggyConfig.shared_from.is_(None),
                                      PluggyConfig.allow_family.is_(True))
            .order_by((PluggyConfig.status != 'ok'), PluggyConfig.id).first())



def family_ids(user_id: int) -> list[int]:
    user = db.session.get(User, user_id)
    if user is None or not user.tenant_id:
        return [user_id]
    return [u.id for u in User.query.filter_by(tenant_id=user.tenant_id).all()]



def key_copies(cfg: PluggyConfig) -> list[PluggyConfig]:
    """Quem da MESMA família usa a cópia desta chave (mesma aplicação Pluggy)."""
    return (PluggyConfig.query.filter(PluggyConfig.shared_from == cfg.user_id, PluggyConfig.client_id == cfg.client_id,
                                      PluggyConfig.user_id.in_(family_ids(cfg.user_id)))
            .order_by(PluggyConfig.id).all())



def drop_webhook(cfg: PluggyConfig) -> None:
    """Apaga o webhook na conta Pluggy (melhor esforço: a chave antiga pode já não valer)."""
    if not cfg.webhook_id:
        return
    try:
        pg.PluggyClient(cfg.client_id, decrypt_field(cfg.client_secret)).delete_webhook(cfg.webhook_id)
    except pg.PluggyError as exc:
        log.warning('Não consegui apagar o webhook %s da Pluggy: %s', cfg.webhook_id, exc)
    cfg.webhook_id = None



def create_webhook(cfg: PluggyConfig, client: pg.PluggyClient) -> str:
    """Cria o webhook da chave na conta Pluggy. Devolve o trecho da mensagem para a pessoa."""
    url = webhook_url(cfg)
    if not url:
        return ' O webhook não foi criado: o administrador precisa definir PUBLIC_URL (https) no servidor.'
    try:
        cfg.webhook_id = client.create_webhook(url, decrypt_field(cfg.hook_secret))
        return ' Aviso de novidades (webhook) configurado na sua conta Pluggy.'
    except pg.PluggyError as exc:
        cfg.status, cfg.last_error = 'error', f'Webhook: {exc}'
        return ' Mas não consegui criar o webhook: a importação automática fica pendente.'



def drop_connections(cfg: PluggyConfig) -> int:
    """Desconecta os bancos da pessoa dona de `cfg` (na Pluggy, melhor esforço) e apaga os dados locais."""
    from app.models import BankConnection
    client = pg.PluggyClient(cfg.client_id, decrypt_field(cfg.client_secret))
    conns = BankConnection.query.filter_by(user_id=cfg.user_id).all()
    for conn in conns:
        try:
            client.delete_item(conn.item_id)
        except pg.PluggyError as exc:
            log.warning('Não consegui apagar o item %s na Pluggy: %s', conn.item_id, exc)
        db.session.delete(conn)
    return len(conns)


def revoke_copies(cfg: PluggyConfig) -> tuple[int, int]:
    """O dono parou de compartilhar: quem usava a cópia perde a chave, e os bancos conectados com ela
    são desconectados (estão na conta Pluggy do dono). Devolve (pessoas, bancos)."""
    people = banks = 0
    for copy in key_copies(cfg):
        banks += drop_connections(copy)
        db.session.delete(copy)
        people += 1
    return people, banks


def update_copies(cfg: PluggyConfig, secret: str) -> int:
    """Segredo novo da mesma aplicação: as cópias da família passam a usar o novo."""
    copies = key_copies(cfg)
    for c in copies:
        c.client_secret, c.status, c.last_error = encrypt_field(secret), 'ok', None
    return len(copies)



def release_key(user_id: int) -> None:
    """A pessoa vai sair da família: a chave dela sai junto, e quem usava a cópia perde a chave."""
    cfg = PluggyConfig.query.filter_by(user_id=user_id).first()
    if cfg is None:
        return
    if cfg.shared_from is None:
        try:
            revoke_copies(cfg)
            drop_webhook(cfg)
        except Exception as exc:  # a Pluggy fora do ar não impede remover a pessoa
            log.warning('Revogar a chave Pluggy da família falhou: %s', exc)
    db.session.delete(cfg)



def hook_owner(cfg: PluggyConfig) -> PluggyConfig:
    """De onde vêm os avisos (webhook): a própria chave, ou a original quando é uma cópia."""
    if cfg.shared_from and cfg.shared_from in family_ids(cfg.user_id):
        source = PluggyConfig.query.filter_by(user_id=cfg.shared_from).first()
        if source is not None:
            return source
    return cfg

