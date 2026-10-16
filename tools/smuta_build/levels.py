"""LVL-01: сравнение вариантов в исходных единицах, без генерации карт."""

from itertools import combinations
import json
from pathlib import Path
import re

from .dat import parse
from .extract import digest
from .ir import COMMON, TAILS, lower_document, provenance
from .names import NameResolver
from .paths import no_links, output_path
from .text import TOKEN, parse_level

LEVEL_NAME = re.compile(r"LEVEL([1-9]|10)-([123])\Z")


def value_tokens(value):
    return [part.hex() for part in TOKEN.findall(
        value.encode("cp1251", errors="surrogateescape"))]


def definition_rows(document):
    """Ключ + номер вхождения: повторы не затираются, порядок проверяется отдельно."""
    counts, result = {}, {}
    for order, event in enumerate(e for e in document["events"] if e["kind"] == "definition"):
        key = event["key_byte"]
        occurrence = counts.get(key, 0)
        counts[key] = occurrence + 1
        result[key, occurrence] = {
            "order": order, "line": event["line"],
            "source_key_byte": event["source_key_byte"],
            "common": dict(zip(COMMON, event["common_values"])),
            "tail_tokens_hex": value_tokens(event["tail"]),
            "tail_raw": event["tail"],
            "context": {k: value_tokens(v["value"]) if v else None
                        for k, v in event["context"].items()},
        }
    return result


def compare_documents(left, right):
    """Чистое сравнение TXT-01; координаты с нуля, строки исходника с единицы."""
    grids = []
    for a, b in zip(left["grids"], right["grids"], strict=True):
        if a["name"] != b["name"]:
            raise ValueError("Разный порядок сеток")
        changes = []
        ar = [bytes.fromhex(r["raw_hex"]) for r in a["rows"]]
        br = [bytes.fromhex(r["raw_hex"]) for r in b["rows"]]
        for y in range(max(len(ar), len(br))):
            aa = ar[y] if y < len(ar) else b""
            bb = br[y] if y < len(br) else b""
            for x in range(max(len(aa), len(bb))):
                av = aa[x] if x < len(aa) else None
                bv = bb[x] if x < len(bb) else None
                if av != bv:
                    changes.append({"column": x, "row": y, "before_byte": av,
                                    "after_byte": bv,
                                    "before_line": a["rows"][y]["line"] if y < len(ar) else None,
                                    "after_line": b["rows"][y]["line"] if y < len(br) else None})
        grids.append({"name": a["name"], "before_size": [a["width"], a["height"]],
                      "after_size": [b["width"], b["height"]], "changes": changes,
                      "changed_cells": len(changes)})
    aa, bb = definition_rows(left), definition_rows(right)
    definitions = []
    for key, occurrence in sorted(aa.keys() | bb.keys()):
        a, b = aa.get((key, occurrence)), bb.get((key, occurrence))
        fields = [f for f in ("order", "source_key_byte", "common", "tail_tokens_hex", "context")
                  if (a or {}).get(f) != (b or {}).get(f)]
        if fields:
            common_changes = [{"field": f, "before": (a or {}).get("common", {}).get(f),
                               "after": (b or {}).get("common", {}).get(f)} for f in COMMON
                              if (a or {}).get("common", {}).get(f) != (b or {}).get("common", {}).get(f)]
            at = (a or {}).get("tail_tokens_hex", [])
            bt = (b or {}).get("tail_tokens_hex", [])
            kind = (a or {}).get("common", {}).get("type")
            names = TAILS.get(kind, ()) if (a and b and len(a["common"]) == len(COMMON)
                and len(b["common"]) == len(COMMON) and kind == b["common"].get("type")) else ()
            token_changes = [{"position": i, "field": names[i] if i < len(names) else None,
                              "before_hex": at[i] if i < len(at) else None,
                              "after_hex": bt[i] if i < len(bt) else None}
                             for i in range(max(len(at), len(bt)))
                             if (at[i] if i < len(at) else None) != (bt[i] if i < len(bt) else None)]
            definitions.append({"key_byte": key, "occurrence": occurrence,
                                "changed_fields": fields, "common_changes": common_changes,
                                "tail_token_changes": token_changes, "before": a, "after": b})
    def directives(doc, object_context):
        return [{"section": e["section"], "name": e["name"], "value_tokens_hex": value_tokens(e["value"])}
                for e in doc["events"] if e["kind"] == "directive"
                and (e["section"] == "Game Object") == object_context]
    directive_changes = {}
    for name, context in (("settings_and_resources", False), ("context_sequence", True)):
        a, b = directives(left, context), directives(right, context)
        directive_changes[name] = {"equal": a == b, "before": a, "after": b}
    return {"grids": grids, "definition_order_equal": list(aa) == list(bb),
            "definitions": definitions, "changed_definitions": len(definitions),
            "parameter_changes": sum(any(f in d["changed_fields"] for f in
                                         ("common", "tail_tokens_hex")) for d in definitions),
            "context_changes": sum("context" in d["changed_fields"] for d in definitions),
            "directives": directive_changes}


