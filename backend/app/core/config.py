"""Configuração lida de variáveis de ambiente (e do arquivo .env, se existir)."""
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # python-dotenv é opcional
    pass

BACKEND_DIR = Path(__file__).resolve().parents[2]
INSTANCE_DIR = BACKEND_DIR / 'instance'


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ('1', 'true', 'yes', 'sim', 'on')


def _persistent_secret() -> str:
    """Em desenvolvimento, gera uma SECRET_KEY e a guarda em instance/.secret_key
    para que tokens e campos criptografados sobrevivam a reinícios."""
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    key_file = INSTANCE_DIR / '.secret_key'
    if key_file.exists():
        return key_file.read_text().strip()
    key = secrets.token_hex(32)
    key_file.write_text(key)
    return key


@dataclass(frozen=True)
class Settings:
    env: str
    secret_key: str
    database_url: str
    access_token_minutes: int
    cors_origins: list[str]
    upload_dir: Path
    frontend_dist: Path | None
    admin_email: str
    admin_initial_password: str | None
    scheduler_enabled: bool
    insecure_external_ssl: bool
    trial_days: int = 90
    api_prefix: str = '/api/v1'
    extra: dict = field(default_factory=dict)

    @property
    def is_production(self) -> bool:
        return self.env == 'production'


def load_settings() -> Settings:
    env = os.environ.get('FINFAM_ENV', 'development').strip().lower()

    secret = os.environ.get('SECRET_KEY', '').strip()
    if not secret:
        if env == 'production':
            raise RuntimeError('SECRET_KEY deve ser definida em produção.')
        secret = _persistent_secret()

    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    default_db = 'sqlite:///' + (INSTANCE_DIR / 'gastos.db').as_posix()

    origins = os.environ.get('CORS_ORIGINS', 'http://localhost:5173,http://127.0.0.1:5173')
    dist = os.environ.get('FRONTEND_DIST', str(BACKEND_DIR.parent / 'frontend' / 'dist'))

    return Settings(
        env=env,
        secret_key=secret,
        database_url=os.environ.get('DATABASE_URL', default_db),
        access_token_minutes=int(os.environ.get('ACCESS_TOKEN_MINUTES', 60 * 24 * 7)),
        cors_origins=[o.strip() for o in origins.split(',') if o.strip()],
        upload_dir=Path(os.environ.get('UPLOAD_DIR', str(INSTANCE_DIR / 'uploads'))),
        frontend_dist=Path(dist) if dist else None,
        admin_email=os.environ.get('ADMIN_EMAIL', 'admin@finfam.app').strip().lower(),
        admin_initial_password=os.environ.get('ADMIN_INITIAL_PASSWORD') or None,
        scheduler_enabled=_bool('SCHEDULER_ENABLED', True),
        insecure_external_ssl=_bool('INSECURE_EXTERNAL_SSL', False),
        trial_days=int(os.environ.get('TRIAL_DAYS', 90)),
    )


settings = load_settings()
