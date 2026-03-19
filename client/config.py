"""SamoanosBox v2 - Client Config"""
import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".samoanosbox"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULTS = {
    "server_url": "http://localhost:7000",
    "username": "",
    "download_dir": str(Path.home() / "Downloads"),
    "shared_files": {},  # file_id → local_path
}


def load_config() -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                return {**DEFAULTS, **json.load(f)}
        except Exception:
            pass
    return DEFAULTS.copy()


def save_config(cfg: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
