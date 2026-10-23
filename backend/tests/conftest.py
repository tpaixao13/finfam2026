import os
import sys
import tempfile
from pathlib import Path

import pytest

_tmp = tempfile.mkdtemp(prefix='finfam-test-')
os.environ.setdefault('DATABASE_URL', f'sqlite:///{_tmp}/test.db')
os.environ.setdefault('SECRET_KEY', 'test-secret-key-' + 'x' * 32)
os.environ.setdefault('SCHEDULER_ENABLED', '0')
os.environ.setdefault('ADMIN_EMAIL', 'admin@finfam.test')
os.environ.setdefault('ADMIN_INITIAL_PASSWORD', 'admin-pass-123')
os.environ.setdefault('UPLOAD_DIR', f'{_tmp}/uploads')
os.environ.setdefault('FRONTEND_DIST', '')
os.environ.setdefault('RATE_LIMIT_ENABLED', '0')

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.core import db as db_module  # noqa: E402
from app.core.db import Base  # noqa: E402
from app.main import app  # noqa: E402

API = '/api/v1'


@pytest.fixture()
def client():
    engine = db_module.get_engine()
    Base.metadata.drop_all(engine)
    with TestClient(app) as c:  # lifespan roda as migrações e cria o admin
        yield c
    db_module.session.remove()


class Family:
    """Ajuda a montar uma família e fazer chamadas autenticadas."""

    def __init__(self, client, name='Tiago', email='tiago@test.com'):
        self.client = client
        r = client.post(f'{API}/auth/register', json={
            'name': name, 'email': email, 'password': 'senha-segura', 'confirm_password': 'senha-segura'})
        assert r.status_code == 201, r.text
        body = r.json()
        self.user = body['user']
        self.tenant = body['tenant']
        self.headers = {'Authorization': f"Bearer {body['access_token']}"}

    def get(self, path, **kw):
        return self.client.get(API + path, headers=self.headers, **kw)

    def post(self, path, **kw):
        return self.client.post(API + path, headers=self.headers, **kw)

    def put(self, path, **kw):
        return self.client.put(API + path, headers=self.headers, **kw)

    def patch(self, path, **kw):
        return self.client.patch(API + path, headers=self.headers, **kw)

    def delete(self, path, **kw):
        return self.client.delete(API + path, headers=self.headers, **kw)


@pytest.fixture()
def family(client):
    return Family(client)


@pytest.fixture()
def other_family(client):
    return Family(client, name='Outra', email='outra@test.com')
