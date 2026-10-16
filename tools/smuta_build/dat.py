"""Чтение каталога DAT без декодирования содержимого ресурсов."""

from dataclasses import dataclass
import struct


@dataclass(frozen=True)
class Entry:
    index: int
    name: str
    raw: bytes
    offset: int
    length: int


@dataclass(frozen=True)
class Archive:
    data: bytes
    directory_end: int
    entries: tuple[Entry, ...]
    gaps: tuple[tuple[int, int], ...]


def parse(data: bytes) -> Archive:
    if len(data) < 2:
        raise ValueError("Усечённый заголовок DAT")
    count, = struct.unpack_from("<H", data)
    directory_end = 2 + count * 30
    if directory_end > len(data):
        raise ValueError("Усечённый каталог DAT")
    entries = []
    for index in range(count):
        raw = data[2 + index * 30:2 + (index + 1) * 30]
        if b"\0" not in raw[:16]:
            raise ValueError(f"Нет NUL в имени записи {index}")
        name = raw[:16].split(b"\0", 1)[0].decode("cp1251")
        length, offset = struct.unpack_from("<II", raw, 16)
        if offset < directory_end or offset + length > len(data):
            raise ValueError(f"Недопустимый диапазон записи {index}")
        entries.append(Entry(index, name, raw, offset, length))
    # Дополнение объединения диапазонов сохраняет промежутки и хвост,
    # в том числе при переставленных или перекрывающихся записях.
    cursor = directory_end
    gaps = []
    for entry in sorted(entries, key=lambda item: item.offset):
        if cursor < entry.offset:
            gaps.append((cursor, entry.offset - cursor))
        cursor = max(cursor, entry.offset + entry.length)
    if cursor < len(data):
        gaps.append((cursor, len(data) - cursor))
    return Archive(data, directory_end, tuple(entries), tuple(gaps))
