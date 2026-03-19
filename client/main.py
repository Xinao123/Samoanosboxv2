"""
SamoanosBox v2.1 - GUI (Flet 0.25)
Tray icon, tempo estimado na lista, uploader destacado, drag-drop zone.
"""
import flet as ft
import threading
import json
import time
import hashlib
import queue
import sys
from pathlib import Path
from datetime import datetime

from config import load_config, save_config
from api_client import SamoanosBoxClient, ApiError
from p2p_server import P2PServer
from updater import check_for_update, download_and_install, CURRENT_VERSION


def format_size(b: int) -> str:
    if b < 1024:
        return f"{b} B"
    if b < 1024 ** 2:
        return f"{b / 1024:.1f} KB"
    if b < 1024 ** 3:
        return f"{b / 1024 ** 2:.1f} MB"
    return f"{b / 1024 ** 3:.2f} GB"


def format_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%d/%m %H:%M")


def format_eta(sent, total, speed):
    if speed <= 0 or sent >= total:
        return ""
    r = (total - sent) / (speed * 1e6)
    if r < 60:
        return f"~{int(r)}s"
    if r < 3600:
        return f"~{int(r / 60)}min"
    return f"~{r / 3600:.1f}h"


def estimate_download_time(size_bytes: int, is_p2p: bool) -> str:
    """Estima tempo baseado em velocidades tipicas."""
    if is_p2p:
        speed = 30 * 1e6  # ~30 MB/s P2P tipico
    else:
        speed = 2.5 * 1e6  # ~2.5 MB/s via Starlink
    secs = size_bytes / speed
    if secs < 60:
        return f"~{int(secs)}s"
    if secs < 3600:
        return f"~{int(secs / 60)}min"
    return f"~{secs / 3600:.1f}h"


FILE_ICONS = {
    ".zip": ft.Icons.FOLDER_ZIP, ".rar": ft.Icons.FOLDER_ZIP,
    ".7z": ft.Icons.FOLDER_ZIP, ".tar": ft.Icons.FOLDER_ZIP,
    ".gz": ft.Icons.FOLDER_ZIP,
    ".mp4": ft.Icons.MOVIE, ".mkv": ft.Icons.MOVIE,
    ".avi": ft.Icons.MOVIE, ".mov": ft.Icons.MOVIE,
    ".webm": ft.Icons.MOVIE,
    ".mp3": ft.Icons.MUSIC_NOTE, ".flac": ft.Icons.MUSIC_NOTE,
    ".wav": ft.Icons.MUSIC_NOTE, ".ogg": ft.Icons.MUSIC_NOTE,
    ".jpg": ft.Icons.IMAGE, ".jpeg": ft.Icons.IMAGE,
    ".png": ft.Icons.IMAGE, ".gif": ft.Icons.IMAGE,
    ".webp": ft.Icons.IMAGE,
    ".pdf": ft.Icons.PICTURE_AS_PDF,
    ".doc": ft.Icons.DESCRIPTION, ".docx": ft.Icons.DESCRIPTION,
    ".txt": ft.Icons.TEXT_SNIPPET,
    ".exe": ft.Icons.SETTINGS_APPLICATIONS,
    ".iso": ft.Icons.ALBUM,
}


# ── Tray Icon (Windows) ──

tray_icon = None


def setup_tray(on_restore, on_quit):
    """Configura tray icon. Silencia se pystray nao esta instalado."""
    global tray_icon
    try:
        import pystray
        from PIL import Image, ImageDraw

        # Cria icone 64x64 programaticamente
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse([4, 4, 60, 60], fill=(59, 130, 246, 255))
        draw.polygon([(22, 40), (32, 20), (42, 40)], fill=(255, 255, 255, 255))
        draw.line([(32, 20), (32, 48)], fill=(255, 255, 255, 255), width=3)

        menu = pystray.Menu(
            pystray.MenuItem("Abrir SamoanosBox", on_restore, default=True),
            pystray.MenuItem("Sair", on_quit),
        )
        tray_icon = pystray.Icon("SamoanosBox", img, "SamoanosBox", menu)
        threading.Thread(target=tray_icon.run, daemon=True).start()
    except ImportError:
        pass  # pystray nao instalado, sem tray icon
    except Exception:
        pass


