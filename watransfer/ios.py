"""iPhone: backups locais (iTunes/Apple Devices) e leitura do ChatStorage.sqlite."""

from __future__ import annotations

import os
import plistlib
import re
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .model import Archive, Chat, Message

Log = Callable[[str], None]

DOMAINS = {
    "business": "AppDomainGroup-group.net.whatsapp.WhatsAppSMB.shared",
    "messenger": "AppDomainGroup-group.net.whatsapp.WhatsApp.shared",
}
CHATSTORAGE = "ChatStorage.sqlite"
MEDIA_PREFIX = "Message/Media/"

BACKUP_ROOTS = [
    Path(os.environ.get("APPDATA", "")) / "Apple Computer/MobileSync/Backup",
    Path(os.environ.get("USERPROFILE", "")) / "Apple/MobileSync/Backup",
]

APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class IOSBackupError(Exception):
    pass


@dataclass
class IOSBackup:
    path: Path
    device_name: str
    product: str
    ios_version: str
    last_backup: datetime | None
    encrypted: bool

    @property
    def label(self) -> str:
        when = self.last_backup.astimezone().strftime("%d/%m/%Y %H:%M") if self.last_backup else "?"
        lock = " 🔒" if self.encrypted else ""
        return f"{self.device_name} ({self.product}, iOS {self.ios_version}) — {when}{lock}"


def read_backup_info(path: Path) -> IOSBackup:
    info_file, manifest_file = path / "Info.plist", path / "Manifest.plist"
    if not manifest_file.is_file():
        raise IOSBackupError(f"{path} não é um backup de iPhone (Manifest.plist ausente).")
    info = plistlib.loads(info_file.read_bytes()) if info_file.is_file() else {}
    manifest = plistlib.loads(manifest_file.read_bytes())
    last = info.get("Last Backup Date")
    if isinstance(last, datetime) and last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return IOSBackup(
        path=path,
        device_name=info.get("Device Name", path.name),
        product=info.get("Product Type", "?"),
        ios_version=info.get("Product Version", "?"),
        last_backup=last if isinstance(last, datetime) else None,
        encrypted=bool(manifest.get("IsEncrypted")),
    )


