"""Leitura e escrita de backups .crypt15 do WhatsApp (Android).

O .crypt15 é gerado quando o backup criptografado de ponta a ponta está ativo.
Com a chave de 64 dígitos (hex) escolhida pelo usuário dá para abrir o backup
localmente, sem contato com os servidores do WhatsApp.

Formato:
    [1 byte: tamanho do protobuf] [0x01 opcional: flag de recursos]
    [protobuf BackupPrefix -> campo 3 (C15_IV) -> campo 1 = IV de 16 bytes]
    [dados zlib cifrados com AES-256-GCM][tag GCM 16 bytes][MD5 16 bytes, opcional]

A chave AES é derivada da chave raiz por HKDF-SHA256 (salt zero,
info "backup encryption").
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import zlib
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SQLITE_MAGIC = b"SQLite format 3\x00"
ZIP_MAGIC = b"PK\x03\x04"


class Crypt15Error(Exception):
    pass


def parse_key(value: str | bytes | Path) -> bytes:
    """Aceita a chave de 64 dígitos hex (com ou sem espaços) ou o arquivo
    encrypted_backup.key (os 32 bytes finais são a chave raiz)."""
    if isinstance(value, Path) or (isinstance(value, str) and os.path.isfile(value)):
        raw = Path(value).read_bytes()
        try:
            return parse_key(raw.decode("ascii"))
        except (UnicodeDecodeError, Crypt15Error):
            pass
        if len(raw) < 32:
            raise Crypt15Error("Arquivo de chave muito pequeno.")
        return raw[-32:]
    if isinstance(value, bytes):
        if len(value) == 32:
            return value
        value = value.decode("ascii", errors="ignore")
    digits = re.sub(r"[^0-9a-fA-F]", "", value)
    if len(digits) != 64:
        raise Crypt15Error(
            f"A chave deve ter 64 dígitos hexadecimais (0-9, a-f); foram encontrados {len(digits)}."
        )
    return bytes.fromhex(digits)


def derive_backup_key(root_key: bytes) -> bytes:
    prk = hmac.new(b"\x00" * 32, root_key, hashlib.sha256).digest()
    return hmac.new(prk, b"backup encryption\x01", hashlib.sha256).digest()


def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if pos >= len(buf):
            raise Crypt15Error("Cabeçalho protobuf truncado.")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


def _proto_fields(buf: bytes):
    pos = 0
    while pos < len(buf):
        tag, pos = _read_varint(buf, pos)
        number, wire = tag >> 3, tag & 7
        if wire == 0:
            value, pos = _read_varint(buf, pos)
        elif wire == 2:
            size, pos = _read_varint(buf, pos)
            value, pos = buf[pos : pos + size], pos + size
        elif wire == 1:
            value, pos = buf[pos : pos + 8], pos + 8
        elif wire == 5:
            value, pos = buf[pos : pos + 4], pos + 4
        else:
            raise Crypt15Error(f"Tipo protobuf não suportado: {wire}")
        yield number, wire, value


def parse_header(data: bytes) -> tuple[bytes, int]:
    """Retorna (iv, deslocamento onde começam os dados cifrados)."""
    if len(data) < 64:
        raise Crypt15Error("Arquivo pequeno demais para ser um .crypt15.")
    size = data[0]
    offset = 1
    if data[1] == 0x01:  # flag de recursos presente em versões recentes
        offset = 2
    proto = data[offset : offset + size]
    for number, wire, value in _proto_fields(proto):
        if number == 3 and wire == 2:
            for inner_number, inner_wire, inner_value in _proto_fields(value):
                if inner_number == 1 and inner_wire == 2 and len(inner_value) == 16:
                    return inner_value, offset + size
    raise Crypt15Error(
        "IV não encontrado no cabeçalho. O arquivo não parece ser .crypt15 "
        "(backups .crypt14/.crypt12 exigem o arquivo de chave do aparelho)."
    )


def decrypt(data: bytes, key: str | bytes | Path) -> bytes:
    iv, start = parse_header(data)
    aes = AESGCM(derive_backup_key(parse_key(key)))
    body = data[start:]
    for trailer in (16, 0):  # com MD5 no final, ou sem
        ciphertext = body[: len(body) - trailer] if trailer else body
        try:
            plain = aes.decrypt(iv, ciphertext, None)
        except InvalidTag:
            continue
        return _decompress(plain)
    raise Crypt15Error("Chave incorreta ou arquivo corrompido (falha na autenticação AES-GCM).")


def _decompress(plain: bytes) -> bytes:
    try:
        out = zlib.decompressobj().decompress(plain)
    except zlib.error as exc:
        raise Crypt15Error(f"Dados descriptografados, mas a descompactação falhou: {exc}") from exc
    if not (out.startswith(SQLITE_MAGIC) or out.startswith(ZIP_MAGIC)):
        raise Crypt15Error("Conteúdo descriptografado não é um banco SQLite nem ZIP.")
    return out


def encrypt(plain: bytes, key: str | bytes | Path, iv: bytes | None = None) -> bytes:
    """Gera um .crypt15 com cabeçalho mínimo. Usado nos testes e na futura
    etapa de restauração; ainda não validado contra o WhatsApp real."""
    iv = iv or os.urandom(16)
    iv_msg = b"\x0a\x10" + iv  # C15_IV { bytes IV = 1 }
    proto = b"\x08\x01" + b"\x1a" + bytes([len(iv_msg)]) + iv_msg  # key_type=1, c15_iv=3
    header = bytes([len(proto), 0x01]) + proto
    aes = AESGCM(derive_backup_key(parse_key(key)))
    body = header + aes.encrypt(iv, zlib.compress(plain), None)
    return body + hashlib.md5(body).digest()


def decrypt_file(src: Path, dest: Path, key: str | bytes | Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(decrypt(Path(src).read_bytes(), key))
    return dest
