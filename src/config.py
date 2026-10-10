from __future__ import annotations

import os
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("MWP_DATA_DIR", REPO_DIR / ".mwp"))
DB_PATH = DATA_DIR / "mwp.sqlite3"

KEYRING_SERVICE = "me-want-pickle"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5151


def ensure_data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR
