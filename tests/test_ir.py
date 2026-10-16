"""IR-01: сохранность данных и границы семантической интерпретации."""

from copy import deepcopy
import json
import unittest

from smuta_build.ir import lower_document, new_instance_state
from smuta_build.names import NameResolver
from smuta_build.text import parse_level
from test_names import archive_with_names
from test_text import fixture


def lower(objects, rows=b"_ \n\xa0\x98"):
    document = parse_level(fixture(objects=objects, rows=rows))
    archive = archive_with_names(["ONE", "ONE", "ANIM", "HIT", "GAMEP1", "LIGHT1"])
    source = {"id": "smuta:hash:7", "container_sha256": "hash", "index": 7}
    return lower_document(document, source, NameResolver(archive))


class IRTests(unittest.TestCase):
    def test_definitions_order_duplicates_and_missing_fields(self):
        model = lower(b"A 3 2 3 4 5 0 100 message # text\nA 4 5 6 7 8\nl 4 8 8 14")
        defs = model["definitions"]
        self.assertEqual([d["key_byte"] for d in defs], [65, 65, 108])
        self.assertNotEqual(defs[0]["id"], defs[1]["id"])
        self.assertEqual(defs[0]["parameters"][1]["value"], 100)
        self.assertEqual(defs[0]["remainder_raw"], " message # text")
        self.assertNotIn("mapColor", defs[2]["common"])
        self.assertIn("incomplete-common-fields", [d["code"] for d in model["decoded"]["diagnostics"]])

    def test_context_refs_are_ordered_exact_and_first_match(self):
        model = lower(b"Sprite = ONE 24\nSound = HIT\nFlic = ANIM\nA 4 2 3 4 5\n"
                      b"Sprite = missing\nB 4 2 3 4 5")
        a, b = model["definitions"]
        self.assertEqual(a["resource_bindings"]["Sound"], b["resource_bindings"]["Sound"])
        self.assertNotEqual(a["resource_bindings"]["Sprite"], b["resource_bindings"]["Sprite"])
        refs = {r["id"]: r for r in model["resource_references"]}
        self.assertEqual(refs[a["resource_bindings"]["Sprite"]]["target"], "smuta:hash:0")
        self.assertEqual(refs[b["resource_bindings"]["Sprite"]]["status"], "unresolved")
        self.assertIsNone(model["animations"][0]["frames"])

    def test_cells_bytes_coordinates_and_ambiguity(self):
        model = lower(b"_ 0 1 2 3 4\n_ 0 5 6 7 8\n\xa0 4 1 2 3 4\n\x98 4 1 2 3 4")
        instances = model["instances"]
        self.assertEqual([i["key_byte"] for i in instances], [95, 32, 160, 152])
        self.assertEqual(instances[0]["definition_ids"], [])
        self.assertEqual(len(instances[1]["definition_ids"]), 2)
        self.assertEqual(instances[3]["position"]["column"], 1)
        self.assertEqual(instances[3]["position"]["row"], 1)
        self.assertEqual(len(model["grids"]), 2)
        self.assertEqual(model["grids"][1]["rows"][0]["raw_hex"], "5b4f")
        for instance in instances:
            row = model["grids"][0]["rows"][instance["position"]["row"]]
            self.assertEqual(instance["source_offset"], row["offset"] + instance["position"]["column"])

    def test_state_is_independent_of_template_and_other_instances(self):
        model = lower(b"_ 3 1 2 3 4 0 100 text", rows=b"  \n  ")
        before = deepcopy(model)
        first = new_instance_state(model["instances"][0])
        second = new_instance_state(model["instances"][1])
        first["position"]["row"] = 999
        first["runtime"]["health"] = 12
        self.assertEqual(second["runtime"], {})
        self.assertEqual(model, before)

    def test_negative_parameters_and_symbol_links(self):
        model = lower(b"1 8 1 2 3 4 -150 -2 ANIM -10 MISSING\n"
            b"M 9 1 2 3 4 150 180 7 ANIM ANIM HIT HIT -52 100 1 2 3 -4 1 -5 6 msg")
        projectile, monster = model["definitions"]
        self.assertEqual([p.get("value") for p in projectile["parameters"]], [-150, -2, None, -10, None])
        self.assertEqual(monster["parameters"][7]["value"], -52)
        self.assertEqual(len(model["definition_links"]), 2)
        self.assertTrue(all(l["target_definition_ids"] == [projectile["id"]]
                            for l in model["definition_links"]))
        self.assertEqual(monster["remainder_raw"], " msg")

    def test_unknown_types_and_tails_are_not_defaults(self):
        model = lower(b"A 11 1 2 3 4 arbitrary tail\nB 99 1 2 3 4 more\n"
                      b"C 13 1 2 3 4 unknown inherited surface and sensor")
        self.assertIsNone(model["definitions"][0]["class"])
        self.assertIsNone(model["definitions"][1]["class"])
        self.assertEqual(model["definition_links"][0]["status"], "opaque")
        self.assertEqual(model["materials"][0]["status"], "opaque")
        self.assertFalse(model["release_ready"])
        for tail in (b"0", b"0 1_000 message", b"invalid 100 message"):
            definition = lower(b"A 3 1 2 3 4 " + tail)["definitions"][0]
            self.assertEqual(definition["tail_status"], "incomplete-prefix")
            self.assertEqual(definition["tail_raw"], " " + tail.decode("ascii"))

    def test_lowering_does_not_mutate_text_and_json_roundtrip(self):
        document = parse_level(fixture())
        before = deepcopy(document)
        lower_document(document, {"id": "smuta:h:1", "container_sha256": "h"},
                       NameResolver(archive_with_names([])))
        self.assertEqual(document, before)
        model = lower(b"\x98 3 1 2 3 4 0 10 text")
        self.assertEqual(json.loads(json.dumps(model)), model)
        bad = deepcopy(document)
        bad["decoder"] = "future"
        with self.assertRaises(ValueError):
            lower_document(bad, {}, None)
