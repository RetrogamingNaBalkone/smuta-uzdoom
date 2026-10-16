"""Статическая приёмка LVL-01 на локальном эталоне; игры не запускаются."""

from collections import Counter
import json
from pathlib import Path
import re
import struct
import subprocess
import sys
import uuid
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from smuta_build.dat import parse
from smuta_build.extract import digest
from smuta_build.ir import provenance
from smuta_build.levels import build_comparison, write_comparison
from smuta_build.paths import REPO


def require(condition, message):
    if not condition:
        raise ValueError(message)


def independent(payload):
    """Прямой байтовый эталон без TXT/IR: для исследованных однозначных определений."""
    parts = re.split(rb"(?m)^\[(?:TMap|FCMap)\]\r?$", payload)
    grids = [[line.removesuffix(b"\r") for line in part.split(b"\n")
              if len(line.removesuffix(b"\r")) == 64] for part in parts[1:]]
    require(len(grids) == 2 and all(len(g) == 64 for g in grids), "Не 64x64")
    objects = parts[0].split(b"[Game Object]", 1)[1]
    definitions, contexts = [], {b"Sprite": None, b"Flic": None, b"Sound": None}
    for raw in objects.split(b"\n"):
        line = raw.strip(b" \t\r")
        if not line or line.startswith(b"#"):
            continue
        directive = re.fullmatch(rb"(Sprite|Flic|Sound)\s*=[ \t]*(.*)", line)
        if directive:
            contexts[directive[1]] = re.findall(rb"[^ \t]+", directive[2])
        else:
            tokens = re.findall(rb"[^ \t]+", line)
            key = b" " if tokens[0] == b"_" else tokens[0]
            definitions.append((key, tokens[1:], dict(contexts)))
    return grids, definitions


def verify_pe(exe, evidence):
    require(exe[:2] == b"MZ", "Нет MZ")
    pe, = struct.unpack_from("<I", exe, 60)
    require(exe[pe:pe + 4] == b"PE\0\0", "Нет PE")
    optional = pe + 24
    image_base, = struct.unpack_from("<I", exe, optional + 28)
    count, = struct.unpack_from("<H", exe, pe + 6)
    opt_size, = struct.unpack_from("<H", exe, pe + 20)
    sections = []
    for i in range(count):
        _, rva, size, raw = struct.unpack_from("<IIII", exe, optional + opt_size + i * 40 + 8)
        require(raw + size <= len(exe), "Секция вне EXE")
        sections.append((rva, size, raw))
    for item in evidence:
        offset, length = item["offset"], item["length"]
        va = int(item["va"], 16)
        require(any(0 <= va - image_base - rva and va - image_base - rva + length <= size
                    and raw + va - image_base - rva == offset for rva, size, raw in sections),
                "VA не соответствует файловому диапазону")
        require(0 <= offset <= offset + length <= len(exe), "Диапазон вне EXE")
        require(exe[offset:offset + length].hex() == item["bytes_hex"], "Изменилось доказательство EXE")


def menu_preview(archive, profile, target):
    source = profile["menu"]["resource"]
    entry = archive.entries[source["index"]]
    require(provenance(archive, entry) == source, "Другой ресурс меню")
    data = archive.data[entry.offset:entry.offset + entry.length]
    width, height = struct.unpack_from("<HH", data)
    require((width, height) == (141, 70) and len(data) == 18 + width * height, "Размер меню")
    # Исследовательская маска ненулевых индексов; палитра/прозрачность игры не утверждаются.
    scanlines = b"".join(b"\0" + bytes(255 if v else 0 for v in data[18 + y * width:18 + (y + 1) * width])
                         for y in range(height))
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    target.write_bytes(b"\x89PNG\r\n\x1a\n" +
        chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)) +
        chunk(b"IDAT", zlib.compress(scanlines)) + chunk(b"IEND", b""))


