"""Testa o envio de e-mail (SMTP do .env) mandando uma mensagem para o endereço informado.

Uso, na pasta backend do servidor:
    .venv/bin/python -m scripts.test_email voce@exemplo.com
"""
import sys

from app.core.config import settings
from app.services import mailer


def main(argv: list[str]) -> int:
    if len(argv) != 1 or '@' not in argv[0]:
        print(__doc__)
        return 2
    if not mailer.enabled():
        print('E-mail desligado: preencha SMTP_HOST, SMTP_USER, SMTP_PASSWORD e SMTP_FROM no .env.')
        return 1
    print(f'Enviando por {settings.smtp_host}:{settings.smtp_port} ({settings.smtp_security}) como {mailer._sender()}…')
    mailer.BACKGROUND = False
    mailer.log.addHandler(__import__('logging').StreamHandler())
    mailer.log.setLevel('INFO')
    link = (settings.public_url or 'https://finfam.net.br') + '/login'
    mailer.send(argv[0], 'FinFam — teste de e-mail', f'Se você recebeu esta mensagem, o e-mail do FinFam está funcionando.\n{link}',
                mailer._layout('Teste de e-mail', ['Se você recebeu esta mensagem, o e-mail do FinFam está funcionando.'],
                               ('Abrir o FinFam', link), 'Mensagem de teste.'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
