"""
SamoanosBox v2 - API Client
Download inteligente: P2P direto se peer online, server se offline.
"""
import httpx
import hashlib
import time
from pathlib import Path
from typing import Callable

CHUNK_SIZE = 1024 * 1024


class ApiError(Exception):
    def __init__(self, code: int, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"[{code}] {detail}")


class SamoanosBoxClient:
    def __init__(self, server_url: str, username: str = ""):
        self.server_url = server_url.rstrip("/")
        self.username = username

    @property
    def _h(self):
        return {"X-Username": self.username, "Accept": "application/json"}

    def _url(self, path: str) -> str:
        return f"{self.server_url}{path}"

    def _check(self, r: httpx.Response) -> dict:
        if r.status_code >= 400:
            try:
                d = r.json().get("detail", r.text)
            except Exception:
                d = r.text
            raise ApiError(r.status_code, d)
        return r.json()

    def health(self) -> dict:
        with httpx.Client(timeout=10) as c:
            return self._check(c.get(self._url("/api/health")))

    def list_files(self) -> list[dict]:
        with httpx.Client(timeout=15) as c:
            return self._check(c.get(self._url("/api/files"), headers=self._h))["files"]

    def delete_file(self, file_id: int):
        with httpx.Client(timeout=15) as c:
            return self._check(c.delete(self._url(f"/api/files/{file_id}"), headers=self._h))

    # ── Registrar arquivo (P2P, sem upload) ──

    def register_file(self, original_name: str, size: int, checksum: str = "") -> int:
        with httpx.Client(timeout=15) as c:
            data = self._check(c.post(
                self._url("/api/files/register"),
                json={"original_name": original_name, "size": size, "checksum": checksum},
                headers=self._h,
            ))
            return data["file_id"]

    # ── Upload pro server (fallback em background) ──

    def upload_to_server(self, file_id: int, file_path: str,
                         on_progress: Callable[[int, int, float], None] | None = None):
        path = Path(file_path)
        total = path.stat().st_size

        with httpx.Client(timeout=15) as c:
            init = self._check(c.post(
                self._url("/api/upload/init"),
                json={"filename": path.name, "total_size": total},
                headers=self._h,
            ))

        upload_id = init["upload_id"]
        sent = 0
        t0 = time.monotonic()

        with open(path, "rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                headers = {**self._h, "Content-Type": "application/octet-stream"}
                with httpx.Client(timeout=300) as c:
                    self._check(c.post(self._url(f"/api/upload/{upload_id}/chunk"),
                                       content=chunk, headers=headers))
                sent += len(chunk)
                elapsed = time.monotonic() - t0
                speed = (sent / elapsed / 1e6) if elapsed > 0 else 0
                if on_progress:
                    on_progress(sent, total, speed)

        with httpx.Client(timeout=60) as c:
            self._check(c.post(self._url(f"/api/upload/{upload_id}/complete/{file_id}"),
                               headers=self._h))

    # ── Download inteligente: P2P ou server ──

    def download_file(self, file_info: dict, save_dir: str,
                      on_progress: Callable[[int, int, float], None] | None = None,
                      on_status: Callable[[str], None] | None = None) -> str:
        """
        Tenta P2P direto. Se falhar, cai pro server.
        Retorna caminho do arquivo salvo.
        """
        file_id = file_info["id"]
        filename = file_info["original_name"]
        total = file_info["size"]
        p2p_host = file_info.get("p2p_host", "")
        p2p_port = file_info.get("p2p_port", 0)
        on_server = file_info.get("on_server", False)
        is_online = file_info.get("uploader_online", False)

        # Tenta P2P direto
        if is_online and p2p_host and p2p_port:
            try:
                if on_status:
                    on_status(f"P2P direto de {file_info.get('uploader', '?')}...")
                return self._download_from_peer(
                    p2p_host, p2p_port, file_id, filename, total, save_dir, on_progress)
            except Exception as e:
                if on_status:
                    on_status(f"P2P falhou ({e}), tentando server...")

        # Fallback pro server
        if on_server:
            if on_status:
                on_status("Baixando do server (pode ser mais lento)...")
            return self._download_from_server(file_id, filename, total, save_dir, on_progress)

        raise ApiError(404, "Dono offline e arquivo nao esta no server ainda")

    def _download_from_peer(self, host, port, file_id, filename, total, save_dir, on_progress) -> str:
        url = f"http://{host}:{port}/download/{file_id}"
        return self._stream_download(url, filename, total, save_dir, on_progress, {})

    def _download_from_server(self, file_id, filename, total, save_dir, on_progress) -> str:
        url = self._url(f"/api/files/{file_id}/download")
        return self._stream_download(url, filename, total, save_dir, on_progress, self._h)

    def _stream_download(self, url, filename, total, save_dir, on_progress, headers) -> str:
        save_path = Path(save_dir) / filename
        save_path.parent.mkdir(parents=True, exist_ok=True)

        if save_path.exists():
            stem, suffix = save_path.stem, save_path.suffix
            counter = 1
            while save_path.exists():
                save_path = Path(save_dir) / f"{stem} ({counter}){suffix}"
                counter += 1

        t0 = time.monotonic()
        received = 0

        with httpx.Client(timeout=600, follow_redirects=True) as c:
            with c.stream("GET", url, headers=headers) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    raise ApiError(resp.status_code, "Download falhou")

                real_total = int(resp.headers.get("content-length", total))

                with open(save_path, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=CHUNK_SIZE):
                        f.write(chunk)
                        received += len(chunk)
                        elapsed = time.monotonic() - t0
                        speed = (received / elapsed / 1e6) if elapsed > 0 else 0
                        if on_progress:
                            on_progress(received, real_total, speed)

        return str(save_path)
