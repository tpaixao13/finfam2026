"""Boas-vindas com confirmação de e-mail e "esqueci minha senha"."""
import re

import pytest

from app.core.db import session
from app.models import User
from app.services import mailer
from conftest import API


@pytest.fixture()
def outbox(monkeypatch):
    """Liga o e-mail e guarda o que seria enviado (sem SMTP)."""
    sent = []
    monkeypatch.setattr(mailer, 'enabled', lambda: True)
    monkeypatch.setattr(mailer, '_deliver', lambda msg: sent.append(msg))
    return sent


def _link(msg, path):
    body = msg.get_body(preferencelist=('plain',)).get_content()
    return re.search(rf'/{path}\?token=([\w.\-]+)', body).group(1)


def _register(client, email='nova@test.com'):
    return client.post(f'{API}/auth/register', json={'name': 'Maria Souza', 'email': email,
                                                     'password': 'senha-segura', 'confirm_password': 'senha-segura'})


def test_register_sends_welcome_and_login_waits_for_confirmation(client, outbox):
    r = _register(client)
    assert r.status_code == 201 and r.json()['verify_required'] and 'access_token' not in r.json()
    assert len(outbox) == 1 and outbox[0]['To'] == 'nova@test.com' and 'Bem-vindo' in outbox[0]['Subject']
    html = outbox[0].get_body(preferencelist=('html',)).get_content()
    assert 'Confirmar e-mail' in html and 'Maria' in html
    login = client.post(f'{API}/auth/login', json={'email': 'nova@test.com', 'password': 'senha-segura'})
    assert login.status_code == 403 and login.json()['detail']['code'] == 'email_not_verified'
    # reenviar: resposta igual exista ou não a conta
    assert client.post(f'{API}/auth/verify/resend', json={'email': 'nova@test.com'}).status_code == 200
    assert client.post(f'{API}/auth/verify/resend', json={'email': 'ninguem@test.com'}).json()['message'] == \
        client.post(f'{API}/auth/verify/resend', json={'email': 'nova@test.com'}).json()['message']
    token = _link(outbox[0], 'confirmar-email')
    v = client.post(f'{API}/auth/verify', json={'token': token})
    assert v.status_code == 200 and v.json()['access_token']
    assert client.post(f'{API}/auth/login', json={'email': 'nova@test.com', 'password': 'senha-segura'}).status_code == 200
    assert client.post(f'{API}/auth/verify', json={'token': 'x' * 20}).status_code == 400


def test_forgot_and_reset_password(client, family, outbox):
    fam = family  # criada antes de ligar o e-mail: já confirmada
    same = client.post(f'{API}/auth/forgot-password', json={'email': 'naoexiste@test.com'})
    r = client.post(f'{API}/auth/forgot-password', json={'email': 'tiago@test.com'})
    assert r.status_code == 200 and r.json()['message'] == same.json()['message']
    assert len(outbox) == 1 and 'nova senha' in outbox[0]['Subject']
    token = _link(outbox[0], 'redefinir-senha')
    bad = client.post(f'{API}/auth/reset-password', json={'token': token, 'password': 'curta', 'confirm_password': 'curta'})
    assert bad.status_code == 422
    ok = client.post(f'{API}/auth/reset-password', json={'token': token, 'password': 'nova-senha-123',
                                                         'confirm_password': 'nova-senha-123'})
    assert ok.status_code == 200
    again = client.post(f'{API}/auth/reset-password', json={'token': token, 'password': 'outra-senha-123',
                                                            'confirm_password': 'outra-senha-123'})
    assert again.status_code == 400  # uso único
    assert fam.get('/auth/me').status_code == 401  # sessão antiga caiu
    assert client.post(f'{API}/auth/login', json={'email': 'tiago@test.com', 'password': 'nova-senha-123'}).status_code == 200
    session.remove()


def test_forgot_without_mail_configured(client):
    r = client.post(f'{API}/auth/forgot-password', json={'email': 'tiago@test.com'})
    assert r.status_code == 503


def test_member_gets_welcome_and_owner_can_resend(client, family, outbox):
    from app.models import Tenant
    session.get(Tenant, family.tenant['id']).plan = 'anual'
    session.commit()
    session.remove()
    r = family.post('/family/members', json={'name': 'Greyce', 'email': 'greyce@test.com', 'password': 'senha-segura', 'confirm_password': 'senha-segura'})
    assert r.status_code == 201, r.text
    assert r.json()['member']['email_verified'] is False
    assert 'Tiago adicionou você' in outbox[-1].get_body(preferencelist=('plain',)).get_content()
    assert client.post(f'{API}/auth/login', json={'email': 'greyce@test.com', 'password': 'senha-segura', 'confirm_password': 'senha-segura'}).status_code == 403
    assert family.post(f"/family/members/{r.json()['member']['id']}/resend").status_code == 200
    assert len(outbox) == 2
