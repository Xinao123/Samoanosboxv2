"""SamoanosBox v2 - Database"""
import aiosqlite
import time
from config import DB_PATH


async def get_db():
    db = await aiosqlite.connect(str(DB_PATH))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    return db


async def init_db():
    db = await get_db()
    try:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                original_name TEXT NOT NULL,
                size INTEGER NOT NULL,
                mime_type TEXT DEFAULT 'application/octet-stream',
                uploader TEXT NOT NULL,
                upload_date REAL NOT NULL,
                checksum TEXT,
                on_server INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_files_date ON files(upload_date DESC);
        """)
        await db.commit()
    finally:
        await db.close()


async def save_file(filename, original_name, size, mime_type, uploader, checksum, on_server=False):
    db = await get_db()
    try:
        cur = await db.execute(
            """INSERT INTO files (filename, original_name, size, mime_type, uploader, upload_date, checksum, on_server)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (filename, original_name, size, mime_type, uploader, time.time(), checksum, int(on_server)),
        )
        await db.commit()
        return cur.lastrowid
    finally:
        await db.close()


async def mark_on_server(file_id):
    db = await get_db()
    try:
        await db.execute("UPDATE files SET on_server = 1 WHERE id = ?", (file_id,))
        await db.commit()
    finally:
        await db.close()


async def list_files():
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM files ORDER BY upload_date DESC")
        return [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()


async def get_file(file_id):
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM files WHERE id = ?", (file_id,))
        row = await cur.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def delete_file_record(file_id):
    db = await get_db()
    try:
        await db.execute("DELETE FROM files WHERE id = ?", (file_id,))
        await db.commit()
    finally:
        await db.close()
