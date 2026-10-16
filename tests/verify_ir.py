"""Статическая приёмка IR-01 на локальном DAT; выход только в build/."""

from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from smuta_build.dat import parse
from smuta_build.extract import digest
from smuta_build.ir import new_instance_state, read_model, write_model
from smuta_build.names import EXE_SHA256
from smuta_build.paths import REPO


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    source = REPO / "originals/smuta_modern_20261013/smuta_modern/SMUTA.DAT"
    paths = [source, source.with_suffix(".EXE"), REPO / "uzdoom/uzdoom.exe"]
    before = {p.name: digest(p.read_bytes()) for p in paths}
    require(before["SMUTA.DAT"] == "6e13c4b65e4530291b11e3d4cd6280d874c489837e7e74c5f53b686ca69a74e8", "Другой DAT")
    require(before["SMUTA.EXE"] == EXE_SHA256, "Другой EXE")
    root = REPO / "build" / ("ir-01 verification " + uuid.uuid4().hex)
    root.mkdir(parents=True)
    report = {"success": False, "python": sys.version, "sources_before": before}
    try:
        version = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
            f"(Get-Item -LiteralPath '{paths[2]}').VersionInfo.FileVersion"],
            capture_output=True, check=True)
        report["uzdoom_version"] = version.stdout.decode().strip()
        report["summary"] = write_model(source, root / "first")
        write_model(source, root / "second")
        first = root / "first/ir.json"
        require(digest(first.read_bytes()) == digest((root / "second/ir.json").read_bytes()), "IR невоспроизводим")
        model = read_model(first)
        archive = parse(source.read_bytes())
        candidates = {e.index for e in archive.entries
                      if b"[Game Ini]" in archive.data[e.offset:e.offset + e.length]}
        require({r["source"]["index"] for r in model["resources"]} == candidates, "Пропущен текст")
        require(len(model["catalog"]) == 460, "Потеря каталога")
        catalog = {r["id"]: r for r in model["catalog"]}
        for entry, metadata in zip(archive.entries, model["catalog"]):
            require(metadata["sha256"] == digest(archive.data[entry.offset:entry.offset + entry.length]), "Хеш ресурса")
        stats = Counter()
        tails = Counter()
        for resource in model["resources"]:
            entry = archive.entries[resource["source"]["index"]]
            payload = archive.data[entry.offset:entry.offset + entry.length]
            require(b"".join(bytes.fromhex(l["raw_hex"]) for l in resource["decoded"]["lines"]) == payload, "Потеря строк")
            definitions = resource["definitions"]
            events = [e for e in resource["decoded"]["events"] if e["kind"] == "definition"]
            require(len(events) == len(definitions), "Потеря определений")
            for definition, event in zip(definitions, events):
                require(definition["tail_raw"] == event["tail"], "Потеря хвоста")
                require(list(definition["common"].values()) == event["common_values"], "Изменены общие поля")
                require(definition["context"] == event["context"], "Потеря контекста")
                tails[definition["tail_status"]] += 1
                for parameter in definition["parameters"]:
                    token = parameter["token"]
                    require(payload[token["offset"]:token["end"]] == bytes.fromhex(token["raw_hex"]), "Позиция параметра")
            for grid in resource["grids"]:
                for row in grid["rows"]:
                    raw = bytes.fromhex(row["raw_hex"])
                    require(payload[row["offset"]:row["offset"] + grid["width"]] == raw, "Потеря сетки")
                    stats["cells"] += len(raw)
                stats["grids"] += 1
            grid = resource["grids"][0]
            require(len(resource["instances"]) == grid["width"] * grid["height"], "Потеря ячейки TMap")
            for instance in resource["instances"]:
                x, y = instance["position"]["column"], instance["position"]["row"]
                key = bytes.fromhex(grid["rows"][y]["raw_hex"])[x]
                require(instance["key_byte"] == key == payload[instance["source_offset"]], "Позиция экземпляра")
                expected = [d["id"] for d in definitions if d["key_byte"] == key]
                require(instance["definition_ids"] == expected, "Не все кандидаты определения")
            for ref in resource["resource_references"]:
                stats["references:" + ref["status"]] += 1
                if ref["target"]:
                    target = catalog[ref["target"]]
                    matches = [e.index for e in archive.entries
                               if e.raw[:16].split(b"\0", 1)[0].hex() == ref["name_hex"]]
                    require(matches and target["index"] == matches[0], "Не первое совпадение DAT")
            state = new_instance_state(resource["instances"][0])
            state["position"]["row"] = -999
            state["runtime"]["test"] = 1
            require(resource["instances"][0]["position"]["row"] == 0, "Общее состояние экземпляров")
            stats["definitions"] += len(definitions)
            stats["instances"] += len(resource["instances"])
            stats["resources"] += 1
        require(stats["resources"] == 42 and stats["grids"] == 84 and stats["cells"] == 344064, "Неверное покрытие")
        require(not model["release_ready"], "Исследовательская модель объявлена релизом")
        try:
            write_model(source, root / "first")
        except ValueError:
            pass
        else:
            raise ValueError("Разрешена перезапись")
        future = root / "future.json"
        future.write_text('{"format":"smuta-ir","schema_version":2}', encoding="utf-8")
        try:
            read_model(future)
        except ValueError:
            pass
        else:
            raise ValueError("Принята неизвестная версия")
        report.update(success=True, counts=dict(stats), tail_statuses=dict(tails),
                      deterministic=True, output_sha256=digest(first.read_bytes()))
    finally:
        report["sources_after"] = {p.name: digest(p.read_bytes()) for p in paths}
        require(before == report["sources_after"], "Изменились входы")
        (root / "report.json").write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
        print(root / "report.json")


if __name__ == "__main__":
    main()
