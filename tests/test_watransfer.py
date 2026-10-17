import hashlib
import plistlib
import sqlite3
from pathlib import Path

import pytest

from watransfer import android, crypt15, export, ios, store, workflows

KEY = "0123456789abcdef" * 4


def make_msgstore(path: Path) -> Path:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE jid(_id INTEGER PRIMARY KEY, raw_string TEXT);
        CREATE TABLE chat(_id INTEGER PRIMARY KEY, jid_row_id INTEGER, subject TEXT);
        CREATE TABLE message(_id INTEGER PRIMARY KEY, chat_row_id INTEGER, from_me INTEGER,
            timestamp INTEGER, text_data TEXT, message_type INTEGER, sender_jid_row_id INTEGER);
        CREATE TABLE message_media(message_row_id INTEGER, file_path TEXT, mime_type TEXT, media_caption TEXT);
        INSERT INTO jid VALUES (1,'5511999990000@s.whatsapp.net'),(2,'1203630@g.us'),
            (3,'5511888880000@s.whatsapp.net'),(4,'status@broadcast');
        INSERT INTO chat VALUES (1,1,NULL),(2,2,'Equipe Vendas'),(3,4,NULL);
        INSERT INTO message VALUES
            (1,1,0,1700000000000,'Olá, tudo bem?',0,NULL),
            (2,1,1,1700000060000,'Tudo sim!',0,NULL),
            (3,2,0,1700000120000,NULL,1,3),
            (4,3,0,1700000000000,'status',0,NULL);
        INSERT INTO message_media VALUES (3,'Media/WhatsApp Business Images/IMG-1.jpg','image/jpeg','Foto');
        """
    )
    con.commit()
    con.close()
    return path


def make_chatstorage(path: Path) -> Path:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE ZWACHATSESSION(Z_PK INTEGER PRIMARY KEY, ZCONTACTJID TEXT, ZPARTNERNAME TEXT);
        CREATE TABLE ZWAMESSAGE(Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER, ZISFROMME INTEGER,
            ZMESSAGEDATE REAL, ZTEXT TEXT, ZMESSAGETYPE INTEGER, ZFROMJID TEXT,
            ZGROUPMEMBER INTEGER, ZMEDIAITEM INTEGER);
        CREATE TABLE ZWAGROUPMEMBER(Z_PK INTEGER PRIMARY KEY, ZMEMBERJID TEXT, ZCONTACTNAME TEXT);
        CREATE TABLE ZWAMEDIAITEM(Z_PK INTEGER PRIMARY KEY, ZMEDIALOCALPATH TEXT, ZTITLE TEXT);
        INSERT INTO ZWACHATSESSION VALUES (1,'5511999990000@s.whatsapp.net','Cliente A'),
            (2,'1203630@g.us','Equipe Vendas');
        INSERT INTO ZWAGROUPMEMBER VALUES (1,'5511888880000@s.whatsapp.net','João');
        INSERT INTO ZWAMEDIAITEM VALUES (1,'Media/1203630@g.us/a/b/foto.jpg','Foto');
        INSERT INTO ZWAMESSAGE VALUES
            (1,1,0,720000000,'Bom dia',0,'5511999990000@s.whatsapp.net',NULL,NULL),
            (2,1,1,720000100,'Bom dia!',0,NULL,NULL,NULL),
            (3,2,0,720000200,NULL,1,'1203630@g.us',1,1);
        """
    )
    con.commit()
    con.close()
    return path


# ------------------------------------------------------------------ crypt15

def test_key_parsing():
    assert crypt15.parse_key(KEY) == bytes.fromhex(KEY)
    spaced = " ".join(KEY[i:i + 4] for i in range(0, 64, 4)).upper()
    assert crypt15.parse_key(spaced) == bytes.fromhex(KEY)
    with pytest.raises(crypt15.Crypt15Error):
        crypt15.parse_key("1234")


def test_key_derivation_matches_hkdf():
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    root = bytes.fromhex(KEY)
    expected = HKDF(hashes.SHA256(), 32, salt=None, info=b"backup encryption").derive(root)
    assert crypt15.derive_backup_key(root) == expected


def test_crypt15_roundtrip(tmp_path):
    db = make_msgstore(tmp_path / "msgstore.db").read_bytes()
    blob = crypt15.encrypt(db, KEY)
    assert blob[-16:] == hashlib.md5(blob[:-16]).digest()
    assert crypt15.decrypt(blob, KEY) == db
    assert crypt15.decrypt(blob[:-16], KEY) == db  # variante sem MD5 no final


def test_crypt15_wrong_key(tmp_path):
    blob = crypt15.encrypt(make_msgstore(tmp_path / "m.db").read_bytes(), KEY)
    with pytest.raises(crypt15.Crypt15Error, match="Chave incorreta"):
        crypt15.decrypt(blob, "f" * 64)


# ------------------------------------------------------------------ leitores

