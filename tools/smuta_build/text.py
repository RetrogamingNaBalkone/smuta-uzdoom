"""TXT-01: последовательное декодирование текста, без игровой семантики."""

import json
import re
from pathlib import Path

from .dat import parse
from .extract import digest
from .paths import no_links, output_path

DECODER = "smuta-text-v1"
SECTIONS = ("Game Ini", "Palette", "Light", "Game Object", "TMap", "FCMap")
TEXT_NAME = re.compile(r"(?:LEVEL(?:[1-9]|10)-[123]|DEMO(?:[1-9]|10)?|TEST)\Z")
# Только ASCII-разделители: Unicode split/strip уничтожает ключ A0.
TOKEN = re.compile(rb"[^ \t\r\n\v\f]+")
INTEGER = re.compile(r"[+-]?[0-9]+\Z")


def decode(data: bytes) -> str:
    # 98 не определён в CP1251, но используется как ключ/ячейка. Не заменять.
    return data.decode("cp1251", errors="surrogateescape")


def parse_level(data: bytes) -> dict:
    """Позиции: строки/колонки с 1, диапазоны байтов [offset, end) с 0."""
    lines = []
    offset = 0
    # Делим только по LF; в отличие от str.splitlines не режем 85/NBSP.
    chunks = data.split(b"\n")
    if chunks[-1] == b"":
        chunks.pop()
    for number, chunk in enumerate(chunks, 1):
        raw = chunk + (b"\n" if number < len(chunks) or data.endswith(b"\n") else b"")
        body = raw[:-1] if raw.endswith(b"\n") else raw
        if body.endswith(b"\r"):
            body = body[:-1]
        lines.append({"line": number, "offset": offset, "end": offset + len(raw),
                      "raw_hex": raw.hex(), "text": decode(body)})
        offset += len(raw)

    diagnostics = []
    events = []
    grids = []
    settings = {}
    context = {"Sprite": None, "Flic": None, "Sound": None}
    definitions = {}
    section = None
    seen = []

    def fail(line, message):
        raise ValueError(f"Строка {line['line']}, байт {line['offset']}: {message}")

    def diagnostic(line, code, **details):
        diagnostics.append({"code": code, "line": line["line"],
                            "offset": line["offset"], **details})

    def tokens(line):
        body = line["text"].encode("cp1251", errors="surrogateescape")
        return [{"text": decode(m.group()), "raw_hex": m.group().hex(),
                 "column": m.start() + 1, "offset": line["offset"] + m.start(),
                 "end": line["offset"] + m.end()} for m in TOKEN.finditer(body)]

    for line in lines:
        text = line["text"]
        raw_body = text.encode("cp1251", errors="surrogateescape")
        if b"\x98" in raw_body:
            diagnostic(line, "undefined-cp1251-byte", byte="98",
                       columns=[i + 1 for i, value in enumerate(raw_body) if value == 0x98])
        if b"\0" in raw_body or b"\r" in raw_body:
            fail(line, "NUL или одиночный CR в содержимом строки")
        # Внутри сетки даже # и [ являются ячейками, не комментариями/секциями.
        if section in ("TMap", "FCMap") and len(grids[-1]["rows"]) < grids[-1]["height"]:
            grid = grids[-1]
            if not grid["rows"] and (not text or (
                    text.startswith("#") and len(raw_body) != grid["width"])):
                continue
            if len(raw_body) != grid["width"]:
                fail(line, f"Ширина {section}: {len(raw_body)}, ожидается {grid['width']}")
            grid["rows"].append({"line": line["line"], "offset": line["offset"],
                                 "text": text, "raw_hex": raw_body.hex()})
            continue
        if not text.strip(" \t") or text.startswith("#"):
            continue
        if text.startswith("["):
            name = text[1:-1] if text.endswith("]") else ""
            if len(seen) >= len(SECTIONS) or name != SECTIONS[len(seen)]:
                fail(line, f"Неожиданная секция {text}")
            section = name
            seen.append(name)
            events.append({"kind": "section", "name": name, "line": line["line"]})
            if name in ("TMap", "FCMap"):
                for key in ("Level Width", "Level Length", "Level Height"):
                    if key not in settings or settings[key] <= 0:
                        fail(line, f"Нет положительного размера {key}")
                if settings["Level Height"] != 1:
                    fail(line, "Многоэтажная грамматика пока не исследована")
                grids.append({"name": name, "width": settings["Level Width"],
                              "height": settings["Level Length"], "rows": []})
            continue
        if section in ("Game Ini", "Palette", "Light") or (
                section == "Game Object" and re.match(r"(?:Sprite|Flic|Sound)[ \t]*=", text)):
            if "=" not in text:
                fail(line, "Ожидается присваивание")
            key, value = text.split("=", 1)
            key, value = key.strip(" \t"), value.strip(" \t")
            if not key or not value:
                fail(line, "Пустое имя или значение директивы")
            event = {"kind": "directive", "section": section, "name": key,
                     "value": value, "line": line["line"], "tokens": tokens(line)}
            events.append(event)
            if section == "Game Ini":
                if not INTEGER.fullmatch(value):
                    fail(line, "Ожидается целое значение Game Ini")
                if key in settings:
                    diagnostic(line, "duplicate-setting", name=key)
                settings[key] = int(value)
            elif section == "Game Object":
                parts = TOKEN.findall(value.encode("cp1251", errors="surrogateescape"))
                if key == "Sprite":
                    if len(parts) not in (1, 2) or (len(parts) == 2 and not INTEGER.fullmatch(decode(parts[1]))):
                        fail(line, "Ожидается Sprite = имя [число]")
                elif len(parts) != 1:
                    fail(line, "Ожидается одно имя Flic/Sound")
                context[key] = {"line": line["line"], "value": value}
            continue
        if section != "Game Object":
            fail(line, "Неожиданная строка вне определений (либо лишняя строка сетки)")
        parts = tokens(line)
        if len(parts) < 2 or len(bytes.fromhex(parts[0]["raw_hex"])) != 1:
            fail(line, "Ожидается однобайтовый ключ и числовые поля")
        if any(not INTEGER.fullmatch(p["text"]) for p in parts[1:6]):
            fail(line, "Общие поля объекта должны быть целыми")
        if len(parts) < 6:
            diagnostic(line, "incomplete-common-fields", present=len(parts) - 1, expected=5)
        source_key = int(parts[0]["raw_hex"], 16)
        key = 32 if source_key == 95 else source_key
        if key in definitions:
            diagnostic(line, "duplicate-definition", key_byte=key,
                       first_line=definitions[key])
        else:
            definitions[key] = line["line"]
        tail_start = parts[min(5, len(parts) - 1)]["end"] - line["offset"]
        events.append({"kind": "definition", "line": line["line"],
                       "source_key_byte": source_key, "key_byte": key,
                       "common_values": [int(p["text"]) for p in parts[1:6]],
                       "tail": decode(raw_body[tail_start:]), "tokens": parts,
                       "context": context.copy()})
    if tuple(seen) != SECTIONS:
        raise ValueError("Отсутствуют обязательные секции текста уровня")
    if any(len(g["rows"]) != g["height"] for g in grids):
        raise ValueError("Усечённая сетка уровня")
    return {"decoder": DECODER, "encoding": "cp1251+surrogateescape",
            "lines": lines, "events": events, "grids": grids,
            "diagnostics": diagnostics,
            "transformations": (
                ["definition-key-5f-to-20"] if any(e.get("source_key_byte") == 95 for e in events) else []
            ) + (["undefined-98-to-udc98-reversible"] if b"\x98" in data else []),
            "validation": "syntax-and-grid-only"}


