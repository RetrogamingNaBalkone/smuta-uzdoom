"""Автономная проверка опасных границ текстовой грамматики."""

import json
import unittest

from smuta_build.text import parse_level


def fixture(objects=b"_ 0 16 8 0 104 tail", rows=b"_ \n\xa0\x98", ending=b"\n"):
    return (b"# comment\n[Game Ini]\nLevel Width = 2\nLevel Length = 2\n"
            b"Level Height = 1\n[Palette]\nPalette = GAMEP1\n[Light]\nLight = LIGHT1\n"
            b"[Game Object]\n" + objects + b"\n[TMap]\n# map\n" + rows +
            b"\n[FCMap]\n[O\n# " + ending)


class TextTests(unittest.TestCase):
    def test_byte_keys_and_grid_characters(self):
        doc = parse_level(fixture(objects=b"_ 0 16 8 0 104\n\xa0 2 2 8 1600 14\n\x98 0 1 2 3 4"))
        definitions = [e for e in doc["events"] if e["kind"] == "definition"]
        self.assertEqual([d["key_byte"] for d in definitions], [32, 160, 152])
        self.assertEqual(doc["grids"][0]["rows"][0]["text"], "_ ")
        self.assertEqual(doc["grids"][1]["rows"][0]["text"], "[O")
        self.assertEqual(doc["grids"][1]["rows"][1]["text"], "# ")
        self.assertEqual(json.loads(json.dumps(doc)), doc)

    def test_context_order_and_optional_sprite_parameter(self):
        doc = parse_level(fixture(objects=(
            b"Sprite = ONE 24\nSound = HIT\nFlic = ANIM\nA 1 2 3 4 5\n"
            b"Sprite = TWO\nB 1 2 3 4 5\nSound = OTHER\nC 1 2 3 4 5")))
        defs = [e for e in doc["events"] if e["kind"] == "definition"]
        self.assertEqual(defs[0]["context"]["Sprite"]["value"], "ONE 24")
        self.assertEqual(defs[1]["context"]["Sprite"]["value"], "TWO")
        self.assertEqual(defs[1]["context"]["Sound"]["value"], "HIT")
        self.assertEqual(defs[2]["context"]["Sound"]["value"], "OTHER")
        self.assertTrue(all(d["context"]["Flic"]["value"] == "ANIM" for d in defs))

    def test_duplicate_definition_is_retained_and_diagnosed(self):
        doc = parse_level(fixture(objects=b"A 1 2 3 4 5\nA 6 7 8 9 10"))
        defs = [e for e in doc["events"] if e["kind"] == "definition"]
        self.assertEqual(len(defs), 2)
        self.assertEqual(doc["diagnostics"][0]["code"], "duplicate-definition")
        self.assertEqual(doc["diagnostics"][0]["first_line"], defs[0]["line"])

    def test_short_definition_has_no_invented_field(self):
        doc = parse_level(fixture(objects=b"l 4 8 8 14"))
        definition = next(e for e in doc["events"] if e["kind"] == "definition")
        self.assertEqual(definition["common_values"], [4, 8, 8, 14])
        self.assertEqual(doc["diagnostics"][0]["code"], "incomplete-common-fields")

    def test_source_ranges_and_roundtrip(self):
        data_lf = fixture(objects=b"\tA 1 -2 +3 4 5  message # tail")
        for data in (data_lf, data_lf[:-1], data_lf.replace(b"\n", b"\r\n")):
            doc = parse_level(data)
            self.assertEqual(b"".join(bytes.fromhex(l["raw_hex"]) for l in doc["lines"]), data)
            for line in doc["lines"]:
                self.assertEqual(data[line["offset"]:line["end"]], bytes.fromhex(line["raw_hex"]))
            for event in doc["events"]:
                for token in event.get("tokens", []):
                    self.assertEqual(data[token["offset"]:token["end"]], bytes.fromhex(token["raw_hex"]))
            definition = next(e for e in doc["events"] if e["kind"] == "definition")
            self.assertEqual(definition["tail"], "  message # tail")

    def test_non_ascii_line_separators_remain_cells(self):
        doc = parse_level(fixture(rows=b"\x85\xa0\n\xc0\xe0"))
        self.assertEqual([r["raw_hex"] for r in doc["grids"][0]["rows"]], ["85a0", "c0e0"])

    def test_bad_structure_and_ranges_are_rejected(self):
        valid = fixture()
        bad = [valid.replace(b"_ \n", b"_\n"), valid + b"EXTRA\n",
               valid.replace(b"Level Height = 1", b"Level Height = 2"),
               valid.replace(b"[Palette]", b"[Unknown]"),
               valid.replace(b"[FCMap]\n[O\n# ", b"[FCMap]\n[O"),
               valid.replace(b"Level Width = 2", b"Level Width = 0"),
               fixture(objects=b"A 1 2 x 4 5"), fixture(objects=b"A 1 2 3 4 5\0"),
               fixture(objects=b"Flic = ONE TWO"), fixture(objects=b"Sprite = ONE x")]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(ValueError):
                parse_level(data)
