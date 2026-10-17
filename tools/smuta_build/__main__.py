"""CLI отдельных этапов. Вход сборки для Windows — tools/Build.ps1."""

import argparse
import json
from pathlib import Path
import sys

from .extract import check, extract
from .pack import pack
from .text import decode_archive
from .ir import write_model
from .validation import write_report
from .levels import write_comparison
from .graphics import decode_graphics
from .animations import decode_animations
from .auxgraphics import decode_auxgraphics


def main() -> int:
    parser = argparse.ArgumentParser(description="Структурные инструменты Смуты")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("extract", "check", "pack", "text", "ir", "validate", "levels", "graphics", "animations", "auxgraphics"):
        command = commands.add_parser(name)
        command.add_argument("--source", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
        if name in ("pack", "validate"):
            command.add_argument("--ir", type=Path, required=name == "validate")
            command.add_argument("--mode", choices=("raw", "research", "release") if name == "pack"
                                 else ("research", "release"), default="raw" if name == "pack" else "research")
        if name == "pack":
            command.add_argument("--dat-source", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "validate":
            report = write_report(args.source, args.ir, args.output, args.mode)
            result = {k: v for k, v in report.items() if k not in ("issues", "research_placeholders")}
        elif args.command == "pack":
            result = pack(args.source, args.output, mode=args.mode,
                          ir_path=args.ir, dat_source=args.dat_source)
        else:
            result = {"extract": extract, "check": check,
                      "text": decode_archive, "ir": write_model,
                      "levels": write_comparison, "graphics": decode_graphics,
                      "animations": decode_animations,
                      "auxgraphics": decode_auxgraphics}[args.command](args.source, args.output)
        if args.command == "extract":
            result = {"entries": len(result["entries"]), **result["container"]}
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    except (OSError, ValueError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
