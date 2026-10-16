"""Отдельная интеграционная приёмка ENV-02/DAT-01/DAT-02 на локальном эталоне."""

import csv
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from smuta_build.extract import check, digest, extract
from smuta_build.dat import parse
from smuta_build.names import EXE_SHA256, NameResolver
from smuta_build.pack import pack, safe_name
from smuta_build.paths import REPO


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def powershell(arguments: list[str], cwd: Path = REPO, expect_failure: bool = False) -> str:
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", *arguments],
                            cwd=cwd, capture_output=True)
    if expect_failure:
        require(result.returncode != 0, "Ожидался ненулевой код PowerShell")
        return result.stdout.decode("utf-8-sig")
    if result.returncode:
        raise RuntimeError(result.stdout.decode("utf-8", errors="replace") +
                           result.stderr.decode("utf-8", errors="replace"))
    return result.stdout.decode("utf-8-sig")


def main() -> None:
    source_root = REPO / "originals/smuta_modern_20261013/smuta_modern"
    source = source_root / "SMUTA.DAT"
    paths = [source, source_root / "SMUTA.EXE", REPO / "uzdoom/uzdoom.exe"]
    before = {path.name: digest(path.read_bytes()) for path in paths}
    require(before["SMUTA.DAT"] == "6e13c4b65e4530291b11e3d4cd6280d874c489837e7e74c5f53b686ca69a74e8", "Другой DAT эталона")
    require(before["SMUTA.EXE"] == EXE_SHA256, "Другой EXE эталона")
    root = REPO / "build" / ("env-02 verification " + uuid.uuid4().hex)
    root.mkdir(parents=True)
    report = {"python": sys.version, "sources_before": before, "success": False}
    try:
        report["uzdoom_version"] = powershell(["-Command",
            f"(Get-Item -LiteralPath '{paths[2]}').VersionInfo.FileVersion"]).strip()
        first, second = root / "first", root / "second"
        manifest = extract(source, first)
        extract(source, second)
        require(len(manifest["entries"]) == 460, "Не 460 записей")
        report["roundtrip"] = check(source, first)
        require(manifest["entries"][182]["name"] == manifest["entries"][183]["name"] == "ЗАСТАВКА_СПР", "Потерян дубликат")
        require(manifest["entries"][199]["name"] == "ТРУБА/2", "Потеряно исходное имя")
        archive = parse(source.read_bytes())
        resolver = NameResolver(archive)
        require(resolver.resolve("ЗАСТАВКА_СПР").index == 182, "Неверный выбор дубликата")
        require(resolver.resolve("ТРУБА/2").index == 199, "Имя стало путём")
        outputs = [entry["output_name"] for entry in manifest["entries"]]
        require(len(set(name.casefold() for name in outputs)) == 460, "Коллизия ASCII-имён")
        for entry in manifest["entries"]:
            key = bytes.fromhex(entry["name_bytes_hex"])
            first_index = next(item.index for item in archive.entries
                               if item.raw[:16].split(b"\0", 1)[0] == key)
            require(resolver.resolve(key).index == entry["resolved_index"] == first_index,
                    "Неверное разрешение имени")
            safe_name(entry["output_name"] + ".bin")
        report["dat_02"] = {"all_entries_resolved": 460, "duplicate_selected": 182,
                            "duplicate_preserved": 183, "slash_name_index": 199,
                            "ascii_names_unique_ignoring_case": True,
                            "policy": manifest["name_resolution"]}
        require((first / "unreferenced/000000.bin").read_bytes() == bytes.fromhex("5a82e642a6018500"), "Не совпал хвост")
        with (REPO / "docs/research/smuta-dat-catalog.csv").open(encoding="utf-8-sig", newline="") as stream:
            catalog = list(csv.DictReader(stream))
        inspector = str(REPO / "tools/Inspect-Smuta.ps1").replace("'", "''")
        command = ("[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false); "
                   f"& '{inspector}' -Catalog | ConvertTo-Json -Depth 5")
        inspected = json.loads(powershell(["-Command", command]))
        require(len(catalog) == len(inspected) == 460, "Размеры каталогов не совпали")
        for entry, csv_row, ps_row in zip(manifest["entries"], catalog, inspected, strict=True):
            for field, original in (("index", "Index"), ("name", "Name"),
                                    ("directory_offset", "DirectoryOffset"), ("offset", "Offset"),
                                    ("length", "Length"), ("sha256", "SHA256")):
                require(str(entry[field]) == str(csv_row[original]) == str(ps_row[original]),
                        f"Расхождение {entry['index']}: {field}")
            require(entry["directory_record_hex"] == csv_row["RawDirectoryHex"].replace("-", "").lower()
                    == ps_row["RawDirectoryHex"].replace("-", "").lower(), "Не совпали исходные записи")
        report["catalog_entries_matched_csv_and_powershell"] = 460
        for path in first.rglob("*"):
            if path.is_file():
                require(path.read_bytes() == (second / path.relative_to(first)).read_bytes(), "Извлечение не воспроизводится")
        report["extractions_identical"] = True
        report["pack"] = pack(first, root / "first.zip")
        pack(second, root / "second.zip")
        require((root / "first.zip").read_bytes() == (root / "second.zip").read_bytes(), "Упаковка не воспроизводится")
        with zipfile.ZipFile(root / "first.zip") as archive:
            for path in first.rglob("*"):
                if path.is_file():
                    require(archive.read(path.relative_to(first).as_posix()) == path.read_bytes(), "ZIP меняет данные")
        report["archives_identical"] = True
        # Отдельная чистая установка, включая запуск вне корня, из пути с пробелами.
        workspace = root / "workspace with spaces"
        shutil.copytree(REPO / "tools", workspace / "tools")
        shutil.copytree(REPO / "tests", workspace / "tests")
        script = str(workspace / "tools/Build.ps1")
        powershell(["-File", script, "-Task", "Setup"], cwd=root)
        stage = workspace / "build/stage"
        stage.mkdir()
        (stage / "fixture.txt").write_bytes(b"synthetic package, not a game\n")
        powershell(["-File", script, "-Task", "Build", "-Source", str(source),
                    "-Output", str(workspace / "build/extracted"), "-Stage", str(stage),
                    "-Archive", str(workspace / "dist/synthetic.zip")], cwd=root)
        powershell(["-File", script, "-Task", "Check", "-Source", str(source),
                    "-Output", str(workspace / "build/extracted")], cwd=root)
        report["clean_setup_and_build_from_path_with_spaces"] = True
        # Ошибка извлечения должна остановить Build до упаковки.
        rejected_archive = workspace / "dist/must-not-exist.zip"
        powershell(["-File", script, "-Task", "Build", "-Source", str(source),
                    "-Output", str(workspace / "build/extracted"), "-Stage", str(stage),
                    "-Archive", str(rejected_archive)], cwd=root, expect_failure=True)
        require(not rejected_archive.exists(), "Build продолжился после ошибки")
        powershell(["-File", script, "-Task", "Setup"], cwd=root, expect_failure=True)
        report["failed_build_stops_before_pack_and_repeated_setup_rejected"] = True
        report["success"] = True
    finally:
        report["sources_after"] = {path.name: digest(path.read_bytes()) for path in paths}
        report["sources_unchanged"] = before == report["sources_after"]
        report["success"] = report["success"] and report["sources_unchanged"]
        (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(root / "report.json")
    require(report["sources_unchanged"], "Изменились исходники")


if __name__ == "__main__":
    main()
