"""
SamoanosBox v2 - P2P Mini Server
HTTP server embutido no client que serve arquivos direto pros peers.
"""
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


# Arquivos compartilhados: file_id -> caminho local
shared_files: dict[int, str] = {}


class P2PHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _resolve_download_target(self):
        parts = self.path.strip("/").split("/")
        if len(parts) != 2 or parts[0] != "download":
            self.send_error(404)
            return None

        try:
            file_id = int(parts[1])
        except ValueError:
            self.send_error(400)
            return None

        file_path = shared_files.get(file_id)
        if not file_path or not Path(file_path).exists():
            self.send_error(404, "Arquivo nao encontrado")
            return None

        return Path(file_path)

    def do_GET(self):
        path = self._resolve_download_target()
        if not path:
            return

        file_size = path.stat().st_size
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
        path = self._resolve_download_target()
        if not path:
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()


def get_local_ip() -> str:
    """Descobre IP local padrao."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class P2PServer:
    def __init__(self, port: int):
        self.port = int(port)
        self.host = get_local_ip()
        self.server: HTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self):
        if self.server:
            return

        if self.port < 1024 or self.port > 65535:
            raise RuntimeError(f"Porta P2P invalida: {self.port}. Use um valor entre 1024 e 65535.")

        try:
            self.server = HTTPServer(("0.0.0.0", self.port), P2PHandler)
        except OSError as ex:
            raise RuntimeError(
                f"Nao foi possivel abrir a porta P2P {self.port}. "
                f"Feche outro app usando essa porta ou altere em Configuracoes."
            ) from ex

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
            self.server.server_close()
            self.server = None
            self.thread = None
