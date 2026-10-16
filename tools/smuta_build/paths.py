"""Ограничения путей записи и отказ от ссылок Windows/Unix."""

from pathlib import Path
import stat

REPO = Path(__file__).resolve().parents[2]


def no_links(path: Path) -> Path:
    path = path.absolute()
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"Ссылка или reparse point: {part}")
    return path


def output_path(path: Path) -> Path:
    path = no_links(path).resolve()
    if not any(path.is_relative_to(REPO / root) and path != REPO / root
               for root in ("build", "dist")):
        raise ValueError("Выход должен находиться внутри build/ или dist/ проекта")
    return path


def files_under(root: Path) -> list[Path]:
    root = no_links(root)
    if not root.is_dir():
        raise ValueError(f"Нет каталога: {root}")
    files = []
    # Проверяем каталог до обхода: junction не должен попадать в рекурсию.
    def visit(directory: Path) -> None:
        for path in sorted(directory.iterdir()):
            no_links(path)
            if path.is_dir():
                visit(path)
            elif path.is_file():
                files.append(path)
            else:
                raise ValueError(f"Не обычный файл: {path}")
    visit(root)
    return files
