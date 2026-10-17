"""GFX-03: шрифты, LIGHT1 и полный каталог; без генерации игрового пакета."""

from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
import struct

from .animations import decode_fl
from .dat import parse
from .extract import digest
from .graphics import IndexedImage, decode_bmp, decode_palette, decode_spr, indexed_png
from .ir import provenance
from .names import NameResolver, output_name
from .paths import REPO, no_links, output_path
from .text import TEXT_NAME, parse_level


def glyph_index(value: int) -> int:
    """0042659F–004265D2: именно алгоритм EXE, включая область 80…BF."""
    if not 0 <= value <= 255:
        raise ValueError("Нужен байт текста")
    return value if value < 128 else value - (64 if value < 240 else 16)


def decode_font(data: bytes) -> tuple[IndexedImage, ...]:
    if len(data) not in (2048, 4096):
        raise ValueError("Ожидается 256 глифов 8×8 или 8×16")
    height = len(data) // 256
    return tuple(IndexedImage(8, height, bytes(
        (row >> (7 - x)) & 1 for row in data[c * height:(c + 1) * height]
        for x in range(8)), {"glyph": c, "orientation": "top-down"}) for c in range(256))


def character_map() -> list[dict]:
    result = []
    for value in range(256):
        char = bytes([value]).decode("cp1251", errors="surrogateescape")
        # Unicode назначаем только проверенным ASCII и русскому алфавиту А…я.
        verified = 32 <= value < 127 or value >= 192
        result.append({"byte": value, "cp1251": None if value == 152 else char,
                       "glyph": glyph_index(value), "unicode_verified": verified,
                       "text_control": {0: "end", 10: "newline", 13: "skip", 36: "end"}.get(value),
                       "note": "Ё/ё не имеют отдельной ветви EXE" if value in (168, 184) else None})
    return result


@dataclass(frozen=True)
class Light:
    indexed: bytes
    rgb555: tuple[int, ...]
    blend: bytes

    def shade(self, pixel: int, level: int) -> int:
        if not 0 <= pixel < 256 or not 0 <= level < 32:
            raise ValueError("Индекс 0…255, ступень освещения 0…31")
        return self.indexed[level * 256 + pixel]

    def mix(self, source: int, destination: int) -> int:
        if not 0 <= source < 256 or not 0 <= destination < 256:
            raise ValueError("Индексы смешивания должны быть байтами")
        return self.blend[source * 256 + destination]


def decode_light(data: bytes) -> Light:
    if len(data) != 90112:
        raise ValueError("LIGHT1 должен содержать 8192 + 16384 + 65536 байт")
    values = struct.unpack("<8192H", data[8192:24576])
    if any(value > 32767 for value in values):
        raise ValueError("Установлен старший бит RGB555")
    return Light(data[:8192], values, data[24576:])


