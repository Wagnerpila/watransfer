"""Linha de comando: python -m watransfer <comando>."""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from . import android, crypt15, export, ios, store, workflows


def _key(args) -> str:
    return args.key or getpass.getpass("Chave de 64 dígitos do backup: ")


def cmd_android_devices(args):
    for d in android.Adb(args.adb).devices():
        print(d.label)


def cmd_android_backup(args):
    workflows.android_backup(args.serial, _key(args), not args.no_media, args.app, args.adb)


def cmd_android_import(args):
    media = Path(args.media) if args.media else None
    workflows.android_import_file(Path(args.file), _key(args), media, args.app)


def cmd_android_decrypt(args):
    out = crypt15.decrypt_file(Path(args.file), Path(args.output), _key(args))
    print(f"Banco salvo em {out}")


def cmd_ios_backups(args):
    for b in ios.list_backups():
        print(f"{b.label}\n    {b.path}")


def cmd_ios_extract(args):
    workflows.ios_extract(Path(args.backup), args.password, not args.no_media, args.app)


def cmd_ios_backup(args):
    workflows.ios_backup_and_extract(args.password, not args.no_media, args.app)


def cmd_list(args):
    for b in store.list_stored():
        print(f"{b.label}\n    {b.path}")


def _load(target: str):
    path = Path(target)
    if (path / store.MANIFEST).is_file():
        return store.open_backup(path).load()
    if path.suffix == ".sqlite":
        return ios.read_chatstorage(path)
    return android.read_msgstore(path)


def cmd_view(args):
    archive = _load(args.target)
    print(f"Origem: {archive.source} ({archive.schema}) — {len(archive.chats)} conversas, "
          f"{archive.message_count} mensagens")
    for chat in archive.chats[: args.limit]:
        print(f"  {chat.title:<40} {len(chat.messages):>6} msgs")


def cmd_export(args):
    archive = _load(args.target)
    out = Path(args.output)
    result = export.export_csv(archive, out) if args.format == "csv" else export.export_html(archive, out)
    print(f"Exportado: {result}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="watransfer", description="Backup e leitura do WhatsApp Business")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp, media=True):
        sp.add_argument("--app", choices=["business", "messenger"], default="business")
        if media:
            sp.add_argument("--no-media", action="store_true", help="não copiar fotos/vídeos/áudios")

    sp = sub.add_parser("android-devices", help="lista celulares Android conectados")
    sp.add_argument("--adb")
    sp.set_defaults(func=cmd_android_devices)

    sp = sub.add_parser("android-backup", help="copia e descriptografa o backup do celular Android")
    sp.add_argument("serial")
    sp.add_argument("--key", help="chave de 64 dígitos (se omitida, será solicitada)")
    sp.add_argument("--adb")
    common(sp)
    sp.set_defaults(func=cmd_android_backup)

    sp = sub.add_parser("android-import", help="importa um msgstore.db.crypt15 já copiado")
    sp.add_argument("file")
    sp.add_argument("--key")
    sp.add_argument("--media", help="pasta Media copiada do celular")
    common(sp, media=False)
    sp.set_defaults(func=cmd_android_import)

    sp = sub.add_parser("android-decrypt", help="apenas descriptografa um .crypt15")
    sp.add_argument("file")
    sp.add_argument("-o", "--output", default="msgstore.db")
    sp.add_argument("--key")
    sp.set_defaults(func=cmd_android_decrypt)

    sp = sub.add_parser("ios-backups", help="lista backups locais de iPhone")
    sp.set_defaults(func=cmd_ios_backups)

    sp = sub.add_parser("ios-extract", help="extrai o WhatsApp de um backup local de iPhone")
    sp.add_argument("backup")
    sp.add_argument("--password")
    common(sp)
    sp.set_defaults(func=cmd_ios_extract)

    sp = sub.add_parser("ios-backup", help="cria backup do iPhone conectado e extrai o WhatsApp")
    sp.add_argument("--password")
    common(sp)
    sp.set_defaults(func=cmd_ios_backup)

    sp = sub.add_parser("list", help="lista os backups salvos")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("view", help="resumo das conversas de um backup ou banco")
    sp.add_argument("target")
    sp.add_argument("--limit", type=int, default=30)
    sp.set_defaults(func=cmd_view)

    sp = sub.add_parser("export", help="exporta conversas para HTML ou CSV")
    sp.add_argument("target")
    sp.add_argument("-o", "--output", required=True)
    sp.add_argument("--format", choices=["html", "csv"], default="html")
    sp.set_defaults(func=cmd_export)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (crypt15.Crypt15Error, android.AdbError, ios.IOSBackupError, ValueError, FileNotFoundError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    return 0
