"""A própria pessoa exclui a conta (ou quem criou a família exclui a família inteira) — LGPD.

Cada função faz uma coisa:
  disconnect_banks   desconecta os bancos da pessoa na Pluggy e tira a chave dela (e as cópias da família)
  delete_member      exclui uma pessoa e tudo o que é dela
  delete_family      exclui a família inteira, com todas as pessoas
Os dados vão embora na hora; as cópias de segurança criptografadas expiram sozinhas (BACKUP_KEEP_DAYS).
"""
import logging

from app.core.db import db
from app.models import PluggyConfig, Tenant, User
from app.services.pluggy_keys import drop_connections, release_key

log = logging.getLogger('finfam.account')


def disconnect_banks(user_id: int) -> None:
    """Melhor esforço: a Pluggy fora do ar não impede excluir (os dados locais saem com a pessoa)."""
    cfg = PluggyConfig.query.filter_by(user_id=user_id).first()
    try:
        if cfg is not None:
            drop_connections(cfg)
        release_key(user_id)
    except Exception as exc:
        log.warning('Desconectar os bancos de %s na Pluggy falhou: %s', user_id, exc)


def delete_member(user: User) -> None:
    disconnect_banks(user.id)
    db.session.delete(user)
    db.session.commit()
    log.info('Conta %s excluída pela própria pessoa.', user.id)


def delete_family(tenant: Tenant) -> int:
    members = User.query.filter_by(tenant_id=tenant.id).all()
    for u in members:
        disconnect_banks(u.id)
    db.session.delete(tenant)  # leva as pessoas e tudo o que é delas
    db.session.commit()
    log.info('Família %s (%s pessoa(s)) excluída por quem a criou.', tenant.id, len(members))
    return len(members)
