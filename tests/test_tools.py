"""Автономные проверки на синтетических данных; оригиналы не требуются."""

import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import shutil
import unittest
import uuid
from unittest.mock import patch
import zipfile

from smuta_build.dat import parse
from smuta_build.extract import check, digest, extract
from smuta_build.pack import pack, safe_name
from smuta_build.paths import REPO, no_links, output_path


def fixture() -> bytes:
    names = ["ЗАСТАВКА_СПР", "ЗАСТАВКА_СПР", "ТРУБА/2", "../x"]
    result = bytearray(struct.pack("<H", len(names)))
    offset = 2 + 30 * len(names) + 3
    for index, name in enumerate(names):
        field = (name.encode("cp1251") + b"\0").ljust(16, b"Q")
        result.extend(field + struct.pack("<II", 2, offset + index * 2) + b"\x81\x00abcd")
    return bytes(result) + b"gap" + b"AABBCCDD" + b"tail\x00\xff"


class DatTests(unittest.TestCase):
    def test_names_unknown_fields_and_gaps(self):
        archive = parse(fixture())
        self.assertEqual([e.name for e in archive.entries],
                         ["ЗАСТАВКА_СПР", "ЗАСТАВКА_СПР", "ТРУБА/2", "../x"])
        self.assertEqual(archive.entries[0].raw[24:], b"\x81\x00abcd")
        self.assertEqual(archive.gaps, ((122, 3), (133, 6)))

    def test_truncated_header_and_directory(self):
        for data in (b"", b"\x01", b"\x01\x00", fixture()[:121]):
            with self.subTest(data=data[:2]), self.assertRaises(ValueError):
                parse(data)

    def test_ranges(self):
        for offset, length in ((0, 1), (121, 1), (138, 2), (0xffffffff, 0xffffffff)):
            data = bytearray(fixture())
            struct.pack_into("<II", data, 18, length, offset)
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                parse(bytes(data))

    def test_unterminated_and_undefined_cp1251(self):
        for field in (b"A" * 16, b"\x98\0" + b"A" * 14):
            data = bytearray(fixture())
            data[2:18] = field
            with self.assertRaises(ValueError):
                parse(bytes(data))

    def test_empty_container_and_zero_length_entry(self):
        self.assertEqual(parse(b"\0\0xyz").gaps, ((2, 3),))
        data = b"\1\0" + b"X\0".ljust(16, b"\0") + struct.pack("<II", 0, 32) + b"\0" * 6
        self.assertEqual(parse(data).entries[0].length, 0)

    def test_overlapping_out_of_order_ranges_preserved(self):
        data = bytearray(fixture())
        struct.pack_into("<II", data, 18, 4, 129)
        struct.pack_into("<II", data, 48, 4, 127)
        archive = parse(bytes(data))
        self.assertEqual(archive.gaps, ((122, 5), (133, 6)))


