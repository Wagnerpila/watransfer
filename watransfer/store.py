"""Pasta de trabalho: cada backup fica numa subpasta com um manifest.json."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .model import Archive

DEFAULT_ROOT = Path(os.environ.get("WATRANSFER_HOME") or Path.home() / "Documents" / "WATransfer")
MANIFEST = "manifest.json"


@dataclass
class StoredBackup:
    path: Path
    meta: dict

    @property
    def source(self) -> str:
        return self.meta.get("source", "?")

    @property
    def database(self) -> Path | None:
        rel = self.meta.get("database")
        return self.path / rel if rel else None

    @property
    def media_root(self) -> Path | None:
        rel = self.meta.get("media_root")
        return self.path / rel if rel else None

    @property
    def label(self) -> str:
        plataforma = {"android": "Android", "ios": "iPhone"}.get(self.source, self.source)
        created = self.meta.get("created", "")[:16].replace("T", " ")
        return f"{created} — {plataforma} — {self.meta.get('device', '?')}"

    def load(self) -> Archive:
        if not self.database or not self.database.is_file():
            raise FileNotFoundError("Banco de dados descriptografado não encontrado neste backup.")
        if self.source == "android":
            from .android import read_msgstore
            return read_msgstore(self.database, self.media_root)
        from .ios import read_chatstorage
        return read_chatstorage(self.database, self.media_root)


def backups_root(root: Path | None = None) -> Path:
    path = Path(root or DEFAULT_ROOT) / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_backup_dir(source: str, device: str, root: Path | None = None) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", device).strip("_")[:40] or "aparelho"
    path = backups_root(root) / f"{stamp}_{source}_{safe}"
    path.mkdir(parents=True)
    return path


def write_manifest(path: Path, **meta) -> StoredBackup:
    meta.setdefault("created", datetime.now().isoformat(timespec="seconds"))
    for key in ("database", "media_root", "encrypted_database"):
        value = meta.get(key)
        if isinstance(value, Path):
            meta[key] = value.relative_to(path).as_posix()
    (path / MANIFEST).write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return StoredBackup(path, meta)


def open_backup(path: Path) -> StoredBackup:
    return StoredBackup(path, json.loads((path / MANIFEST).read_text(encoding="utf-8")))


def list_stored(root: Path | None = None) -> list[StoredBackup]:
    items = []
    for child in backups_root(root).iterdir():
        manifest = child / MANIFEST
        if manifest.is_file():
            try:
                items.append(StoredBackup(child, json.loads(manifest.read_text(encoding="utf-8"))))
            except ValueError:
                continue
    items.sort(key=lambda b: b.meta.get("created", ""), reverse=True)
    return items