def direct_colors(palette: bytes, *, rgb565: bool = False) -> tuple[int, ...]:
    """00410B64: пересчёт из RGB6; режим 2 использует RGB565."""
    decode_palette(palette)
    result = []
    for level in range(32):
        for pixel in range(256):
            r, g, b = (palette[pixel * 3 + k] * (32 - level) // 32 for k in range(3))
            result.append(((r // 2) << (11 if rgb565 else 10))
                          | ((g if rgb565 else g // 2) << 5) | (b // 2))
    return tuple(result)


def classify(archive, profile: dict) -> list[dict]:
    """Формат и использование — разные сведения; отсутствие ссылки не значит неиспользование."""
    resolver = NameResolver(archive)
    rows = []
    sprites = set(profile["spr18_without_extension"])
    animations = set(profile["fl_without_extension"])
    for entry in archive.entries:
        raw = archive.data[entry.offset:entry.offset + entry.length]
        evidence = ""
        if TEXT_NAME.fullmatch(entry.name):
            parse_level(raw)
            kind, evidence = "level-text", "TXT-01: последовательная грамматика и сетки"
        elif raw[:4] == b"RIFF" and raw[8:12] == b"WAVE":
            if len(raw) < 12 or struct.unpack_from("<I", raw, 4)[0] + 8 != len(raw):
                raise ValueError("Некорректный диапазон RIFF")
            kind, evidence = "riff-wave", "RIFF/WAVE и длина; чанки/семантика остаются SND-01"
        elif raw[:2] == b"BM":
            decode_bmp(raw)
            kind, evidence = "bmp", "GFX-01: DIB40, диапазоны, пиксели"
        elif entry.name.upper().endswith(".SPR") or entry.index in sprites:
            decode_spr(raw)
            kind, evidence = "spr18", "GFX-01: SPR18 и точный диапазон width×height"
        elif entry.name.upper().endswith(".FL") or entry.index in animations:
            decode_fl(raw)
            kind, evidence = "fl12-spr18", "GFX-02: таблица FL и все вложенные SPR"
        elif entry.name in ("GAMEP1", "ЗАСТАВКА_ПАЛ"):
            decode_palette(raw)
            kind, evidence = "palette-rgb6", "GFX-01: 256 RGB6"
        elif entry.name in ("FONT8", "FONT16"):
            decode_font(raw)
            kind, evidence = "font-bitmap", "GFX-03: 004264C0, 00426592; 256 глифов"
        elif entry.name == "LIGHT1":
            decode_light(raw)
            kind, evidence = "light-tables", "GFX-03: 00410EA8–00410F03"
        elif entry.name == "PANEL_DAT":
            if raw != b"28 255 86\r\n":
                raise ValueError("Неизвестный PANEL_DAT")
            kind, evidence = "hud-parameters", "GFX-03: 00408162–004081F9; три числа, не изображение"
        else:
            kind, evidence = "unknown", "Проверенные декодеры не определили формат"
        resolved = resolver.resolve(entry.name).index
        rows.append({"source": provenance(archive, entry), "kind": kind, "evidence": evidence,
                     "resolved_index": resolved,
                     "usage": "shadowed-by-first-match" if resolved != entry.index else "consumer-context-required"})
    return rows


def decode_auxgraphics(source: Path, destination: Path) -> dict:
    archive = parse(no_links(source).read_bytes())
    profile_raw = (REPO / "profiles/smuta/graphics.json").read_bytes()
    profile = json.loads(profile_raw)
    rules_raw = (REPO / "profiles/smuta/auxgraphics.json").read_bytes()
    rules = json.loads(rules_raw)
    if digest(archive.data) != profile["container_sha256"] or rules["container_sha256"] != profile["container_sha256"]:
        raise ValueError("Профиль GFX-03 относится к другому DAT")
    destination = output_path(destination)
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError("Выходной каталог должен быть новым или пустым")
    catalog = classify(archive, profile)
    resolver = NameResolver(archive)
    files, resources = {}, []
    def read(name):
        entry = resolver.resolve(name)
        return entry, archive.data[entry.offset:entry.offset + entry.length]
    for name in ("FONT8", "FONT16"):
        entry, raw = read(name)
        base = output_name(entry)
        glyphs = decode_font(raw)
        height = glyphs[0].height
        atlas = bytearray(128 * height * 16)
        for c, glyph in enumerate(glyphs):
            for y in range(height):
                start = ((c // 16) * height + y) * 128 + (c % 16) * 8
                atlas[start:start + 8] = glyph.pixels[y * 8:y * 8 + 8]
        files[base + ".source"] = raw
        files[base + ".indices"] = b"".join(g.pixels for g in glyphs)
        files[base + ".png"] = indexed_png(IndexedImage(128, height * 16, bytes(atlas), {}),
                                           b"\xff\xff\xff\0\0\0")
        resources.append({"source": provenance(archive, entry), "format": "bitmap-font-msb-left",
                          "width": 8, "height": height, "glyph_count": 256,
                          "raw_path": base + ".source", "pixels_path": base + ".indices",
                          "atlas_path": base + ".png", "transformations": ["unpack-msb-first-top-down"]})
    entry, raw = read("LIGHT1")
    light = decode_light(raw)
    palette_entry, palette = read("GAMEP1")
    if light.rgb555 != direct_colors(palette):
        raise ValueError("RGB555 LIGHT1 не совпадает с формулой EXE и GAMEP1")
    base = output_name(entry)
    for suffix, data in (("source", raw), ("indexed", light.indexed),
                         ("rgb555", raw[8192:24576]), ("blend", light.blend),
                         ("rgb565", struct.pack("<8192H", *direct_colors(palette, rgb565=True)))):
        files[f"{base}.{suffix}"] = data
    resources.append({"source": provenance(archive, entry), "format": "light-tables",
                      "palette_source": provenance(archive, palette_entry),
                      "raw_path": base + ".source", "sections": [
                          {"offset": 0, "length": 8192, "format": "32x256-index8"},
                          {"offset": 8192, "length": 16384, "format": "32x256-rgb555-le"},
                          {"offset": 24576, "length": 65536, "format": "source256-destination256-index8"}],
                      "rgb555_formula_verified": True,
                      "rgb565_transformation": "regenerate-from-GAMEP1-per-00410B64"})
    files["characters.json"] = (json.dumps(character_map(), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    files["catalog.json"] = (json.dumps(catalog, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    manifest = {"format": "smuta-auxgraphics-research", "version": 1,
                "container_sha256": digest(archive.data), "profile_sha256": digest(profile_raw),
                "rules_sha256": digest(rules_raw), "rules": rules,
                "resources": resources, "counts": dict(sorted(Counter(r["kind"] for r in catalog).items())),
                "release_ready": False,
                "blockers": ["Интеграция освещения и интерфейса в UZDoom и сравнение CHECK-02 не выполнены"],
                "files": {name: {"length": len(data), "sha256": digest(data)} for name, data in sorted(files.items())}}
    destination.mkdir(parents=True, exist_ok=True)
    for name, data in sorted(files.items()):
        (destination / name).write_bytes(data)
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"resources": len(resources), "catalog_entries": len(catalog), "counts": manifest["counts"], "release_ready": False}
