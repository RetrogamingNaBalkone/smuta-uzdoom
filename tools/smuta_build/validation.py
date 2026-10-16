"""IR-02: сверка с DAT и консервативный допуск исследовательской модели."""

from collections import Counter
import json
from pathlib import Path

from .dat import parse
from .extract import digest
from .ir import build_model, read_model
from .paths import no_links, output_path


def validate_model(model: dict, archive) -> dict:
    # Не доверяем release_ready, статусам ссылок и копии decoded из самого IR.
    expected = build_model(archive)
    issues = []

    def issue(code, owner, origin=None, **details):
        issues.append({"code": code, "owner": owner, "origin": origin,
                       "blocks_release": True, **details})

    integrity = model == expected
    if not integrity:
        issue("source-model-mismatch", "model",
              sections=[key for key in sorted(set(model) | set(expected))
                        if model.get(key) != expected.get(key)])
    counts = Counter(resources=len(expected["resources"]), catalog=len(expected["catalog"]))
    text_indices = {r["source"]["index"] for r in expected["resources"]}
    for entry in archive.entries:
        if (b"[Game Ini]" in archive.data[entry.offset:entry.offset + entry.length]
                and entry.index not in text_indices):
            issue("uncatalogued-level-text", expected["catalog"][entry.index]["id"])
    used_resources = set()
    for resource in expected["resources"]:
        rid = resource["id"]
        counts["definitions"] += len(resource["definitions"])
        counts["instances"] += len(resource["instances"])
        counts["grids"] += len(resource["grids"])
        definitions = {d["id"]: d for d in resource["definitions"]}
        used = set()
        for grid in resource["grids"]:
            keys = Counter(value for row in grid["rows"] for value in bytes.fromhex(row["raw_hex"]))
            for key, number in sorted(keys.items()):
                targets = grid["definition_bindings"].get(str(key), [])
                counts["cells"] += number
                state = "unique" if len(targets) == 1 else ("ambiguous" if targets else "unresolved")
                counts["cells:" + state] += number
                used.update(targets)
                if state != "unique":
                    issue("cell-definition-" + state, grid["id"], key_byte=key, cells=number)
            issue("grid-role-unverified", grid["id"])
        # Замыкание ссылок: снаряды/выпадения могут не стоять в сетке напрямую.
        changed = True
        while changed:
            previous = len(used)
            for link in resource["definition_links"]:
                if link["owner"] in used:
                    used.update(link.get("target_definition_ids", []))
            changed = len(used) != previous
        counts["used_definitions"] += len(used)
        for link in resource["definition_links"]:
            counts["definition_links:" + link["status"]] += 1
            if link["owner"] in used and link["status"] != "unique-candidate":
                issue("definition-link-" + link["status"], link["owner"], link["origin"], field=link["field"])
        for did in sorted(used):
            definition = definitions[did]
            origin = definition["origin"]
            counts["used_types:" + str(definition["common"].get("type"))] += 1
            if len(definition["common"]) != 5:
                issue("incomplete-common-fields", did, origin, registry_id="SM-U21")
            if definition["class"] is None:
                issue("unknown-class", did, origin)
            if definition["tail_status"] != "prefix-decoded" or definition["remainder_raw"].strip():
                issue("unverified-tail", did, origin, status=definition["tail_status"])
            # Даже разобранный префикс не подтверждает единицы, flags и поведение.
            issue("mechanic-unverified", did, origin, registry="docs/RESEARCH_REGISTRY.md")
        for ref in resource["resource_references"]:
            counts["resource_references:" + ref["status"]] += 1
            if ref["target"]:
                used_resources.add(ref["target"])
            else:
                issue("resource-unresolved", ref["id"], ref["origin"], name_hex=ref["name_hex"])
        for animation in resource["animations"]:
            counts["animations:" + animation["status"]] += 1
        if resource["animations"]:
            issue("animations-not-decoded", rid, count=len(resource["animations"]))
        if resource["materials"]:
            issue("materials-opaque", rid, count=len(resource["materials"]))
        issue("source-units-unverified", rid, units=resource["source_units"])
        issue("campaign-unassigned", rid)
        counts["text_diagnostics"] += len(resource["decoded"]["diagnostics"])
    # IR v1 не хранит доказательств реализации и сравнения игровой логики.
    issue("game-generation-and-comparison-unavailable", "model")
    counts["referenced_catalog_entries"] = len(used_resources)
    counts["unreferenced_catalog_entries"] = len(expected["catalog"]) - len(used_resources)
    return {"format": "smuta-ir-validation", "schema_version": 1,
            "source_sha256": digest(archive.data), "integrity_ok": integrity,
            "coverage_basis": "reconstructed-from-source", "coverage": dict(sorted(counts.items())),
            "issue_counts": dict(sorted(Counter(i["code"] for i in issues).items())),
            "issues": issues, "release_ready": not issues,
            "research_placeholders": [{"code": i["code"], "owner": i["owner"],
                "origin": i["origin"], "status": "unimplemented-no-runtime-substitution"}
                for i in issues if i["code"] != "source-model-mismatch"]}


def check_model(source: Path, model_path: Path) -> dict:
    return validate_model(read_model(model_path), parse(no_links(source).read_bytes()))


def write_report(source: Path, model_path: Path, destination: Path, mode="research") -> dict:
    if mode not in ("research", "release"):
        raise ValueError("Неизвестный режим проверки")
    destination = output_path(destination)
    report = check_model(source, model_path)
    report["mode"] = mode
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
    if not report["integrity_ok"] or (mode == "release" and not report["release_ready"]):
        raise ValueError(f"IR не допущен в режиме {mode}; отчёт: {destination}")
    return report
