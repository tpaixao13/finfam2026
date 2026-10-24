"""Recuperação do 2FA pelo servidor (ex.: o administrador perdeu o celular ou foi bloqueado).

Uso, na pasta backend do servidor:
    .venv/bin/python -m scripts.reset_2fa admin@finfam.app            # zera os erros e desbloqueia
    .venv/bin/python -m scripts.reset_2fa admin@finfam.app --remover  # remove o 2FA (entra só com a senha)

Só quem tem acesso ao servidor consegue rodar — por isso é o caminho para contas de administrador.
"""
import sys

from app.core.db import session
from app.core.migrations import run_migrations
from app.models import User
from app.services import two_factor as tfa


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith('--')]
    if len(args) != 1:
        print(__doc__)
        return 2
    run_migrations()  # garante as colunas do 2FA num banco antigo
    user = User.query.filter_by(email=args[0].strip().lower()).first()
    if user is None:
        print(f'Nenhum usuário com o e-mail {args[0]}.')
        return 1
    if '--remover' in argv:
        tfa.reset(user)
        msg = f'2FA removido de {user.name} ({user.email}). Entra só com a senha e pode ativar de novo.'
    else:
        tfa.unlock(user)
        msg = f'Erros do 2FA de {user.name} ({user.email}) zerados e conta desbloqueada.'
    session.commit()
    print(msg)
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
