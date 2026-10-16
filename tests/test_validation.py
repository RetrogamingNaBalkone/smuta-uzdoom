"""IR-02: отрицательные случаи допуска и маркировка исследовательских пакетов."""

from copy import deepcopy
import json
import shutil
import struct
import subprocess
import sys
import unittest
import uuid
import zipfile

from smuta_build.dat import parse
from smuta_build.ir import build_model
from smuta_build.pack import pack
from smuta_build.paths import REPO
from smuta_build.validation import validate_model, write_report
from test_text import fixture


def archive_fixture(objects=b"A 11 1 2 3 4 tail", rows=b"AA\nAA"):
    payload = fixture(objects=objects, rows=rows)
    records = []
    offset = 2 + 30 * 5
    for name, data in [("LEVEL1-1", payload), ("GAMEP1", b"palette"),
                       ("LIGHT1", b"light"), ("ONE", b"first"), ("ONE", b"second")]:
        records.append(name.encode().ljust(16, b"\0") + struct.pack("<II", len(data), offset) + b"\0" * 6)
        offset += len(data)
    return parse(struct.pack("<H", 5) + b"".join(records) + payload + b"palettelightfirstsecond")


class ValidationTests(unittest.TestCase):
    def test_coverage_and_unknown_used_type(self):
        archive = archive_fixture()
        model = build_model(archive)
        before = deepcopy(model)
        report = validate_model(model, archive)
        self.assertTrue(report["integrity_ok"])
        self.assertFalse(report["release_ready"])
        self.assertEqual(report["coverage"]["cells"], 8)
        self.assertEqual(report["coverage"]["instances"], 4)
        self.assertIn("unknown-class", report["issue_counts"])
        self.assertEqual(model, before)
        self.assertEqual(report, validate_model(model, archive))

    def test_tampering_cannot_be_hidden_by_flags_or_decoded_copy(self):
        archive = archive_fixture()
        for mutate in (
            lambda m: m["resources"][0]["instances"].pop(),
            lambda m: m["resources"].clear(),
            lambda m: m["catalog"].pop(),
            lambda m: m.update(release_ready=True),
            lambda m: m["resources"][0]["grids"][1]["rows"][0].update(raw_hex="4141"),
            lambda m: m["resources"][0]["resource_references"][0].update(target="forged"),
            lambda m: m["resources"][0]["decoded"].update(lines=[]),
        ):
            model = build_model(archive)
            mutate(model)
            report = validate_model(model, archive)
            self.assertFalse(report["integrity_ok"])
            self.assertIn("source-model-mismatch", report["issue_counts"])

    def test_duplicate_resource_uses_proven_first_match(self):
        archive = archive_fixture(objects=b"Sprite = ONE\nA 4 1 2 3 4")
        model = build_model(archive)
        report = validate_model(model, archive)
        self.assertNotIn("resource-unresolved", report["issue_counts"])
        ref = model["resources"][0]["resource_references"][-1]
        self.assertTrue(ref["target"].endswith(":3"))

    def test_missing_resources_ambiguous_cells_and_links(self):
        archive = archive_fixture(objects=(b"Sprite = MISSING\nA 4 1 2 3 4\nA 4 1 2 3 4\n"
            b"M 9 1 2 3 4 1 2 3 ONE ONE ONE ONE 1 2 Z 0 0 0 A 0 0"), rows=b"AM\nAM")
        report = validate_model(build_model(archive), archive)
        for code in ("resource-unresolved", "cell-definition-ambiguous", "definition-link-unresolved",
                     "definition-link-ambiguous", "mechanic-unverified"):
            self.assertIn(code, report["issue_counts"])

    def test_used_incomplete_fields_and_transitive_mechanics(self):
        archive = archive_fixture(objects=(b"l 4 8 8 14\nZ 11 1 2 3 4\n"
            b"M 9 1 2 3 4 1 2 3 ONE ONE ONE ONE 1 2 Z 0 0 0 Z 0 0"), rows=b"lM\nlM")
        report = validate_model(build_model(archive), archive)
        self.assertIn("incomplete-common-fields", report["issue_counts"])
        self.assertIn("unknown-class", report["issue_counts"])
        self.assertEqual(report["coverage"]["used_definitions"], 3)


class ValidationFileTests(unittest.TestCase):
    def setUp(self):
        self.root = REPO / "build" / ("ir02 tests " + uuid.uuid4().hex)
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)
        self.source = self.root / "source.dat"
        archive = archive_fixture()
        self.source.write_bytes(archive.data)
        self.ir = self.root / "ir.json"
        self.ir.write_text(json.dumps(build_model(archive)), encoding="utf-8")
        self.stage = self.root / "stage"
        self.stage.mkdir()
        (self.stage / "example.txt").write_text("research", encoding="utf-8")

    def test_reports_and_refusal_before_archive_creation(self):
        output = self.root / "report.json"
        write_report(self.source, self.ir, output)
        with self.assertRaises(FileExistsError):
            write_report(self.source, self.ir, output)
        with self.assertRaises(ValueError):
            write_report(self.source, self.ir, self.root / "release.json", "release")
        self.assertFalse(json.loads((self.root / "release.json").read_text())["release_ready"])
        for mode, kwargs in (("raw", {}), ("release", {}),
                             ("release", {"ir_path": self.ir, "dat_source": self.source})):
            destination = self.root / (mode + ".ipk3")
            with self.assertRaises(ValueError):
                pack(self.stage, destination, mode=mode, **kwargs)
            self.assertFalse(destination.exists())

    def test_research_marker_placeholders_and_determinism(self):
        first, second = self.root / "first.pk3", self.root / "second.ipk3"
        for destination in (first, second):
            pack(self.stage, destination, mode="research", ir_path=self.ir, dat_source=self.source)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with zipfile.ZipFile(first) as archive:
            marker = json.loads(archive.read("smuta-build-report.json"))
            self.assertEqual(marker["mode"], "research")
            self.assertTrue(marker["validation"]["research_placeholders"])
            self.assertFalse(marker["playable_game_verified"])
        (self.stage / "SMUTA-BUILD-REPORT.JSON").touch()
        with self.assertRaises(ValueError):
            pack(self.stage, self.root / "collision.pk3", mode="research", ir_path=self.ir, dat_source=self.source)

    def test_cli_release_exit_and_saved_report(self):
        output = self.root / "cli.json"
        result = subprocess.run([sys.executable, "-B", "-m", "smuta_build", "validate",
            "--source", str(self.source), "--ir", str(self.ir), "--output", str(output),
            "--mode", "release"], capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertTrue(output.exists())

    def test_forged_ready_flag_cannot_enable_pack(self):
        model = json.loads(self.ir.read_text())
        model["release_ready"] = True
        self.ir.write_text(json.dumps(model), encoding="utf-8")
        with self.assertRaises(ValueError):
            pack(self.stage, self.root / "forged.pk3", mode="research", ir_path=self.ir, dat_source=self.source)
