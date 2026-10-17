"""Fluxos completos de backup, usados pela interface e pela linha de comando."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from . import android, crypt15, ios, store

Log = Callable[[str], None]


def _summary(backup: store.StoredBackup, log: Log) -> store.StoredBackup:
    archive = backup.load()
    log(f"Pronto: {len(archive.chats)} conversas, {archive.message_count} mensagens.")
    log(f"Salvo em: {backup.path}")
    return backup


def android_backup(
    serial: str, key: str, include_media: bool = True, app: str = "business",
    adb_path: str | None = None, root: Path | None = None, log: Log = print,
) -> store.StoredBackup:
    crypt15.parse_key(key)  # valida antes de copiar gigabytes de mídia
    adb = android.Adb(adb_path)
    device = next((d for d in adb.devices() if d.serial == serial), None)
    if device is None or device.state != "device":
        raise android.AdbError(
            "Aparelho não autorizado ou desconectado. Ative a Depuração USB e aceite o aviso no celular."
        )
    dest = store.new_backup_dir("android", device.model or serial, root)
    try:
        pulled = android.pull_backup(adb, serial, dest, app, include_media, log)
        log("Descriptografando msgstore.db.crypt15…")
        db = crypt15.decrypt_file(pulled["db_encrypted"], dest / "msgstore.db", key)
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    backup = store.write_manifest(
        dest, source="android", app=app, device=device.model or serial, serial=serial,
        database=db, encrypted_database=pulled["db_encrypted"],
        media_root=dest if pulled["media"] else None,  # pasta que contém "Media/"
    )
    return _summary(backup, log)


def android_import_file(
    crypt15_file: Path, key: str, media_dir: Path | None = None, app: str = "business",
    root: Path | None = None, log: Log = print,
) -> store.StoredBackup:
    """Importa um msgstore.db.crypt15 já copiado do celular (ex.: via cabo/Explorer)."""
    dest = store.new_backup_dir("android", "importado", root)
    try:
        log("Descriptografando arquivo…")
        enc = dest / "msgstore.db.crypt15"
        shutil.copy2(crypt15_file, enc)
        db = crypt15.decrypt_file(enc, dest / "msgstore.db", key)
        media = None
        if media_dir:
            log("Copiando mídias…")
            shutil.copytree(media_dir, dest / "Media")
            media = dest
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    backup = store.write_manifest(
        dest, source="android", app=app, device="importado", database=db,
        encrypted_database=enc, media_root=media,
    )
    return _summary(backup, log)


def ios_extract(
    backup_path: Path, password: str | None = None, include_media: bool = True,
    app: str = "business", root: Path | None = None, log: Log = print,
) -> store.StoredBackup:
    info = ios.read_backup_info(backup_path)
    dest = store.new_backup_dir("ios", info.device_name, root)
    try:
        result = ios.extract_whatsapp(backup_path, dest, app, password, include_media, log)
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    backup = store.write_manifest(
        dest, source="ios", app=app, device=info.device_name, ios_version=info.ios_version,
        source_backup=str(backup_path), database=result["db"], media_root=result["media"],
    )
    return _summary(backup, log)


def ios_backup_and_extract(
    password: str | None = None, include_media: bool = True, app: str = "business",
    root: Path | None = None, log: Log = print,
) -> store.StoredBackup:
    raw_root = store.backups_root(root).parent / "iphone-raw"
    backup_path = ios.create_backup(raw_root, log)
    return ios_extract(backup_path, password, include_media, app, root, log)