def list_backups(extra_roots: list[Path] | None = None) -> list[IOSBackup]:
    found = []
    for root in BACKUP_ROOTS + list(extra_roots or []):
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if (child / "Manifest.plist").is_file():
                try:
                    found.append(read_backup_info(child))
                except Exception:
                    continue
    found.sort(key=lambda b: b.last_backup or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return found


def _pymobiledevice(*args: str, timeout: int | None = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pymobiledevice3", *args],
        capture_output=True, timeout=timeout, creationflags=_NO_WINDOW,
    )


def connected_devices() -> list[str]:
    """UDIDs dos iPhones conectados via USB (requer o serviço Apple Mobile Device)."""
    import json

    proc = _pymobiledevice("usbmux", "list")
    if proc.returncode != 0:
        raise IOSBackupError(proc.stderr.decode("utf-8", errors="replace").strip()[-500:])
    try:
        data = json.loads(proc.stdout.decode("utf-8", errors="replace"))
    except ValueError:
        return []
    return [d.get("UniqueDeviceID") or d.get("Identifier", "?") for d in data]


def create_backup(dest_root: Path, log: Log = print) -> Path:
    """Cria um backup completo do iPhone conectado em dest_root/<UDID>."""
    dest_root.mkdir(parents=True, exist_ok=True)
    log("Iniciando backup do iPhone. Desbloqueie o aparelho e toque em 'Confiar' se solicitado…")
    proc = subprocess.Popen(
        # sem --full: incremental quando já existe um backup completo do aparelho
        [sys.executable, "-m", "pymobiledevice3", "backup2", "backup", str(dest_root)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=_NO_WINDOW,
        env={**os.environ, "NO_COLOR": "1", "PYTHONUNBUFFERED": "1"},
    )
    assert proc.stdout
    buffer, last, last_progress = "", "", 0.0
    while chunk := proc.stdout.read1(4096):
        buffer += chunk.decode("utf-8", errors="replace")
        # a barra de progresso reescreve a linha com \r; tratamos \r e \n como fim de linha
        *lines, buffer = re.split(r"[\r\n]", buffer)
        for raw in lines:
            line = _ANSI.sub("", raw).strip()
            if not line or line == last:
                continue
            percent = re.match(r"^\s*(\d{1,3}(?:\.\d+)?)%", line)
            if percent:  # progresso: registra só a cada 1%
                value = float(percent.group(1))
                if value - last_progress < 1 and value < 100:
                    continue
                last_progress = value
                line = f"Progresso do backup: {value:.0f}%"
                if line == last:
                    continue
            log(line[-200:])
            last = line
    if proc.wait() != 0:
        raise IOSBackupError("O backup do iPhone falhou. Veja o log acima.")
    backups = [p for p in dest_root.iterdir() if (p / "Manifest.plist").is_file()]
    if not backups:
        raise IOSBackupError("Backup concluído, mas nenhuma pasta de backup foi encontrada.")
    return max(backups, key=lambda p: p.stat().st_mtime)


def extract_whatsapp(
    backup: Path,
    dest: Path,
    app: str = "business",
    password: str | None = None,
    include_media: bool = True,
    log: Log = print,
) -> dict:
    """Extrai ChatStorage.sqlite (e mídias) do WhatsApp de um backup local."""
    domain = DOMAINS[app]
    info = read_backup_info(backup)
    dest.mkdir(parents=True, exist_ok=True)
    if info.encrypted:
        if not password:
            raise IOSBackupError("Este backup é criptografado. Informe a senha do backup do iPhone.")
        _extract_encrypted(backup, dest, domain, password, include_media, log)
    else:
        _extract_plain(backup, dest, domain, include_media, log)
    db = dest / CHATSTORAGE
    if not db.is_file():
        raise IOSBackupError(
            "ChatStorage.sqlite não encontrado no backup. Confirme se o WhatsApp Business "
            "está instalado no iPhone e se o backup é completo."
        )
    return {"db": db, "media": dest / "Message" if (dest / "Message").is_dir() else None, "info": info}


def _extract_plain(backup: Path, dest: Path, domain: str, include_media: bool, log: Log) -> None:
    con = sqlite3.connect(f"file:{(backup / 'Manifest.db').as_posix()}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT fileID, relativePath FROM Files WHERE domain = ? AND flags = 1", (domain,)
        ).fetchall()
    finally:
        con.close()
    if not rows:
        raise IOSBackupError("O WhatsApp Business não aparece neste backup.")
    wanted = [
        (fid, rel) for fid, rel in rows
        if rel == CHATSTORAGE or rel.startswith(CHATSTORAGE + "-")
        or (include_media and rel.startswith(MEDIA_PREFIX))
    ]
    log(f"Extraindo {len(wanted)} arquivo(s)…")
    for i, (fid, rel) in enumerate(wanted, 1):
        src = backup / fid[:2] / fid
        if not src.is_file():
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        if i % 500 == 0:
            log(f"  {i}/{len(wanted)}")


def _extract_encrypted(
    backup: Path, dest: Path, domain: str, password: str, include_media: bool, log: Log
) -> None:
    try:
        from iphone_backup_decrypt import EncryptedBackup
    except ImportError as exc:
        raise IOSBackupError("Instale o pacote iphone_backup_decrypt para abrir backups criptografados.") from exc
    log("Descriptografando backup (a verificação da senha pode levar alguns segundos)…")
    eb = EncryptedBackup(backup_directory=str(backup), passphrase=password)
    try:
        eb.test_decryption()
    except Exception as exc:
        raise IOSBackupError("Senha do backup incorreta.") from exc
    eb.extract_file(relative_path=CHATSTORAGE, domain_like=domain, output_filename=str(dest / CHATSTORAGE))
    if include_media:
        log("Extraindo mídias…")
        eb.extract_files(
            relative_paths_like=MEDIA_PREFIX + "%", domain_like=domain,
            output_folder=str(dest), preserve_folders=True,
        )


# ------------------------------------------------------------- ChatStorage

IOS_TYPES = {
    0: "text", 1: "image", 2: "video", 3: "audio", 4: "contact", 5: "location",
    6: "system", 7: "text", 8: "document", 10: "system", 11: "image", 14: "deleted", 15: "sticker",
}


def _apple_ts(value) -> datetime | None:
    if value is None:
        return None
    return APPLE_EPOCH + timedelta(seconds=float(value))


def read_chatstorage(db_path: Path, media_root: Path | None = None) -> Archive:
    con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"ZWACHATSESSION", "ZWAMESSAGE"} <= tables:
            raise ValueError("Banco não reconhecido como ChatStorage.sqlite do WhatsApp.")
        cols = lambda t: {r[1] for r in con.execute(f"PRAGMA table_info({t})")}  # noqa: E731

        chats: dict[int, Chat] = {}
        for row in con.execute("SELECT Z_PK, ZCONTACTJID, ZPARTNERNAME FROM ZWACHATSESSION"):
            jid = row["ZCONTACTJID"] or ""
            if jid == "status@broadcast":
                continue
            chats[row["Z_PK"]] = Chat(row["Z_PK"], jid, row["ZPARTNERNAME"])

        joins, extra = [], ["NULL AS member_jid", "NULL AS member_name", "NULL AS media_path", "NULL AS media_title"]
        if "ZWAGROUPMEMBER" in tables:
            gm = cols("ZWAGROUPMEMBER")
            name_col = "ZCONTACTNAME" if "ZCONTACTNAME" in gm else ("ZFIRSTNAME" if "ZFIRSTNAME" in gm else None)
            joins.append("LEFT JOIN ZWAGROUPMEMBER gm ON gm.Z_PK = m.ZGROUPMEMBER")
            extra[0] = "gm.ZMEMBERJID AS member_jid"
            extra[1] = f"gm.{name_col} AS member_name" if name_col else "NULL AS member_name"
        if "ZWAMEDIAITEM" in tables:
            joins.append("LEFT JOIN ZWAMEDIAITEM mi ON mi.Z_PK = m.ZMEDIAITEM")
            extra[2] = "mi.ZMEDIALOCALPATH AS media_path"
            extra[3] = "mi.ZTITLE AS media_title"
        sql = (
            "SELECT m.Z_PK, m.ZCHATSESSION, m.ZISFROMME, m.ZMESSAGEDATE, m.ZTEXT, m.ZMESSAGETYPE, "
            f"m.ZFROMJID, {', '.join(extra)} FROM ZWAMESSAGE m {' '.join(joins)} "
            "ORDER BY m.ZCHATSESSION, m.ZMESSAGEDATE, m.Z_PK"
        )
        for row in con.execute(sql):
            chat = chats.get(row["ZCHATSESSION"])
            if chat is None:
                continue
            chat.messages.append(
                Message(
                    id=row["Z_PK"],
                    from_me=bool(row["ZISFROMME"]),
                    timestamp=_apple_ts(row["ZMESSAGEDATE"]),
                    text=row["ZTEXT"],
                    kind=IOS_TYPES.get(row["ZMESSAGETYPE"], "other"),
                    sender=row["member_name"] or row["member_jid"],
                    media_path=row["media_path"],
                    caption=row["media_title"],
                )
            )
    finally:
        con.close()
    result = [c for c in chats.values() if c.messages]
    result.sort(key=lambda c: c.last_timestamp or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return Archive("ios", Path(db_path), result, media_root, "ios-chatstorage")
