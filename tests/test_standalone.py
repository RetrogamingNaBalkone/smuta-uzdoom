"""Проверки упаковки синтетического UZD-01 без оригиналов и движка."""

import json
import shutil
import struct
import unittest
import uuid
import zipfile

from smuta_build.paths import REPO
from smuta_build.standalone import build_standalone


class StandaloneTests(unittest.TestCase):
    def test_package_reproducibility_and_map_container(self):
        root = REPO / "build" / ("standalone test " + uuid.uuid4().hex)
        root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, root)
        first, second = root / "a.ipk3", root / "b.ipk3"
        build_standalone(first)
        build_standalone(second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with zipfile.ZipFile(first) as z:
            report = json.loads(z.read("smuta-build-report.json"))
            self.assertFalse(report["release_ready"])
            self.assertFalse(report["original_resources"])
            self.assertFalse(any(n.startswith(("music/", "sounds/")) for n in z.namelist()))
            wad = z.read("maps/smtest.wad")
            magic, count, directory = struct.unpack_from("<4sII", wad)
            self.assertEqual((magic, count), (b"PWAD", 3))
            self.assertEqual(directory + count * 16, len(wad))
            text_offset, text_size, name = struct.unpack_from("<II8s", wad, directory + 16)
            self.assertEqual(name.rstrip(b"\0"), b"TEXTMAP")
            self.assertEqual(wad[text_offset:text_offset + text_size],
                             (REPO / "game/standalone/room.udmf").read_bytes())
        with self.assertRaises(ValueError):
            build_standalone(first)
        with self.assertRaises(ValueError):
            build_standalone(root / "bad.pk3")


if __name__ == "__main__":
    unittest.main()
