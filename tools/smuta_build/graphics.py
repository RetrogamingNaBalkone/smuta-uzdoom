"""GFX-01: структурные декодеры; исследовательский экспорт, без игровой семантики."""

from dataclasses import dataclass
import json
from pathlib import Path
import re
import struct
import zlib

from .dat import parse
from .extract import digest
from .ir import provenance
from .names import NameResolver, output_name
from .paths import REPO, no_links, output_path

DECODER = "smuta-graphics-structural-v2"


@dataclass(frozen=True)
class IndexedImage:
    width: int
    height: int
    pixels: bytes
    metadata: dict
    palette: bytes | None = None


def decode_palette(data: bytes) -> bytes:
    """RGB6 → RGB8 как в EXE; освещение и экранная коррекция сюда не входят."""
    if len(data) != 768 or max(data) > 63:
        raise ValueError("Ожидается палитра из 256 троек компонент 0..63")
    return bytes(value << 2 for value in data)


def decode_spr(data: bytes) -> IndexedImage:
    if len(data) < 18:
        raise ValueError("Усечённый заголовок SPR")
    width, height = struct.unpack_from("<HH", data)
    if not width or not height or len(data) != 18 + width * height:
        raise ValueError("Диапазон пикселей SPR не соответствует размерам")
    return IndexedImage(width, height, data[18:], {
        "format": "smuta-spr18", "header_hex": data[:18].hex(),
        "uninterpreted_header_hex": data[4:18].hex(),
        "pixel_offset": 18, "pixel_length": width * height,
        "storage": "row-major", "orientation": "top-down",
        "transparent_index": None, "anchor": None,
        "validation": "structure-and-exe-storage", "transformations": []})


def decode_bmp(data: bytes, *, allow_smuta_bfsize: bool = False) -> IndexedImage:
    """Только BITMAPINFOHEADER / BI_RGB / 8bpp; остальные форматы — ошибка."""
    if len(data) < 54 or data[:2] != b"BM":
        raise ValueError("Нет полного заголовка BMP")
    size, reserved1, reserved2, offset = struct.unpack_from("<IHHI", data, 2)
    dib, width, signed_height, planes, bits, compression, image_size = struct.unpack_from(
        "<IiiHHII", data, 14)
    used, = struct.unpack_from("<I", data, 46)
    if dib != 40 or width <= 0 or signed_height == 0 or planes != 1 or bits != 8 or compression != 0:
        raise ValueError("Неподдержанный формат BMP: нужен DIB40, BI_RGB, 8bpp")
    colors = used or 256
    if not 1 <= colors <= 256 or offset < 54 + colors * 4:
        raise ValueError("Недопустимый диапазон палитры BMP")
    height = abs(signed_height)
    stride = (width + 3) & ~3
    end = offset + stride * height
    if end > len(data) or image_size not in (0, stride * height):
        raise ValueError("Усечённый или противоречивый диапазон пикселей BMP")
    anomaly = (allow_smuta_bfsize and size == 32539 and len(data) == 65078
               and (width, signed_height, offset, colors, image_size) == (320, 200, 1078, 256, 64000)
               and reserved1 == reserved2 == 0 and end == len(data))
    if size != len(data) and not anomaly:
        raise ValueError("Неизвестное несоответствие bfSize BMP")
    rows = [data[offset + y * stride:offset + y * stride + width] for y in range(height)]
    if signed_height > 0:
        rows.reverse()
    pixels = b"".join(rows)
    if max(pixels) >= colors:
        raise ValueError("Индекс пикселя за пределами палитры BMP")
    palette = b"".join(bytes((data[p + 2], data[p + 1], data[p]))
                       for p in range(54, 54 + colors * 4, 4))
    return IndexedImage(width, height, pixels, {
        "format": "bmp-dib40-indexed8", "header_hex": data[:offset].hex(),
        "bfSize": size, "actual_size": len(data), "bfSize_anomaly": bool(anomaly),
        "pixel_offset": offset, "pixel_length": stride * height, "stride": stride,
        "signed_height": signed_height, "orientation": "top-down",
        "transparent_index": None, "anchor": None, "validation": "format-and-ranges",
        "transformations": (["bmp-bottom-up-to-top-down"] if signed_height > 0 else [])
        + ["bmp-bgrx-to-rgb"] + (["allow-known-smuta-bfSize-32539"] if anomaly else [])}, palette)


