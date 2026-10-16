"""LVL-01: сравнение не теряет ключи, контексты, повторы и края сеток."""

from copy import deepcopy
import unittest

from smuta_build.levels import build_comparison, compare_documents
from smuta_build.text import parse_level
from test_text import fixture


class LevelTests(unittest.TestCase):
    def test_context_values_ignore_line_numbers_but_detect_binding_change(self):
        a = parse_level(fixture(objects=b"Sprite = ONE 24\nA 1 2 3 4 5\nB 1 2 3 4 5"))
        b = parse_level(fixture(objects=b"# extra\nSprite = ONE 24\nA 1 2 3 4 5\nB 1 2 3 4 5"))
        self.assertEqual(compare_documents(a, b)["changed_definitions"], 0)
        b = parse_level(fixture(objects=b"Sprite = ONE 24\nA 1 2 3 4 5\nSprite = TWO\nB 1 2 3 4 5"))
        diff = compare_documents(a, b)
        self.assertEqual(diff["context_changes"], 1)
        self.assertEqual(diff["parameter_changes"], 0)
        self.assertFalse(diff["directives"]["context_sequence"]["equal"])

    def test_each_byte_including_undefined_nbsp_and_spaces(self):
        a = parse_level(fixture(rows=b" \xa0\n\x98A"))
        b = parse_level(fixture(rows=b"_ \n\x98a"))
        changes = compare_documents(a, b)["grids"][0]["changes"]
        self.assertEqual([(d["row"], d["column"], d["before_byte"], d["after_byte"])
                          for d in changes], [(0, 0, 32, 95), (0, 1, 160, 32), (1, 1, 65, 97)])

    def test_duplicate_keys_and_definition_order(self):
        a = parse_level(fixture(objects=b"_ 0 1 2 3 4\nA 3 1 2 3 4 2 10\nA 3 1 2 3 4 2 20"))
        b = parse_level(fixture(objects=b"A 3 1 2 3 4 2 10\n_ 0 1 2 3 4\nA 3 1 2 3 4 2 21"))
        diff = compare_documents(a, b)
        self.assertFalse(diff["definition_order_equal"])
        self.assertEqual(diff["parameter_changes"], 1)
        self.assertEqual([d["occurrence"] for d in diff["definitions"] if d["key_byte"] == 65], [0, 1])
        changed = next(d for d in diff["definitions"] if d["key_byte"] == 65 and d["occurrence"] == 1)
        self.assertEqual(changed["tail_token_changes"][0]["field"], "amount")

    def test_incomplete_fields_missing_definitions_and_changed_dimensions(self):
        a = parse_level(fixture(objects=b"l 4 8 8 14\nB 1 2 3 4 5"))
        b = parse_level(fixture(objects=b"l 4 8 8 14 0"))
        changes = compare_documents(a, b)["definitions"]
        self.assertEqual(len(changes), 2)
        incomplete = next(d for d in changes if d["key_byte"] == 108)
        self.assertNotIn("mapColor", incomplete["before"]["common"])
        smaller = deepcopy(a)
        smaller["grids"][0]["rows"].pop()
        smaller["grids"][0]["height"] = 1
        self.assertEqual(compare_documents(a, smaller)["grids"][0]["changed_cells"], 2)

    def test_ordered_settings_and_context_directives_not_dictionary(self):
        a = parse_level(fixture().replace(b"[Palette]", b"X = 1\nX = 2\n[Palette]"))
        b = parse_level(fixture().replace(b"[Palette]", b"X = 2\n[Palette]"))
        self.assertFalse(compare_documents(a, b)["directives"]["settings_and_resources"]["equal"])
        with self.assertRaisesRegex(ValueError, "десять троек"):
            build_comparison(b"\0\0")
