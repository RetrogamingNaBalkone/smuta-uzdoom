"""Интеграционная приёмка TXT-01; локальные источники только читаются."""

from collections import Counter
import json
from pathlib import Path
import re
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from smuta_build.dat import parse
from smuta_build.extract import digest
from smuta_build.names import EXE_SHA256
from smuta_build.paths import REPO
from smuta_build.text import decode_archive


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    source = REPO / "originals/smuta_modern_20261013/smuta_modern/SMUTA.DAT"
    paths = [source, source.with_suffix(".EXE"), REPO / "uzdoom/uzdoom.exe"]
    before = {p.name: digest(p.read_bytes()) for p in paths}
    require(before["SMUTA.DAT"] == "6e13c4b65e4530291b11e3d4cd6280d874c489837e7e74c5f53b686ca69a74e8", "Другой DAT")
    require(before["SMUTA.EXE"] == EXE_SHA256, "Другой EXE")
    root = REPO / "build" / ("txt-01 verification " + uuid.uuid4().hex)
    root.mkdir(parents=True)
    report = {"success": False, "python": sys.version, "sources_before": before}
    try:
        version = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
            f"(Get-Item -LiteralPath '{paths[2]}').VersionInfo.FileVersion"],
            capture_output=True, check=True)
        report["uzdoom_version"] = version.stdout.decode().strip()
        report["summary"] = decode_archive(source, root / "first")
        decode_archive(source, root / "second")
        first = (root / "first/text.json").read_bytes()
        require(first == (root / "second/text.json").read_bytes(), "Недетерминированный вывод")
        resources = json.loads(first)["resources"]
        require(len(resources) == 42, "Не 42 ресурса")
        archive = parse(source.read_bytes())
        # Независимый от TEXT_NAME список: обнаружение по секции в содержимом.
        candidates = {e.index for e in archive.entries
                      if b"[Game Ini]" in archive.data[e.offset:e.offset + e.length]}
        require({r["source"]["index"] for r in resources} == candidates, "Пропущен текстовый ресурс")
        details = []
        level_grids = 0
        definitions = 0
        for document in resources:
            entry = archive.entries[document["source"]["index"]]
            payload = archive.data[entry.offset:entry.offset + entry.length]
            require(b"".join(bytes.fromhex(l["raw_hex"]) for l in document["lines"]) == payload,
                    "Потеря исходных строк")
            # Независимый эталон: строки точной ширины между маркерами секций.
            sections = re.split(rb"(?m)^\[(?:TMap|FCMap)\]\r?$", payload)
            require(len(sections) == 3, "Нет двух сеток")
            for grid, block in zip(document["grids"], sections[1:]):
                expected = [line.removesuffix(b"\r") for line in block.split(b"\n")
                            if len(line.removesuffix(b"\r")) == 64]
                actual = [bytes.fromhex(row["raw_hex"]) for row in grid["rows"]]
                require(actual == expected and len(actual) == 64, "Сетка не совпала")
                require(all(row["text"].encode("cp1251", errors="surrogateescape") == raw
                            for row, raw in zip(grid["rows"], actual)), "Потеря символа")
                for row, raw in zip(grid["rows"], actual):
                    require(payload[row["offset"]:row["offset"] + 64] == raw, "Позиция сетки")
                if entry.name.startswith("LEVEL"):
                    level_grids += 1
            current = {"Sprite": None, "Flic": None, "Sound": None}
            for event in document["events"]:
                for token in event.get("tokens", []):
                    require(payload[token["offset"]:token["end"]] == bytes.fromhex(token["raw_hex"]), "Позиция токена")
                if event["kind"] == "directive" and event["section"] == "Game Object":
                    current[event["name"]] = {"line": event["line"], "value": event["value"]}
                if event["kind"] == "definition":
                    definitions += 1
                    require(event["context"] == current, "Потеря контекста")
            details.append({"source": document["source"], "diagnostics": document["diagnostics"],
                            "grid_hashes": {g["name"]: digest(b"".join(bytes.fromhex(r["raw_hex"])
                                           for r in g["rows"])) for g in document["grids"]}})
        require(level_grids == 60, "Не 60 сеток LEVEL")
        level10 = next(r for r in resources if r["source"]["name"] == "LEVEL10-1")
        require(any(e.get("key_byte") == 160 and e["line"] == 124 for e in level10["events"]), "Потерян A0")
        report.update(resources=details, level_grids=level_grids, definitions=definitions,
                      output_sha256=digest(first), deterministic=True,
                      diagnostics=dict(Counter(d["code"] for r in resources for d in r["diagnostics"])))
        try:
            decode_archive(source, root / "first")
        except ValueError:
            pass
        else:
            raise ValueError("Разрешена перезапись результата")
        report["success"] = True
    finally:
        report["sources_after"] = {p.name: digest(p.read_bytes()) for p in paths}
        require(before == report["sources_after"], "Изменились входы")
        (root / "report.json").write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
        print(root / "report.json")


if __name__ == "__main__":
    main()
