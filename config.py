import json
import os
from pathlib import Path

_PATH = Path.home() / ".config" / "claude-usage-menu" / "config.json"


def get_key() -> str:
    return _load().get("admin_api_key") or os.environ.get("ANTHROPIC_ADMIN_KEY", "")


def set_key(key: str) -> None:
    data = _load()
    data["admin_api_key"] = key
    _save(data)


def _load() -> dict:
    if _PATH.exists():
        try:
            return json.loads(_PATH.read_text())
        except Exception:
            return {}
    return {}


def _save(data: dict) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(json.dumps(data, indent=2))
