"""Приёмка GFX-02 на локальном DAT; игры не запускаются этим скриптом."""

import csv
import json
from pathlib import Path
import struct
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from smuta_build.animations import decode_animations
from smuta_build.dat import parse
from smuta_build.extract import digest
from smuta_build.ir import build_model
from smuta_build.paths import REPO
from verify_graphics import png_indices, require


def main():
    source = REPO / "originals/smuta_modern_20261013/smuta_modern/SMUTA.DAT"
    inputs = [p for p in source.parent.rglob("*") if p.is_file()] + [
        REPO / "uzdoom/uzdoom.exe", REPO / "uzdoom/uzdoom.pk3"]
    hashes = lambda: {str(p.relative_to(REPO)): digest(p.read_bytes()) for p in inputs}
    before = hashes()
    root = REPO / "build" / f"gfx-02 verification {uuid.uuid4().hex}"
    root.mkdir()
    first = decode_animations(source, root / "a")
    second = decode_animations(source, root / "b")
    require(first == second, "Разный состав экспорта")
    files = lambda path: {p.name: digest(p.read_bytes()) for p in path.iterdir()}
    require(files(root / "a") == files(root / "b"), "Экспорты FL различаются")
    manifest = json.loads((root / "a/manifest.json").read_text(encoding="utf-8"))
    archive = parse(source.read_bytes())
    profile = json.loads((REPO / "profiles/smuta/graphics.json").read_text(encoding="utf-8"))
    exe = source.with_suffix(".EXE").read_bytes()
    require(digest(exe) == profile["exe_sha256"], "Другой EXE")
    require(manifest["rendering_rules"] == profile["rendering_rules"], "Правила рисования")
    require(manifest["timing_rules"] == profile["timing_rules"], "Правила времени")
    for evidence in profile["fl_evidence"] + profile["render_evidence"] + profile["timing_evidence"]:
        start, size = evidence["offset"], evidence["length"]
        require(exe[start:start + size].hex() == evidence["bytes_hex"], "Изменились байты EXE FL")
    frames, directional, suffix = 0, 0, 0
    for item in manifest["resources"]:
        entry = archive.entries[item["source"]["index"]]
        raw = archive.data[entry.offset:entry.offset + entry.length]
        require(digest(raw) == item["source"]["sha256"], "Хеш FL")
        require((root / "a" / item["raw_path"]).read_bytes() == raw, "Потерян исходный FL")
        step, count, total = struct.unpack_from("<hhh", raw)
        require((step, count, total) == (item["angle_step"], item["frames_per_direction"],
                                       item["total_frames"]), "Заголовок FL")
        suffix += entry.name.upper().endswith(".FL")
        directional += bool(step)
        pos = 12 + total * 10
        for index, record in enumerate(item["frames"]):
            table = raw[12 + 10*index:22 + 10*index]
            require(bytes.fromhex(record["table_hex"]) == table, "Таблица изменена")
            require(struct.unpack("<hhhI", table) == (record["word0_raw"], record["word2_raw"],
                    record["word4_raw"], record["runtime_pointer_raw"]), "Потеря слов таблицы")
            delay, x, y, _ = struct.unpack("<hhhI", table)
            require(record["delay_counter_if_scheduled"] == (delay * 24 // 1000 if delay >= 0 else None),
                    "Счётчик задержки")
            require(record["weapon_screen_offset"] == {"x": x, "y": y, "unit": "source-screen-pixels"},
                    "Смещения оружия")
            width, height = struct.unpack_from("<HH", raw, pos)
            size, = struct.unpack_from("<I", raw, pos + 10)
            require(size == width * height, "Размер SPR во FL")
            require(record["spr_offset"] == pos and record["pixel_offset"] == pos + 18,
                    "Смещён встроенный кадр")
            end = pos + 18 + size
            require(digest(raw[pos:end]) == record["spr_sha256"], "Хеш встроенного SPR")
            require(bytes.fromhex(record["spr_header_hex"]) == raw[pos:pos + 18], "Заголовок SPR")
            expected = raw[pos + 18:end]
            require((root / "a" / record["indices_path"]).read_bytes() == expected, "Индексы FL")
            pixels, _ = png_indices((root / "a" / record["preview_path"]).read_bytes())
            require(pixels == expected, "PNG изменил кадр FL")
            require((record["direction"], record["frame"]) == divmod(index, count), "Порядок кадров")
            require(record["transparent_index"] is None and record["anchor"] is None,
                    "Неподтверждённая альфа/привязка")
            pos, frames = end, frames + 1
        require(pos == len(raw), "Неразобранный хвост FL")
    require((len(manifest["resources"]), frames, suffix, directional) == (98, 1021, 64, 11),
            "Изменился состав FL")
    decoded = {item["source"]["id"] for item in manifest["resources"]}
    model = build_model(archive)
    coverage = []
    for document in model["resources"]:
        for ref in document["resource_references"]:
            if ref["role"] == "animation":
                require(ref["target"] in decoded, "Ссылка анимации не декодирована: " + ref["id"])
                coverage.append({"document_index": document["source"]["index"], **ref})
    for name, metadata in manifest["files"].items():
        data = (root / "a" / name).read_bytes()
        require(metadata == {"length": len(data), "sha256": digest(data)}, "Хеши выходов")
    for bad_source, target in ((source, root / "a"), (root / "unknown.dat", root / "rejected")):
        if bad_source != source:
            bad_source.write_bytes(source.read_bytes()[:-1])
        try:
            decode_animations(bad_source, target)
        except ValueError:
            pass
        else:
            raise ValueError("Нужен отказ при непустом выходе/неизвестном DAT")
    require(not (root / "rejected").exists(), "Ошибка создала выход")
    require(before == hashes(), "Изменены исходники")
    (root / "coverage.json").write_text(json.dumps(coverage, ensure_ascii=False, indent=2) + "\n",
                                         encoding="utf-8")
    with (root / "catalog.csv").open("w", encoding="utf-8", newline="") as stream:
        columns = ("index", "name", "offset", "length", "sha256", "container_sha256",
                   "angle_step", "directions", "frames_per_direction", "total_frames", "word0_values")
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for item in manifest["resources"]:
            writer.writerow({**{key: item["source"][key] for key in columns[:6]},
                             **{key: item[key] for key in columns[6:-1]},
                             "word0_values": json.dumps(sorted({f["word0_raw"] for f in item["frames"]}))})
    report = {**first, "directional_animations": directional, "fl_extension": suffix,
              "references": len(coverage), "referenced_animations": len({r["target"] for r in coverage}),
              "text_resources": len(model["resources"]), "inputs_sha256": before,
              "source_hashes_unchanged": True, "reproducible": True,
              "coverage_sha256": digest((root / "coverage.json").read_bytes()),
              "catalog_sha256": digest((root / "catalog.csv").read_bytes()),
              "reference_timing_comparison": "not-performed", "uzdoom_direction_scene": "not-performed"}
    (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(root / "report.json")
    print(json.dumps({k: v for k, v in report.items() if k != "inputs_sha256"}))


if __name__ == "__main__":
    main()
