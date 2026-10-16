"""DAT-02: байтовое разрешение ссылок и безопасные имена результата."""

import struct
import unittest

from smuta_build.dat import parse
from smuta_build.extract import layout
from smuta_build.names import NameResolver, name_bytes, output_name
from smuta_build.pack import safe_name


def archive_with_names(names):
    end = 2 + 30 * len(names)
    records = []
    for name in names:
        raw = name if isinstance(name, bytes) else name.encode("cp1251")
        records.append((raw + b"\0").ljust(16, b"Q") +
                       struct.pack("<II", 0, end) + b"\0" * 6)
    return parse(struct.pack("<H", len(names)) + b"".join(records))


class NameTests(unittest.TestCase):
    def test_exact_bytes_first_match_and_padding(self):
        archive = archive_with_names(["ЗАСТАВКА_СПР", "ЗАСТАВКА_СПР", "заставка_спр",
                                      "ABC", "abc", " ABC", "ABC ", "\xa0", " ", ""])
        resolver = NameResolver(archive)
        self.assertEqual(resolver.resolve("ЗАСТАВКА_СПР").index, 0)
        self.assertEqual(resolver.resolve("заставка_спр").index, 2)
        for index in range(2, len(archive.entries)):
            entry = archive.entries[index]
            self.assertIs(resolver.resolve(entry.name), entry)
            self.assertIs(resolver.resolve(name_bytes(entry)), entry)
        # Заполнение после NUL не участвует, даже если отличается у дубликата.
        data = bytearray(archive.data)
        data[2 + 30 + 15] = 0x98
        self.assertEqual(NameResolver(parse(bytes(data))).resolve("ЗАСТАВКА_СПР").index, 0)
        self.assertEqual(len(archive.entries), 10)

    def test_missing_or_invalid_reference_fails(self):
        resolver = NameResolver(archive_with_names(["ABC", "ТРУБА/2"]))
        for query in ("Abc", "ТРУБА\\2", "missing", "ABC\0", b"ABC\0suffix",
                      "x" * 16, "🙂", b"\x98"):
            with self.subTest(query=query), self.assertRaises(ValueError):
                resolver.resolve(query)
        with self.assertRaises(ValueError):
            NameResolver(archive_with_names([])).resolve("")

    def test_stable_unique_names_and_explicit_table(self):
        names = ["ТРУБА/2", "../x", "..", ".", "a\\b", "/root", "C:x", "NUL",
                 "CON.txt", "COM1", "x.", "x ", "A", "a", "A", ""]
        archive = archive_with_names(names)
        manifest, files = layout(archive)
        outputs = [entry["output_name"] for entry in manifest["entries"]]
        self.assertEqual(len(outputs), len(set(name.casefold() for name in outputs)))
        self.assertEqual(outputs, [f"sm{index:06d}" for index in range(len(names))])
        for index, entry in enumerate(manifest["entries"]):
            name = entry["output_name"]
            self.assertTrue(name.isascii())
            self.assertEqual(len(name), 8)
            safe_name(name + ".bin")
            self.assertEqual(entry["resolved_index"], names.index(names[index]))
            self.assertEqual(entry["name_bytes_hex"], names[index].encode("cp1251").hex())
            self.assertIn(entry["path"], files)
        renamed = archive_with_names(["other"] * len(names))
        self.assertEqual(outputs, [output_name(entry) for entry in renamed.entries])
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["name_resolution"]["id"], "cp1251-exact-bytes-first-v1")


if __name__ == "__main__":
    unittest.main()