class FileTests(unittest.TestCase):
    def setUp(self):
        (REPO / "build").mkdir(exist_ok=True)
        # mkdir с наследуемой ACL: tempfile в Python 3.14 задаёт Windows DACL,
        # исключающую дополнительный SID ограниченной среды исполнения.
        self.root = REPO / "build" / ("unit tests " + uuid.uuid4().hex)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)
        self.source = self.root / "source.dat"
        self.source.write_bytes(fixture())
        self.output = self.root / "extracted"

    def test_roundtrip_and_reproducibility(self):
        manifest = extract(self.source, self.output)
        other = self.root / "second"
        extract(self.source, other)
        self.assertTrue(check(self.source, self.output)["restored_byte_for_byte"])
        self.assertEqual(manifest["container"]["sha256"], digest(fixture()))
        for path in self.output.rglob("*"):
            if path.is_file():
                self.assertEqual(path.read_bytes(), (other / path.relative_to(self.output)).read_bytes())
        self.assertEqual(manifest["entries"][0]["path"], "entries/000000.bin")
        self.assertFalse((self.root / "x").exists())
        self.assertEqual(self.source.read_bytes(), fixture())

    def test_overlap_roundtrip(self):
        data = bytearray(fixture())
        struct.pack_into("<II", data, 18, 4, 129)
        self.source.write_bytes(data)
        extract(self.source, self.output)
        self.assertTrue(check(self.source, self.output)["restored_byte_for_byte"])

    def test_invalid_archive_writes_nothing(self):
        self.source.write_bytes(b"\1\0")
        with self.assertRaises(ValueError):
            extract(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_existing_output_not_modified(self):
        extract(self.source, self.output)
        before = (self.output / "manifest.json").read_bytes()
        with self.assertRaises(ValueError):
            extract(self.source, self.output)
        self.assertEqual((self.output / "manifest.json").read_bytes(), before)

    def test_damage_missing_extra_files_and_manifest(self):
        extract(self.source, self.output)
        entry = self.output / "entries/000000.bin"
        entry.write_bytes(b"xx")
        with self.assertRaises(ValueError):
            check(self.source, self.output)
        entry.unlink()
        with self.assertRaises(ValueError):
            check(self.source, self.output)
        entry.write_bytes(b"AA")
        extra = self.output / "extra"
        extra.touch()
        with self.assertRaises(ValueError):
            check(self.source, self.output)
        extra.unlink()
        path = self.output / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["entries"][0]["path"] = "../../source.dat"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(ValueError):
            check(self.source, self.output)

    def test_output_boundaries(self):
        for path in (REPO / "docs/no", REPO / "build", REPO / "dist", REPO / "build/../outside"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                output_path(path)

    def test_link_and_windows_reparse_rejected(self):
        info = self.source.lstat()
        class Reparse:
            st_mode = info.st_mode
            st_file_attributes = 0x400
        with patch.object(Path, "lstat", return_value=Reparse()), self.assertRaises(ValueError):
            no_links(self.source)

    @unittest.skipUnless(sys.platform == "win32", "Проверка Windows junction")
    def test_real_junction_rejected(self):
        target = self.root / "target"
        target.mkdir()
        junction = self.root / "junction"
        quoted_link = str(junction).replace("'", "''")
        quoted_target = str(target).replace("'", "''")
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                                 f"New-Item -ItemType Junction -Path '{quoted_link}' -Target '{quoted_target}' | Out-Null"],
                                capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        try:
            with self.assertRaises(ValueError):
                extract(self.source, junction / "output")
            with self.assertRaises(ValueError):
                pack(self.root, self.root.parent / (self.root.name + ".zip"))
        finally:
            # Удаляем только сам junction в созданном тестовом каталоге.
            os.rmdir(junction)
        self.assertEqual(list(target.iterdir()), [])

    def test_directory_and_tail_corruption(self):
        extract(self.source, self.output)
        directory = self.output / "directory.bin"
        directory.write_bytes(b"bad")
        with self.assertRaises(ValueError):
            check(self.source, self.output)
        directory.write_bytes(fixture()[:122])
        (self.output / "unreferenced/000001.bin").write_bytes(b"wrong tail")
        with self.assertRaises(ValueError):
            check(self.source, self.output)

    def test_empty_archive_roundtrip(self):
        self.source.write_bytes(b"\0\0tail")
        extract(self.source, self.output)
        self.assertTrue(check(self.source, self.output)["restored_byte_for_byte"])

    def test_pack_bytes_and_metadata(self):
        stage = self.root / "stage"
        stage.mkdir()
        (stage / "z").write_bytes(b"last")
        (stage / "a").write_bytes(b"first")
        first, second = self.root / "a.zip", self.root / "b.pk3"
        pack(stage, first)
        (stage / "a").touch()
        pack(stage, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with zipfile.ZipFile(first) as archive:
            self.assertEqual(archive.namelist(), ["a", "z"])
            self.assertEqual(archive.read("a"), b"first")
            for info in archive.infolist():
                self.assertEqual(info.compress_type, zipfile.ZIP_STORED)
                self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))
        with self.assertRaises(ValueError):
            pack(stage, first)
        with self.assertRaises(ValueError):
            pack(stage, stage / "self.zip")
        with self.assertRaises(ValueError):
            pack(stage, self.root / "bad.exe")

    def test_pack_failure_leaves_no_archive(self):
        destination = self.root / "failed.zip"
        stage = self.root / "stage"
        stage.mkdir()
        (stage / "file").write_bytes(b"data")
        with patch.object(Path, "read_bytes", side_effect=OSError("read failed")):
            with self.assertRaises(OSError):
                pack(stage, destination)
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.root.glob(".pack-*.tmp")), [])

    def test_cli_failure_exit(self):
        result = subprocess.run([sys.executable, "-B", "-m", "smuta_build", "extract",
                                 "--source", str(self.root / "missing"), "--output", str(self.output)],
                                capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.output.exists())


class PackageNamesTests(unittest.TestCase):
    def test_unsafe_names(self):
        for name in ("../x", "/x", "a//b", "a\\b", "C:x", "x.", "NUL.txt", "a/COM1", "x\x00"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                safe_name(name)
        safe_name("sprites/SMU0001.png")


if __name__ == "__main__":
    unittest.main()
