"""Приёмка UZD-01 на локальном UZDoom; не входит в автономные тесты."""

import json
from pathlib import Path
import subprocess
import sys
import time
import uuid
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from smuta_build.paths import REPO, no_links
from smuta_build.extract import digest
from smuta_build.standalone import build_standalone


def main():
    root = REPO / "build" / ("uzd01 verification " + uuid.uuid4().hex)
    root.mkdir()
    engine = REPO / "build/run/uzdoom/uzdoom.exe"
    inputs = [REPO / "uzdoom" / name for name in ("uzdoom.exe", "uzdoom.pk3", "game_support.pk3")]
    inputs += [REPO / "originals/smuta_modern_20261013/smuta_modern" / name
               for name in ("SMUTA.EXE", "SMUTA.DAT")]
    before = {str(p.relative_to(REPO)): digest(no_links(p).read_bytes()) for p in inputs}
    for p in inputs[:3]:
        assert digest((engine.parent / p.name).read_bytes()) == before[str(p.relative_to(REPO))]
    report = {"sources_before": before, "passed": False}
    try:
        first = root / "smuta-uzd01.ipk3"
        report["package"] = build_standalone(first)
        second = root / "repeat.ipk3"
        build_standalone(second)
        assert first.read_bytes() == second.read_bytes()
        report["reproducible"] = True
        # Наблюдатель только для приёмки; игровой пакет не меняется.
        probe = root / "observer.pk3"
        with zipfile.ZipFile(probe, "w") as z:
            z.writestr("MAPINFO", 'GameInfo { AddEventHandlers = "SMVerifyObserver" }')
            z.writestr("ZSCRIPT", '''version "4.14"
class SMVerifyObserver : EventHandler {
 ui bool rendered;
 override void WorldTick() {
  if (level.time == 35) Console.Printf("UZD01_MAP_READY %s player=%s", level.MapName, players[0].mo.GetClassName());
  if (level.time == 70) Console.Printf("UZD01_CHECK_COMPLETE");
 }
 override void RenderOverlay(RenderEvent e) {
  if (!rendered && level.time >= 35) {
   rendered = true;
   Console.Printf("UZD01_FRAME_READY");
  }
 }
}''')
        args = [str(engine), "-iwad", str(first), "-file", str(probe),
                "-config", str(root / "uzdoom.ini"), "-savedir", str(root), "-shotdir", str(root),
                "-noautoload", "-noautoexec", "-nolights", "-nobrightmaps", "-nowidescreen",
                "-window", "+vid_activeinbackground", "true", "+i_pauseinbackground", "false",
                "+map", "SMTEST"]
        report["arguments"] = args
        report["cwd"] = str(engine.parent)
        (root / "command.txt").write_text(subprocess.list2cmdline(args), encoding="utf-8")
        stdout_path = root / "engine.log"
        with stdout_path.open("w") as stdout, (root / "stderr.log").open("w") as stderr:
            process = subprocess.Popen(args, cwd=engine.parent, stdout=stdout, stderr=stderr,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                deadline = time.monotonic() + 25
                while process.poll() is None and time.monotonic() < deadline:
                    log = stdout_path.read_text(errors="replace")
                    if "UZD01_CHECK_COMPLETE" in log and "UZD01_FRAME_READY" in log:
                        break
                    time.sleep(0.2)
            finally:
                # Остановка только созданного процесса после наблюдения, не проверка выхода.
                report["controlled_stop"] = process.poll() is None
                if process.poll() is None:
                    process.kill()
                report["exit_code"] = process.wait()
        log = stdout_path.read_text(errors="replace")
        for marker in ("UZDoom version 5.0.3", "UZD01_MAP_READY SMTEST player=SMProbePlayer",
                       "UZD01_CHECK_COMPLETE", "UZD01_FRAME_READY"):
            assert marker in log, marker
        for error in ("Script error", "Unknown texture", "Missing menu texture", "VM abort", "Fatal error"):
            assert error not in log, error
        loaded = [line for line in log.splitlines() if line.startswith("adding ")]
        report["loaded_archives"] = loaded
        assert len(loaded) == 4, loaded  # Только ядро, game_support, IPK3 и наблюдатель.
        report["passed"] = True
    finally:
        after = {str(p.relative_to(REPO)): digest(p.read_bytes()) for p in inputs}
        report["sources_after"] = after
        report["sources_unchanged"] = before == after
        if before != after:
            report["passed"] = False
        (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(root)
    assert report["passed"] and report["sources_unchanged"]


if __name__ == "__main__":
    main()
