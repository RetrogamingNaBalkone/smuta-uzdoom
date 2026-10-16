"""Приёмка IR-02 на локальном Smutabor Patch, без запуска игр."""

import json
from pathlib import Path
import subprocess
import sys
import uuid
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from smuta_build.extract import digest
from smuta_build.ir import write_model
from smuta_build.names import EXE_SHA256
from smuta_build.pack import pack
from smuta_build.paths import REPO
from smuta_build.validation import write_report


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    source = REPO / "originals/smuta_modern_20261013/smuta_modern/SMUTA.DAT"
    inputs = [source, source.with_suffix(".EXE"), REPO / "uzdoom/uzdoom.exe"]
    before = {p.name: digest(p.read_bytes()) for p in inputs}
    require(before["SMUTA.DAT"] == "6e13c4b65e4530291b11e3d4cd6280d874c489837e7e74c5f53b686ca69a74e8", "Другой DAT")
    require(before["SMUTA.EXE"] == EXE_SHA256, "Другой EXE")
    root = REPO / "build" / ("ir-02 verification " + uuid.uuid4().hex)
    root.mkdir(parents=True)
    result = {"success": False, "python": sys.version, "sources_before": before}
    try:
        version = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
            f"(Get-Item -LiteralPath '{inputs[2]}').VersionInfo.FileVersion"],
            capture_output=True, check=True)
        result["uzdoom_version"] = version.stdout.decode().strip()
        write_model(source, root / "model")
        ir = root / "model/ir.json"
        report = write_report(source, ir, root / "first.json")
        write_report(source, ir, root / "second.json")
        require((root / "first.json").read_bytes() == (root / "second.json").read_bytes(), "Разные отчёты")
        require(report["integrity_ok"] and not report["release_ready"], "Неверный допуск")
        coverage = report["coverage"]
        for key, expected in {"resources": 42, "catalog": 460, "definitions": 6127,
                              "instances": 172032, "grids": 84, "cells": 344064,
                              "resource_references:catalog-resolved": 18212}.items():
            require(coverage[key] == expected, f"Неверное покрытие {key}")
        stage = root / "stage"
        stage.mkdir()
        (stage / "research.txt").write_text("Исследовательская упаковка IR-02; игровой реализации нет.\n", encoding="utf-8")
        for name in ("first.pk3", "second.pk3"):
            pack(stage, root / name, mode="research", ir_path=ir, dat_source=source)
        require((root / "first.pk3").read_bytes() == (root / "second.pk3").read_bytes(), "Разные пакеты")
        with zipfile.ZipFile(root / "first.pk3") as archive:
            marker = json.loads(archive.read("smuta-build-report.json"))
            require(marker["mode"] == "research" and marker["validation"] == report, "Потеря маркировки")
        try:
            pack(stage, root / "release.ipk3", mode="release", ir_path=ir, dat_source=source)
        except ValueError:
            pass
        else:
            raise ValueError("Релиз пропущен")
        require(not (root / "release.ipk3").exists(), "Остался релизный архив")
        # Проверяем публичный PowerShell-вход и сохранение отчёта при ненулевом коде.
        cli = subprocess.run(["powershell.exe", "-NoProfile", "-File", str(REPO / "tools/Build.ps1"),
            "-Task", "Validate", "-Source", str(source), "-IR", str(ir),
            "-Output", str(root / "release.json"), "-Mode", "release"], capture_output=True)
        require(cli.returncode != 0 and (root / "release.json").exists(), "Нет отказа Validate")
        result.update(success=True, coverage=coverage, issue_counts=report["issue_counts"],
                      deterministic=True, release_refused=True,
                      validation_sha256=digest((root / "first.json").read_bytes()),
                      research_package_sha256=digest((root / "first.pk3").read_bytes()))
    finally:
        result["sources_after"] = {p.name: digest(p.read_bytes()) for p in inputs}
        require(before == result["sources_after"], "Изменились входы")
        (root / "report.json").write_text(json.dumps(result, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
        print(root / "report.json")


if __name__ == "__main__":
    main()
