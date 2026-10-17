"""Android: acesso ao aparelho via adb e leitura do msgstore.db."""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .model import Archive, Chat, Message

Log = Callable[[str], None]

# Pastas do WhatsApp no armazenamento compartilhado (sem root).
APP_FOLDERS = {
    "business": [
        "/sdcard/Android/media/com.whatsapp.w4b/WhatsApp Business",
        "/sdcard/WhatsApp Business",
    ],
    "messenger": [
        "/sdcard/Android/media/com.whatsapp/WhatsApp",
        "/sdcard/WhatsApp",
    ],
}

ADB_CANDIDATES = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Android/Sdk/platform-tools/adb.exe",
    Path("C:/platform-tools/adb.exe"),
    Path("C:/Program Files/iCareFone Transfer/TS_Android/adb/adb.exe"),
]

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class AdbError(Exception):
    pass


def find_adb() -> str | None:
    env = os.environ.get("WATRANSFER_ADB")
    if env and Path(env).is_file():
        return env
    found = shutil.which("adb")
    if found:
        return found
    for candidate in ADB_CANDIDATES:
        if candidate.is_file():
            return str(candidate)
    return None


@dataclass
class AndroidDevice:
    serial: str
    state: str
    model: str

    @property
    def label(self) -> str:
        return f"{self.model or 'Android'} ({self.serial}) — {self.state}"


class Adb:
    def __init__(self, adb_path: str | None = None):
        self.path = adb_path or find_adb()
        if not self.path:
            raise AdbError(
                "adb não encontrado. Instale o Android Platform Tools ou defina WATRANSFER_ADB."
            )

    def run(self, *args: str, serial: str | None = None, timeout: int | None = 60) -> str:
        cmd = [self.path] + (["-s", serial] if serial else []) + list(args)
        proc = subprocess.run(
            cmd, capture_output=True, timeout=timeout, creationflags=_NO_WINDOW
        )
        out = proc.stdout.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", errors="replace").strip()
            raise AdbError(err or out.strip() or f"adb saiu com código {proc.returncode}")
        return out

    def devices(self) -> list[AndroidDevice]:
        result = []
        for line in self.run("devices", "-l").splitlines()[1:]:
            parts = line.split()
            if len(parts) < 2:
                continue
            props = dict(p.split(":", 1) for p in parts[2:] if ":" in p)
            result.append(AndroidDevice(parts[0], parts[1], props.get("model", "").replace("_", " ")))
        return result

    def exists(self, serial: str, remote: str) -> bool:
        out = self.run("shell", f'[ -e "{remote}" ] && echo SIM || echo NAO', serial=serial)
        return out.strip().endswith("SIM")

    def pull(self, serial: str, remote: str, local: Path) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)
        self.run("pull", "-a", remote, str(local), serial=serial, timeout=None)


def locate_app_folder(adb: Adb, serial: str, app: str = "business") -> str:
    for folder in APP_FOLDERS[app]:
        if adb.exists(serial, f"{folder}/Databases"):
            return folder
    raise AdbError(
        "Pasta do WhatsApp não encontrada no aparelho. Abra o WhatsApp Business, "
        "faça um backup em Configurações > Conversas > Backup e tente novamente."
    )


def pull_backup(
    adb: Adb, serial: str, dest: Path, app: str = "business", include_media: bool = True, log: Log = print
) -> dict:
    """Copia o msgstore.db.crypt15 (e opcionalmente a pasta Media) para dest."""
    folder = locate_app_folder(adb, serial, app)
    log(f"Pasta encontrada: {folder}")
    db_remote = f"{folder}/Databases/msgstore.db.crypt15"
    if not adb.exists(serial, db_remote):
        listing = adb.run("shell", f'ls "{folder}/Databases"', serial=serial).split()
        raise AdbError(
            "msgstore.db.crypt15 não encontrado. Arquivos na pasta: "
            + (", ".join(listing) or "nenhum")
            + ". Ative o backup criptografado de ponta a ponta com chave de 64 dígitos."
        )
    db_local = dest / "msgstore.db.crypt15"
    log("Copiando banco de dados criptografado…")
    adb.pull(serial, db_remote, db_local)
    media_local = None
    if include_media and adb.exists(serial, f"{folder}/Media"):
        log("Copiando mídias (pode demorar)…")
        media_local = dest / "Media"
        adb.pull(serial, f"{folder}/Media", dest)
    return {"db_encrypted": db_local, "media": media_local, "remote_folder": folder}


# ---------------------------------------------------------------- msgstore.db

