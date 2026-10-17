"""UZD-01: изолированная синтетическая проба, без допуска IR к релизу."""

import json
import os
from pathlib import Path
import struct
import tempfile
import zipfile
import zlib

from .extract import digest
from .paths import REPO, no_links, output_path


def png(width: int, height: int, pixel) -> bytes:
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data)))
    rows = b"".join(b"\0" + bytes(c for x in range(width) for c in pixel(x, y))
                    for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


def map_wad(text: bytes) -> bytes:
    lumps = [(b"SMTEST", b""), (b"TEXTMAP", text), (b"ENDMAP", b"")]
    payload = b"".join(data for _, data in lumps)
    offset = 12
    directory = bytearray()
    for name, data in lumps:
        directory.extend(struct.pack("<II8s", offset, len(data), name))
        offset += len(data)
    return struct.pack("<4sII", b"PWAD", len(lumps), offset) + payload + directory


def build_standalone(destination: Path) -> dict:
    destination = output_path(destination)
    if destination.suffix.lower() != ".ipk3":
        raise ValueError("Для UZD-01 ожидается IPK3")
    if destination.exists():
        raise ValueError(f"Архив уже существует: {destination}")
    source = REPO / "game/standalone"
    names = ("IWADINFO", "GAMEINFO", "MAPINFO", "MENUDEF", "ZSCRIPT", "SMUZD01", "room.udmf")
    inputs = {name: no_links(source / name).read_bytes() for name in names}
    entries = {name: data for name, data in inputs.items() if name != "room.udmf"}
    entries["maps/smtest.wad"] = map_wad(inputs["room.udmf"])
    # Собственные синтетические ресурсы. Не извлекаются из DAT или IWAD.
    for name, base in (("SMWALL", (60, 95, 110)), ("SMFLOOR", (60, 65, 70)),
                       ("SMCEIL", (90, 85, 70))):
        entries[f"textures/{name}.png"] = png(64, 64, lambda x, y, b=base:
            b if x % 32 and y % 32 else tuple(c // 2 for c in b))
    entries["sprites/SMPBA0.png"] = png(16, 56, lambda x, y: (90, 170, 190))
    entries["graphics/SMTITLE.png"] = png(320, 200, lambda x, y:
        (35, 65, 80) if 24 < x < 296 and 24 < y < 176 else (12, 22, 28))
    entries["graphics/SMCURSOR.png"] = png(8, 8, lambda x, y: (90, 170, 190))
    # Встроенный MENUDEF разбирается до нашего: обязательные имена его графики.
    # Это собственные пиксели совместимости, а не ресурсы Doom.
    for name in ("M_SKULL1", "M_SKULL2", "M_DOOM", "M_NGAME", "M_OPTION",
                 "M_LOADG", "M_SAVEG", "M_QUITG", "M_EPISOD", "M_NEWG", "M_SKILL"):
        entries[f"graphics/{name}.png"] = png(1, 1, lambda x, y: (35, 65, 80))
    entries["PLAYPAL"] = bytes(c for i in range(256) for c in (i, i, i))
    for name in ("F_SKY1", "brdr_b", "brdr_t", "brdr_l", "brdr_r",
                 "brdr_tl", "brdr_tr", "brdr_bl", "brdr_br"):
        entries[f"textures/{name}.png"] = png(8, 8, lambda x, y: (35, 65, 80))
    report = {"schema": "smuta-uzd01-v1", "mode": "synthetic-research",
              "original_resources": False, "smuta_mechanics_implemented": False,
              "release_ready": False,
              "inputs": {name: digest(data) for name, data in inputs.items()},
              "files": {name: digest(data) for name, data in sorted(entries.items())}}
    entries["smuta-build-report.json"] = (json.dumps(report, sort_keys=True, indent=2) + "\n").encode()
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".uzd01-", dir=destination.parent)
    try:
        with os.fdopen(fd, "w+b") as stream:
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
                for name, data in sorted(entries.items()):
                    info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                    info.create_system = 3
                    info.external_attr = 0o100644 << 16
                    archive.writestr(info, data)
        if os.name == "nt":
            os.rename(temporary, destination)
        else:
            os.link(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {"path": str(destination), "sha256": digest(destination.read_bytes()),
            "files": len(entries), "mode": report["mode"]}
