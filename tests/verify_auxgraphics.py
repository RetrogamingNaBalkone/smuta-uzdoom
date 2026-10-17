"""Реальная статическая приёмка GFX-03; оригиналы и движок не запускаются."""

import csv
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from smuta_build.auxgraphics import decode_auxgraphics
from smuta_build.dat import parse
from smuta_build.extract import digest
from smuta_build.graphics import decode_bmp, indexed_png
from smuta_build.paths import REPO
from verify_graphics import png_indices, require


def main():
    source = REPO / "originals/smuta_modern_20261013/smuta_modern/SMUTA.DAT"
    engine = REPO / "uzdoom/uzdoom.exe"
    inputs = [p for p in source.parent.rglob("*") if p.is_file()] + [engine, engine.with_suffix(".pk3")]
    before = {str(p.relative_to(REPO)): digest(p.read_bytes()) for p in inputs}
    rules = json.loads((REPO / "profiles/smuta/auxgraphics.json").read_text(encoding="utf-8"))
    exe = source.with_name("SMUTA.EXE").read_bytes()
    require(digest(exe) == rules["exe_sha256"], "Другой EXE")
    pe = struct.unpack_from("<I", exe, 60)[0]
    opt = pe + 24
    section_start = opt + struct.unpack_from("<H", exe, pe + 20)[0]
    base = struct.unpack_from("<I", exe, opt + 28)[0]
    sections = [struct.unpack_from("<IIII", exe, section_start + 40 * i + 8)
                for i in range(struct.unpack_from("<H", exe, pe + 6)[0])]
    for proof in rules["evidence"]:
        start, end = int(proof["va"], 16) - base, int(proof["end_va_exclusive"], 16) - base
        offset = next(raw + start - rva for _, rva, size, raw in sections
                      if rva <= start and end <= rva + size)
        require(offset == proof["file_offset"] and end - start == proof["length"], "Диапазон PE")
        require(digest(exe[offset:offset + proof["length"]]) == proof["sha256"], "Доказательство EXE")
    masks = rules["evidence"][3]["file_offset"]
    require(exe[masks:masks + 16] == bytes([128, 64, 32, 16, 8, 4, 2, 1]) * 2, "Маски шрифта")
    root = REPO / "build" / f"gfx-03 verification {uuid.uuid4().hex}"
    root.mkdir()
    decode_auxgraphics(source, root / "a")
    decode_auxgraphics(source, root / "b")
    files = lambda path: {p.name: p.read_bytes() for p in path.iterdir()}
    require(files(root / "a") == files(root / "b"), "Невоспроизводимый экспорт")
    try:
        decode_auxgraphics(source, root / "a")
    except ValueError:
        pass
    else:
        raise ValueError("Допущена перезапись результатов")
    manifest = json.loads((root / "a/manifest.json").read_text(encoding="utf-8"))
    expected = {"bmp": 1, "fl12-spr18": 98, "font-bitmap": 2, "hud-parameters": 1,
                "level-text": 42, "light-tables": 1, "palette-rgb6": 2, "riff-wave": 156, "spr18": 157}
    require(manifest["counts"] == expected and sum(expected.values()) == 460, "Неполная классификация")
    archive = parse(source.read_bytes())
    require(digest(archive.data) == rules["container_sha256"], "Другой DAT")
    catalog = json.loads((root / "a/catalog.json").read_text(encoding="utf-8"))
    original_catalog = list(csv.DictReader((REPO / "docs/research/smuta-dat-catalog.csv").read_text(encoding="utf-8-sig").splitlines()))
    require(len(catalog) == len(original_catalog) == 460, "Число строк каталога")
    for row, original in zip(catalog, original_catalog, strict=True):
        origin = row["source"]
        require(origin["index"] == int(original["Index"]) and origin["name"] == original["Name"]
                and origin["offset"] == int(original["Offset"]) and origin["length"] == int(original["Length"])
                and origin["sha256"] == original["SHA256"], "Изменены метаданные каталога")
    require(catalog[183]["resolved_index"] == 182 and catalog[183]["usage"] == "shadowed-by-first-match", "Потерян дубликат")
    for resource in manifest["resources"]:
        origin = resource["source"]
        raw = archive.data[origin["offset"]:origin["offset"] + origin["length"]]
        require((root / "a" / resource["raw_path"]).read_bytes() == raw, "Изменены исходные байты")
        if resource["format"] != "bitmap-font-msb-left":
            continue
        pixels = (root / "a" / resource["pixels_path"]).read_bytes()
        height = resource["height"]
        # Независимое обратное упаковывание всех 512 глифов.
        restored = bytes(sum(pixels[pos + x] * (128 >> x) for x in range(8))
                         for pos in range(0, len(pixels), 8))
        require(restored == raw, "Глифы не восстанавливают шрифт")
        atlas, _ = png_indices((root / "a" / resource["atlas_path"]).read_bytes())
        for c in range(256):
            for y in range(height):
                pos = ((c // 16) * height + y) * 128 + (c % 16) * 8
                require(atlas[pos:pos + 8] == pixels[(c * height + y) * 8:(c * height + y + 1) * 8], "Атлас глифов")
    for name, metadata in manifest["files"].items():
        data = (root / "a" / name).read_bytes()
        require(metadata == {"length": len(data), "sha256": digest(data)}, "Хеш результата")
    screens = []
    for number in range(11):
        path = source.with_name(f"Z{number}.SPR")
        raw = path.read_bytes()
        image = decode_bmp(raw, allow_smuta_bfsize=True)
        preview = indexed_png(image, image.palette)
        require(png_indices(preview)[0] == b"".join(raw[1078 + y * 320:1078 + (y + 1) * 320]
                                                  for y in range(199, -1, -1)), "Пиксели экрана")
        (root / f"smext{number:02d}.png").write_bytes(preview)
        screens.append({"relative_path": path.name, "length": len(raw), "sha256": digest(raw), "role": "loading-screen"})
    require(archive.entries[335].name == "HELP.SPR" and catalog[335]["kind"] == "spr18"
            and archive.entries[335].length == 64018, "Изменился экран справки HELP.SPR")
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["index", "name", "offset", "length", "sha256", "kind", "resolved_index", "usage", "evidence"])
    for row in catalog:
        origin = row["source"]
        writer.writerow([origin[k] for k in ("index", "name", "offset", "length", "sha256")]
                        + [row[k] for k in ("kind", "resolved_index", "usage", "evidence")])
    (root / "catalog.csv").write_text(output.getvalue(), encoding="utf-8")
    version = subprocess.check_output(["powershell", "-NoProfile", "-Command",
        "(Get-Item -LiteralPath '" + str(engine).replace("'", "''") + "').VersionInfo.FileVersion"], text=True).strip()
    require(before == {str(p.relative_to(REPO)): digest(p.read_bytes()) for p in inputs}, "Изменены входы")
    report = {"counts": expected, "glyphs_roundtrip": 512, "rgb555_values_verified": 8192,
              "exe_evidence_ranges": len(rules["evidence"]), "files_identical": True,
              "source_hashes_unchanged": True, "inputs_sha256": before,
              "uzdoom_file_version": version, "external_screens": screens,
              "catalog_sha256": digest((root / "catalog.csv").read_bytes()),
              "manifest_sha256": digest((root / "a/manifest.json").read_bytes()),
              "reference_comparison": "not-performed", "release_ready": False}
    (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(root / "report.json")
    print(json.dumps(expected))


if __name__ == "__main__":
    main()
