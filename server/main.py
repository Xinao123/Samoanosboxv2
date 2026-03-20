"""
SamoanosBox v2 - Server Unificado
P2P direto entre amigos + fallback pro server quando offline.

Funções:
  - Rastreia quem está online e seu IP:porta P2P
  - Armazena metadados dos arquivos
  - Serve como fallback de storage quando o dono está offline
  - WebSocket pra notificações em tempo real
"""
import asyncio
import hashlib
import json
import mimetypes
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Header, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import config
import database as db

# ── Online Users ──

online_users: dict[str, dict] = {}
# username → {"ws": WebSocket, "p2p_host": str, "p2p_port": int}

ws_connections: dict[str, WebSocket] = {}


async def broadcast(event: str, data: dict):
    msg = json.dumps({"event": event, **data})
    dead = []
    for user, ws in ws_connections.items():
        try:
            await ws.send_text(msg)
        except Exception:
            dead.append(user)
    for u in dead:
        ws_connections.pop(u, None)
        online_users.pop(u, None)


# ── App ──

@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    print(f"[SamoanosBox v2] Rodando em {config.HOST}:{config.PORT}")
    yield


app = FastAPI(title="SamoanosBox", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
                   expose_headers=["Content-Length", "Content-Range", "Accept-Ranges"])


# ── Schemas ──

class RegisterFileRequest(BaseModel):
    original_name: str
    size: int
    checksum: str = ""


class InitUploadRequest(BaseModel):
    filename: str
    total_size: int


# ── WebSocket: tracking + notificações ──

@app.websocket("/ws/{username}")
async def websocket_endpoint(ws: WebSocket, username: str):
    await ws.accept()

    # Aguarda primeira mensagem com info do P2P server
    try:
        init_raw = await asyncio.wait_for(ws.receive_text(), timeout=10)
        init_msg = json.loads(init_raw)
        p2p_host = init_msg.get("p2p_host", "")
        p2p_port = init_msg.get("p2p_port", 0)
    except Exception:
        p2p_host, p2p_port = "", 0

    online_users[username] = {"ws": ws, "p2p_host": p2p_host, "p2p_port": p2p_port}
    ws_connections[username] = ws

    await broadcast("user_status", {
        "username": username,
        "status": "online",
        "online": list(online_users.keys()),
    })
    print(f"[WS] {username} online (P2P: {p2p_host}:{p2p_port})")

    try:
        while True:
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_text(json.dumps({"event": "pong"}))
    except WebSocketDisconnect:
        pass
    finally:
        online_users.pop(username, None)
        ws_connections.pop(username, None)
        await broadcast("user_status", {
            "username": username,
            "status": "offline",
            "online": list(online_users.keys()),
        })
        print(f"[WS] {username} offline")


# ── Arquivo: registrar (P2P, sem upload pro server) ──

@app.post("/api/files/register")
async def register_file(req: RegisterFileRequest, x_username: str = Header(..., alias="X-Username")):
    """Registra arquivo que fica no PC do usuário (P2P). Não sobe pro server."""
    username = x_username.strip()
    mime, _ = mimetypes.guess_type(req.original_name)
    file_id = await db.save_file(
        filename="", original_name=req.original_name,
        size=req.size, mime_type=mime or "application/octet-stream",
        uploader=username, checksum=req.checksum, on_server=False,
    )

    await broadcast("file_added", {
        "username": username, "filename": req.original_name,
        "size": req.size, "file_id": file_id,
    })

    return {"file_id": file_id}


# ── Arquivo: upload pro server (fallback) ──

@app.post("/api/upload/init")
async def init_upload(req: InitUploadRequest, x_username: str = Header(..., alias="X-Username")):
    upload_id = uuid.uuid4().hex
    (config.UPLOAD_DIR / f".tmp_{upload_id}").touch()
    return {"upload_id": upload_id, "chunk_size": config.CHUNK_SIZE}


@app.post("/api/upload/{upload_id}/chunk")
async def upload_chunk(upload_id: str, request: Request):
    temp = config.UPLOAD_DIR / f".tmp_{upload_id}"
    if not temp.exists():
        raise HTTPException(404, "Sessao perdida")
    with open(temp, "ab") as f:
        async for chunk in request.stream():
            f.write(chunk)
    return {"ok": True}


