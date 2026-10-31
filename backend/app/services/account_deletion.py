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


def disconnect_banks(user_id: int) -> bool:
    """Melhor esforço: a Pluggy fora do ar não impede excluir (os dados locais saem com a pessoa).
    Devolve False se algo falhou na Pluggy (a pessoa é avisada para conferir no painel da Pluggy)."""
    cfg = PluggyConfig.query.filter_by(user_id=user_id).first()
    try:
        if cfg is not None:
            drop_connections(cfg)
        release_key(user_id)
        return True
    except Exception as exc:
        log.warning('Desconectar os bancos de %s na Pluggy falhou: %s', user_id, exc)
        return False


def delete_member(user: User) -> bool:
    """Devolve se os bancos foram desconectados na Pluggy sem erro."""
    user_id = user.id
    banks_ok = disconnect_banks(user_id)
    db.session.delete(user)
    db.session.commit()
    log.info('Conta %s excluída pela própria pessoa.', user_id)
    return banks_ok


def drop_family_backups(tenant_id: int) -> None:
    """Os backups da família que o admin gerou (instance/backups) vão embora junto."""
    from app.services.family_backup import BACKUP_DIR
    for path in BACKUP_DIR.glob(f'familia_{tenant_id}_*.json.gz') if BACKUP_DIR.exists() else []:
        path.unlink(missing_ok=True)


def delete_family(tenant: Tenant) -> tuple[int, bool]:
    """(pessoas excluídas, bancos desconectados na Pluggy sem erro)."""
    members = User.query.filter_by(tenant_id=tenant.id).all()
    tenant_id, count = tenant.id, len(members)
    banks_ok = all([disconnect_banks(u.id) for u in members])
    db.session.delete(tenant)  # leva as pessoas e tudo o que é delas
    db.session.commit()
    drop_family_backups(tenant_id)
    log.info('Família %s (%s pessoa(s)) excluída por quem a criou.', tenant_id, count)
    return count, banks_ok
