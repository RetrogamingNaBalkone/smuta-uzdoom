"""Реальная приёмка GFX-01: диапазоны, независимая сверка и воспроизводимость."""

import json
from pathlib import Path
import struct
import sys
import uuid
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from smuta_build.dat import parse
from smuta_build.extract import digest
from smuta_build.graphics import decode_graphics
from smuta_build.paths import REPO


def require(condition, message):
    if not condition:
        raise ValueError(message)


def png_indices(data):
    require(data[:8] == b"\x89PNG\r\n\x1a\n", "Нет PNG")
    pos, compressed, palette = 8, b"", None
    while pos < len(data):
        size, = struct.unpack_from(">I", data, pos)
        kind, payload = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + size]
        require(zlib.crc32(kind + payload) == struct.unpack_from(">I", data, pos + 8 + size)[0], "CRC PNG")
        if kind == b"IHDR":
            width, height, depth, color, *_ = struct.unpack(">IIBBBBB", payload)
            require((depth, color) == (8, 3), "Не индексный PNG8")
        elif kind == b"PLTE":
            palette = payload
        elif kind == b"IDAT":
            compressed += payload
        require(kind != b"tRNS", "Неподтверждённая прозрачность")
        pos += size + 12
    raw = zlib.decompress(compressed)
    require(len(raw) == height * (width + 1), "Размер PNG")
    require(all(raw[y * (width + 1)] == 0 for y in range(height)), "Фильтр PNG")
    return b"".join(raw[y * (width + 1) + 1:(y + 1) * (width + 1)] for y in range(height)), palette


def main():
    source = REPO / "originals/smuta_modern_20261013/smuta_modern/SMUTA.DAT"
    exe = source.with_name("SMUTA.EXE")
    engine = REPO / "uzdoom/uzdoom.exe"
    originals = [p for p in source.parent.rglob("*") if p.is_file()] + [engine]
    before = {str(p.relative_to(REPO)): digest(p.read_bytes()) for p in originals}
    root = REPO / "build" / f"gfx-01 verification {uuid.uuid4().hex}"
    root.mkdir()
    for target in (root / "a", root / "b"):
        decode_graphics(source, target)
    files = lambda p: {f.name: f.read_bytes() for f in p.iterdir()}
    require(files(root / "a") == files(root / "b"), "Два экспорта различаются")
    def rejected(source_path, output):
        try:
            decode_graphics(source_path, output)
        except ValueError:
            return
        raise ValueError("Ожидался отказ экспорта")
    rejected(source, root / "a")
    copied_source = root / "SMUTA.DAT"
    copied_source.write_bytes(source.read_bytes())
    rejected(copied_source, root / "missing-external")
    require(not (root / "missing-external").exists(), "Неполный вход создал выход")
    copied_source.write_bytes(source.read_bytes()[:-1])
    rejected(copied_source, root / "unknown-source")
    require(not (root / "unknown-source").exists(), "Другой DAT создал выход")
    manifest = json.loads((root / "a/manifest.json").read_text(encoding="utf-8"))
    archive = parse(source.read_bytes())
    counts = {"spr": 0, "bmp": 0, "external_bmp": 0, "palette": 0}
    for item in manifest["resources"]:
        origin = item["source"]
        if "index" in origin:
            entry = archive.entries[origin["index"]]
            raw = archive.data[entry.offset:entry.offset + entry.length]
        else:
            raw = (source.parent / origin["relative_path"]).read_bytes()
        require(digest(raw) == origin["sha256"], "Хеш ресурса")
        require((root / "a" / item["raw_path"]).read_bytes() == raw, "Утеряны исходные байты")
        if "indices_path" not in item:
            counts["palette"] += 1
            continue
        if raw[:2] == b"BM":
            counts["bmp" if "index" in origin else "external_bmp"] += 1
            offset = int.from_bytes(raw[10:14], "little")
            width = int.from_bytes(raw[18:22], "little", signed=True)
            height = int.from_bytes(raw[22:26], "little", signed=True)
            stride = ((width + 3) // 4) * 4
            expected = bytes(raw[offset + y * stride + x]
                             for y in (range(height - 1, -1, -1) if height > 0 else range(-height))
                             for x in range(width))
            _, palette = png_indices((root / "a" / item["preview_path"]).read_bytes())
            require(palette == b"".join(bytes((raw[p + 2], raw[p + 1], raw[p]))
                                      for p in range(54, 1078, 4)), "Каналы BMP")
        else:
            counts["spr"] += 1
            expected = raw[18:]
            require(len(expected) == item["width"] * item["height"], "Размер SPR")
            require(item["anchor"] is None and item["transparent_index"] is None, "Догадка о SPR")
        require((root / "a" / item["indices_path"]).read_bytes() == expected, "Индексы изменились")
        pixels, _ = png_indices((root / "a" / item["preview_path"]).read_bytes())
        require(pixels == expected, "PNG изменил индексы")
    require(counts == {"spr": 157, "bmp": 1, "external_bmp": 11, "palette": 2}, "Состав ресурсов")
    for name, metadata in manifest["files"].items():
        data = (root / "a" / name).read_bytes()
        require(metadata == {"length": len(data), "sha256": digest(data)}, "Манифест выхода")
    profile = json.loads((REPO / "profiles/smuta/graphics.json").read_text(encoding="utf-8"))
    require(digest(exe.read_bytes()) == profile["exe_sha256"], "Другой EXE")
    require(manifest["rendering_rules"] == profile["rendering_rules"], "Правила рисования")
    for evidence in profile["evidence"] + profile["render_evidence"]:
        offset, size = evidence["offset"], evidence["length"]
        require(exe.read_bytes()[offset:offset + size].hex() == evidence["bytes_hex"], "Байты EXE")
    # Win32 PE FileVersion читается без запуска движка.
    import subprocess
    version = subprocess.check_output(["powershell", "-NoProfile", "-Command",
        "(Get-Item -LiteralPath '" + str(engine).replace("'", "''") + "').VersionInfo.FileVersion"], text=True).strip()
    require(before == {str(p.relative_to(REPO)): digest(p.read_bytes()) for p in originals}, "Изменены оригиналы")
    report = {"counts": counts, "files_identical": True, "source_hashes_unchanged": True,
              "inputs_sha256": before, "uzdoom_file_version": version,
              "manifest_sha256": digest((root / "a/manifest.json").read_bytes()),
              "reference_comparison": "not-performed", "release_ready": False}
    (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(root / "report.json")
    print(json.dumps(counts))


if __name__ == "__main__":
    main()
