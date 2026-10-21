import json
import os
from dataclasses import asdict, dataclass, field

import keyring
from keyring.errors import KeyringError

from .paths import APP_NAME, data_dir

KEYRING_USER = "anthropic_api_key"

# Paper types the app will currently recommend. Academic papers are switched off for now;
# add "academic" back here to re-enable them (saved configs are filtered against this).
ENABLED_PAPER_TYPES = ("industry",)


@dataclass
class Config:
    interests: str = ""
    # "academic" (journals, conferences, arXiv) and/or "industry" (vendor/engineering whitepapers)
    paper_types: list[str] = field(default_factory=lambda: list(ENABLED_PAPER_TYPES))
    model: str = "claude-opus-5-5"

    @classmethod
    def load(cls) -> "Config":
        path = data_dir() / "config.json"
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        config = cls(**known)
        config.paper_types = [t for t in config.paper_types if t in ENABLED_PAPER_TYPES] or list(ENABLED_PAPER_TYPES)
        return config

    def save(self) -> None:
        (data_dir() / "config.json").write_text(json.dumps(asdict(self), indent=2))


def get_api_key() -> str | None:
    """Keychain first, then ANTHROPIC_API_KEY. None means let the SDK resolve credentials."""
    try:
        key = keyring.get_password(APP_NAME, KEYRING_USER)
    except KeyringError:
        key = None
    return key or os.environ.get("ANTHROPIC_API_KEY")


def set_api_key(key: str) -> None:
    keyring.set_password(APP_NAME, KEYRING_USER, key.strip())
