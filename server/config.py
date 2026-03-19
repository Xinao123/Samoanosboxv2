"""SamoanosBox v2 - Config"""
import os
from pathlib import Path

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/data/uploads"))
DB_PATH = Path(os.getenv("DB_PATH", "/data/samoanosbox.db"))
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "7000"))
CHUNK_SIZE = 1024 * 1024

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
