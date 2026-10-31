"""Arquivos de estado em JSON que os scripts do cron escrevem (monitor e backup) e o painel do admin lê."""
import json
from pathlib import Path


def read_state(path: Path) -> dict | None:
    """O conteúdo do arquivo, ou None se ele não existe ou está ilegível."""
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def write_state(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1))
