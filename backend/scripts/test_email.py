"""Testa o envio de e-mail (SMTP do .env) mandando uma mensagem para o endereço informado.

Uso, na pasta backend do servidor (sem sudo):
    .venv/bin/python -m scripts.test_email voce@exemplo.com                  # teste simples
    .venv/bin/python -m scripts.test_email voce@exemplo.com --boas-vindas    # como chega no cadastro
    .venv/bin/python -m scripts.test_email voce@exemplo.com --convite        # membro adicionado pela família
    .venv/bin/python -m scripts.test_email voce@exemplo.com --senha          # "esqueci minha senha"

Os modelos vão com um link de exemplo (não confirma nem troca senha de ninguém).
"""
import logging
import sys

from app.core.config import settings
from app.services import mailer


def main(argv: list[str]) -> int:
    to = next((a for a in argv if '@' in a), None)
    kind = next((a for a in argv if a.startswith('--')), '--teste')
    if not to or kind not in ('--teste', '--boas-vindas', '--convite', '--senha'):
        print(__doc__)
        return 2
    if not mailer.enabled():
        print('E-mail desligado: preencha SMTP_HOST, SMTP_USER, SMTP_PASSWORD, SMTP_FROM e PUBLIC_URL no .env.')
        return 1
    print(f'Enviando "{kind[2:]}" por {settings.smtp_host}:{settings.smtp_port} ({settings.smtp_security}) '
          f'como {mailer._sender()}…')
    mailer.BACKGROUND = False
    mailer.log.addHandler(logging.StreamHandler())
    mailer.log.setLevel('INFO')
    base = settings.public_url
    if kind == '--boas-vindas':
        mailer.send_welcome(to, 'Maria Exemplo', 'Família Exemplo', f'{base}/confirmar-email?token=exemplo')
    elif kind == '--convite':
        mailer.send_welcome(to, 'Maria Exemplo', 'Família Exemplo', f'{base}/confirmar-email?token=exemplo',
                            invited_by='Tiago')
    elif kind == '--senha':
        mailer.send_reset(to, 'Maria Exemplo', f'{base}/redefinir-senha?token=exemplo')
    else:
        mailer.send(to, 'FinFam — teste de e-mail', f'Se você recebeu esta mensagem, o e-mail do FinFam está funcionando.\n{base}/login',
                    mailer._layout('Teste de e-mail', ['Se você recebeu esta mensagem, o e-mail do FinFam está funcionando.'],
                                   ('Abrir o FinFam', f'{base}/login'), 'Mensagem de teste.'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
