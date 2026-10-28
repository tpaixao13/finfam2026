"""E-mail do FinFam (SMTP: Locaweb, Gmail…): boas-vindas com confirmação e esqueci a senha.

Configuração no .env (ver .env.example). Sem SMTP_HOST o e-mail fica desligado: o cadastro
não pede confirmação e "esqueci a senha" responde que o recurso não está disponível.
"""
import html
import logging
import smtplib
import ssl
import threading
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr

from app.core.config import settings

log = logging.getLogger('finfam.mail')
BACKGROUND = True  # testes desligam: o envio vira chamada direta (e é trocado por um falso)


def enabled() -> bool:
    """Precisa do SMTP e do PUBLIC_URL: os links dos e-mails nunca usam o endereço que veio no pedido
    (um Host forjado mandaria o link de nova senha para o site de outra pessoa)."""
    return bool(settings.smtp_host and (settings.smtp_from or settings.smtp_user) and settings.public_url)


def _sender() -> str:
    name, addr = parseaddr(settings.smtp_from or settings.smtp_user)
    return formataddr((name or 'FinFam', addr))


def _deliver(msg: EmailMessage) -> None:
    host, port, mode = settings.smtp_host, settings.smtp_port, settings.smtp_security
    ctx = ssl.create_default_context()
    if mode == 'ssl':
        server = smtplib.SMTP_SSL(host, port, context=ctx, timeout=20)
    else:
        server = smtplib.SMTP(host, port, timeout=20)
        if mode == 'starttls':
            server.starttls(context=ctx)
    with server:
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(msg)


def send(to: str, subject: str, text: str, html_body: str) -> None:
    """Manda em segundo plano (o cadastro não espera o servidor de e-mail). Falha só vai pro log."""
    msg = EmailMessage()
    msg['From'], msg['To'], msg['Subject'] = _sender(), to, subject
    msg['Message-ID'] = make_msgid(domain=parseaddr(_sender())[1].split('@')[-1] or 'finfam')
    msg.set_content(text)
    msg.add_alternative(html_body, subtype='html')

    def run():
        try:
            _deliver(msg)
            log.info('E-mail "%s" enviado.', subject)
        except Exception as exc:  # noqa: BLE001 — nunca derruba a API
            log.warning('E-mail "%s" não foi enviado: %s', subject, exc)
    if BACKGROUND:
        threading.Thread(target=run, daemon=True, name='finfam-mail').start()
    else:
        run()


def _layout(title: str, lines: list[str], button: tuple[str, str], foot: str) -> str:
    """HTML simples e compatível com clientes de e-mail (tabela, estilos inline)."""
    label, url = button
    body = ''.join(f'<p style="margin:0 0 14px;font-size:15px;line-height:1.5;color:#1b2733">{line}</p>' for line in lines)
    return f"""<!doctype html><html lang="pt-BR"><body style="margin:0;background:#f1f4f6;font-family:Arial,Helvetica,sans-serif">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f1f4f6;padding:24px 12px"><tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;background:#ffffff;border-radius:12px;overflow:hidden">
<tr><td style="background:#13293d;padding:18px 24px;color:#ffffff;font-size:20px;font-weight:bold">FinFam</td></tr>
<tr><td style="padding:24px">
<h1 style="margin:0 0 16px;font-size:20px;color:#13293d">{html.escape(title)}</h1>{body}
<p style="margin:22px 0"><a href="{html.escape(url)}" style="background:#0e6f7e;color:#ffffff;text-decoration:none;padding:12px 20px;border-radius:8px;font-weight:bold;display:inline-block">{html.escape(label)}</a></p>
<p style="margin:0 0 6px;font-size:12px;color:#5d6e7e">Se o botão não abrir, copie este endereço no navegador:</p>
<p style="margin:0 0 18px;font-size:12px;color:#0e6f7e;word-break:break-all">{html.escape(url)}</p>
<p style="margin:0;font-size:12px;color:#5d6e7e">{foot}</p>
</td></tr></table>
<p style="font-size:11px;color:#5d6e7e;margin:14px 0 0">Este e-mail é automático, não precisa responder.</p>
</td></tr></table></body></html>"""


def send_welcome(to: str, name: str, family: str | None, link: str, invited_by: str | None = None) -> None:
    first = html.escape((name or '').split()[0] if name else '')
    if invited_by:
        intro = f'{html.escape(invited_by)} adicionou você à <b>{html.escape(family or "família")}</b> no FinFam.'
    else:
        intro = 'Sua conta no FinFam foi criada. Aqui você organiza gastos, cartões, contas a pagar e metas da família.'
    lines = [f'Olá, {first}!', intro, 'Para começar, confirme que este e-mail é seu:']
    foot = 'O link vale por 48 horas. Se você não criou esta conta, ignore este e-mail.'
    text = (f'Olá, {(name or "").split()[0] if name else ""}!\n\n'
            + (f'{invited_by} adicionou você à {family or "família"} no FinFam.\n\n' if invited_by
               else 'Sua conta no FinFam foi criada.\n\n')
            + f'Confirme seu e-mail neste link (vale 48 horas):\n{link}\n\nSe você não criou esta conta, ignore este e-mail.')
    send(to, 'Bem-vindo ao FinFam — confirme seu e-mail', text,
         _layout('Bem-vindo ao FinFam', lines, ('Confirmar e-mail', link), foot))


def send_reset(to: str, name: str, link: str) -> None:
    first = (name or '').split()[0] if name else ''
    lines = [f'Olá, {html.escape(first)}!', 'Recebemos um pedido para criar uma nova senha da sua conta no FinFam.']
    foot = ('O link vale por 1 hora e só pode ser usado uma vez. Se não foi você, ignore este e-mail: '
            'sua senha continua a mesma.')
    text = (f'Olá, {first}!\n\nPara criar uma nova senha do FinFam, abra este link (vale 1 hora):\n{link}\n\n'
            'Se não foi você, ignore este e-mail: sua senha continua a mesma.')
    send(to, 'FinFam — criar uma nova senha', text, _layout('Criar uma nova senha', lines, ('Criar nova senha', link), foot))
