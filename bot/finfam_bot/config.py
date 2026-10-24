"""Configuração do bot. Lê do backend/.env SÓ as variáveis do bot — o processo do bot,
exposto a qualquer usuário do Telegram, não precisa da SECRET_KEY nem de outros segredos."""
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


def _clean(raw: str) -> str:
    """Remove comentário no fim da linha ("VALOR   # comentário") e aspas."""
    value = re.split(r'(?:^|\s+)#', raw.strip(), maxsplit=1)[0].strip()
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'' else value


BOT_VARS = ('TELEGRAM_BOT_TOKEN', 'BOT_API_KEY', 'FINFAM_API_URL', 'FINFAM_SITE_URL')


def load_env_file(path: Path, only: tuple[str, ...] = BOT_VARS) -> None:
    """Carrega do .env apenas `only`, sem sobrescrever o que já está no ambiente."""
    if not path.is_file():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        if key.strip() in only:
            os.environ.setdefault(key.strip(), value)


def _env(name: str, default: str = '') -> str:
    return _clean(os.environ.get(name, default))


@dataclass(frozen=True)
class Config:
    telegram_token: str
    bot_api_key: str
    api_url: str
    site_url: str

    @classmethod
    def from_env(cls) -> 'Config':
        load_env_file(Path(os.environ.get('FINFAM_ENV_FILE', Path(__file__).resolve().parents[2] / 'backend' / '.env')))
        cfg = cls(telegram_token=_env('TELEGRAM_BOT_TOKEN'), bot_api_key=_env('BOT_API_KEY'),
                  api_url=_env('FINFAM_API_URL', 'http://127.0.0.1:8001/api/v1').rstrip('/'),
                  site_url=_env('FINFAM_SITE_URL').rstrip('/'))
        missing = [n for n, v in (('TELEGRAM_BOT_TOKEN', cfg.telegram_token), ('BOT_API_KEY', cfg.bot_api_key)) if not v]
        if missing:
            raise SystemExit(f'Defina {", ".join(missing)} no backend/.env (veja o .env.example).')
        if len(cfg.bot_api_key) < 32 or len(set(cfg.bot_api_key)) < 10:
            raise SystemExit('BOT_API_KEY precisa ter pelo menos 32 caracteres variados '
                             '(gere com: python3 -c "import secrets; print(secrets.token_urlsafe(40))").')
        host = urlsplit(cfg.api_url)
        if host.scheme != 'https' and host.hostname not in ('127.0.0.1', 'localhost', '::1'):
            raise SystemExit('FINFAM_API_URL precisa ser http://127.0.0.1:... ou https:// (a chave do bot não pode ir sem criptografia).')
        return cfg
