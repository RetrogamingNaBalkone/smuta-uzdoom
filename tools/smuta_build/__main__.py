"""CLI отдельных этапов. Вход сборки для Windows — tools/Build.ps1."""

import argparse
import json
from pathlib import Path
import sys

from .extract import check, extract
from .pack import pack


def main() -> int:
    parser = argparse.ArgumentParser(description="Структурные инструменты Смуты")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("extract", "check", "pack"):
        command = commands.add_parser(name)
        command.add_argument("--source", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = {"extract": extract, "check": check, "pack": pack}[args.command](args.source, args.output)
        if args.command == "extract":
            result = {"entries": len(result["entries"]), **result["container"]}
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    except (OSError, ValueError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
