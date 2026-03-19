"""
SamoanosBox - Build Script
Gera o .exe com PyInstaller.

    pip install pyinstaller
    python build.py
"""
import subprocess
import sys
import shutil
from pathlib import Path

APP_NAME = "SamoanosBox"


def build():
    for d in [Path("dist"), Path("build")]:
        if d.exists():
            shutil.rmtree(d)
    spec = Path(f"{APP_NAME}.spec")
    if spec.exists():
        spec.unlink()

    sep = ";" if sys.platform == "win32" else ":"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", APP_NAME,
        "--onedir",
        "--windowed",
        "--noconfirm",
        "--clean",
        # "--icon", "assets/icon.ico",
        "--add-data", f"config.py{sep}.",
        "--add-data", f"api_client.py{sep}.",
        "--add-data", f"p2p_server.py{sep}.",
        "--hidden-import", "flet",
        "--hidden-import", "websocket",
        "--hidden-import", "pystray",
        "--hidden-import", "PIL",
        "main.py",
    ]

    print(f"[BUILD] {' '.join(cmd)}")
    result = subprocess.run(cmd)

    if result.returncode == 0:
        print(f"\n[OK] Executavel em: dist/{APP_NAME}/")
        print(f"[OK] Agora compile installer/samoanosbox.nsi no NSIS")
    else:
        print(f"\n[ERRO] Build falhou ({result.returncode})")
        sys.exit(1)


if __name__ == "__main__":
    build()
