"""IR-01: модель Смуты в исходных единицах, без генерации UZDoom."""

from copy import deepcopy
import json
from pathlib import Path

from .dat import parse
from .extract import digest
from .names import NameResolver, name_bytes, resolution_policy
from .paths import no_links, output_path
from .text import DECODER, INTEGER, TEXT_NAME, TOKEN, parse_level

FORMAT = "smuta-ir"
VERSION = 1
COMMON = ("type", "flags", "scale", "vertical", "mapColor")
CLASSES = ("Empty_CelGo", "CharacterGo", "Thick_WallGo", "ThingGo", "WallGo",
           "ActorGo", "PropetyGo", "DoorGo", "MagicGo", "MonsterGo", "WeaponGo",
           None, "WaterGo", "SZoneGo")
# Только порядок, подтверждённый SMUTA_EXE_LOGIC.md. Остальные хвосты непрозрачны.
MOVEMENT = ("speed", "angle", "turnStep")
TAILS = {
    1: MOVEMENT + tuple(f"player{i:02d}" for i in range(11)) +
       ("sound0", "sound1", "unknown96", "unknown98", "unknown9A"),
    3: ("action", "amount"),
    5: MOVEMENT,
    7: ("requiredKeys", "mode", "delay"),
    8: ("speed", "verticalSpeed", "hitFlic", "damage", "destroyFlic"),
    9: MOVEMENT + ("animation0", "animation1", "sound0", "sound1", "health",
        "distanceParameter", "projectileSymbol", "unknown132", "unknown134",
        "unknown112", "dropSymbol", "verticalParameter", "unknownF8"),
    10: ("slot", "fireFrame", "action", "amount"),
}
RESOURCE_FIELDS = {"hitFlic": "animation", "destroyFlic": "animation",
                   "animation0": "animation", "animation1": "animation",
                   "sound0": "sound", "sound1": "sound"}
SYMBOL_FIELDS = {"projectileSymbol", "dropSymbol"}


def resource_id(container_hash: str, index: int) -> str:
    return f"smuta:{container_hash}:{index}"


def provenance(archive, entry, container_hash=None):
    container_hash = container_hash or digest(archive.data)
    return {"id": resource_id(container_hash, entry.index), "game": "smuta",
            "container_sha256": container_hash, "index": entry.index,
            "name": entry.name, "name_bytes_hex": name_bytes(entry).hex(),
            "offset": entry.offset, "length": entry.length,
            "sha256": digest(archive.data[entry.offset:entry.offset + entry.length])}


def new_instance_state(instance: dict) -> dict:
    """Независимое хранилище. Пустой runtime не имитирует конструктор EXE."""
    return {"instance_id": instance["id"], "status": "not-initialized",
            "position": deepcopy(instance["position"]), "runtime": {}}