def test_read_msgstore_modern(tmp_path):
    archive = android.read_msgstore(make_msgstore(tmp_path / "msgstore.db"))
    assert archive.schema == "android-moderno"
    assert [c.jid for c in archive.chats] == ["1203630@g.us", "5511999990000@s.whatsapp.net"]
    group = archive.chats[0]
    assert group.title == "Equipe Vendas" and group.is_group
    msg = group.messages[0]
    assert msg.kind == "image" and msg.caption == "Foto" and msg.sender.startswith("5511888880000")
    direct = archive.chats[1]
    assert [m.from_me for m in direct.messages] == [False, True]
    assert direct.messages[0].timestamp.year == 2023


def test_read_msgstore_legacy(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE messages(_id INTEGER PRIMARY KEY, key_remote_jid TEXT, key_from_me INTEGER,
            timestamp INTEGER, data TEXT, media_wa_type TEXT, remote_resource TEXT);
        INSERT INTO messages VALUES (1,'5511@s.whatsapp.net',0,1500000000000,'oi','0',''),
            (2,'-1',0,1500000000000,NULL,'0','');
        """
    )
    con.commit()
    con.close()
    archive = android.read_msgstore(path)
    assert archive.schema == "android-legado"
    assert len(archive.chats) == 1 and archive.chats[0].messages[0].text == "oi"


def test_read_chatstorage(tmp_path):
    archive = ios.read_chatstorage(make_chatstorage(tmp_path / "ChatStorage.sqlite"))
    assert {c.title for c in archive.chats} == {"Cliente A", "Equipe Vendas"}
    group = next(c for c in archive.chats if c.is_group)
    assert group.messages[0].sender == "João" and group.messages[0].kind == "image"
    direct = next(c for c in archive.chats if not c.is_group)
    assert direct.messages[0].timestamp.year == 2023


# ------------------------------------------------------------------ iPhone backup

def make_ios_backup(root: Path) -> Path:
    backup = root / "00008030-TEST"
    backup.mkdir(parents=True)
    (backup / "Info.plist").write_bytes(plistlib.dumps({"Device Name": "iPhone Teste", "Product Version": "18.0"}))
    (backup / "Manifest.plist").write_bytes(plistlib.dumps({"IsEncrypted": False}))
    db_src = make_chatstorage(root / "src.sqlite")
    con = sqlite3.connect(backup / "Manifest.db")
    con.execute("CREATE TABLE Files(fileID TEXT, domain TEXT, relativePath TEXT, flags INTEGER, file BLOB)")
    files = {
        "aa11": ("ChatStorage.sqlite", db_src.read_bytes()),
        "bb22": ("Message/Media/1203630@g.us/a/b/foto.jpg", b"\xff\xd8jpeg"),
    }
    for fid, (rel, data) in files.items():
        con.execute("INSERT INTO Files VALUES (?,?,?,1,NULL)", (fid, ios.DOMAINS["business"], rel))
        (backup / fid[:2]).mkdir(exist_ok=True)
        (backup / fid[:2] / fid).write_bytes(data)
    con.execute("INSERT INTO Files VALUES ('cc33','HomeDomain','Library/x',1,NULL)")
    con.commit()
    con.close()
    return backup


def test_ios_extract_workflow(tmp_path):
    backup = make_ios_backup(tmp_path / "raw")
    stored = workflows.ios_extract(backup, root=tmp_path / "ws", log=lambda _: None)
    archive = stored.load()
    assert len(archive.chats) == 2
    group = next(c for c in archive.chats if c.is_group)
    assert export.media_file(archive, group.messages[0]).read_bytes() == b"\xff\xd8jpeg"
    assert [b.path for b in store.list_stored(tmp_path / "ws")] == [stored.path]


def test_android_import_workflow_and_export(tmp_path):
    enc = tmp_path / "msgstore.db.crypt15"
    enc.write_bytes(crypt15.encrypt(make_msgstore(tmp_path / "m.db").read_bytes(), KEY))
    media = tmp_path / "Media" / "WhatsApp Business Images"
    media.mkdir(parents=True)
    (media / "IMG-1.jpg").write_bytes(b"img")
    stored = workflows.android_import_file(enc, KEY, tmp_path / "Media", root=tmp_path / "ws", log=lambda _: None)
    archive = stored.load()
    assert archive.message_count == 3
    assert export.media_file(archive, archive.chats[0].messages[0]).read_bytes() == b"img"
    index = export.export_html(archive, tmp_path / "html")
    assert "Equipe Vendas" in index.read_text(encoding="utf-8")
    csv_text = export.export_csv(archive, tmp_path / "c.csv").read_text(encoding="utf-8-sig")
    assert "Tudo sim!" in csv_text


def test_android_import_wrong_key_cleans_up(tmp_path):
    enc = tmp_path / "msgstore.db.crypt15"
    enc.write_bytes(crypt15.encrypt(make_msgstore(tmp_path / "m.db").read_bytes(), KEY))
    with pytest.raises(crypt15.Crypt15Error):
        workflows.android_import_file(enc, "e" * 64, root=tmp_path / "ws", log=lambda _: None)
    assert store.list_stored(tmp_path / "ws") == []
    assert list(store.backups_root(tmp_path / "ws").iterdir()) == []
