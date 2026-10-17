"""Статическая приёмка SND-01 на реальном DAT; без запуска игр."""

from collections import Counter
import csv
import io
import json
from pathlib import Path
import struct
import sys
import uuid
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from smuta_build.dat import parse
from smuta_build.extract import digest
from smuta_build.names import NameResolver
from smuta_build.paths import REPO
from smuta_build.sounds import decode_sounds
from smuta_build.text import TEXT_NAME, parse_level


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    source = REPO / "originals/smuta_modern_20261013/smuta_modern/SMUTA.DAT"
    engine = REPO / "uzdoom/uzdoom.exe"
    inputs = [p for p in source.parent.rglob("*") if p.is_file()] + [engine, engine.with_suffix(".pk3")]
    before = {str(p.relative_to(REPO)): digest(p.read_bytes()) for p in inputs}
    rules = json.loads((REPO / "profiles/smuta/sounds.json").read_text(encoding="utf-8"))
    exe = source.with_name("SMUTA.EXE").read_bytes()
    require(digest(exe) == rules["exe_sha256"], "Другой EXE")
    pe = struct.unpack_from("<I", exe, 60)[0]
    opt = pe + 24
    base = struct.unpack_from("<I", exe, opt + 28)[0]
    table = opt + struct.unpack_from("<H", exe, pe + 20)[0]
    sections = [struct.unpack_from("<IIII", exe, table + 40*i + 8)
                for i in range(struct.unpack_from("<H", exe, pe + 6)[0])]
    for proof in rules["evidence"]:
        start, end = int(proof["va"], 16) - base, int(proof["end_va_exclusive"], 16) - base
        offset = next(raw + start - rva for _, rva, size, raw in sections
                      if rva <= start and end <= rva + size)
        require(offset == proof["file_offset"] and end-start == proof["length"], "Диапазон PE")
        require(digest(exe[offset:offset + proof["length"]]) == proof["sha256"], "Изменён код доказательства")
    root = REPO / "build" / f"snd-01 verification {uuid.uuid4().hex}"
    root.mkdir()
    summary = decode_sounds(source, root / "a")
    decode_sounds(source, root / "b")
    files = lambda path: {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}
    require(files(root / "a") == files(root / "b"), "Экспорт не воспроизводится")
    try:
        decode_sounds(source, root / "a")
    except ValueError:
        pass
    else:
        raise ValueError("Допущена перезапись результата")
    manifest = json.loads((root / "a/manifest.json").read_text(encoding="utf-8"))
    bindings = json.loads((root / "a/bindings.json").read_text(encoding="utf-8"))
    a = parse(source.read_bytes())
    require(digest(a.data) == rules["container_sha256"], "Другой DAT")
    catalog = {int(r["Index"]): r for r in csv.DictReader(
        (REPO / "docs/research/smuta-dat-catalog.csv").read_text(encoding="utf-8-sig").splitlines())}
    rows, signatures, rates, empty = [], Counter(), Counter(), []
    by_index = {r["source"]["index"]: r for r in manifest["resources"]}
    require(set(by_index) == {e.index for e in a.entries
                             if a.data[e.offset:e.offset+4] == b"RIFF"}, "Неполный набор WAV")
    for index, resource in by_index.items():
        entry = a.entries[index]
        origin = resource["source"]
        raw = a.data[entry.offset:entry.offset + entry.length]
        original = catalog[index]
        require(origin["offset"] == int(original["Offset"]) and origin["length"] == int(original["Length"])
                and origin["sha256"] == original["SHA256"] and origin["name"] == original["Name"], "Происхождение")
        require((root / "a" / resource["path"]).read_bytes() == raw, "Изменён WAV")
        # Стандартная библиотека независимо проверяет формат и все PCM-данные.
        with wave.open(io.BytesIO(raw), "rb") as stream:
            require((stream.getnchannels(), stream.getsampwidth(), stream.getframerate(), stream.getnframes()) ==
                    (resource["channels"], resource["bits_per_sample"]//8, resource["sample_rate"],
                     resource["sample_frames"]), "Несовпадение с wave")
            require(digest(stream.readframes(stream.getnframes())) == resource["data_sha256"], "Изменён PCM")
        pos, tags = 12, []
        for record in resource["chunks"]:
            tag, length = struct.unpack_from("<4sI", raw, pos)
            require(record["offset"] == pos and record["length"] == length and record["tag_hex"] == tag.hex(), "Чанки")
            require(record["sha256"] == digest(raw[pos+8:pos+8+length]), "Хеш чанка")
            tags.append(tag.decode("ascii"))
            pos += 8 + length + (length & 1)
        require(pos == len(raw), "Пропущен хвост RIFF")
        require(resource["fact_matches_pcm"] is not False, "fact не совпадает с PCM")
        signatures[tuple(tags)] += 1
        rates[resource["sample_rate"]] += 1
        if not resource["sample_frames"]:
            empty.append(index)
        rows.append({"index": index, "name": entry.name, "offset": entry.offset,
                     "length": entry.length, "sha256": digest(raw), "sample_rate": resource["sample_rate"],
                     "sample_frames": resource["sample_frames"], "data_sha256": resource["data_sha256"],
                     "chunks": "/".join(tags), "logical_name": resource["logical_name"]})
    # Сверка событий Sound напрямую с TXT, независимо от понижения IR.
    resolver = NameResolver(a)
    contexts = {(b["source"]["index"], b["origin"]["line"]): b for b in bindings
                if any(u["binding"] == "context.Sound" for u in b["uses"])}
    context_uses = 0
    for entry in a.entries:
        if not TEXT_NAME.fullmatch(entry.name):
            continue
        document = parse_level(a.data[entry.offset:entry.offset+entry.length])
        current = None
        for event in document["events"]:
            if event["kind"] == "directive" and event["name"] == "Sound":
                current = (event["line"], resolver.resolve(event["value"]).index)
            if event["kind"] == "definition" and current is not None:
                binding = contexts[(entry.index, current[0])]
                require(binding["target_index"] == current[1] and
                        any(u["binding"] == "context.Sound" and u["origin"]["line"] == event["line"]
                            for u in binding["uses"]), "Потерян последовательный контекст Sound")
                context_uses += 1
    require(context_uses == sum(u["binding"] == "context.Sound" for b in bindings for u in b["uses"]), "Лишние привязки")
    for name, metadata in manifest["files"].items():
        raw = (root / "a" / name).read_bytes()
        require(metadata == {"length": len(raw), "sha256": digest(raw)}, "Хеш результата")
    require(empty == [332] and rates == {11025: 149, 22050: 7}, "Изменена сводка WAV")
    after = {str(p.relative_to(REPO)): digest(p.read_bytes()) for p in inputs}
    require(before == after, "Изменены исходники")
    with (root / "catalog.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    report = {"task": "SND-01", "date": "2026-10-17", "verification": "static-only",
              "summary": summary, "context_definition_bindings": context_uses,
              "sample_rates": dict(rates), "chunk_sequences": {"/".join(k): v for k, v in signatures.items()},
              "empty_data_indices": empty, "inputs": before, "inputs_unchanged": before == after,
              "reproducible": True, "independent_pcm_check": "Python wave",
              "exe_evidence_ranges": len(rules["evidence"]), "engine_version": "5.0.3 (ENV-01; not launched)",
              "playback_compared": False, "release_ready": False}
    (root / "report.json").write_text(json.dumps(report, ensure_ascii=True, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"report": str(root / "report.json"), **summary}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