def lower_document(document: dict, source: dict, resolver: NameResolver) -> dict:
    """Чистое понижение результата TXT-01; не меняет декодированный документ."""
    if document.get("decoder") != DECODER:
        raise ValueError("Неподдержанный декодер текста")
    rid = source["id"]
    lines = {line["line"]: line for line in document["lines"]}
    definitions, references, links, bindings = [], [], [], {}

    def origin(line_number):
        line = lines[line_number]
        return {"line": line_number, "offset": line["offset"], "end": line["end"]}

    def reference(raw, role, location):
        ref = {"id": f"{rid}/ref/{len(references)}", "role": role,
               "name_hex": raw.hex(), "origin": location, "target": None,
               "status": "unresolved"}
        try:
            entry = resolver.resolve(raw)
        except ValueError:
            pass
        else:
            ref.update(target=resource_id(source["container_sha256"], entry.index),
                       status="catalog-resolved")
        references.append(ref)
        return ref["id"]

    # Контексты являются событиями, а не словарём эффективных значений.
    context_refs = {}
    for event in document["events"]:
        if event["kind"] == "directive" and event["section"] != "Game Ini":
            value = event["value"].encode("cp1251", errors="surrogateescape")
            parts = TOKEN.findall(value)
            role = {"Sprite": "sprite", "Flic": "animation", "Sound": "sound",
                    "Palette": "palette", "Light": "light"}.get(event["name"])
            if role and parts:
                context_refs[event["line"]] = reference(parts[0], role, origin(event["line"]))
        if event["kind"] != "definition":
            continue
        did = f"{rid}/definition/{len(definitions)}"
        common = dict(zip(COMMON, event["common_values"]))
        kind = common.get("type")
        definition = {"id": did, "order": len(definitions),
            "origin": origin(event["line"]), "source_key_byte": event["source_key_byte"],
            "key_byte": event["key_byte"], "common": common,
            "class": CLASSES[kind] if kind is not None and 0 <= kind < len(CLASSES) else None,
            "context": deepcopy(event["context"]),
            "resource_bindings": {k: context_refs.get(v["line"]) if v else None
                                  for k, v in event["context"].items()},
            "tail_raw": event["tail"], "parameters": [], "remainder_raw": event["tail"],
            "tail_status": "opaque", "runtime_state": "per-instance"}
        tokens = event["tokens"][6:]
        fields = TAILS.get(kind, ()) if len(common) == 5 else ()
        consumed = 0
        for field, token in zip(fields, tokens):
            raw = bytes.fromhex(token["raw_hex"])
            param = {"name": field, "token": deepcopy(token), "unit": "source-unverified"}
            if field in RESOURCE_FIELDS:
                param["reference_id"] = reference(raw, RESOURCE_FIELDS[field],
                    {"line": event["line"], "offset": token["offset"], "end": token["end"]})
            elif field in SYMBOL_FIELDS:
                if len(raw) != 1:
                    break
                param["key_byte"] = raw[0]
                links.append({"owner": did, "field": field, "key_byte": raw[0],
                              "origin": deepcopy(token), "target_definition_ids": [],
                              "status": "unresolved"})
            else:
                if not INTEGER.fullmatch(token["text"]):
                    break
                param["value"] = int(token["text"])
            definition["parameters"].append(param)
            consumed += 1
        if fields:
            end = event["tokens"][5 + consumed]["end"] if consumed else event["tokens"][5]["end"]
            line = lines[event["line"]]
            raw_line = bytes.fromhex(line["raw_hex"])
            remainder = raw_line[end - line["offset"]:].rstrip(b"\r\n")
            definition["remainder_raw"] = remainder.decode("cp1251", errors="surrogateescape")
            definition["tail_status"] = "prefix-decoded" if consumed == len(fields) else "incomplete-prefix"
        # Поверхности и сенсорные связи требуют отдельного разбора наследуемого хвоста.
        if kind in (0, 12, 13):
            definition["surface_binding"] = {"status": "opaque", "tail_raw": event["tail"]}
        if kind == 13:
            links.append({"owner": did, "field": "sensor-tail", "status": "opaque",
                          "origin": origin(event["line"]), "tail_raw": event["tail"]})
        definitions.append(definition)
        bindings.setdefault(str(event["key_byte"]), []).append(did)

    for link in links:
        if "key_byte" in link:
            targets = bindings.get(str(link["key_byte"]), [])
            link["target_definition_ids"] = targets.copy()
            link["status"] = "unique-candidate" if len(targets) == 1 else (
                "ambiguous" if targets else "unresolved")
    settings = [deepcopy(e) for e in document["events"]
                if e["kind"] == "directive" and e["section"] == "Game Ini"]
    effective = {e["name"]: int(e["value"]) for e in settings}
    # Позиции клеток — индексы исходных строк/столбцов, не координаты Doom.
    grids, instances = [], []
    for grid in document["grids"]:
        gid = f"{rid}/grid/{grid['name']}"
        grids.append({"id": gid, **deepcopy(grid), "definition_bindings": deepcopy(bindings),
                      "role": "unverified", "origin_axes": "column-row-from-zero"})
        if grid["name"] != "TMap":
            continue
        for y, row in enumerate(grid["rows"]):
            for x, key in enumerate(bytes.fromhex(row["raw_hex"])):
                instances.append({"id": f"{gid}/{x}/{y}", "key_byte": key,
                    "definition_ids": bindings.get(str(key), []).copy(),
                    "position": {"grid_id": gid, "column": x, "row": y},
                    "source_offset": row["offset"] + x,
                    "status": "cell-candidate"})
    return {"id": rid, "source": deepcopy(source), "decoded": deepcopy(document),
            "transformations": [*document["transformations"], "lower-to-smuta-ir-v1"],
            "settings": settings, "source_units": {
                "cell_side": effective.get("Cell Side"), "cell_height": effective.get("Cell Height"),
                "horizontal_to_uzdoom": None, "vertical_to_uzdoom": None,
                "time_to_seconds": None, "world_origin": None, "world_axes": None},
            "campaign_assignment": None, "definitions": definitions,
            "resource_references": references, "definition_links": links,
            "grids": grids, "instances": instances,
            "materials": [{"definition_id": d["id"], **d["surface_binding"]}
                          for d in definitions if "surface_binding" in d],
            "animations": [{"reference_id": r["id"], "status": "not-decoded",
                            "directions": None, "frames": None, "delay_unit": None}
                           for r in references if r["role"] == "animation"],
            "validation": "research-model-only", "release_ready": False}


def build_model(archive) -> dict:
    resolver = NameResolver(archive)
    container_hash = digest(archive.data)
    catalog = [provenance(archive, entry, container_hash) for entry in archive.entries]
    resources = []
    for entry in archive.entries:
        if TEXT_NAME.fullmatch(entry.name):
            document = parse_level(archive.data[entry.offset:entry.offset + entry.length])
            resources.append(lower_document(document, catalog[entry.index], resolver))
    if not resources:
        raise ValueError("Нет текстовых ресурсов Смуты")
    return {"format": FORMAT, "schema_version": VERSION, "game": "smuta",
            "resolution_policy": resolution_policy(),
            "semantic_evidence": "docs/SMUTA_EXE_LOGIC.md",
            "catalog": catalog, "resources": resources, "release_ready": False}


def read_model(path: Path) -> dict:
    model = json.loads(no_links(path).read_text(encoding="utf-8"))
    if model.get("format") != FORMAT or model.get("schema_version") != VERSION:
        raise ValueError("Неподдержанный формат/версия IR; нужна явная миграция")
    return model


def write_model(source: Path, destination: Path) -> dict:
    destination = output_path(destination)
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError(f"Выходной каталог не пуст: {destination}")
    model = build_model(parse(no_links(source).read_bytes()))
    destination.mkdir(parents=True, exist_ok=True)
    with (destination / "ir.json").open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(model, stream, ensure_ascii=True, separators=(",", ":"))
        stream.write("\n")
    return {"format": FORMAT, "schema_version": VERSION,
            "resources": len(model["resources"]),
            "definitions": sum(len(r["definitions"]) for r in model["resources"]),
            "instances": sum(len(r["instances"]) for r in model["resources"]),
            "release_ready": False}
