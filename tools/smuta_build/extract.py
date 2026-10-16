"""Извлечение без потерь и стабильный манифест происхождения v1."""

import hashlib
import json
from pathlib import Path

from . import __version__
from .dat import Archive, parse
from .paths import files_under, no_links, output_path


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def layout(archive: Archive) -> tuple[dict, dict[str, bytes]]:
    data = archive.data
    files = {"directory.bin": data[:archive.directory_end]}
    entries = []
    for entry in archive.entries:
        path = f"entries/{entry.index:06d}.bin"
        payload = data[entry.offset:entry.offset + entry.length]
        files[path] = payload
        entries.append({
            "index": entry.index, "name": entry.name,
            "name_field_hex": entry.raw[:16].hex(),
            "directory_record_hex": entry.raw.hex(),
            "directory_offset": 2 + entry.index * 30,
            "unknown_fields_hex": entry.raw[24:].hex(),
            "offset": entry.offset, "length": entry.length,
            "sha256": digest(payload), "path": path,
            "decoder": None, "transformations": [],
            "validation": "range-and-hash-only",
        })
    gaps = []
    for index, (offset, length) in enumerate(archive.gaps):
        path = f"unreferenced/{index:06d}.bin"
        payload = data[offset:offset + length]
        files[path] = payload
        gaps.append({"offset": offset, "length": length,
                     "sha256": digest(payload), "path": path})
    manifest = {
        "schema_version": 1, "game": "smuta", "tool_version": __version__,
        "container": {"sha256": digest(data), "length": len(data)},
        "directory": {"path": "directory.bin", "length": archive.directory_end,
                      "sha256": digest(files["directory.bin"])},
        "entries": entries, "unreferenced": gaps,
    }
    return manifest, files


def extract(source: Path, destination: Path) -> dict:
    archive = parse(no_links(source).read_bytes())
    manifest, files = layout(archive)
    destination = output_path(destination)
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError(f"Выходной каталог не пуст: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(data)
    # Маркер завершённого извлечения записывается последним.
    with (destination / "manifest.json").open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def check(source: Path, destination: Path) -> dict:
    original = no_links(source).read_bytes()
    archive = parse(original)
    expected, files = layout(archive)
    destination = no_links(destination)
    actual_files = {path.relative_to(destination).as_posix()
                    for path in files_under(destination)}
    if actual_files != set(files) | {"manifest.json"}:
        raise ValueError("Состав извлечения отличается от ожидаемого")
    actual = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    # Не доверяем путям/смещениям из внешнего JSON: сначала сравниваем всю модель.
    if actual != expected:
        raise ValueError("Манифест не соответствует исходному DAT или версии инструмента")
    restored = bytearray(len(original))
    for name, expected_bytes in files.items():
        saved = (destination / name).read_bytes()
        if saved != expected_bytes:
            raise ValueError(f"Повреждён файл: {name}")
    directory = expected["directory"]
    restored[:directory["length"]] = (destination / directory["path"]).read_bytes()
    for segment in expected["entries"] + expected["unreferenced"]:
        offset = segment["offset"]
        restored[offset:offset + segment["length"]] = (destination / segment["path"]).read_bytes()
    if restored != original or digest(restored) != expected["container"]["sha256"]:
        raise ValueError("Побайтное восстановление DAT не совпало с оригиналом")
    return {"entries": len(archive.entries), "sha256": digest(restored),
            "restored_byte_for_byte": True}
