"""Loads config.toml and .env into one Settings object."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Settings:
    raw: dict
    anthropic_api_key: str | None = None
    bitpanda_api_key: str | None = None
    simulate: bool = False  # offline mode: synthetic prices, for tests and demos
    db_path: Path = field(default_factory=lambda: DATA_DIR / "tradingbotty.db")

    def __getitem__(self, section: str) -> dict:
        return self.raw[section]


def load_settings(config_path: Path | None = None) -> Settings:
    _load_dotenv(ROOT / ".env")
    path = config_path or ROOT / "config.toml"
    raw = tomllib.loads(path.read_text())
    DATA_DIR.mkdir(exist_ok=True)
    return Settings(
        raw=raw,
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
        bitpanda_api_key=os.environ.get("BITPANDA_API_KEY") or None,
        simulate=os.environ.get("TB_SIMULATE", "0") == "1",
        db_path=Path(os.environ.get("TB_DB", DATA_DIR / "tradingbotty.db")),
    )
