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
BASE_DIR = Path(__file__).resolve().parent


def build():
    dist_dir = BASE_DIR / "dist"
    build_dir = BASE_DIR / "build"

    for d in [dist_dir, build_dir]:
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)

    spec = BASE_DIR / f"{APP_NAME}.spec"
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
        "--distpath", str(dist_dir),
        "--workpath", str(build_dir),
        "--icon", str(BASE_DIR / "assets" / "icon.ico"),
        "--add-data", f"{BASE_DIR / 'config.py'}{sep}.",
        "--add-data", f"{BASE_DIR / 'api_client.py'}{sep}.",
        "--add-data", f"{BASE_DIR / 'p2p_server.py'}{sep}.",
        "--hidden-import", "flet",
        "--hidden-import", "websocket",
        "--hidden-import", "pystray",
        "--hidden-import", "PIL",
        str(BASE_DIR / "main.py"),
    ]

    print(f"[BUILD] {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=BASE_DIR)

    if result.returncode == 0:
        print(f"\n[OK] Executavel em: {BASE_DIR / 'dist' / APP_NAME}")
        print(f"[OK] Agora compile installer/samoanosbox.nsi no NSIS")
    else:
        print(f"\n[ERRO] Build falhou ({result.returncode})")
        sys.exit(1)


if __name__ == "__main__":
    build()