def build_comparison(data):
    archive = parse(data)
    resolver = NameResolver(archive)
    documents, variants = {}, []
    for entry in archive.entries:
        match = LEVEL_NAME.fullmatch(entry.name)
        if not match:
            continue
        key = tuple(map(int, match.groups()))
        if key in documents:
            raise ValueError(f"Неоднозначный вариант: {entry.name}")
        document = parse_level(data[entry.offset:entry.offset + entry.length])
        source = provenance(archive, entry)
        model = lower_document(document, source, resolver)
        documents[key] = document
        variants.append({"number": key[0], "variant": key[1], "source": source,
                         "diagnostics": document["diagnostics"],
                         "resource_references": model["resource_references"],
                         "definitions": [{"key_byte": d["key_byte"], "order": d["order"],
                             "origin": d["origin"], "common": d["common"],
                             "parameters": d["parameters"], "tail_status": d["tail_status"],
                             "remainder_raw": d["remainder_raw"], "context": d["context"]}
                             for d in model["definitions"]],
                         "grids": [{"name": g["name"], "width": g["width"], "height": g["height"],
                             "sha256": digest(b"".join(bytes.fromhex(r["raw_hex"]) for r in g["rows"]))}
                             for g in document["grids"]]})
    if set(documents) != {(n, v) for n in range(1, 11) for v in range(1, 4)}:
        raise ValueError("Ожидаются все десять троек LEVELn-1/2/3")
    groups = [{"number": n, "pairs": [
        {"before": f"LEVEL{n}-{a}", "after": f"LEVEL{n}-{b}",
         **compare_documents(documents[n, a], documents[n, b])}
        for a, b in combinations(range(1, 4), 2)]} for n in range(1, 11)]
    return {"schema_version": 1, "format": "smuta-level-comparison",
            "container_sha256": digest(data), "validation": "static-comparison-only",
            "representation": "ten-level-numbers-with-three-independent-variants",
            "variants": sorted(variants, key=lambda v: (v["number"], v["variant"])),
            "groups": groups,
            "excluded_from_campaign": [provenance(archive, e) for e in archive.entries
                                       if re.fullmatch(r"DEMO(?:[1-9]|10)?|TEST", e.name)]}


def write_comparison(source: Path, destination: Path):
    report = build_comparison(no_links(source).read_bytes())
    destination = output_path(destination)
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError(f"Выходной каталог не пуст: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    with (destination / "levels.json").open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(report, ensure_ascii=True, indent=2) + "\n")
    return {"groups": len(report["groups"]), "variants": len(report["variants"]),
            "pairs": sum(len(g["pairs"]) for g in report["groups"]),
            "container_sha256": report["container_sha256"]}
