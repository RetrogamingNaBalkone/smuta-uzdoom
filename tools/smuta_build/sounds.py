"""SND-01: проверка RIFF/PCM и привязки в исходных единицах, без игровых догадок."""

import json
from pathlib import Path
import struct

from .dat import parse
from .extract import digest
from .ir import lower_document, provenance
from .names import NameResolver, output_name
from .paths import REPO, no_links, output_path
from .text import TEXT_NAME, parse_level

DECODER = "smuta-riff-pcm-v1"


def chunks(data: bytes, start: int, end: int, depth: int = 0) -> list[dict]:
    """Размер не включает заголовок и байт выравнивания. Поиск сигнатур запрещён."""
    if depth > 32:
        raise ValueError("Слишком глубокая вложенность RIFF")
    result = []
    while start < end:
        if end - start < 8:
            raise ValueError("Усечённый заголовок чанка RIFF")
        tag, length = struct.unpack_from("<4sI", data, start)
        stop = start + 8 + length
        padded = stop + (length & 1)
        if padded > end:
            raise ValueError("Чанк RIFF или его padding выходит за родителя")
        row = {"tag_hex": tag.hex(), "offset": start, "data_offset": start + 8,
               "length": length, "sha256": digest(data[start + 8:stop]),
               "padding_hex": data[stop:padded].hex()}
        if tag == b"LIST":
            if length < 4:
                raise ValueError("Нет типа LIST")
            row["list_type_hex"] = data[start + 8:start + 12].hex()
            # INFO содержит вложенные чанки; остальные типы храним непрозрачно.
            if data[start + 8:start + 12] == b"INFO":
                row["children"] = chunks(data, start + 12, stop, depth + 1)
        result.append(row)
        start = padded
    return result


