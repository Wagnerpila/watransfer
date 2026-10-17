"""Exportação das conversas para HTML e CSV."""

from __future__ import annotations

import csv
import html
import os
import re
from pathlib import Path

from .model import Archive, Chat, Message

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

CSS = """
body{font-family:Segoe UI,Arial,sans-serif;background:#efeae2;margin:0;color:#111}
header{background:#075e54;color:#fff;padding:12px 16px;position:sticky;top:0}
header a{color:#cfe}
main{max-width:860px;margin:0 auto;padding:12px 16px}
.msg{max-width:75%;margin:4px 0;padding:6px 10px;border-radius:8px;background:#fff;clear:both;
     box-shadow:0 1px 1px rgba(0,0,0,.1);white-space:pre-wrap;word-wrap:break-word}
.me{float:right;background:#d9fdd3}.them{float:left}
.meta{font-size:11px;color:#667;margin-top:2px}.sender{font-size:12px;font-weight:600;color:#075e54}
.sys{text-align:center;color:#556;font-size:12px;clear:both;padding:4px}
img{max-width:280px;border-radius:6px;display:block}
.clear{clear:both}
ul{list-style:none;padding:0}li{background:#fff;margin:4px 0;padding:8px 12px;border-radius:6px}
li span{color:#667;font-size:12px}
"""


def _fmt(ts) -> str:
    return ts.astimezone().strftime("%d/%m/%Y %H:%M") if ts else ""


def _slug(chat: Chat) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", chat.jid)[:60] + f"_{chat.id}"


def media_file(archive: Archive, message: Message) -> Path | None:
    if not message.media_path or not archive.media_root:
        return None
    rel = message.media_path.replace("\\", "/")
    for marker in ("Media/", "Message/Media/"):
        if marker in rel:
            rel = "Media/" + rel.split(marker, 1)[1]
            break
    candidate = archive.media_root / rel
    if candidate.is_file():
        return candidate
    candidate = archive.media_root / Path(rel).name
    return candidate if candidate.is_file() else None


def render_message_html(
    archive: Archive, m: Message, out_dir: Path | None = None, show_sender: bool = False
) -> str:
    if m.kind == "system":
        return f'<div class="sys">{html.escape(m.display_text)} · {_fmt(m.timestamp)}</div>'
    body = ""
    if show_sender and m.sender and not m.from_me:
        body += f'<div class="sender">{html.escape(m.sender.split("@")[0])}</div>'
    path = media_file(archive, m)
    if path:
        href = Path(os.path.relpath(path, out_dir)).as_posix() if out_dir else path.as_uri()
        if path.suffix.lower() in IMAGE_EXT:
            body += f'<a href="{html.escape(href)}"><img src="{html.escape(href)}"></a>'
        else:
            body += f'<a href="{html.escape(href)}">📎 {html.escape(path.name)}</a><br>'
    text = m.text or m.caption or ("" if path else m.display_text)
    body += html.escape(text)
    body += f'<div class="meta">{_fmt(m.timestamp)}</div>'
    return f'<div class="msg {"me" if m.from_me else "them"}">{body}</div>'


def export_html(archive: Archive, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    items = []
    for chat in archive.chats:
        name = f"{_slug(chat)}.html"
        rows = "\n".join(
            render_message_html(archive, m, out_dir, chat.is_group) for m in chat.messages
        )
        (out_dir / name).write_text(
            f"<!doctype html><meta charset=utf-8><title>{html.escape(chat.title)}</title>"
            f"<style>{CSS}</style><header><a href=index.html>← Conversas</a> · "
            f"<b>{html.escape(chat.title)}</b> · {len(chat.messages)} mensagens</header>"
            f"<main>{rows}<div class=clear></div></main>",
            encoding="utf-8",
        )
        items.append(
            f'<li><a href="{name}">{html.escape(chat.title)}</a> '
            f"<span>{len(chat.messages)} mensagens · {_fmt(chat.last_timestamp)}</span></li>"
        )
    index = out_dir / "index.html"
    index.write_text(
        f"<!doctype html><meta charset=utf-8><title>Backup WhatsApp Business</title>"
        f"<style>{CSS}</style><header><b>Backup WhatsApp Business</b> · origem: {archive.source} · "
        f"{len(archive.chats)} conversas · {archive.message_count} mensagens</header>"
        f"<main><ul>{''.join(items)}</ul></main>",
        encoding="utf-8",
    )
    return index


def export_csv(archive: Archive, out_file: Path) -> Path:
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with out_file.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(["conversa", "jid", "data_hora", "enviada_por_mim", "remetente", "tipo", "texto", "midia"])
        for chat in archive.chats:
            for m in chat.messages:
                writer.writerow([
                    chat.title, chat.jid, _fmt(m.timestamp), "sim" if m.from_me else "não",
                    m.sender or "", m.kind, m.text or m.caption or "", m.media_path or "",
                ])
    return out_file
