"""
SamoanosBox v2 - P2P Mini Server
HTTP server embutido no client que serve arquivos direto pros peers.
"""
import os
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


P2P_CHUNK_SIZE = 4 * 1024 * 1024

# Arquivos compartilhados: file_id -> caminho local
shared_files: dict[int, str] = {}
# Estado por arquivo para revogar transferencias em andamento
file_states: dict[int, dict[str, int | bool]] = {}
state_lock = threading.Lock()


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

        with state_lock:
            file_path = shared_files.get(file_id)
            state = file_states.get(file_id, {"token": 0, "revoked": True})
            token = int(state.get("token", 0))
            revoked = bool(state.get("revoked", True))

        if revoked or not file_path:
            self.send_error(404, "Arquivo nao encontrado")
            return None

        path = Path(file_path)
        if not path.exists():
            self.send_error(404, "Arquivo nao encontrado")
            return None

        return file_id, path, token

    def _still_valid_stream_target(self, file_id: int, token: int, expected_path: Path) -> bool:
        with state_lock:
            state = file_states.get(file_id, {"token": 0, "revoked": True})
            current_token = int(state.get("token", 0))
            revoked = bool(state.get("revoked", True))
            current_path = shared_files.get(file_id)

        if revoked or current_token != token or not current_path:
            return False

        try:
            if Path(current_path) != expected_path:
                return False
        except Exception:
            return False

        return expected_path.exists()

    def _terminate_stream(self):
        self.close_connection = True
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass

    def _send_common_headers(self, path: Path, size: int):
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.send_header("Accept-Ranges", "bytes")

    def do_GET(self):
        target = self._resolve_download_target()
        if not target:
            return

        file_id, path, token = target
        file_size = path.stat().st_size
        range_header = self.headers.get("Range")

        if range_header:
            try:
                range_spec = range_header.replace("bytes=", "")
                start_str, end_str = range_spec.split("-")
                start = int(start_str)
                end = int(end_str) if end_str else file_size - 1
                if start < 0 or start >= file_size:
                    self.send_error(416)
                    return
                end = min(end, file_size - 1)
                if end < start:
                    self.send_error(416)
                    return
                length = end - start + 1

                self.send_response(206)
                self._send_common_headers(path, length)
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                self.end_headers()

                with open(path, "rb") as f:
                    f.seek(start)
                    remaining = length
                    while remaining > 0:
                        if not self._still_valid_stream_target(file_id, token, path):
                            raise ConnectionAbortedError("arquivo revogado")
                        chunk = f.read(min(P2P_CHUNK_SIZE, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                return
            except (BrokenPipeError, ConnectionResetError):
                return
            except ConnectionAbortedError:
                self._terminate_stream()
                return
            except Exception:
                pass

        try:
            self.send_response(200)
            self._send_common_headers(path, file_size)
            self.end_headers()

            with open(path, "rb") as f:
                while True:
                    if not self._still_valid_stream_target(file_id, token, path):
                        raise ConnectionAbortedError("arquivo revogado")
                    chunk = f.read(P2P_CHUNK_SIZE)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            return
        except ConnectionAbortedError:
            self._terminate_stream()
            return

    def do_HEAD(self):
        target = self._resolve_download_target()
        if not target:
            return

        _, path, _ = target
        self.send_response(200)
        self._send_common_headers(path, path.stat().st_size)
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
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.last_warning = ""

    def _ensure_windows_firewall_rule(self) -> str:
        if os.name != "nt":
            return ""

        rule_name = f"SamoanosBox P2P {self.port}"
        try:
            check_cmd = [
                "netsh", "advfirewall", "firewall", "show", "rule", f"name={rule_name}",
            ]
            check = subprocess.run(check_cmd, capture_output=True, text=True, timeout=10)
            check_out = (check.stdout or "") + (check.stderr or "")
            needs_add = (
                check.returncode != 0
                or "No rules match" in check_out
                or "Nenhuma regra corresponde" in check_out
            )
            if not needs_add:
                return ""

            add_cmd = [
                "netsh", "advfirewall", "firewall", "add", "rule",
                f"name={rule_name}",
                "dir=in", "action=allow", "protocol=TCP",
                f"localport={self.port}", "profile=private",
            ]
            add = subprocess.run(add_cmd, capture_output=True, text=True, timeout=10)
            if add.returncode == 0:
                return ""
            return f"Nao foi possivel liberar firewall automaticamente (porta {self.port})."
        except Exception:
            return f"Nao foi possivel validar/liberar firewall automaticamente (porta {self.port})."

    def start(self):
        if self.server:
            return

        if self.port < 1024 or self.port > 65535:
            raise RuntimeError(f"Porta P2P invalida: {self.port}. Use um valor entre 1024 e 65535.")

        try:
            self.server = ThreadingHTTPServer(("0.0.0.0", self.port), P2PHandler)
        except OSError as ex:
            raise RuntimeError(
                f"Nao foi possivel abrir a porta P2P {self.port}. "
                f"Feche outro app usando essa porta ou altere em Configuracoes."
            ) from ex

        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.last_warning = self._ensure_windows_firewall_rule()
        print(f"[P2P] Servindo em {self.host}:{self.port}")

    def share_file(self, file_id: int, file_path: str):
        with state_lock:
            state = file_states.get(file_id, {"token": 0, "revoked": False})
            state["token"] = int(state.get("token", 0)) + 1
            state["revoked"] = False
            file_states[file_id] = state
            shared_files[file_id] = file_path

    def unshare_file(self, file_id: int):
        with state_lock:
            shared_files.pop(file_id, None)
            state = file_states.get(file_id, {"token": 0, "revoked": False})
            state["token"] = int(state.get("token", 0)) + 1
            state["revoked"] = True
            file_states[file_id] = state

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
            self.thread = None