ANDROID_TYPES = {
    0: "text", 1: "image", 2: "audio", 3: "video", 4: "contact", 5: "location",
    7: "system", 8: "audio", 9: "document", 10: "system", 13: "image", 15: "deleted",
    16: "location", 20: "sticker", 42: "image", 43: "video",
}

SKIP_JIDS = {"status@broadcast", "-1"}


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}


def _col(cols: set[str], name: str, alias: str | None = None) -> str:
    alias = alias or name.split(".")[-1]
    bare = name.split(".")[-1]
    return f"{name} AS {alias}" if bare in cols else f"NULL AS {alias}"


def _ts_ms(value) -> datetime | None:
    if not value:
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


def read_msgstore(db_path: Path, media_root: Path | None = None) -> Archive:
    con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if {"message", "chat", "jid"} <= tables:
            chats, schema = _read_modern(con, tables), "android-moderno"
        elif "messages" in tables:
            chats, schema = _read_legacy(con, tables), "android-legado"
        else:
            raise ValueError("Banco não reconhecido como msgstore.db do WhatsApp.")
    finally:
        con.close()
    chats = [c for c in chats if c.messages]
    chats.sort(key=lambda c: c.last_timestamp or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return Archive("android", Path(db_path), chats, media_root, schema)


def _read_modern(con: sqlite3.Connection, tables: set[str]) -> list[Chat]:
    chat_cols = _columns(con, "chat")
    chats: dict[int, Chat] = {}
    for row in con.execute(
        f"SELECT c._id AS id, j.raw_string AS jid, {_col(chat_cols, 'c.subject')} "
        "FROM chat c JOIN jid j ON j._id = c.jid_row_id"
    ):
        if row["jid"] in SKIP_JIDS:
            continue
        chats[row["id"]] = Chat(row["id"], row["jid"], row["subject"])

    msg_cols = _columns(con, "message")
    media_join, media_sel = "", "NULL AS file_path, NULL AS mime_type, NULL AS media_caption"
    if "message_media" in tables:
        mm = _columns(con, "message_media")
        media_join = "LEFT JOIN message_media mm ON mm.message_row_id = m._id"
        media_sel = ", ".join(
            [_col(mm, "mm.file_path"), _col(mm, "mm.mime_type"), _col(mm, "mm.media_caption")]
        )
    sql = (
        "SELECT m._id AS id, m.chat_row_id AS chat_id, m.from_me AS from_me, "
        f"m.timestamp AS ts, {_col(msg_cols, 'm.text_data')}, {_col(msg_cols, 'm.message_type')}, "
        f"sj.raw_string AS sender, {media_sel} "
        "FROM message m LEFT JOIN jid sj ON sj._id = m.sender_jid_row_id "
        f"{media_join} ORDER BY m.chat_row_id, m.timestamp, m._id"
    )
    for row in con.execute(sql):
        chat = chats.get(row["chat_id"])
        if chat is None:
            continue
        chat.messages.append(
            Message(
                id=row["id"],
                from_me=bool(row["from_me"]),
                timestamp=_ts_ms(row["ts"]),
                text=row["text_data"],
                kind=ANDROID_TYPES.get(row["message_type"], "other"),
                sender=row["sender"],
                media_path=row["file_path"],
                caption=row["media_caption"],
            )
        )
    return list(chats.values())


def _read_legacy(con: sqlite3.Connection, tables: set[str]) -> list[Chat]:
    cols = _columns(con, "messages")
    names: dict[str, str | None] = {}
    if "chat_list" in tables and "subject" in _columns(con, "chat_list"):
        names = {r[0]: r[1] for r in con.execute("SELECT key_remote_jid, subject FROM chat_list")}
    chats: dict[str, Chat] = {}
    sql = (
        "SELECT _id, key_remote_jid, key_from_me, timestamp, "
        f"{_col(cols, 'data')}, {_col(cols, 'media_wa_type')}, {_col(cols, 'remote_resource')}, "
        f"{_col(cols, 'media_caption')} FROM messages ORDER BY key_remote_jid, timestamp, _id"
    )
    for row in con.execute(sql):
        jid = row["key_remote_jid"]
        if not jid or jid in SKIP_JIDS:
            continue
        chat = chats.get(jid)
        if chat is None:
            chat = chats[jid] = Chat(len(chats) + 1, jid, names.get(jid))
        chat.messages.append(
            Message(
                id=row["_id"],
                from_me=bool(row["key_from_me"]),
                timestamp=_ts_ms(row["timestamp"]),
                text=row["data"],
                kind=ANDROID_TYPES.get(int(row["media_wa_type"] or 0), "other"),
                sender=row["remote_resource"] or None,
                caption=row["media_caption"],
            )
        )
    return list(chats.values())
