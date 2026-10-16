"""Детерминированный ZIP подготовленного каталога, без проверки игровых механик."""

import os
import json
from pathlib import Path
import re
import tempfile
import zipfile

from .extract import digest
from .paths import files_under, no_links, output_path


def safe_name(name: str) -> None:
    for part in name.split("/"):
        if (not part or part in (".", "..") or part.endswith((".", " "))
                or re.search(r'[\\:\x00-\x1f<>"|?*]', part)
                or re.fullmatch(r"(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)):
            raise ValueError(f"Небезопасное имя в пакете: {name}")


def pack(source: Path, destination: Path, *, mode="raw", ir_path=None, dat_source=None) -> dict:
    source = no_links(source).resolve()
    destination = output_path(destination)
    if destination.suffix.lower() not in (".zip", ".ipk3", ".pk3"):
        raise ValueError("Ожидается расширение ZIP, IPK3 или PK3")
    if destination.is_relative_to(source):
        raise ValueError("Архив не может находиться внутри упаковываемого каталога")
    if destination.exists():
        raise ValueError(f"Архив уже существует: {destination}")
    if mode not in ("raw", "research", "release"):
        raise ValueError("Неизвестный режим упаковки")
    if mode == "raw" and destination.suffix.lower() != ".zip":
        raise ValueError("Сырая упаковка разрешена только в ZIP; для PK3/IPK3 выберите research или release")
    marker = None
    if mode != "raw":
        if ir_path is None or dat_source is None:
            raise ValueError("Для research/release обязательны IR и исходный DAT")
        from .validation import check_model
        report = check_model(dat_source, ir_path)
        report["mode"] = mode
        if not report["integrity_ok"]:
            raise ValueError("IR не совпадает с исходным DAT")
        if mode == "release" and not report["release_ready"]:
            raise ValueError("Релиз заблокирован: " + json.dumps(report["issue_counts"], ensure_ascii=True))
        marker = json.dumps({"mode": mode, "playable_game_verified": False,
                             "validation": report}, ensure_ascii=True, sort_keys=True,
                            separators=(",", ":")).encode("ascii") + b"\n"
    files = sorted(files_under(source), key=lambda path: path.relative_to(source).as_posix())
    seen = set()
    for path in files:
        name = path.relative_to(source).as_posix()
        safe_name(name)
        if name.casefold() in seen:
            raise ValueError(f"Коллизия регистра в пакете: {name}")
        seen.add(name.casefold())
    marker_name = "smuta-build-report.json"
    if marker is not None and marker_name in seen:
        raise ValueError(f"Зарезервированное имя отчёта: {marker_name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".pack-", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(fd, "w+b") as stream:
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
                entries = {p.relative_to(source).as_posix(): p for p in files}
                if marker is not None:
                    entries[marker_name] = None
                for name, path in sorted(entries.items()):
                    if path is not None:
                        no_links(path)
                    info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                    info.create_system = 3
                    info.external_attr = 0o100644 << 16
                    archive.writestr(info, path.read_bytes() if path is not None else marker)
        # Не перезаписываем готовый результат даже при конкурентном появлении файла.
        if os.name == "nt":
            os.rename(temporary, destination)
        else:
            os.link(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {"files": len(files) + int(marker is not None), "mode": mode,
            "sha256": digest(destination.read_bytes()),
            "game_readiness_checked": False}
