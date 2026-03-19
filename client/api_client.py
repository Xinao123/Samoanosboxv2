"""
SamoanosBox v2.1 - API Client
Timeout P2P 10s + fallback rapido. Resume + verificacao SHA-256.
"""
import httpx
import hashlib
import time
from pathlib import Path
from typing import Callable

CHUNK_SIZE = 1024 * 1024
P2P_TIMEOUT = 10  # Segundos - se P2P nao responder, cai pro server rapido
SERVER_TIMEOUT = 600


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

    def register_file(self, original_name: str, size: int, checksum: str = "") -> int:
        with httpx.Client(timeout=15) as c:
            data = self._check(c.post(
                self._url("/api/files/register"),
                json={"original_name": original_name, "size": size, "checksum": checksum},
                headers=self._h,
            ))
            return data["file_id"]

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
                    self._check(c.post(
                        self._url(f"/api/upload/{upload_id}/chunk"),
                        content=chunk, headers=headers,
                    ))
                sent += len(chunk)
                elapsed = time.monotonic() - t0
                speed = (sent / elapsed / 1e6) if elapsed > 0 else 0
                if on_progress:
                    on_progress(sent, total, speed)

        with httpx.Client(timeout=60) as c:
            self._check(c.post(
                self._url(f"/api/upload/{upload_id}/complete/{file_id}"),
                headers=self._h,
            ))

    # ── Download inteligente com resume e verificacao ──

    def download_file(self, file_info: dict, save_dir: str,
                      on_progress: Callable[[int, int, float], None] | None = None,
                      on_status: Callable[[str], None] | None = None) -> str:
        file_id = file_info["id"]
        filename = file_info["original_name"]
        total = file_info["size"]
        p2p_host = file_info.get("p2p_host", "")
        p2p_port = file_info.get("p2p_port", 0)
        on_server = file_info.get("on_server", False)
        is_online = file_info.get("uploader_online", False)
        expected_checksum = file_info.get("checksum", "")

        save_path = Path(save_dir) / filename
        save_path.parent.mkdir(parents=True, exist_ok=True)

        if save_path.exists() and save_path.stat().st_size == total:
            stem, suffix = save_path.stem, save_path.suffix
            counter = 1
            while save_path.exists():
                save_path = Path(save_dir) / f"{stem} ({counter}){suffix}"
                counter += 1

        partial_path = Path(save_dir) / f"{filename}.partial"
        resume_from = 0
        if partial_path.exists():
            resume_from = partial_path.stat().st_size
            if resume_from >= total:
                partial_path.unlink()
                resume_from = 0

        # Tenta P2P direto (timeout rapido de 10s)
        if is_online and p2p_host and p2p_port:
            try:
                if on_status:
                    src = "P2P direto" + (" (resumindo)" if resume_from > 0 else "")
                    on_status(f"{src} de {file_info.get('uploader', '?')}...")

                # Primeiro testa se o peer responde (HEAD request com 5s timeout)
                try:
                    with httpx.Client(timeout=5) as c:
                        c.head(f"http://{p2p_host}:{p2p_port}/download/{file_id}")
                except Exception:
                    raise Exception("Peer nao respondeu")

                result = self._download_stream(
                    f"http://{p2p_host}:{p2p_port}/download/{file_id}",
                    {}, partial_path, save_path, total, resume_from, on_progress,
                    timeout=P2P_TIMEOUT,
                )
                self._verify_checksum(result, expected_checksum, on_status)
                return result
            except Exception as e:
                if on_status:
                    on_status(f"P2P falhou ({e}), tentando server...")

        # Fallback pro server
        if on_server:
            if on_status:
                src = "Baixando do server" + (" (resumindo)" if resume_from > 0 else "")
                on_status(f"{src} (pode ser mais lento)...")
            result = self._download_stream(
                self._url(f"/api/files/{file_id}/download"),
                self._h, partial_path, save_path, total, resume_from, on_progress,
                timeout=SERVER_TIMEOUT,
            )
            self._verify_checksum(result, expected_checksum, on_status)
            return result

        raise ApiError(404, "Dono offline e arquivo nao esta no server ainda")

    def _download_stream(self, url, headers, partial_path, final_path,
                         total, resume_from, on_progress, timeout) -> str:
        t0 = time.monotonic()
        received = resume_from

        dl_headers = {**headers}
        if resume_from > 0:
            dl_headers["Range"] = f"bytes={resume_from}-"

        mode = "ab" if resume_from > 0 else "wb"

        # Timeout de conexão rapido, timeout de leitura mais longo
        timeouts = httpx.Timeout(connect=timeout, read=max(timeout, 60), write=30, pool=10)

        with httpx.Client(timeout=timeouts, follow_redirects=True) as c:
            with c.stream("GET", url, headers=dl_headers) as resp:
                if resume_from > 0 and resp.status_code == 200:
                    received = 0
                    mode = "wb"
                elif resp.status_code == 206:
                    pass
                elif resp.status_code >= 400:
                    resp.read()
                    raise ApiError(resp.status_code, "Download falhou")

                content_length = resp.headers.get("content-length")
                if content_length:
                    real_total = (resume_from + int(content_length)) if resp.status_code == 206 else int(content_length)
                else:
                    real_total = total

                with open(partial_path, mode) as f:
                    for chunk in resp.iter_bytes(chunk_size=CHUNK_SIZE):
                        f.write(chunk)
                        received += len(chunk)
                        elapsed = time.monotonic() - t0
                        speed = ((received - resume_from) / elapsed / 1e6) if elapsed > 0 else 0
                        if on_progress:
                            on_progress(received, real_total, speed)

        if final_path.exists():
            stem, suffix = final_path.stem, final_path.suffix
            counter = 1
            while final_path.exists():
                final_path = final_path.parent / f"{stem} ({counter}){suffix}"
                counter += 1

        partial_path.rename(final_path)
        return str(final_path)

    def _verify_checksum(self, file_path, expected, on_status=None):
        if not expected:
            return
        if on_status:
            on_status("Verificando integridade...")
        sha = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(8192):
                sha.update(chunk)
        actual = sha.hexdigest()
        if actual != expected:
            if on_status:
                on_status("AVISO: checksum diferente!")
            raise ApiError(0, f"Checksum invalido: esperado {expected[:12]}... obteve {actual[:12]}...")