@app.post("/api/upload/{upload_id}/complete/{file_id}")
async def complete_upload(upload_id: str, file_id: int, x_username: str = Header(..., alias="X-Username")):
    temp = config.UPLOAD_DIR / f".tmp_{upload_id}"
    if not temp.exists():
        raise HTTPException(404, "Arquivo temporario perdido")

    rec = await db.get_file(file_id)
    if not rec:
        raise HTTPException(404, "Arquivo nao registrado")

    sha256 = hashlib.sha256()
    with open(temp, "rb") as f:
        while chunk := f.read(8192):
            sha256.update(chunk)

    ext = Path(rec["original_name"]).suffix
    safe_name = f"{upload_id}{ext}"
    final = config.UPLOAD_DIR / safe_name
    temp.rename(final)

    await db.mark_on_server(file_id)

    # Atualiza filename no banco (reabrir pra update)
    import aiosqlite
    async with aiosqlite.connect(str(config.DB_PATH)) as conn:
        await conn.execute("UPDATE files SET filename = ?, checksum = ? WHERE id = ?",
                           (safe_name, sha256.hexdigest(), file_id))
        await conn.commit()

    await broadcast("file_backup_ready", {
        "username": rec["uploader"],
        "filename": rec["original_name"],
        "file_id": file_id,
    })

    print(f"[Upload] {rec['original_name']} salvo no server (fallback)")
    return {"ok": True}


# ── Arquivo: listar (com status online/offline do dono) ──

@app.get("/api/files")
async def list_files():
    files = await db.list_files()
    for f in files:
        uploader = f["uploader"]
        is_online = uploader in online_users
        peer = online_users.get(uploader, {})
        f["uploader_online"] = is_online
        f["p2p_host"] = peer.get("p2p_host", "") if is_online else ""
        f["p2p_port"] = peer.get("p2p_port", 0) if is_online else 0
    return {"files": files}


# ── Arquivo: download do server (fallback) ──

@app.get("/api/files/{file_id}/download")
async def download_file(file_id: int, request: Request):
    rec = await db.get_file(file_id)
    if not rec:
        raise HTTPException(404, "Arquivo nao encontrado")
    if not rec["on_server"] or not rec["filename"]:
        raise HTTPException(404, "Arquivo nao esta no server (dono offline e sem fallback)")

    path = config.UPLOAD_DIR / rec["filename"]
    if not path.exists():
        raise HTTPException(404, "Arquivo nao existe no disco")

    file_size = path.stat().st_size

    # Range request support
    range_header = request.headers.get("range")
    if range_header:
        try:
            start_str, end_str = range_header.replace("bytes=", "").split("-")
            start = int(start_str)
            end = int(end_str) if end_str else file_size - 1
            end = min(end, file_size - 1)
            length = end - start + 1

            async def stream_range():
                with open(path, "rb") as f:
                    f.seek(start)
                    rem = length
                    while rem > 0:
                        chunk = f.read(min(config.CHUNK_SIZE, rem))
                        if not chunk:
                            break
                        rem -= len(chunk)
                        yield chunk

            return StreamingResponse(stream_range(), status_code=206, media_type=rec["mime_type"],
                                     headers={"Content-Length": str(length),
                                              "Content-Range": f"bytes {start}-{end}/{file_size}",
                                              "Accept-Ranges": "bytes",
                                              "Content-Disposition": f'attachment; filename="{rec["original_name"]}"'})
        except Exception:
            pass

    async def stream():
        with open(path, "rb") as f:
            while chunk := f.read(config.CHUNK_SIZE):
                yield chunk

    return StreamingResponse(stream(), media_type=rec["mime_type"],
                             headers={"Content-Length": str(file_size),
                                      "Accept-Ranges": "bytes",
                                      "Content-Disposition": f'attachment; filename="{rec["original_name"]}"'})


# ── Arquivo: deletar ──

@app.delete("/api/files/{file_id}")
async def delete_file(file_id: int, x_username: str = Header(..., alias="X-Username")):
    username = x_username.strip()
    rec = await db.get_file(file_id)
    if not rec:
        raise HTTPException(404, "Nao encontrado")
    if rec["uploader"] != username:
        raise HTTPException(403, "Apenas quem compartilhou pode remover")
    if rec["on_server"] and rec["filename"]:
        path = config.UPLOAD_DIR / rec["filename"]
        if path.exists():
            path.unlink()
    await db.delete_file_record(file_id)
    await broadcast("file_deleted", {"username": x_username.strip(),
                                      "filename": rec["original_name"], "file_id": file_id})
    return {"ok": True}


# ── Health ──

@app.get("/api/health")
async def health():
    total, used, free_space = 0, 0, 0
    try:
        stat = os.statvfs(str(config.UPLOAD_DIR))
        total = stat.f_frsize * stat.f_blocks
        free_space = stat.f_frsize * stat.f_bavail
        used = total - free_space
    except Exception:
        pass

    return {
        "status": "online", "version": "2.0.0",
        "online_users": list(online_users.keys()),
        "total_files": len(await db.list_files()),
        "storage": {"total_gb": round(total / 1e9, 2), "used_gb": round(used / 1e9, 2),
                     "free_gb": round(free_space / 1e9, 2)},
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=config.HOST, port=config.PORT, reload=False)
