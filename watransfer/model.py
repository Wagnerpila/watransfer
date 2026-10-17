"""Modelo unificado de conversas, independente da plataforma de origem."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class Message:
    id: int
    from_me: bool
    timestamp: datetime | None
    text: str | None
    kind: str  # text, image, video, audio, document, sticker, contact, location, system, deleted, other
    sender: str | None = None  # JID ou nome de quem enviou (grupos)
    media_path: str | None = None  # relativo à pasta de mídia do backup
    caption: str | None = None

    @property
    def display_text(self) -> str:
        if self.text:
            return self.text
        if self.caption:
            return self.caption
        if self.kind == "text":
            return ""
        return f"[{self.kind}]"


@dataclass
class Chat:
    id: int
    jid: str
    name: str | None
    messages: list[Message] = field(default_factory=list)

    @property
    def is_group(self) -> bool:
        return self.jid.endswith("@g.us")

    @property
    def title(self) -> str:
        if self.name:
            return self.name
        return self.jid.split("@", 1)[0]

    @property
    def last_timestamp(self) -> datetime | None:
        stamps = [m.timestamp for m in self.messages if m.timestamp]
        return max(stamps) if stamps else None


@dataclass
class Archive:
    source: str  # "android" ou "ios"
    database: Path
    chats: list[Chat]
    media_root: Path | None = None
    schema: str = ""

    @property
    def message_count(self) -> int:
        return sum(len(c.messages) for c in self.chats)
