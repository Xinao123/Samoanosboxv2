"""
SamoanosBox - Auto Updater
Checa GitHub Releases pra versão nova.
"""
import httpx
import webbrowser
import subprocess
import tempfile
import sys
import os
from pathlib import Path

CURRENT_VERSION = "2.2.3"
GITHUB_REPO = "Xinao123/Samoanosboxv2"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def parse_version(v: str) -> tuple:
    """Converte '2.1.0' ou 'v2.1.0' em (2, 1, 0) pra comparacao."""
    v = v.strip().lstrip("vV")
    parts = []
    for p in v.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def check_for_update() -> dict | None:
    """
    Checa se tem versão nova no GitHub.
    Retorna dict com info da release ou None se está atualizado.
    
    Retorno:
    {
        "version": "2.2.0",
        "changelog": "- Correção de bugs\n- Nova feature",
        "download_url": "https://github.com/.../SamoanosBox_Setup_v2.2.0.exe",
        "browser_url": "https://github.com/.../releases/tag/v2.2.0",
    }
    """
    try:
        with httpx.Client(timeout=10) as c:
            resp = c.get(GITHUB_API_URL, headers={"Accept": "application/vnd.github.v3+json"})
            if resp.status_code != 200:
                return None

            data = resp.json()
            remote_tag = data.get("tag_name", "")
            remote_version = parse_version(remote_tag)
            local_version = parse_version(CURRENT_VERSION)

            if remote_version <= local_version:
                return None

            # Procura asset .exe (instalador)
            download_url = ""
            for asset in data.get("assets", []):
                name = asset.get("name", "").lower()
                if name.endswith(".exe") or name.endswith(".zip"):
                    download_url = asset.get("browser_download_url", "")
                    break

            return {
                "version": remote_tag.lstrip("vV"),
                "changelog": data.get("body", "Sem detalhes"),
                "download_url": download_url,
                "browser_url": data.get("html_url", f"https://github.com/{GITHUB_REPO}/releases"),
            }

    except Exception:
        return None


def open_release_page(url: str):
    """Abre a pagina do release no browser."""
    webbrowser.open(url)


def download_and_install(download_url: str, on_progress=None) -> bool:
    """
    Baixa o instalador e executa.
    Retorna True se iniciou a instalação.
    """
    if not download_url:
        return False

    try:
        filename = download_url.split("/")[-1]
        temp_dir = Path(tempfile.gettempdir()) / "samoanosbox_update"
        temp_dir.mkdir(exist_ok=True)
        save_path = temp_dir / filename

        # Baixa o arquivo
        with httpx.Client(timeout=300, follow_redirects=True) as c:
            with c.stream("GET", download_url) as resp:
                if resp.status_code >= 400:
                    return False

                total = int(resp.headers.get("content-length", 0))
                received = 0

                with open(save_path, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                        f.write(chunk)
                        received += len(chunk)
                        if on_progress and total > 0:
                            on_progress(received, total)

        # Executa o instalador
        if save_path.suffix.lower() == ".exe":
            subprocess.Popen([str(save_path)], shell=True)
            return True
        else:
            # Se for .zip, abre a pasta
            os.startfile(str(temp_dir)) if sys.platform == "win32" else None
            return True

    except Exception:
        return False