def stop_tray():
    global tray_icon
    if tray_icon:
        try:
            tray_icon.stop()
        except Exception:
            pass
        tray_icon = None


def tray_notify(title: str, msg: str):
    """Envia notificacao Windows via tray icon."""
    if tray_icon:
        try:
            tray_icon.notify(msg, title)
        except Exception:
            pass


def main(page: ft.Page):
    cfg = load_config()
    api = SamoanosBoxClient(cfg["server_url"], cfg.get("username", ""))
    p2p = P2PServer()

    page.title = "SamoanosBox"
    page.theme_mode = ft.ThemeMode.DARK
    page.window.width = 920
    page.window.height = 660
    page.window.min_width = 680
    page.window.min_height = 480
    page.padding = 0
    page.theme = ft.Theme(color_scheme_seed=ft.Colors.BLUE_ACCENT)

    files_list = []
    ws_thread = None
    upload_queue = queue.Queue()
    upload_worker_running = False
    active_backups = {}

    # ── Tray: minimizar pra bandeja ──

    def on_tray_restore(icon=None, item=None):
        page.window.visible = True
        page.window.focused = True
        try:
            page.update()
        except Exception:
            pass

    def on_tray_quit(icon=None, item=None):
        stop_tray()
        p2p.stop()
        page.window.destroy()

    def on_window_event(e):
        if e.data == "close":
            # Minimiza pra tray em vez de fechar
            if tray_icon:
                page.window.visible = False
                page.update()
                tray_notify("SamoanosBox", "Rodando na bandeja. P2P ativo.")
            else:
                p2p.stop()
                page.window.destroy()

    page.window.prevent_close = True
    page.window.on_event = on_window_event

    setup_tray(on_tray_restore, on_tray_quit)

    # ══════════════════════════════════════
    #   HELPERS
    # ══════════════════════════════════════

    def snack(msg, error=False):
        page.overlay.clear()
        page.overlay.append(
            ft.SnackBar(
                content=ft.Text(msg, color=ft.Colors.WHITE, size=13),
                bgcolor=ft.Colors.RED_700 if error else ft.Colors.GREEN_700,
                open=True,
            )
        )
        page.update()

    # ══════════════════════════════════════
    #   TELA DE ENTRADA
    # ══════════════════════════════════════

    server_field = ft.TextField(
        label="Servidor", value=cfg["server_url"],
        prefix_icon=ft.Icons.DNS, border_radius=10,
    )
    name_field = ft.TextField(
        label="Seu nome", value=cfg.get("username", ""),
        prefix_icon=ft.Icons.PERSON, border_radius=10,
        max_length=30, autofocus=True,
    )
    entry_status = ft.Text("", size=12, color=ft.Colors.RED_400)
    entry_loading = ft.ProgressRing(visible=False, width=20, height=20, stroke_width=2)

    def do_enter(e=None):
        name = name_field.value.strip()
        server = server_field.value.strip().rstrip("/")
        if len(name) < 2:
            entry_status.value = "Nome deve ter pelo menos 2 caracteres"
            page.update()
            return
        entry_loading.visible = True
        entry_status.value = ""
        page.update()

        api.server_url = server
        api.username = name
        try:
            api.health()
            cfg["server_url"] = server
            cfg["username"] = name
            save_config(cfg)
            entry_loading.visible = False
            p2p.start()
            show_main_view()
        except Exception as ex:
            entry_loading.visible = False
            entry_status.value = f"Nao conectou: {ex}"
            page.update()

    def on_entry_key(e):
        if e.key == "Enter":
            do_enter()

    entry_view = ft.Column(
        [
            ft.Container(height=50),
            ft.Icon(ft.Icons.CLOUD, size=64, color=ft.Colors.BLUE_400),
            ft.Text("SamoanosBox", size=34, weight=ft.FontWeight.BOLD),
            ft.Text("Compartilhe arquivos com os Samoanos", size=14, color=ft.Colors.GREY_400),
            ft.Container(height=25),
            ft.Container(content=server_field, width=360),
            ft.Container(content=name_field, width=360),
            ft.Container(height=5),
            entry_status,
            ft.Row(
                [
                    ft.ElevatedButton(
                        "Entrar", icon=ft.Icons.ARROW_FORWARD,
                        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=10)),
                        on_click=do_enter, width=200, height=45,
                    ),
                    entry_loading,
                ],
                alignment=ft.MainAxisAlignment.CENTER,
            ),
        ],
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        alignment=ft.MainAxisAlignment.CENTER,
        spacing=10,
        expand=True,
    )

    # ══════════════════════════════════════
    #   TELA PRINCIPAL - CONTROLES
    # ══════════════════════════════════════

    files_column = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True, spacing=4)
    share_progress = ft.ProgressBar(visible=False, value=0, bar_height=6, border_radius=5)
    share_text = ft.Text("", size=12)
    queue_text = ft.Text("", size=11, color=ft.Colors.GREY_500)
    bg_upload_text = ft.Text("", size=11, color=ft.Colors.GREY_500)
    download_progress = ft.ProgressBar(visible=False, value=0, bar_height=6, border_radius=5)
    download_text = ft.Text("", size=12)
    online_chip = ft.Text("", size=11, color=ft.Colors.GREEN_400)
    storage_chip = ft.Text("", size=11, color=ft.Colors.GREY_500)
    notification_banner = ft.Container(
        visible=False,
        padding=ft.padding.symmetric(horizontal=16, vertical=8),
    )
    connection_banner = ft.Container(
        visible=False,
        content=ft.Row(
            [
                ft.ProgressRing(width=14, height=14, stroke_width=2),
                ft.Text("Reconectando ao servidor...", size=12, color=ft.Colors.ORANGE_400),
            ],
            spacing=8,
        ),
        padding=ft.padding.symmetric(horizontal=16, vertical=6),
        bgcolor="#33ff9800",
        border_radius=8,
    )

    # ── Update Banner ──
    update_info_ref = {}  # guarda info da update pra usar nos callbacks

    update_banner = ft.Container(
        visible=False,
        padding=ft.padding.symmetric(horizontal=16, vertical=10),
        bgcolor="#331976d2",
        border_radius=8,
    )

    def check_update_on_startup():
        """Checa update em background ao abrir o app."""
        try:
            info = check_for_update()
            if not info:
                return

            update_info_ref["data"] = info

            def do_update(e):
                update_banner.visible = False
                page.update()

                if info.get("download_url"):
                    snack("Baixando atualizacao...")

                    def dl():
                        def on_prog(recv, total):
                            pass
                        ok = download_and_install(info["download_url"], on_progress=on_prog)
                        if ok:
                            snack("Instalador baixado! Fechando pra atualizar...")
                            time.sleep(2)
                            p2p.stop()
                            stop_tray()
                            page.window.destroy()
                        else:
                            from updater import open_release_page
                            open_release_page(info["browser_url"])

                    threading.Thread(target=dl, daemon=True).start()
                else:
                    from updater import open_release_page
                    open_release_page(info["browser_url"])

            def dismiss_update(e):
                update_banner.visible = False
                page.update()

            changelog = info.get("changelog", "")
            if len(changelog) > 120:
                changelog = changelog[:120] + "..."

            update_banner.content = ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.SYSTEM_UPDATE, size=18, color=ft.Colors.BLUE_300),
                            ft.Text(
                                f"Nova versao disponivel: v{info['version']}  (atual: v{CURRENT_VERSION})",
                                size=13, weight=ft.FontWeight.W_500, color=ft.Colors.BLUE_300,
                            ),
                        ],
                        spacing=8,
                    ),
                    ft.Text(changelog, size=11, color=ft.Colors.GREY_400) if changelog else ft.Container(),
                    ft.Row(
                        [
                            ft.ElevatedButton(
                                "Atualizar agora",
                                icon=ft.Icons.DOWNLOAD,
                                style=ft.ButtonStyle(
                                    shape=ft.RoundedRectangleBorder(radius=8),
                                    bgcolor=ft.Colors.BLUE_700,
                                    color=ft.Colors.WHITE,
                                ),
                                height=32,
                                on_click=do_update,
                            ),
                            ft.TextButton(
                                "Depois",
                                style=ft.ButtonStyle(color=ft.Colors.GREY_500),
                                on_click=dismiss_update,
                            ),
                        ],
                        spacing=8,
                    ),
                ],
                spacing=6,
            )
            update_banner.visible = True
            try:
                page.update()
            except Exception:
                pass

        except Exception:
            pass

    search_field = ft.TextField(
        hint_text="Buscar arquivos...", prefix_icon=ft.Icons.SEARCH,
        border_radius=10, height=40, text_size=13,
        content_padding=ft.padding.symmetric(horizontal=10),
        on_change=lambda e: filter_files(e.control.value),
    )

    # ── File Picker ──

    def pick_result(e):
        if not e.files:
            return
        for f in e.files:
            upload_queue.put({"path": f.path, "name": f.name, "size": f.size})
        pending = upload_queue.qsize()
        if pending > 0:
            queue_text.value = f"Na fila: {pending} arquivo(s)"
            page.update()
        ensure_upload_worker()

    def open_picker(e):
        page.overlay.clear()
        fp = ft.FilePicker(on_result=pick_result)
        page.overlay.append(fp)
        page.update()
        fp.pick_files(allow_multiple=True)

    # ── Upload Worker ──

    def ensure_upload_worker():
        nonlocal upload_worker_running
        if upload_worker_running:
            return
        upload_worker_running = True
        threading.Thread(target=upload_worker, daemon=True).start()

    def upload_worker():
        nonlocal upload_worker_running
        while not upload_queue.empty():
            item = upload_queue.get()
            pending = upload_queue.qsize()
            queue_text.value = f"Na fila: {pending} arquivo(s)" if pending > 0 else ""
            try:
                page.update()
            except Exception:
                pass
            process_share(item["path"], item["name"], item["size"])
        queue_text.value = ""
        upload_worker_running = False
        try:
            page.update()
        except Exception:
            pass

    def process_share(file_path, file_name, file_size):
        share_progress.visible = True
        share_progress.value = 0
        share_text.value = f"Calculando checksum: {file_name}..."
        try:
            page.update()
        except Exception:
            pass

        sha = hashlib.sha256()
        processed = 0
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                sha.update(chunk)
                processed += len(chunk)
                share_progress.value = processed / file_size if file_size > 0 else 1
                share_text.value = (
                    f"Checksum: {file_name} "
                    f"{int(processed / file_size * 100) if file_size > 0 else 100}%"
                )
                try:
                    page.update()
                except Exception:
                    pass

        checksum = sha.hexdigest()
        share_text.value = f"Registrando: {file_name}..."
        try:
            page.update()
        except Exception:
            pass

        try:
            file_id = api.register_file(file_name, file_size, checksum)
        except Exception as ex:
            share_progress.visible = False
            share_text.value = ""
            snack(f"Erro ao registrar: {ex}", error=True)
            return

        p2p.share_file(file_id, file_path)
        cfg.setdefault("shared_files", {})[str(file_id)] = file_path
        save_config(cfg)

        share_progress.visible = False
        share_text.value = ""
        snack(f"Compartilhado: {file_name} (P2P ativo)")
        refresh_files()

        # Notifica via tray se janela nao esta visivel
        tray_notify("SamoanosBox", f"Compartilhado: {file_name}")

        backup_state = {"cancel": False}
        active_backups[file_id] = backup_state

        def bg_upload():
            try:
                bg_upload_text.value = f"Backup: {file_name}..."
                try:
                    page.update()
                except Exception:
                    pass

                def on_prog(sent, total, speed):
                    if backup_state["cancel"]:
                        raise Exception("Backup cancelado")
                    pct = int(sent / total * 100) if total > 0 else 100
                    bg_upload_text.value = f"Backup: {file_name} {pct}% ({speed:.1f} MB/s)"
                    try:
                        page.update()
                    except Exception:
                        pass

                api.upload_to_server(file_id, file_path, on_progress=on_prog)
                bg_upload_text.value = ""
                refresh_files()
            except Exception as ex:
                if "cancelado" in str(ex).lower():
                    bg_upload_text.value = ""
                else:
                    bg_upload_text.value = f"Backup falhou: {ex}"
                try:
                    page.update()
                except Exception:
                    pass
            finally:
                active_backups.pop(file_id, None)

        threading.Thread(target=bg_upload, daemon=True).start()

    # ── Build file tile ──

    def build_file_tile(f):
        fid = f["id"]
        icon = FILE_ICONS.get(
            Path(f["original_name"]).suffix.lower(),
            ft.Icons.INSERT_DRIVE_FILE,
        )
        is_online = f.get("uploader_online", False)
        on_server = f.get("on_server", False)
        uploader = f.get("uploader", "?")

        if is_online:
            status_icon = ft.Icon(ft.Icons.CIRCLE, size=10, color=ft.Colors.GREEN_400)
            status_label = "online - P2P direto"
            status_color = ft.Colors.GREEN_400
            time_est = estimate_download_time(f["size"], True)
            speed_hint = f"P2P {time_est}"
            speed_color = ft.Colors.GREEN_400
        elif on_server:
            status_icon = ft.Icon(ft.Icons.CLOUD_DONE, size=10, color=ft.Colors.ORANGE_400)
            status_label = "offline - via server"
            status_color = ft.Colors.ORANGE_400
            time_est = estimate_download_time(f["size"], False)
            speed_hint = f"Server {time_est}"
            speed_color = ft.Colors.ORANGE_400
        else:
            status_icon = ft.Icon(ft.Icons.CLOUD_OFF, size=10, color=ft.Colors.RED_400)
            status_label = "offline - indisponivel"
            status_color = ft.Colors.RED_400
            speed_hint = ""
            speed_color = ft.Colors.RED_400

        can_download = is_online or on_server
        is_mine = uploader == api.username

        # Info row: tamanho | data | tempo estimado
        info_parts = f'{format_size(f["size"])}  |  {format_ts(f["upload_date"])}'

        row_controls = [
            ft.Icon(icon, size=26, color=ft.Colors.BLUE_300),
            ft.Column(
                [
                    ft.Text(
                        f["original_name"], size=13,
                        weight=ft.FontWeight.W_500,
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                    ft.Row([
                        ft.Icon(ft.Icons.PERSON, size=10, color=ft.Colors.GREY_500),
                        ft.Text(uploader, size=11, weight=ft.FontWeight.W_500, color=ft.Colors.BLUE_300),
                        ft.Container(width=8),
                        status_icon,
                        ft.Text(status_label, size=10, color=status_color),
                    ], spacing=4),
                    ft.Row([
                        ft.Text(info_parts, size=10, color=ft.Colors.GREY_600),
                        ft.Container(width=8),
                        ft.Text(speed_hint, size=10, color=speed_color) if speed_hint else ft.Container(),
                    ], spacing=0),
                ],
                spacing=2,
                expand=True,
            ),
            ft.IconButton(
                ft.Icons.DOWNLOAD_ROUNDED,
                tooltip=f"Baixar ({speed_hint})" if can_download else "Indisponivel",
                icon_color=ft.Colors.GREEN_400 if can_download else ft.Colors.GREY_700,
                icon_size=20,
                disabled=not can_download,
                on_click=lambda e, fi=f: do_download(fi),
            ),
        ]

        if is_mine:
            row_controls.append(
                ft.IconButton(
                    ft.Icons.DELETE_OUTLINE,
                    tooltip="Remover",
                    icon_color=ft.Colors.RED_400,
                    icon_size=20,
                    on_click=lambda e, fid=fid, fn=f["original_name"]: confirm_delete(fid, fn),
                ),
            )

        return ft.Container(
            content=ft.Row(
                row_controls,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.padding.symmetric(horizontal=16, vertical=10),
            border_radius=8,
            ink=True,
            bgcolor="#0affffff",
            border=ft.border.all(1, "#10ffffff"),
        )

    # ── Refresh ──

    def refresh_files():
        try:
            nonlocal files_list
            files_list = api.list_files()
            render_files(files_list)
            try:
                h = api.health()
                s = h.get("storage", {})
                storage_chip.value = f"Disco: {s.get('used_gb', 0):.1f} / {s.get('total_gb', 0):.1f} GB"
                users = h.get("online_users", [])
                online_chip.value = f"  {len(users)} online" if users else ""
            except Exception:
                pass
            page.update()
        except Exception as ex:
            snack(f"Erro: {ex}", error=True)

    def render_files(files):
        files_column.controls.clear()
        if not files:
            files_column.controls.append(
                ft.Column(
                    [
                        ft.Icon(ft.Icons.CLOUD_UPLOAD, size=52, color=ft.Colors.GREY_700),
                        ft.Text("Nenhum arquivo ainda", size=15, color=ft.Colors.GREY_500),
                        ft.Text("Clique em Compartilhar ou arraste arquivos", size=12, color=ft.Colors.GREY_600),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    alignment=ft.MainAxisAlignment.CENTER,
                    spacing=8,
                    expand=True,
                )
            )
        else:
            for f in files:
                files_column.controls.append(build_file_tile(f))

    def filter_files(q):
        if not q:
            render_files(files_list)
        else:
            render_files([f for f in files_list if q.lower() in f["original_name"].lower()])
        page.update()

    # ── Notificacao ──

    def show_notification(text, icon=ft.Icons.INFO_OUTLINE, color=ft.Colors.BLUE_400):
        notification_banner.content = ft.Row(
            [
                ft.Icon(icon, size=16, color=color),
                ft.Text(text, size=12, color=ft.Colors.GREY_300, expand=True),
            ],
            spacing=8,
        )
        notification_banner.bgcolor = color
        notification_banner.border_radius = 8
        notification_banner.visible = True
        page.update()

        # Tambem notifica via tray se janela nao visivel
        tray_notify("SamoanosBox", text)

        def hide():
            time.sleep(4)
            notification_banner.visible = False
            try:
                page.update()
            except Exception:
                pass

        threading.Thread(target=hide, daemon=True).start()

    # ── Download ──

    def do_download(file_info):
        download_progress.visible = True
        download_progress.value = 0
        download_text.value = "Conectando..."
        page.update()

        def run():
            try:
                def on_prog(recv, total, speed):
                    download_progress.value = recv / total if total > 0 else 1
                    pct = int(recv / total * 100) if total > 0 else 100
                    eta = format_eta(recv, total, speed)
                    download_text.value = (
                        f"DL {format_size(recv)}/{format_size(total)} "
                        f"{pct}% | {speed:.1f} MB/s {eta}"
                    )
                    try:
                        page.update()
                    except Exception:
                        pass

                def on_status(s):
                    download_text.value = s
                    try:
                        page.update()
                    except Exception:
                        pass

                saved = api.download_file(
                    file_info,
                    cfg.get("download_dir", str(Path.home() / "Downloads")),
                    on_progress=on_prog,
                    on_status=on_status,
                )
                download_progress.visible = False
                download_text.value = ""
                snack(f"Salvo: {Path(saved).name} (verificado)")
                tray_notify("SamoanosBox", f"Download completo: {Path(saved).name}")
            except ApiError as ex:
                download_progress.visible = False
                download_text.value = ""
                if "checksum" in ex.detail.lower():
                    snack(f"Arquivo corrompido! {ex.detail}", error=True)
                else:
                    snack(f"Erro: {ex.detail}", error=True)
                try:
                    page.update()
                except Exception:
                    pass
            except Exception as ex:
                download_progress.visible = False
                download_text.value = ""
                snack(f"Erro: {ex}", error=True)
                try:
                    page.update()
                except Exception:
                    pass

        threading.Thread(target=run, daemon=True).start()

    # ── Delete ──

    def confirm_delete(file_id, filename):
        def yes(e):
            dlg.open = False
            page.update()
            try:
                backup = active_backups.get(file_id)
                if backup:
                    backup["cancel"] = True
                api.delete_file(file_id)
                p2p.unshare_file(file_id)
                cfg.get("shared_files", {}).pop(str(file_id), None)
                save_config(cfg)
                snack(f"Removido: {filename}")
                refresh_files()
            except ApiError as ex:
                snack(f"Erro: {ex.detail}", error=True)

        def no(e):
            dlg.open = False
            page.update()

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("Confirmar"),
            content=ft.Text(f'Remover "{filename}"?'),
            actions=[
                ft.TextButton("Cancelar", on_click=no),
                ft.TextButton(
                    "Remover", on_click=yes,
                    style=ft.ButtonStyle(color=ft.Colors.RED_400),
                ),
            ],
        )
        page.overlay.append(dlg)
        dlg.open = True
        page.update()

    # ── Settings ──

    def show_settings(e):
        dl_field = ft.TextField(
            label="Pasta de Download",
            value=cfg.get("download_dir", ""),
            expand=True, border_radius=10,
        )

        def save(e):
            cfg["download_dir"] = dl_field.value
            save_config(cfg)
            dlg.open = False
            snack("Configuracoes salvas!")
            page.update()

        def close(e):
            dlg.open = False
            page.update()

        dlg = ft.AlertDialog(
            title=ft.Text("Configuracoes"),
            content=ft.Container(
                content=ft.Column(
                    [
                        ft.Text(f"Servidor: {api.server_url}", size=12, color=ft.Colors.GREY_400),
                        ft.Text(f"Usuario: {api.username}", size=12, color=ft.Colors.GREY_400),
                        ft.Text(f"P2P: {p2p.host}:{p2p.port}", size=12, color=ft.Colors.GREY_400),
                        ft.Divider(),
                        dl_field,
                    ],
                    spacing=10, tight=True,
                ),
                width=400,
            ),
            actions=[
                ft.TextButton("Fechar", on_click=close),
                ft.ElevatedButton("Salvar", on_click=save),
            ],
        )
        page.overlay.append(dlg)
        dlg.open = True
        page.update()

    # ── Logout ──

    def do_logout(e=None):
        p2p.stop()
        cfg["username"] = ""
        save_config(cfg)
        api.username = ""
        show_entry_view()

    # ── WebSocket ──

    def start_ws():
        nonlocal ws_thread
        import websocket as ws_lib

        ws_url = api.server_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = f"{ws_url}/ws/{api.username}"

        def on_open(ws):
            ws.send(json.dumps({"p2p_host": p2p.host, "p2p_port": p2p.port}))
            connection_banner.visible = False
            try:
                page.update()
            except Exception:
                pass
            refresh_files()

        def on_message(ws, message):
            try:
                data = json.loads(message)
                ev = data.get("event", "")
                who = data.get("username", "")

                if ev == "file_added" and who != api.username:
                    fname = data.get("filename", "?")
                    fsize = format_size(data.get("size", 0))
                    show_notification(
                        f"{who} compartilhou: {fname} ({fsize})",
                        ft.Icons.UPLOAD_FILE, ft.Colors.BLUE_400,
                    )
                    refresh_files()
                elif ev == "file_deleted" and who != api.username:
                    show_notification(
                        f"{who} removeu: {data.get('filename', '?')}",
                        ft.Icons.DELETE, ft.Colors.ORANGE_400,
                    )
                    refresh_files()
                elif ev == "user_status":
                    users = data.get("online", [])
                    online_chip.value = f"  {len(users)} online" if users else ""
                    status = data.get("status", "")
                    if who != api.username:
                        if status == "online":
                            show_notification(f"{who} entrou", ft.Icons.PERSON_ADD, ft.Colors.GREEN_400)
                        else:
                            show_notification(f"{who} saiu", ft.Icons.PERSON_REMOVE, ft.Colors.GREY_500)
                    refresh_files()
                    page.update()
            except Exception:
                pass

        def on_error(ws, error):
            connection_banner.visible = True
            try:
                page.update()
            except Exception:
                pass

        def on_close(ws, code, msg):
            connection_banner.visible = True
            try:
                page.update()
            except Exception:
                pass
            time.sleep(5)
            try:
                start_ws()
            except Exception:
                pass

        def run():
            try:
                ws = ws_lib.WebSocketApp(
                    ws_url, on_open=on_open,
                    on_message=on_message, on_error=on_error, on_close=on_close,
                )
                ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception:
                connection_banner.visible = True
                try:
                    page.update()
                except Exception:
                    pass

        ws_thread = threading.Thread(target=run, daemon=True)
        ws_thread.start()

    # ── Restaura shares ──

    def restore_shares():
        shared = cfg.get("shared_files", {})
        for fid_str, path in list(shared.items()):
            if Path(path).exists():
                p2p.share_file(int(fid_str), path)
            else:
                shared.pop(fid_str, None)
        save_config(cfg)

    # ══════════════════════════════════════
    #   LAYOUT
    # ══════════════════════════════════════

    # Drop zone visual (abre picker ao clicar)
    drop_zone = ft.Container(
        content=ft.Column(
            [
                ft.Icon(ft.Icons.UPLOAD_FILE, size=28, color=ft.Colors.BLUE_400),
                ft.Text("Arraste arquivos aqui ou clique", size=12, color=ft.Colors.GREY_500),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=4,
        ),
        border=ft.border.all(2, "#20ffffff"),
        border_radius=10,
        padding=ft.padding.symmetric(vertical=16),
        on_click=open_picker,
        ink=True,
    )

    main_view = ft.Column(
        [
            # ── Header ──
            ft.Container(
                content=ft.Row(
                    [
                        ft.Row(
                            [
                                ft.Icon(ft.Icons.CLOUD, size=22, color=ft.Colors.BLUE_400),
                                ft.Text("SamoanosBox", size=17, weight=ft.FontWeight.BOLD),
                                online_chip,
                            ],
                            spacing=10,
                        ),
                        ft.Row(
                            [
                                storage_chip,
                                ft.IconButton(ft.Icons.SETTINGS, tooltip="Config", icon_size=19, on_click=show_settings),
                                ft.IconButton(ft.Icons.REFRESH, tooltip="Atualizar", icon_size=19, on_click=lambda e: refresh_files()),
                                ft.IconButton(ft.Icons.LOGOUT, tooltip="Sair", icon_size=19, icon_color=ft.Colors.RED_400, on_click=do_logout),
                            ],
                            spacing=0,
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                padding=ft.padding.symmetric(horizontal=20, vertical=8),
                border=ft.border.only(bottom=ft.BorderSide(1, "#15ffffff")),
            ),
            # ── Banners ──
            notification_banner,
            connection_banner,
            update_banner,
            # ── Toolbar ──
            ft.Container(
                content=ft.Column(
                    [
                        drop_zone,
                        ft.Row(
                            [
                                ft.Container(content=search_field, expand=True),
                                ft.ElevatedButton(
                                    "Compartilhar",
                                    icon=ft.Icons.SHARE,
                                    style=ft.ButtonStyle(
                                        shape=ft.RoundedRectangleBorder(radius=10),
                                        bgcolor=ft.Colors.BLUE_700,
                                        color=ft.Colors.WHITE,
                                    ),
                                    height=40,
                                    on_click=open_picker,
                                ),
                            ],
                            spacing=12,
                        ),
                        share_progress,
                        share_text,
                        queue_text,
                        bg_upload_text,
                        download_progress,
                        download_text,
                    ],
                    spacing=4,
                ),
                padding=ft.padding.symmetric(horizontal=20, vertical=8),
            ),
            # ── File List ──
            ft.Container(
                content=files_column,
                expand=True,
                padding=ft.padding.symmetric(horizontal=20),
            ),
        ],
        spacing=0,
        expand=True,
    )

    # ══════════════════════════════════════
    #   NAVEGACAO
    # ══════════════════════════════════════

    def show_entry_view():
        page.on_keyboard_event = on_entry_key
        page.controls.clear()
        page.controls.append(entry_view)
        page.update()

    def show_main_view():
        page.on_keyboard_event = None
        page.controls.clear()
        page.controls.append(main_view)
        page.update()
        restore_shares()
        refresh_files()
        start_ws()
        # Checa update em background
        threading.Thread(target=check_update_on_startup, daemon=True).start()

    # ── Auto-enter ──

    if cfg.get("username"):
        api.server_url = cfg["server_url"]
        api.username = cfg["username"]
        try:
            api.health()
            p2p.start()
            show_main_view()
        except Exception:
            show_entry_view()
    else:
        show_entry_view()


if __name__ == "__main__":
    ft.app(target=main, port=8550)