def indexed_png(image: IndexedImage, palette: bytes) -> bytes:
    """PNG сохраняет индексы; отсутствие tRNS намеренно, альфа не угадывается."""
    if (not palette or len(palette) % 3 or len(palette) > 768
            or max(image.pixels) >= len(palette) // 3
            or len(image.pixels) != image.width * image.height):
        raise ValueError("Палитра или пиксели не соответствуют изображению")
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b"".join(b"\0" + image.pixels[y * image.width:(y + 1) * image.width]
                    for y in range(image.height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", image.width, image.height, 8, 3, 0, 0, 0))
            + chunk(b"PLTE", palette) + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


def decode_graphics(source: Path, destination: Path) -> dict:
    archive = parse(no_links(source).read_bytes())
    profile_data = (REPO / "profiles/smuta/graphics.json").read_bytes()
    profile = json.loads(profile_data)
    if digest(archive.data) != profile["container_sha256"]:
        raise ValueError("Профиль графики относится к другому DAT")
    external = [path for path in sorted(no_links(source.parent).iterdir())
                if re.fullmatch(r"Z(?:[0-9]|10)\.SPR", path.name, re.IGNORECASE)]
    if len(external) != 11 or {int(path.stem[1:]) for path in external} != set(range(11)):
        raise ValueError("Рядом с DAT нужны все 11 внешних BMP Z0.SPR…Z10.SPR без дубликатов")
    resolver = NameResolver(archive)
    destination = output_path(destination)
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError("Выходной каталог должен быть новым или пустым")
    resources, files = [], {}
    for name in ("GAMEP1", "ЗАСТАВКА_ПАЛ"):
        entry = resolver.resolve(name)
        raw = archive.data[entry.offset:entry.offset + entry.length]
        palette = decode_palette(raw)
        base = output_name(entry)
        files[f"{base}.rgb6"] = raw
        files[f"{base}.rgb8"] = palette
        resources.append({"source": provenance(archive, entry), "decoder": "palette-rgb6-preview-v1",
                          "raw_path": f"{base}.rgb6", "path": f"{base}.rgb8",
                          "transformations": ["rgb-order", "component-left-shift-2"],
                          "validation": "range-and-exe-conversion; final-display-comparison-deferred"})
    palette_entry = resolver.resolve("GAMEP1")
    preview_palette = decode_palette(archive.data[palette_entry.offset:palette_entry.offset + palette_entry.length])
    for entry in archive.entries:
        raw = archive.data[entry.offset:entry.offset + entry.length]
        if raw.startswith(b"BM"):
            image = decode_bmp(raw)
        elif entry.name.upper().endswith(".SPR") or entry.index in profile["spr18_without_extension"]:
            image = decode_spr(raw)
        else:
            continue
        base = output_name(entry)
        files[f"{base}.source"] = raw
        files[f"{base}.indices"] = image.pixels
        files[f"{base}.png"] = indexed_png(image, image.palette or preview_palette)
        resources.append({"source": provenance(archive, entry), "decoder": DECODER,
                          "width": image.width, "height": image.height, **image.metadata,
                          "raw_path": f"{base}.source",
                          "indices_path": f"{base}.indices", "preview_path": f"{base}.png",
                          "preview_palette": "embedded-bmp" if image.palette else "GAMEP1-candidate",
                          "preview_palette_source": None if image.palette else provenance(archive, palette_entry),
                          "preview_transformations": [] if image.palette else
                              ["row-major-top-down", "rgb6-left-shift-2", "opaque-preview"],
                          "preview_only": True})
    # Внешние имена выбираются строго, но никогда не используются как выходной путь.
    for path in external:
        raw = no_links(path).read_bytes()
        image = decode_bmp(raw, allow_smuta_bfsize=True)
        base = f"smext{int(path.stem[1:]):02d}"
        files[f"{base}.source"] = raw
        files[f"{base}.indices"] = image.pixels
        files[f"{base}.png"] = indexed_png(image, image.palette)
        resources.append({"source": {"game": "smuta", "relative_path": path.name,
                                    "length": len(raw), "sha256": digest(raw)},
                          "decoder": DECODER, "width": image.width, "height": image.height,
                          **image.metadata, "indices_path": f"{base}.indices",
                          "raw_path": f"{base}.source",
                          "preview_path": f"{base}.png", "preview_only": True})
    manifest = {"format": "smuta-graphics-research", "version": 2,
                "container_sha256": digest(archive.data), "profile_sha256": digest(profile_data),
                "rendering_rules": profile["rendering_rules"],
                "release_ready": False, "blockers": ["Rendering rules require UZDoom caller integration",
                    "Final in-game comparison deferred to playable fragment"],
                "resources": resources,
                "files": {name: {"length": len(data), "sha256": digest(data)} for name, data in sorted(files.items())}}
    destination.mkdir(parents=True, exist_ok=True)
    for name, data in sorted(files.items()):
        (destination / name).write_bytes(data)
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"resources": len(resources), "files": len(files), "release_ready": False}
