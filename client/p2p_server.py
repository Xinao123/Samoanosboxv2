"""
SamoanosBox v2 - P2P Mini Server
HTTP server embutido no client que serve arquivos direto pros peers.
Roda numa porta aleatória. Os outros clients baixam direto daqui.
"""
import threading
import socket
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote


# Arquivos compartilhados: file_id → caminho local
shared_files: dict[int, str] = {}


class P2PHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Silencia logs

    def do_GET(self):
        # URL: /download/{file_id}
        parts = self.path.strip("/").split("/")
        if len(parts) != 2 or parts[0] != "download":
            self.send_error(404)
            return

        try:
            file_id = int(parts[1])
        except ValueError:
            self.send_error(400)
            return

        file_path = shared_files.get(file_id)
        if not file_path or not Path(file_path).exists():
            self.send_error(404, "Arquivo nao encontrado")
            return

        path = Path(file_path)
        file_size = path.stat().st_size

        # Range request support
        range_header = self.headers.get("Range")
        if range_header:
            try:
                range_spec = range_header.replace("bytes=", "")
                start_str, end_str = range_spec.split("-")
                start = int(start_str)
                end = int(end_str) if end_str else file_size - 1
                end = min(end, file_size - 1)
                length = end - start + 1

                self.send_response(206)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(length))
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()

                with open(path, "rb") as f:
                    f.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = f.read(min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                return
            except Exception:
                pass

        # Full download
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(file_size))
        self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

        with open(path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                self.wfile.write(chunk)

    def do_HEAD(self):
        """Pra checar se o peer tá vivo."""
        self.send_response(200)
        self.end_headers()


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def get_local_ip() -> str:
    """Descobre o IP local (funciona com ZeroTier/LAN)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class P2PServer:
    def __init__(self):
        self.port = find_free_port()
        self.host = get_local_ip()
        self.server: HTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self):
        self.server = HTTPServer(("0.0.0.0", self.port), P2PHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        print(f"[P2P] Servindo em {self.host}:{self.port}")

    def share_file(self, file_id: int, file_path: str):
        shared_files[file_id] = file_path

    def unshare_file(self, file_id: int):
        shared_files.pop(file_id, None)

    def stop(self):
        if self.server:
            self.server.shutdown()