def main():
    source = REPO / "originals/smuta_modern_20261013/smuta_modern/SMUTA.DAT"
    paths = [source, source.with_suffix(".EXE"), REPO / "uzdoom/uzdoom.exe"]
    before = {p.name: digest(p.read_bytes()) for p in paths}
    profile = json.loads((REPO / "profiles/smuta/campaign.json").read_text(encoding="utf-8"))
    require(before["SMUTA.DAT"] == profile["dat_sha256"], "Другой DAT")
    require(before["SMUTA.EXE"] == profile["exe_sha256"], "Другой EXE")
    root = REPO / "build" / ("lvl-01 verification " + uuid.uuid4().hex)
    root.mkdir(parents=True)
    report = {"success": False, "python": sys.version, "sources_before": before,
              "method": "static-only-no-game-launch"}
    try:
        version = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
            f"(Get-Item -LiteralPath '{paths[2]}').VersionInfo.FileVersion"], capture_output=True, check=True)
        report["uzdoom_version"] = version.stdout.decode().strip()
        write_comparison(source, root / "first")
        write_comparison(source, root / "second")
        data = (root / "first/levels.json").read_bytes()
        require(data == (root / "second/levels.json").read_bytes(), "Различается повторный вывод")
        result = json.loads(data)
        for variant in result["variants"]:
            references = {r["id"] for r in variant["resource_references"]}
            require(all(p["reference_id"] in references for d in variant["definitions"]
                        for p in d["parameters"] if "reference_id" in p), "Потеря параметрической ссылки")
        archive = parse(source.read_bytes())
        docs = {e.name: independent(archive.data[e.offset:e.offset + e.length])
                for e in archive.entries if re.fullmatch(r"LEVEL(?:[1-9]|10)-[123]", e.name)}
        summary = []
        for group in result["groups"]:
            for pair in group["pairs"]:
                (ag, ad), (bg, bd) = docs[pair["before"]], docs[pair["after"]]
                require([d[0] for d in ad] == [d[0] for d in bd], "Изменён порядок ключей")
                for g, a, b in zip(pair["grids"], ag, bg, strict=True):
                    expected = [(y, x, av, bv) for y, (ar, br) in enumerate(zip(a, b, strict=True))
                                for x, (av, bv) in enumerate(zip(ar, br, strict=True)) if av != bv]
                    actual = [(c["row"], c["column"], c["before_byte"], c["after_byte"]) for c in g["changes"]]
                    require(actual == expected, "Потеря изменённых ячеек")
                params = sum(a[1] != b[1] for a, b in zip(ad, bd, strict=True))
                contexts = sum(a[2] != b[2] for a, b in zip(ad, bd, strict=True))
                require(params == pair["parameter_changes"] and contexts == pair["context_changes"],
                        "Неверное сравнение параметров/контекстов")
                summary.append({"number": group["number"], "before": pair["before"], "after": pair["after"],
                    "tmap_cells": pair["grids"][0]["changed_cells"], "fcmap_cells": pair["grids"][1]["changed_cells"],
                    "parameter_changes": params, "context_changes": contexts,
                    "changed_types": dict(Counter(str(d["before"]["common"]["type"]) for d in pair["definitions"]))})
        for variant in profile["variants"]:
            require(len(variant["route"]) == 10, "Неверная длина маршрута")
            for n, item in enumerate(variant["route"], 1):
                entry = archive.entries[item["index"]]
                require(provenance(archive, entry) == item and entry.name == f"LEVEL{n}-{variant['index']}",
                        "Потеря варианта или неверный маршрут")
        require(len(result["excluded_from_campaign"]) == 12, "Потеря DEMO/TEST")
        verify_pe(paths[1].read_bytes(), profile["exe_evidence"])
        menu_preview(archive, profile, root / "difficulty-menu-mask.png")
        try:
            write_comparison(source, root / "first")
        except ValueError:
            pass
        else:
            raise ValueError("Разрешена перезапись")
        # Полный каталог обязателен: отсутствие/дубликат не превращается в частичный успех.
        for replacement in (b"DELETED\0", b"LEVEL1-1\0"):
            damaged = bytearray(archive.data)
            entry = next(e for e in archive.entries if e.name == "LEVEL1-2")
            damaged[2 + entry.index * 30:18 + entry.index * 30] = replacement.ljust(16, b"\0")
            try:
                build_comparison(bytes(damaged))
            except ValueError:
                pass
            else:
                raise ValueError("Принят неполный или неоднозначный набор")
        report.update(success=True, deterministic=True, output_sha256=digest(data),
                      variants=30, pairs=summary, exe_ranges=len(profile["exe_evidence"]),
                      menu_preview_transformation="18-byte-header/nonzero-indices-white/row-order-as-stored")
    finally:
        report["sources_after"] = {p.name: digest(p.read_bytes()) for p in paths}
        require(before == report["sources_after"], "Изменились исходники")
        (root / "report.json").write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
        print(root / "report.json")


if __name__ == "__main__":
    main()