def parse_wave(data: bytes) -> dict:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("Ожидается RIFF/WAVE")
    if struct.unpack_from("<I", data, 4)[0] + 8 != len(data):
        raise ValueError("Размер RIFF не совпадает с записью")
    records = chunks(data, 12, len(data))
    def one(tag):
        found = [r for r in records if r["tag_hex"] == tag.hex()]
        if len(found) != 1:
            raise ValueError(f"Ожидается единственный чанк {tag!r}")
        r = found[0]
        return r, data[r["data_offset"]:r["data_offset"] + r["length"]]
    fmt, raw = one(b"fmt ")
    if len(raw) < 16 or len(raw) == 17:
        raise ValueError("Усечённый fmt")
    codec, channels, rate, byte_rate, align, bits = struct.unpack_from("<HHIIHH", raw)
    if codec != 1:
        raise ValueError(f"Неподдержанный кодек WAVE: {codec}")
    if not channels or not rate or bits not in (8, 16, 24, 32):
        raise ValueError("Недопустимые параметры PCM")
    if align != channels * (bits // 8) or byte_rate != rate * align:
        raise ValueError("Несогласованные blockAlign/byteRate PCM")
    if len(raw) > 16 and struct.unpack_from("<H", raw, 16)[0] != len(raw) - 18:
        raise ValueError("Размер расширения fmt не совпадает с cbSize")
    samples, payload = one(b"data")
    if len(payload) % align:
        raise ValueError("Неполный кадр PCM")
    if fmt["offset"] > samples["offset"]:
        raise ValueError("data предшествует fmt")
    facts = [r for r in records if r["tag_hex"] == b"fact".hex()]
    if len(facts) > 1 or any(r["length"] < 4 for r in facts):
        raise ValueError("Некорректный fact")
    frame_count = len(payload) // align
    # fact остаётся исходным утверждением файла; несовпадение не исправляется.
    fact_count = struct.unpack_from("<I", data, facts[0]["data_offset"])[0] if facts else None
    return {"format": "riff-wave-pcm", "riff_header_hex": data[:12].hex(),
            "chunks": records, "fmt_hex": raw.hex(), "codec": codec,
            "channels": channels, "sample_rate": rate, "byte_rate": byte_rate,
            "block_align": align, "bits_per_sample": bits,
            "data_offset": samples["data_offset"], "data_length": len(payload),
            "data_sha256": digest(payload), "sample_frames": frame_count,
            "duration": {"numerator": frame_count, "denominator": rate, "unit": "seconds"},
            "fact_sample_frames_raw": fact_count,
            "fact_matches_pcm": None if fact_count is None else fact_count == frame_count}


def sound_bindings(archive, sound_indices: set[int]) -> list[dict]:
    resolver = NameResolver(archive)
    result = []
    for entry in archive.entries:
        if not TEXT_NAME.fullmatch(entry.name):
            continue
        origin = provenance(archive, entry)
        model = lower_document(parse_level(archive.data[entry.offset:entry.offset + entry.length]), origin, resolver)
        refs = {r["id"]: r for r in model["resource_references"] if r["role"] == "sound"}
        uses = {rid: [] for rid in refs}
        for definition in model["definitions"]:
            base = {"definition_id": definition["id"], "key_byte": definition["key_byte"],
                    "type": definition["common"].get("type"), "origin": definition["origin"]}
            rid = definition["resource_bindings"]["Sound"]
            if rid is not None:
                uses[rid].append({**base, "binding": "context.Sound"})
            for parameter in definition["parameters"]:
                rid = parameter.get("reference_id")
                if rid in refs:
                    uses[rid].append({**base, "binding": parameter["name"]})
        for rid, ref in refs.items():
            if ref["status"] != "catalog-resolved":
                raise ValueError(f"Не разрешена звуковая ссылка {rid}")
            index = int(ref["target"].rsplit(":", 1)[1])
            if index not in sound_indices:
                raise ValueError(f"Звуковая ссылка {rid} указывает не на WAV")
            result.append({**ref, "source": origin, "target_index": index,
                           "logical_name": f"smuta/{output_name(archive.entries[index])}",
                           "uses": uses[rid], "event": None,
                           "playback_status": "requires-caller-and-scene-verification"})
    return result


def decode_sounds(source: Path, destination: Path) -> dict:
    archive = parse(no_links(source).read_bytes())
    rules_raw = (REPO / "profiles/smuta/sounds.json").read_bytes()
    rules = json.loads(rules_raw)
    if digest(archive.data) != rules["container_sha256"]:
        raise ValueError("Профиль звуков относится к другому DAT")
    destination = output_path(destination)
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError("Выходной каталог должен быть новым или пустым")
    resources, files = [], {}
    for entry in archive.entries:
        raw = archive.data[entry.offset:entry.offset + entry.length]
        # Расширение не участвует в обнаружении формата.
        if raw[:4] != b"RIFF":
            continue
        try:
            metadata = parse_wave(raw)
        except ValueError as error:
            raise ValueError(f"Запись {entry.index}: {error}") from error
        path = f"sounds/{output_name(entry)}.wav"
        files[path] = raw
        resources.append({"source": provenance(archive, entry), "decoder": DECODER,
                          "path": path, "logical_name": f"smuta/{output_name(entry)}",
                          "transformations": [], **metadata})
    if len(resources) != rules["wave_count"]:
        raise ValueError("Число WAV не совпадает с профилем")
    bindings = sound_bindings(archive, {r["source"]["index"] for r in resources})
    # Только имена и пути: политика воспроизведения остаётся у будущего генератора.
    files["SNDINFO"] = ("// SND-01: исследовательские имена; поведение ещё не перенесено.\n" +
        "".join(f'{r["logical_name"]} {r["path"]}\n' for r in resources)).encode("utf-8")
    files["bindings.json"] = (json.dumps(bindings, ensure_ascii=True, indent=2) + "\n").encode("utf-8")
    manifest = {"format": "smuta-sounds-research", "version": 1, "decoder": DECODER,
                "container_sha256": digest(archive.data), "rules_sha256": digest(rules_raw),
                "rules": rules, "resources": resources, "release_ready": False,
                "blockers": ["Условия запуска, повторы, одновременность и затухание требуют CMP-14 и интеграции"],
                "files": {name: {"length": len(data), "sha256": digest(data)}
                          for name, data in sorted(files.items())}}
    destination.mkdir(parents=True, exist_ok=True)
    for name, data in sorted(files.items()):
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return {"sounds": len(resources), "references": len(bindings),
            "definition_bindings": sum(len(b["uses"]) for b in bindings), "release_ready": False}
