"""BUILD-01/QA-01: воспроизводимая сборка и проверка локального UZDoom."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid
import zipfile

from .extract import digest
from .paths import REPO, files_under, no_links, output_path
from .standalone import build_standalone


OBSERVER = '''version "4.14"
class SMVerifyObserver : EventHandler {
 ui bool rendered;
 override void WorldTick() {
  if (level.time == 35) Console.Printf("QA01_MAP_READY %s player=%s", level.MapName, players[0].mo.GetClassName());
  if (level.time == 70) Console.Printf("QA01_CHECK_COMPLETE");
 }
 override void RenderOverlay(RenderEvent e) {
  if (!rendered && level.time >= 35) {
   rendered = true;
   Console.Printf("QA01_FRAME_READY");
  }
 }
}'''


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                    encoding="utf-8")


def validate_log(log: str, expected_archives: list[Path]) -> dict:
    version = re.search(r"UZDoom version ([^\s]+)", log)
    if not version:
        raise ValueError("В журнале отсутствует версия UZDoom")
    for marker in ("QA01_MAP_READY SMTEST player=SMProbePlayer",
                   "QA01_CHECK_COMPLETE", "QA01_FRAME_READY"):
        if marker not in log:
            raise ValueError(f"Не подтверждён запуск: {marker}")
    for error in ("Script error", "Unknown texture", "Missing menu texture", "VM abort", "Fatal error"):
        if error.lower() in log.lower():
            raise ValueError(f"Ошибка UZDoom: {error}")
    loaded = [line[7:].rsplit(", ", 1)[0] for line in log.splitlines() if line.startswith("adding ")]
    if [os.path.normcase(str(Path(p))) for p in loaded] != [os.path.normcase(str(p)) for p in expected_archives]:
        raise ValueError(f"Неожиданный состав загруженных архивов: {loaded}")
    return {"version": version.group(1), "loaded_archives": loaded}


def run_pipeline(destination: Path | None = None, engine_source: Path | None = None,
                 timeout: float = 25) -> dict:
    if timeout <= 0 or timeout > 120:
        raise ValueError("Тайм-аут должен быть в пределах (0, 120] секунд")
    root = output_path(destination or REPO / "build" / ("qa01 " + uuid.uuid4().hex))
    if not root.is_relative_to(REPO / "build"):
        raise ValueError("Рабочая проверка разрешена только в build/")
    # Новый каталог для каждого запуска: никакой старый журнал не подтвердит проверку.
    root.mkdir(parents=True, exist_ok=False)
    report = {"schema": "smuta-qa01-v1", "passed": False, "release_ready": False,
              "mode": "synthetic-research", "root": str(root)}
    sources = {}
    try:
        first, second = root / "smuta.ipk3", root / "repeat.ipk3"
        report["package"] = build_standalone(first)
        report["repeat"] = build_standalone(second)
        if first.read_bytes() != second.read_bytes():
            raise ValueError("Две сборки одинаковых входов различаются")
        report["reproducible"] = True
        with zipfile.ZipFile(first) as archive:
            manifest = json.loads(archive.read("smuta-build-report.json"))
            if set(archive.namelist()) != set(manifest["files"]) | {"smuta-build-report.json"}:
                raise ValueError("Состав пакета отличается от манифеста")
            for name, sha256 in manifest["files"].items():
                if digest(archive.read(name)) != sha256:
                    raise ValueError(f"Хеш файла не совпал с манифестом: {name}")
        report["manifest_verified"] = True
        # Отдельный манифест инструментов: абсолютные пути запуска не попадают в IPK3.
        tracked_inputs = files_under(REPO / "tools/smuta_build") + files_under(REPO / "game/standalone")
        tracked_inputs += [REPO / "tools/Build.ps1"]
        report["build_inputs"] = {p.relative_to(REPO).as_posix(): digest(p.read_bytes())
                                  for p in tracked_inputs if "__pycache__" not in p.parts}
        source = no_links(engine_source or REPO / "uzdoom").resolve()
        runtime = root / "runtime"
        runtime.mkdir()
        names = ["uzdoom.exe", "uzdoom.pk3", "game_support.pk3"]
        names += [p.name for p in sorted(source.glob("*.dll"))]
        sources = {name: digest(no_links(source / name).read_bytes()) for name in names}
        report["engine_sources_before"] = sources
        for name in names:
            shutil.copyfile(source / name, runtime / name)
            if digest((runtime / name).read_bytes()) != sources[name]:
                raise ValueError(f"Рабочая копия отличается: {name}")
        probe = root / "observer.pk3"
        with zipfile.ZipFile(probe, "w") as archive:
            for name, data in (("MAPINFO", 'GameInfo { AddEventHandlers = "SMVerifyObserver" }'),
                               ("ZSCRIPT", OBSERVER)):
                archive.writestr(zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0)), data)
        report["observer_sha256"] = digest(probe.read_bytes())
        args = [str(runtime / "uzdoom.exe"), "-iwad", str(first), "-file", str(probe),
                "-config", str(root / "uzdoom.ini"), "-savedir", str(root), "-shotdir", str(root),
                "-noautoload", "-noautoexec", "-nolights", "-nobrightmaps", "-nowidescreen",
                "-window", "+vid_activeinbackground", "true", "+i_pauseinbackground", "false",
                "+map", "SMTEST"]
        report.update(arguments=args, cwd=str(runtime), timeout_seconds=timeout)
        (root / "command.txt").write_text(subprocess.list2cmdline(args), encoding="utf-8")
        stdout_path = root / "engine.log"
        with stdout_path.open("w") as stdout, (root / "stderr.log").open("w") as stderr:
            process = subprocess.Popen(args, cwd=runtime, stdout=stdout, stderr=stderr,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                deadline = time.monotonic() + timeout
                while process.poll() is None and time.monotonic() < deadline:
                    log = stdout_path.read_text(errors="replace")
                    if "QA01_CHECK_COMPLETE" in log and "QA01_FRAME_READY" in log:
                        break
                    time.sleep(0.2)
                report["timed_out"] = process.poll() is None and time.monotonic() >= deadline
            finally:
                report["controlled_stop"] = process.poll() is None
                if process.poll() is None:
                    process.kill()
                report["exit_code"] = process.wait()
        if report["timed_out"]:
            raise ValueError("Истёк тайм-аут проверки запуска UZDoom")
        if not report["controlled_stop"]:
            raise ValueError("UZDoom завершился до контролируемой остановки")
        report["engine"] = validate_log(stdout_path.read_text(errors="replace"),
            [runtime / "uzdoom.pk3", runtime / "game_support.pk3", first, probe])
        report["passed"] = True
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        if sources:
            try:
                after = {name: digest(no_links(source / name).read_bytes()) for name in sources}
                report["engine_sources_after"] = after
                report["sources_unchanged"] = sources == after
            except (OSError, ValueError) as error:
                report["sources_unchanged"] = False
                report["source_check_error"] = str(error)
            if not report["sources_unchanged"]:
                report["passed"] = False
                report["error"] = "Исходные файлы UZDoom изменились"
        write_json(root / "report.json", report)
    if not report["passed"]:
        raise ValueError(report["error"])
    return report