def decode_archive(source: Path, destination: Path) -> dict:
    archive = parse(no_links(source).read_bytes())
    container_hash = digest(archive.data)
    resources = []
    for entry in archive.entries:
        if not TEXT_NAME.fullmatch(entry.name):
            continue
        payload = archive.data[entry.offset:entry.offset + entry.length]
        try:
            document = parse_level(payload)
        except ValueError as error:
            raise ValueError(f"Запись {entry.index} ({entry.name}): {error}") from error
        resources.append({"source": {"game": "smuta", "container_sha256": container_hash,
                          "index": entry.index, "name": entry.name, "offset": entry.offset,
                          "length": entry.length, "sha256": digest(payload)}, **document})
    if not resources:
        raise ValueError("Текстовые ресурсы Смуты не найдены")
    result = {"schema_version": 1, "decoder": DECODER, "resources": resources}
    destination = output_path(destination)
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError(f"Выходной каталог не пуст: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    # ASCII JSON допускает обратимое представление неопределённого байта 98.
    with (destination / "text.json").open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(result, ensure_ascii=True, indent=2) + "\n")
    return {"resources": len(resources), "grids": sum(len(r["grids"]) for r in resources),
            "diagnostics": sum(len(r["diagnostics"]) for r in resources),
            "container_sha256": container_hash}
