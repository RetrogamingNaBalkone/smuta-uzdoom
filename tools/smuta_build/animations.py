"""GFX-02: собственный FL Смуты; исходные единицы, без генерации Doom."""

from dataclasses import dataclass
import json
from pathlib import Path
import struct

from .dat import parse
from .extract import digest
from .graphics import IndexedImage, decode_palette, decode_spr, indexed_png
from .ir import provenance
from .names import NameResolver, output_name
from .paths import REPO, no_links, output_path

DECODER = "smuta-fl-research-v2"


@dataclass(frozen=True)
class Animation:
    angle_step: int
    frames_per_direction: int
    directions: int
    frames: tuple[IndexedImage, ...]
    metadata: dict


def delay_counter(delay: int) -> int:
    """00427D84: параметр FL int16 >= 0 → счётчик, без перевода в тики Doom."""
    if not 0 <= delay <= 32767:
        raise ValueError("Неподдержанный параметр задержки FL")
    return delay * 24 // 1000


def callback_updates(counter: int) -> int:
    """00427E08–00427E2A проверяет старое значение: ноль ждёт один обход."""
    if not 0 <= counter <= 0x7fffffff:
        raise ValueError("Нужен неотрицательный счётчик планировщика")
    return counter + 1


def frame_index(animation: Animation, frame: int, angle: int) -> int:
    """Выбор 004269F7; только подтверждённая область угла 0…359."""
    if not 0 <= angle < 360:
        raise ValueError("Угол FL должен быть в диапазоне 0…359")
    frame = max(0, min(frame, animation.frames_per_direction - 1))
    direction = ((angle + animation.angle_step // 2) % 360
                 // animation.angle_step) if animation.angle_step else 0
    return direction * animation.frames_per_direction + frame


def decode_fl(data: bytes) -> Animation:
    if len(data) < 12:
        raise ValueError("Усечённый заголовок FL")
    step, count, total, unknown, pointer = struct.unpack_from("<hhhhI", data)
    if step < 0 or (step and (step > 360 or 360 % step)) or count <= 0 or total <= 0:
        raise ValueError("Недопустимые размеры/угловой шаг FL")
    directions = 360 // step if step else 1
    if total != count * directions:
        raise ValueError("Число кадров FL не соответствует направлениям")
    table_end = 12 + total * 10
    if table_end > len(data):
        raise ValueError("Усечённая таблица FL")
    frames, records, pos = [], [], table_end
    for index in range(total):
        record_offset = 12 + index * 10
        raw_record = data[record_offset:record_offset + 10]
        values = struct.unpack("<hhhI", raw_record)
        if pos + 18 > len(data):
            raise ValueError(f"Усечённый SPR кадра FL {index}")
        size, = struct.unpack_from("<I", data, pos + 10)
        end = pos + 18 + size
        if end > len(data):
            raise ValueError(f"Пиксели кадра FL {index} выходят за ресурс")
        image = decode_spr(data[pos:end])
        # В отличие от отдельных SPR, загрузчик FL использует именно +10.
        if size != image.width * image.height:
            raise ValueError(f"Длина кадра FL {index} не соответствует размерам")
        frames.append(image)
        records.append({"index": index, "direction": index // count,
                        "frame": index % count,
                        "table_offset": record_offset, "table_hex": raw_record.hex(),
                        "word0_raw": values[0], "word2_raw": values[1],
                        "word4_raw": values[2], "runtime_pointer_raw": values[3],
                        "delay_counter_if_scheduled": delay_counter(values[0]) if values[0] >= 0 else None,
                        "weapon_screen_offset": {"x": values[1], "y": values[2],
                                                 "unit": "source-screen-pixels"},
                        "spr_offset": pos, "spr_length": end - pos,
                        "spr_sha256": digest(data[pos:end]),
                        "pixel_offset": pos + 18, "pixel_length": size})
        pos = end
    if pos != len(data):
        raise ValueError("Неизвестный хвост после кадров FL")
    return Animation(step, count, directions, tuple(frames), {
        "format": "smuta-fl12-spr18", "header_hex": data[:12].hex(),
        "unknown_word6": unknown, "runtime_table_pointer_raw": pointer,
        "angle_step": step, "frames_per_direction": count,
        "directions": directions, "total_frames": total,
        "table_offset": 12, "table_record_size": 10,
        "storage_order": "direction-major-then-frame",
        "direction_world_mapping": None, "duration_seconds": None,
        "validation": "structure-and-exe-indexing-timing; final-playback-comparison-deferred",
        "frames": records})


def decode_animations(source: Path, destination: Path) -> dict:
    archive = parse(no_links(source).read_bytes())
    profile_raw = (REPO / "profiles/smuta/graphics.json").read_bytes()
    profile = json.loads(profile_raw)
    if digest(archive.data) != profile["container_sha256"]:
        raise ValueError("Профиль анимаций относится к другому DAT")
    selection = profile["fl_without_extension"]
    if len(set(selection)) != len(selection) or any(
            not isinstance(i, int) or not 0 <= i < len(archive.entries) for i in selection):
        raise ValueError("Недопустимый список анимаций профиля")
    destination = output_path(destination)
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError("Выходной каталог должен быть новым или пустым")
    palette_entry = NameResolver(archive).resolve("GAMEP1")
    palette = decode_palette(archive.data[palette_entry.offset:palette_entry.offset + palette_entry.length])
    resources, files = [], {}
    # Все входы разбираются до первой записи на диск.
    for entry in archive.entries:
        if not entry.name.upper().endswith(".FL") and entry.index not in selection:
            continue
        raw = archive.data[entry.offset:entry.offset + entry.length]
        animation = decode_fl(raw)
        base = output_name(entry)
        files[base + ".source"] = raw
        records = []
        for image, record in zip(animation.frames, animation.metadata["frames"], strict=True):
            prefix = f"{base}-d{record['direction']:02d}-f{record['frame']:03d}"
            indices, preview = prefix + ".indices", prefix + ".png"
            files[indices], files[preview] = image.pixels, indexed_png(image, palette)
            records.append({**record, "width": image.width, "height": image.height,
                            "spr_header_hex": image.metadata["header_hex"],
                            "indices_path": indices, "preview_path": preview,
                            "transparent_index": None, "anchor": None})
        resources.append({**animation.metadata, "frames": records,
                          "source": provenance(archive, entry), "decoder": DECODER,
                          "raw_path": base + ".source", "preview_only": True,
                          "preview_palette_source": provenance(archive, palette_entry),
                          "preview_transformations": ["row-major-top-down",
                              "GAMEP1-rgb6-left-shift-2", "opaque-preview"]})
    manifest = {"format": "smuta-animations-research", "version": 2,
                "decoder": DECODER, "container_sha256": digest(archive.data),
                "profile_sha256": digest(profile_raw), "release_ready": False,
                "rendering_rules": profile["rendering_rules"], "timing_rules": profile["timing_rules"],
                "blockers": ["Rendering/timing rules require UZDoom caller integration",
                             "Weapon fire events verified with PLAY-02; final comparison deferred",
                             "UZDoom direction representation not tested"],
                "resources": resources,
                "files": {name: {"length": len(data), "sha256": digest(data)}
                          for name, data in sorted(files.items())}}
    destination.mkdir(parents=True, exist_ok=True)
    for name, data in sorted(files.items()):
        (destination / name).write_bytes(data)
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"animations": len(resources), "frames": sum(len(a["frames"]) for a in resources),
            "files": len(files), "release_ready": False}
